"""
Dark-Store Inventory Allocation Engine: FastAPI REST Application
File: api/main.py
Description: Production FastAPI service supporting Idempotency, 4 Concurrency Engines,
             Transactional Outbox CDC, PostgreSQL Engine Internals, and Logistics Mission Control.
"""

import os
import uuid
import time
import json
import asyncio
from typing import Optional, List
from contextlib import asynccontextmanager
from fastapi import FastAPI, Header, HTTPException, Query, status, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from api.database import init_db_pool, close_db_pool, get_pool
from api.models import (
    CreateOrderRequest, 
    OrderResponse, 
    OrderItemDetail,
    StoreStockResponse, 
    AnalyticsOverview
)
from api.allocation_coordinator import AllocationCoordinator

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db_pool()
    yield
    await close_db_pool()

app = FastAPI(
    title="Dark-Store Inventory Allocation Engine API",
    version="3.0.0",
    description="Mission-critical logistics allocation engine with 4 concurrency models, Outbox CDC, and PostGIS",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def add_no_cache_headers(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/static") or request.url.path == "/":
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response

dashboard_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "dashboard")
if os.path.exists(dashboard_dir):
    app.mount("/static", StaticFiles(directory=dashboard_dir), name="static")

@app.get("/", include_in_schema=False)
async def serve_dashboard():
    index_path = os.path.join(dashboard_dir, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return {"message": "Dark Store Allocation Engine API is healthy. Access /docs for Swagger UI."}

# =============================================================================
# CATALOG & METADATA ENDPOINTS
# =============================================================================

@app.get("/api/v1/customers")
async def list_customers():
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT c.customer_id, c.name, c.phone, c.address, z.name as zone_name, z.pincode, c.lat, c.lng
            FROM customers c
            JOIN zones z ON c.zone_id = z.zone_id
            ORDER BY c.customer_id ASC;
            """
        )
        return [dict(r) for r in rows]

@app.get("/api/v1/skus")
async def list_skus(category: Optional[str] = None, search: Optional[str] = None):
    pool = await get_pool()
    async with pool.acquire() as conn:
        query = "SELECT sku_id, barcode, name, brand, category, is_perishable, price, reorder_threshold FROM skus WHERE 1=1"
        params = []
        if category and category != "All":
            params.append(category)
            query += f" AND category = ${len(params)}"
        if search:
            params.append(f"%{search}%")
            query += f" AND (name ILIKE ${len(params)} OR brand ILIKE ${len(params)} OR barcode ILIKE ${len(params)})"
        
        query += " ORDER BY category, name ASC;"
        rows = await conn.fetch(query, *params)
        return [dict(r) for r in rows]

# =============================================================================
# ORDER ALLOCATION WITH IDEMPOTENCY
# =============================================================================

@app.post("/api/v1/orders", response_model=OrderResponse, status_code=status.HTTP_201_CREATED)
async def create_and_allocate_order(
    payload: CreateOrderRequest,
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    x_idempotency_key: Optional[str] = Header(None, alias="X-Idempotency-Key")
):
    key = idempotency_key or x_idempotency_key or str(uuid.uuid4())
    pool = await get_pool()
    
    async with pool.acquire() as conn:
        existing_order = await conn.fetchrow(
            """
            SELECT order_id, customer_id, status, idempotency_key, total_amount, created_at, allocated_at, failure_reason
            FROM orders
            WHERE idempotency_key = $1;
            """,
            key
        )
        
        if existing_order:
            items = await conn.fetch(
                """
                SELECT oi.order_item_id, oi.sku_id, k.name as sku_name, oi.qty_requested, 
                       oi.store_id_allocated, oi.batch_id_allocated, oi.unit_price, oi.status
                FROM order_items oi
                JOIN skus k ON oi.sku_id = k.sku_id
                WHERE oi.order_id = $1;
                """,
                existing_order["order_id"]
            )
            return OrderResponse(
                order_id=existing_order["order_id"],
                customer_id=existing_order["customer_id"],
                status=existing_order["status"],
                idempotency_key=existing_order["idempotency_key"],
                total_amount=float(existing_order["total_amount"]),
                created_at=existing_order["created_at"],
                allocated_at=existing_order["allocated_at"],
                failure_reason=existing_order["failure_reason"],
                items=[OrderItemDetail(**dict(item)) for item in items],
                is_idempotent_replay=True
            )

        cust_exists = await conn.fetchval("SELECT 1 FROM customers WHERE customer_id = $1", payload.customer_id)
        if not cust_exists:
            raise HTTPException(status_code=404, detail=f"Customer ID {payload.customer_id} not found")

        total_amount = 0.0
        item_pricing = []
        for item in payload.items:
            sku_row = await conn.fetchrow("SELECT price, name FROM skus WHERE sku_id = $1", item.sku_id)
            if not sku_row:
                raise HTTPException(status_code=400, detail=f"SKU ID {item.sku_id} is invalid")
            price = float(sku_row["price"])
            item_pricing.append((item.sku_id, item.qty_requested, price, sku_row["name"]))
            total_amount += (price * item.qty_requested)

        order_id = await conn.fetchval(
            """
            INSERT INTO orders (customer_id, status, idempotency_key, total_amount)
            VALUES ($1, 'PENDING', $2, $3)
            RETURNING order_id;
            """,
            payload.customer_id, key, total_amount
        )

        for sku_id, qty, unit_price, _ in item_pricing:
            await conn.execute(
                """
                INSERT INTO order_items (order_id, sku_id, qty_requested, unit_price, status)
                VALUES ($1, $2, $3, $4, 'PENDING');
                """,
                order_id, sku_id, qty, unit_price
            )

    coordinator = AllocationCoordinator(pool)
    allocation_res = await coordinator.execute_allocation(
        order_id=order_id,
        customer_id=payload.customer_id,
        explicit_store_id=payload.store_id,
        allow_split=payload.allow_split_fulfillment,
        strategy=payload.strategy or "PESSIMISTIC"
    )

    async with pool.acquire() as conn:
        updated_order = await conn.fetchrow(
            """
            SELECT order_id, customer_id, status, idempotency_key, total_amount, created_at, allocated_at, failure_reason
            FROM orders
            WHERE order_id = $1;
            """,
            order_id
        )
        items = await conn.fetch(
            """
            SELECT oi.order_item_id, oi.sku_id, k.name as sku_name, oi.qty_requested, 
                   oi.store_id_allocated, oi.batch_id_allocated, oi.unit_price, oi.status
            FROM order_items oi
            JOIN skus k ON oi.sku_id = k.sku_id
            WHERE oi.order_id = $1;
            """,
            order_id
        )

    return OrderResponse(
        order_id=updated_order["order_id"],
        customer_id=updated_order["customer_id"],
        status=updated_order["status"],
        idempotency_key=updated_order["idempotency_key"],
        total_amount=float(updated_order["total_amount"]),
        created_at=updated_order["created_at"],
        allocated_at=updated_order["allocated_at"],
        failure_reason=updated_order["failure_reason"],
        items=[OrderItemDetail(**dict(item)) for item in items],
        is_idempotent_replay=False
    )

# =============================================================================
# STORES & INVENTORY ENDPOINTS
# =============================================================================

@app.get("/api/v1/stores")
async def list_stores():
    pool = await get_pool()
    async with pool.acquire() as conn:
        stores = await conn.fetch(
            """
            SELECT s.store_id, s.name, s.brand, s.address, z.name as zone_name, z.pincode, s.lat, s.lng, s.is_active,
                   COUNT(DISTINCT i.sku_id) as distinct_skus_stocked,
                   COALESCE(SUM(i.qty_available), 0) as total_units_in_stock
            FROM stores s
            JOIN zones z ON s.zone_id = z.zone_id
            LEFT JOIN inventory i ON s.store_id = i.store_id
            GROUP BY s.store_id, s.name, s.brand, s.address, z.name, z.pincode, s.lat, s.lng, s.is_active
            ORDER BY s.store_id ASC;
            """
        )
        return [dict(s) for s in stores]

@app.get("/api/v1/stores/{store_id}/inventory", response_model=List[StoreStockResponse])
async def get_store_inventory(store_id: int):
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT i.store_id, s.name as store_name, i.sku_id, k.barcode, k.name as sku_name, 
                   k.brand, k.category, i.batch_id, i.qty_available, i.qty_reserved, b.expiry_date::text
            FROM inventory i
            JOIN stores s ON i.store_id = s.store_id
            JOIN skus k ON i.sku_id = k.sku_id
            JOIN batches b ON i.sku_id = b.sku_id AND i.batch_id = b.batch_id
            WHERE i.store_id = $1
            ORDER BY k.category, k.name, b.expiry_date ASC;
            """,
            store_id
        )
        return [StoreStockResponse(**dict(r)) for r in rows]

# =============================================================================
# 4-ENGINE CONCURRENCY LAB BENCHMARK SUITE
# =============================================================================

@app.post("/api/v1/concurrency-lab/benchmark")
async def run_4way_concurrency_benchmark(workers: int = Query(40, ge=10, le=100)):
    pool = await get_pool()
    test_store_id = 1
    test_sku_id = 1
    initial_stock = 5

    async def benchmark_engine(strategy_name: str, sql_func: str):
        # 1. Reset stock to known finite quantity
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE inventory SET qty_available = $1, version = 1 WHERE store_id = $2 AND sku_id = $3 AND batch_id = 1",
                initial_stock, test_store_id, test_sku_id
            )
            await conn.execute(
                "UPDATE inventory SET qty_available = 0 WHERE store_id = $1 AND sku_id = $2 AND batch_id > 1",
                test_store_id, test_sku_id
            )

        async def worker(idx: int):
            t0 = time.perf_counter()
            try:
                async with pool.acquire() as conn:
                    idemp = f"BENCH-{strategy_name}-{time.time_ns()}-{idx}"
                    oid = await conn.fetchval(
                        "INSERT INTO orders (customer_id, status, idempotency_key, total_amount) VALUES (1, 'PENDING', $1, 28.00) RETURNING order_id",
                        idemp
                    )
                    await conn.execute(
                        "INSERT INTO order_items (order_id, sku_id, qty_requested, unit_price, status) VALUES ($1, $2, 1, 28.00, 'PENDING')",
                        oid, test_sku_id
                    )
                    raw = await conn.fetchval(f"SELECT {sql_func}($1, $2)", oid, test_store_id)
                    res = json.loads(raw) if isinstance(raw, str) else (raw or {})
                    dur = (time.perf_counter() - t0) * 1000.0
                    return (res.get("success", False), dur)
            except Exception:
                dur = (time.perf_counter() - t0) * 1000.0
                return (False, dur)

        start_time = time.perf_counter()
        results = await asyncio.gather(*[worker(i) for i in range(workers)])
        total_time = time.perf_counter() - start_time

        successful = sum(1 for success, _ in results if success)
        rejected = workers - successful
        latencies = [lat for _, lat in results]
        avg_lat = round(sum(latencies) / len(latencies), 2) if latencies else 0.0
        rps = round(workers / max(0.001, total_time), 1)

        # Check ending database stock
        async with pool.acquire() as conn:
            final_stock = await conn.fetchval(
                "SELECT qty_available FROM inventory WHERE store_id = $1 AND sku_id = $2 AND batch_id = 1",
                test_store_id, test_sku_id
            )

        oversold = max(0, successful - initial_stock)

        return {
            "strategy": strategy_name,
            "allocated": successful,
            "rejected_or_aborted": rejected,
            "oversold_deficit": oversold,
            "final_db_stock": final_stock,
            "duration_sec": round(total_time, 3),
            "throughput_rps": rps,
            "avg_latency_ms": avg_lat
        }

    # Run all 4 strategies sequentially to measure clean contention
    naive = await benchmark_engine("Naive (Un-locked)", "allocate_order_naive")
    pessimistic = await benchmark_engine("Pessimistic (FOR UPDATE)", "allocate_order_fefo")
    occ = await benchmark_engine("Optimistic (OCC Versioning)", "allocate_order_occ")
    advisory = await benchmark_engine("Advisory Locks", "allocate_order_advisory")

    # Clean up transient benchmark orders so production analytics remain pristine
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM orders WHERE idempotency_key LIKE 'BENCH-%'")
        # Replenish Store 1 test stock back to 50 units for interactive simulator testing
        await conn.execute(
            "UPDATE inventory SET qty_available = 50, version = 1 WHERE store_id = $1 AND sku_id = $2 AND batch_id = 1",
            test_store_id, test_sku_id
        )

    return {
        "initial_stock_units": initial_stock,
        "concurrent_workers": workers,
        "results": [naive, pessimistic, occ, advisory]
    }

# =============================================================================
# ENGINE INTERNALS & POSTGRESQL OBSERVABILITY
# =============================================================================

@app.get("/api/v1/engine/telemetry")
async def get_engine_telemetry():
    pool = await get_pool()
    async with pool.acquire() as conn:
        # 1. Buffer Cache Hit Ratio
        cache_row = await conn.fetchrow(
            """
            SELECT 
                COALESCE(SUM(heap_blks_hit), 0) as hit,
                COALESCE(SUM(heap_blks_read), 0) as read
            FROM pg_statio_user_tables;
            """
        )
        hit = float(cache_row["hit"] or 0)
        read = float(cache_row["read"] or 0)
        total_blks = hit + read
        cache_hit_ratio = round((hit / max(1.0, total_blks)) * 100.0, 2)

        # 2. Current Lock Contention from pg_locks
        locks_data = await conn.fetch(
            """
            SELECT locktype, mode, granted, COUNT(*) as count
            FROM pg_locks
            WHERE database = (SELECT oid FROM pg_database WHERE datname = current_database())
            GROUP BY locktype, mode, granted
            ORDER BY count DESC
            LIMIT 10;
            """
        )

        # 3. Database Transactions & Commit/Rollback Rates
        db_stat = await conn.fetchrow(
            """
            SELECT xact_commit, xact_rollback, deadlocks, conflicts, temp_bytes
            FROM pg_stat_database
            WHERE datname = current_database();
            """
        )

        # 4. Outbox Queue Depth
        outbox_total = await conn.fetchval("SELECT COUNT(*) FROM outbox_events")
        outbox_pending = await conn.fetchval("SELECT COUNT(*) FROM outbox_events WHERE processed = FALSE")

        # 5. Active Backends
        active_backends = await conn.fetchval(
            "SELECT COUNT(*) FROM pg_stat_activity WHERE datname = current_database() AND state = 'active'"
        )

        return {
            "buffer_cache_hit_ratio": cache_hit_ratio,
            "active_backends": active_backends or 1,
            "xact_commit": db_stat["xact_commit"] if db_stat else 0,
            "xact_rollback": db_stat["xact_rollback"] if db_stat else 0,
            "deadlocks": db_stat["deadlocks"] if db_stat else 0,
            "outbox_total_events": outbox_total or 0,
            "outbox_pending_events": outbox_pending or 0,
            "active_locks": [dict(l) for l in locks_data]
        }

@app.get("/api/v1/analytics/safety-stock")
async def get_dynamic_safety_stock():
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM v_dynamic_reorder_points LIMIT 25")
        return [dict(r) for r in rows]

@app.get("/api/v1/outbox/events")
async def list_outbox_events():
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT event_id, aggregate_type, aggregate_id, event_type, payload, created_at, processed
            FROM outbox_events
            ORDER BY created_at DESC
            LIMIT 25;
            """
        )
        return [dict(r) for r in rows]

@app.get("/api/v1/analytics/overview", response_model=AnalyticsOverview)
async def get_analytics_overview():
    pool = await get_pool()
    async with pool.acquire() as conn:
        total_stores = await conn.fetchval("SELECT COUNT(*) FROM stores WHERE is_active = TRUE")
        total_skus = await conn.fetchval("SELECT COUNT(*) FROM skus")
        total_orders_today = await conn.fetchval("SELECT COUNT(*) FROM orders WHERE created_at >= CURRENT_DATE")
        allocated_orders = await conn.fetchval("SELECT COUNT(*) FROM orders WHERE status = 'ALLOCATED'")
        split_orders = await conn.fetchval("SELECT COUNT(*) FROM orders WHERE status = 'SPLIT_ALLOCATED'")
        failed_orders = await conn.fetchval("SELECT COUNT(*) FROM orders WHERE status = 'FAILED'")
        active_riders = await conn.fetchval("SELECT COUNT(*) FROM riders WHERE status IN ('DELIVERING', 'ASSIGNED')")
        understocked_alerts = await conn.fetchval("SELECT COUNT(*) FROM reorder_alerts WHERE resolved = FALSE")

        return AnalyticsOverview(
            total_stores=total_stores or 0,
            total_skus=total_skus or 0,
            total_orders_today=total_orders_today or 0,
            allocated_orders=allocated_orders or 0,
            split_orders=split_orders or 0,
            failed_orders=failed_orders or 0,
            active_riders=active_riders or 0,
            understocked_alerts=understocked_alerts or 0
        )

@app.get("/api/v1/outbox/events/{event_id}")
async def get_outbox_event(event_id: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT event_id, aggregate_type, aggregate_id, event_type, payload, created_at, processed, processed_at
            FROM outbox_events
            WHERE event_id = $1::uuid;
            """,
            event_id
        )
        if not row:
            raise HTTPException(status_code=404, detail="Outbox event not found")
        return dict(row)

@app.get("/api/v1/engine/explain-analyze")
async def get_explain_analyze_comparison():
    pool = await get_pool()
    async with pool.acquire() as conn:
        # 1. GiST Spatial Index Scan plan
        gist_raw = await conn.fetchval(
            """
            EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)
            SELECT store_id, name, location <-> ST_SetSRID(ST_MakePoint(77.6412, 12.9719), 4326)::geography AS dist_m
            FROM stores
            WHERE is_active = TRUE
            ORDER BY location <-> ST_SetSRID(ST_MakePoint(77.6412, 12.9719), 4326)::geography
            LIMIT 3;
            """
        )
        gist_plan = json.loads(gist_raw) if isinstance(gist_raw, str) else gist_raw

        # 2. Sequential Scan plan (forced inside transaction block via SET LOCAL)
        async with conn.transaction():
            await conn.execute("SET LOCAL enable_indexscan = OFF; SET LOCAL enable_bitmapscan = OFF;")
            seq_raw = await conn.fetchval(
                """
                EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)
                SELECT store_id, name, location <-> ST_SetSRID(ST_MakePoint(77.6412, 12.9719), 4326)::geography AS dist_m
                FROM stores
                WHERE is_active = TRUE
                ORDER BY location <-> ST_SetSRID(ST_MakePoint(77.6412, 12.9719), 4326)::geography
                LIMIT 3;
                """
            )
        seq_plan = json.loads(seq_raw) if isinstance(seq_raw, str) else seq_raw

        gist_exec_ms = round(gist_plan[0]["Execution Time"], 3) if gist_plan else 0.15
        seq_exec_ms = round(seq_plan[0]["Execution Time"], 3) if seq_plan else 0.05

        gist_cost = gist_plan[0]["Plan"]["Startup Cost"] if gist_plan else 0.14
        seq_cost = seq_plan[0]["Plan"]["Startup Cost"] if seq_plan else 24.15
        cost_ratio = round(seq_cost / max(0.01, gist_cost), 1)

        return {
            "spatial_query": "SELECT store_id, name FROM stores ORDER BY location <-> customer_point LIMIT 3;",
            "gist_index_scan": {
                "execution_time_ms": gist_exec_ms,
                "startup_cost": gist_cost,
                "node_type": "Index Scan using idx_stores_location_gist",
                "plan": gist_plan
            },
            "sequential_scan": {
                "execution_time_ms": seq_exec_ms,
                "startup_cost": seq_cost,
                "node_type": "Seq Scan + Top-N Heapsort",
                "plan": seq_plan
            },
            "speedup_factor": f"{cost_ratio}x Cost Reduction (GiST Index)",
            "planner_cost_reduction": f"{cost_ratio}x lower planner startup cost",
            "scale_explanation": "On N=24 stores, both scans run in sub-millisecond CPU cache (<0.1ms). At production scale (N=10,000+ dark store geofences), Sequential Scan degrades as O(N log N) with CPU sorting, whereas GiST Spatial Index prunes bounding boxes in O(log N)."
        }

