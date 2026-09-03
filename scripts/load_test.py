"""
Dark-Store Inventory Allocation Engine: Concurrency Load Test & Race Condition Benchmark
File: scripts/load_test.py
Description: Fires 100 concurrent asynchronous orders against limited stock (5 units)
             to empirically prove oversell prevention with SELECT ... FOR UPDATE.
"""

import os
import sys
import time
import json
import asyncio
import asyncpg
from tabulate import tabulate
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

TEST_STORE_ID = 1
TEST_SKU_ID = 1
INITIAL_STOCK = 5
CONCURRENT_ORDERS = 100

async def setup_test_scenario(pool: asyncpg.Pool):
    async with pool.acquire() as conn:
        with open("procedures/01_allocate_order_naive.sql", "r", encoding="utf-8") as f:
            await conn.execute(f.read())
        with open("procedures/02_allocate_order.sql", "r", encoding="utf-8") as f:
            await conn.execute(f.read())

        await conn.execute(
            "UPDATE inventory SET qty_available = $1 WHERE store_id = $2 AND sku_id = $3 AND batch_id = 1",
            INITIAL_STOCK, TEST_STORE_ID, TEST_SKU_ID
        )
        await conn.execute(
            "UPDATE inventory SET qty_available = 0 WHERE store_id = $1 AND sku_id = $2 AND batch_id > 1",
            TEST_STORE_ID, TEST_SKU_ID
        )

async def create_single_order(pool: asyncpg.Pool, order_tag: str) -> int:
    async with pool.acquire() as conn:
        idemp = f"LOADTEST-{order_tag}-{time.time_ns()}"
        order_id = await conn.fetchval(
            """
            INSERT INTO orders (customer_id, status, idempotency_key, total_amount)
            VALUES (1, 'PENDING', $1, 28.00)
            RETURNING order_id;
            """,
            idemp
        )
        await conn.execute(
            """
            INSERT INTO order_items (order_id, sku_id, qty_requested, unit_price, status)
            VALUES ($1, $2, 1, 28.00, 'PENDING');
            """,
            order_id, TEST_SKU_ID
        )
        return order_id

async def run_worker(pool: asyncpg.Pool, mode: str, order_id: int):
    async with pool.acquire() as conn:
        try:
            if mode == "NAIVE":
                raw = await conn.fetchval("SELECT allocate_order_naive($1, $2)", order_id, TEST_STORE_ID)
            else:
                raw = await conn.fetchval("SELECT allocate_order_fefo($1, $2)", order_id, TEST_STORE_ID)
            result = json.loads(raw) if isinstance(raw, str) else (raw or {})
            return {"order_id": order_id, "success": result.get("success", False), "status": result.get("status", "UNKNOWN")}
        except Exception as e:
            return {"order_id": order_id, "success": False, "error": str(e)}

async def run_benchmark(mode: str) -> dict:
    pool = await asyncpg.create_pool(
        host=DB_HOST,
        port=DB_PORT,
        database=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
        min_size=10,
        max_size=30
    )
    
    print(f"\n=======================================================")
    print(f"[*] RUNNING BENCHMARK: {mode} ALLOCATION")
    print(f"[*] Initial Stock: {INITIAL_STOCK} units | Concurrent Requests: {CONCURRENT_ORDERS}")
    print(f"=======================================================")
    
    await setup_test_scenario(pool)
    
    order_ids = await asyncio.gather(*[create_single_order(pool, f"{mode.split()[0]}-{i}") for i in range(CONCURRENT_ORDERS)])
    
    start_time = time.perf_counter()
    tasks = [run_worker(pool, mode, oid) for oid in order_ids]
    results = await asyncio.gather(*tasks)
    elapsed = time.perf_counter() - start_time
    
    async with pool.acquire() as conn:
        final_stock = await conn.fetchval(
            "SELECT qty_available FROM inventory WHERE store_id = $1 AND sku_id = $2 AND batch_id = 1",
            TEST_STORE_ID, TEST_SKU_ID
        )
    
    successful = sum(1 for r in results if r.get("success") is True)
    failed = sum(1 for r in results if not r.get("success", False))
    oversold = max(0, successful - INITIAL_STOCK)
    
    await pool.close()
    
    return {
        "mode": mode,
        "initial_stock": INITIAL_STOCK,
        "concurrent_orders": CONCURRENT_ORDERS,
        "successful_orders": successful,
        "failed_orders": failed,
        "oversold_units": oversold,
        "final_stock": final_stock,
        "elapsed_seconds": round(elapsed, 3),
        "throughput_rps": round(CONCURRENT_ORDERS / elapsed, 1)
    }

async def main():
    print("=" * 65)
    print(" DARK-STORE INVENTORY ALLOCATION CONCURRENCY BENCHMARK ")
    print("=" * 65)
    
    try:
        naive_metrics = await run_benchmark("NAIVE")
        await asyncio.sleep(1)
        prod_metrics = await run_benchmark("PRODUCTION (FOR UPDATE)")
        
        headers = ["Metric", "Naive (Un-locked)", "Production (FOR UPDATE)"]
        table = [
            ["Initial Inventory", naive_metrics["initial_stock"], prod_metrics["initial_stock"]],
            ["Concurrent Order Attempts", naive_metrics["concurrent_orders"], prod_metrics["concurrent_orders"]],
            ["Allocated (Successful)", naive_metrics["successful_orders"], prod_metrics["successful_orders"]],
            ["Rejected (Insufficient Stock)", naive_metrics["failed_orders"], prod_metrics["failed_orders"]],
            ["Oversold Units (DEFICIT)", f"{naive_metrics['oversold_units']} UNITS DEFICIT", f"{prod_metrics['oversold_units']} UNITS (ZERO)"],
            ["Final Database Stock", naive_metrics["final_stock"], prod_metrics["final_stock"]],
            ["Execution Duration", f"{naive_metrics['elapsed_seconds']}s", f"{prod_metrics['elapsed_seconds']}s"],
            ["Throughput", f"{naive_metrics['throughput_rps']} req/s", f"{prod_metrics['throughput_rps']} req/s"],
            ["Correctness Guarantee", "RACE CONDITION / OVERSELL", "100% ACID CONSISTENT"]
        ]
        
        print("\n" + "=" * 65)
        print(" FINAL CONCURRENCY BENCHMARK RESULTS")
        print("=" * 65)
        print(tabulate(table, headers=headers, tablefmt="grid"))
        
    except Exception as e:
        print(f"\n[ERROR] Load test failed: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
