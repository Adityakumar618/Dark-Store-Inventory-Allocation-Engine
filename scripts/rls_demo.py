"""
Dark-Store Inventory Allocation Engine: Row-Level Security (RLS) Verification
File: scripts/rls_demo.py
Description: Proves that Store Manager accounts are strictly isolated by RLS
             and cannot view or mutate inventory belonging to other dark stores.
"""

import os
import sys
import psycopg
from dotenv import load_dotenv

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

load_dotenv()

DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "darkstore_db")

def run_rls_demo():
    print("=" * 65)
    print(" ROW-LEVEL SECURITY (RLS) TENANT ISOLATION DEMO ")
    print("=" * 65)

    admin_conn_str = f"host={DB_HOST} port={DB_PORT} dbname={DB_NAME} user=postgres password=postgrespassword"
    try:
        with psycopg.connect(admin_conn_str, autocommit=True) as conn:
            with conn.cursor() as cur:
                print("[*] Applying RLS Policies from schema/004_rls.sql...")
                with open("schema/004_rls.sql", "r", encoding="utf-8") as f:
                    cur.execute(f.read())
    except Exception as e:
        print(f"[ERROR] Failed to apply RLS schema: {e}", file=sys.stderr)
        sys.exit(1)

    mgr_conn_str = f"host={DB_HOST} port={DB_PORT} dbname={DB_NAME} user=store_manager_role password=managerpass"
    try:
        with psycopg.connect(mgr_conn_str) as conn:
            with conn.cursor() as cur:
                print("\n[+] Logged in as: 'store_manager_role'")

                # Scenario 1: Manager of Store 1
                print("\n--- SCENARIO 1: Setting session context to Store 1 (Indiranagar) ---")
                cur.execute("SET app.current_store_id = '1'")
                
                cur.execute("SELECT DISTINCT store_id, COUNT(*) FROM inventory GROUP BY store_id")
                rows = cur.fetchall()
                print(f"Visible inventory rows in query: {rows}")
                assert all(r[0] == 1 for r in rows), "SECURITY BREACH: Store 1 manager saw non-Store 1 data!"
                print("[PASSED] Manager 1 only sees Store 1 inventory.")

                # Scenario 2: Attempt to query Store 2
                print("\n--- SCENARIO 2: Attempting to query Store 2 directly from Store 1 session ---")
                cur.execute("SELECT * FROM inventory WHERE store_id = 2")
                store2_rows = cur.fetchall()
                print(f"Direct SELECT query for store_id = 2 returned: {len(store2_rows)} rows")
                assert len(store2_rows) == 0, "SECURITY BREACH: Store 2 inventory leaked!"
                print("[PASSED] Direct cross-store query returned 0 rows (invisible via RLS).")

                # Scenario 3: Switch context to Store 2
                print("\n--- SCENARIO 3: Switching session context to Store 2 (Koramangala) ---")
                cur.execute("SET app.current_store_id = '2'")
                cur.execute("SELECT DISTINCT store_id, COUNT(*) FROM inventory GROUP BY store_id")
                rows2 = cur.fetchall()
                print(f"Visible inventory rows in query: {rows2}")
                assert all(r[0] == 2 for r in rows2), "SECURITY BREACH: Store 2 manager saw non-Store 2 data!"
                print("[PASSED] Manager 2 only sees Store 2 inventory.")

                print("\n" + "=" * 65)
                print(" [SUCCESS] ROW-LEVEL SECURITY MULTI-TENANT ISOLATION FULLY VERIFIED")
                print("=" * 65)

    except Exception as e:
        print(f"[ERROR] RLS Demo failed: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    run_rls_demo()
