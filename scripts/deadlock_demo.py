"""
Dark-Store Inventory Allocation Engine: Deadlock Simulation & Resolution
File: scripts/deadlock_demo.py
Description: Demonstrates PostgreSQL 40P01 Deadlock Detection when transactions
             acquire row locks in reverse order, followed by the deterministic
             lock ordering fix that eliminates deadlocks.
"""

import os
import sys
import asyncio
import asyncpg
from dotenv import load_dotenv

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

load_dotenv()

DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = int(os.getenv("DB_PORT", "5432"))
DB_NAME = os.getenv("DB_NAME", "darkstore_db")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "postgrespassword")

STORE_ID = 1
SKU_A = 10  # e.g., Milk
SKU_B = 20  # e.g., Bread

async def run_unsorted_transaction(pool: asyncpg.Pool, tx_name: str, lock_order: list[int], delay: float):
    print(f"[{tx_name}] Starting transaction with lock order: {lock_order}")
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                sku1 = lock_order[0]
                print(f"[{tx_name}] Requesting lock on SKU {sku1}...")
                await conn.execute(
                    "SELECT * FROM inventory WHERE store_id = $1 AND sku_id = $2 FOR UPDATE",
                    STORE_ID, sku1
                )
                print(f"[{tx_name}] -> Acquired lock on SKU {sku1}. Sleeping {delay}s...")
                await asyncio.sleep(delay)

                sku2 = lock_order[1]
                print(f"[{tx_name}] Requesting lock on SKU {sku2}...")
                await conn.execute(
                    "SELECT * FROM inventory WHERE store_id = $1 AND sku_id = $2 FOR UPDATE",
                    STORE_ID, sku2
                )
                print(f"[{tx_name}] -> Acquired lock on SKU {sku2}! Transaction Committing.")
                return {"tx": tx_name, "status": "COMMITTED"}
    except asyncpg.exceptions.DeadlockDetectedError as e:
        print(f"\n[DEADLOCK] {tx_name}: PostgreSQL aborted transaction (SQLSTATE 40P01): {e}\n")
        return {"tx": tx_name, "status": "DEADLOCK_ABORTED", "error": str(e)}
    except Exception as e:
        print(f"[{tx_name}] Error: {e}")
        return {"tx": tx_name, "status": "ERROR", "error": str(e)}

async def run_deterministic_transaction(pool: asyncpg.Pool, tx_name: str, requested_skus: list[int], delay: float):
    sorted_order = sorted(requested_skus)
    print(f"[{tx_name}] Basket: {requested_skus} -> Sorted Lock Order: {sorted_order}")
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                for sku in sorted_order:
                    print(f"[{tx_name}] Locking SKU {sku}...")
                    await conn.execute(
                        "SELECT * FROM inventory WHERE store_id = $1 AND sku_id = $2 FOR UPDATE",
                        STORE_ID, sku
                    )
                    await asyncio.sleep(delay)
                print(f"[{tx_name}] -> All locks acquired cleanly! Committing.")
                return {"tx": tx_name, "status": "COMMITTED"}
    except Exception as e:
        print(f"[{tx_name}] Error: {e}")
        return {"tx": tx_name, "status": "ERROR", "error": str(e)}

async def main():
    print("=" * 70)
    print(" DEADLOCK SIMULATION & DETERMINISTIC RESOLUTION DEMO ")
    print("=" * 70)

    pool = await asyncpg.create_pool(
        host=DB_HOST,
        port=DB_PORT,
        database=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
        min_size=2,
        max_size=10
    )

    print("\n--- TEST 1: Unordered Lock Acquisition (Deadlock Trigger) ---")
    print("Tx1 will lock [SKU 10, then SKU 20]")
    print("Tx2 will lock [SKU 20, then SKU 10]")
    
    t1 = asyncio.create_task(run_unsorted_transaction(pool, "Tx-1 (Order A)", [SKU_A, SKU_B], 0.4))
    t2 = asyncio.create_task(run_unsorted_transaction(pool, "Tx-2 (Order B)", [SKU_B, SKU_A], 0.4))
    results_deadlock = await asyncio.gather(t1, t2)

    await asyncio.sleep(1)

    print("\n--- TEST 2: Deterministic Ascending Lock Ordering (Deadlock Fixed) ---")
    print("Both transactions sort keys to [SKU 10, then SKU 20] before requesting locks.")
    
    t3 = asyncio.create_task(run_deterministic_transaction(pool, "Tx-3 (Order A)", [SKU_A, SKU_B], 0.2))
    t4 = asyncio.create_task(run_deterministic_transaction(pool, "Tx-4 (Order B)", [SKU_B, SKU_A], 0.2))
    results_fixed = await asyncio.gather(t3, t4)

    print("\n" + "=" * 70)
    print(" SUMMARY")
    print("=" * 70)
    print(f"Test 1 (Unordered): {[r['status'] for r in results_deadlock]} (PostgreSQL 40P01 Abort Triggered)")
    print(f"Test 2 (Deterministic): {[r['status'] for r in results_fixed]} (Both Transactions Serialized Cleanly)")
    print("=" * 70)

    await pool.close()

if __name__ == "__main__":
    asyncio.run(main())
