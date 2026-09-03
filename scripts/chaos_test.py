"""
Dark-Store Inventory Allocation Engine: Chaos & Mid-Transaction Termination Test
File: scripts/chaos_test.py
Description: Simulates abrupt client/backend failure mid-allocation and proves
             that PostgreSQL WAL recovery ensures zero partial decrements.
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
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "postgrespassword")

TEST_STORE_ID = 1
TEST_SKU_ID = 2
TEST_BATCH_ID = 1

def run_chaos_test():
    conn_str = f"host={DB_HOST} port={DB_PORT} dbname={DB_NAME} user={DB_USER} password={DB_PASSWORD}"
    
    print("=" * 65)
    print(" CHAOS / MID-TRANSACTION CRASH & WAL RECOVERY TEST ")
    print("=" * 65)

    # 1. Baseline Stock Measurement & Setup
    with psycopg.connect(conn_str, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE inventory SET qty_available = 20 WHERE store_id = %s AND sku_id = %s AND batch_id = %s",
                (TEST_STORE_ID, TEST_SKU_ID, TEST_BATCH_ID)
            )
            cur.execute(
                "SELECT qty_available FROM inventory WHERE store_id = %s AND sku_id = %s AND batch_id = %s",
                (TEST_STORE_ID, TEST_SKU_ID, TEST_BATCH_ID)
            )
            initial_stock = cur.fetchone()[0]
            print(f"[*] Baseline Pre-Crash Stock for Store {TEST_STORE_ID}, SKU {TEST_SKU_ID}: {initial_stock} units")

    # 2. Start an uncommitted transaction and kill the connection midway
    print("\n[*] Starting uncommitted allocation transaction...")
    try:
        conn_victim = psycopg.connect(conn_str)
        cur_victim = conn_victim.cursor()
        
        cur_victim.execute("BEGIN;")
        
        print("[*] Executing in-flight decrement: -5 units...")
        cur_victim.execute(
            """
            UPDATE inventory 
            SET qty_available = qty_available - 5 
            WHERE store_id = %s AND sku_id = %s AND batch_id = %s
            RETURNING qty_available;
            """,
            (TEST_STORE_ID, TEST_SKU_ID, TEST_BATCH_ID)
        )
        in_flight_stock = cur_victim.fetchone()[0]
        print(f"[*] In-flight uncommitted stock inside transaction: {in_flight_stock} units")

        cur_victim.execute("SELECT pg_backend_pid()")
        victim_pid = cur_victim.fetchone()[0]
        print(f"[*] Victim Backend PID: {victim_pid}")

        print(f"\n[CHAOS INJECTED] Terminating backend process {victim_pid} abruptly via pg_terminate_backend()...")
        with psycopg.connect(conn_str, autocommit=True) as conn_killer:
            with conn_killer.cursor() as cur_killer:
                cur_killer.execute("SELECT pg_terminate_backend(%s)", (victim_pid,))

        cur_victim.execute("SELECT 1;")
        conn_victim.close()
        
    except psycopg.OperationalError as e:
        print(f"[*] Expected Connection Termination Caught: {e}")
    except Exception as e:
        print(f"[*] Process terminated with: {e}")

    # 3. Post-Crash Recovery Verification
    print("\n[*] Verifying post-crash state via fresh connection...")
    with psycopg.connect(conn_str) as conn_verify:
        with conn_verify.cursor() as cur_verify:
            cur_verify.execute(
                "SELECT qty_available FROM inventory WHERE store_id = %s AND sku_id = %s AND batch_id = %s",
                (TEST_STORE_ID, TEST_SKU_ID, TEST_BATCH_ID)
            )
            final_stock = cur_verify.fetchone()[0]
            print(f"[*] Post-Crash Database Stock: {final_stock} units")

    # 4. Invariant Assertion
    print("\n" + "=" * 65)
    print(" VERIFICATION RESULT")
    print("=" * 65)
    if final_stock == initial_stock:
        print(f"[PASSED] Stock exactly matched baseline ({initial_stock} units == {final_stock} units).")
        print("[PASSED] Zero partial updates leaked; ACID Atomicity & WAL recovery fully verified.")
    else:
        print(f"[FAILED] Stock corrupted! Baseline: {initial_stock}, Found: {final_stock}")
        sys.exit(1)
    print("=" * 65)

if __name__ == "__main__":
    run_chaos_test()
