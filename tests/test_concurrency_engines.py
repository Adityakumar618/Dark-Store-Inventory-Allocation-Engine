"""
Dark-Store Inventory Allocation Engine: Concurrency Engine Test Suite
File: tests/test_concurrency_engines.py
Description: Validates Pessimistic (FOR UPDATE), Optimistic (OCC),
             Advisory Locks, Multi-Batch FEFO Draining, and Transactional Outbox.
"""

import json
import uuid
import pytest
from httpx import AsyncClient, ASGITransport
from api.main import app
from api.database import get_pool

@pytest.mark.asyncio
async def test_pessimistic_engine_and_outbox():
    pool = await get_pool()
    async with pool.acquire() as conn:
        key = f"TEST-PESSIMISTIC-{uuid.uuid4()}"
        order_id = await conn.fetchval(
            "INSERT INTO orders (customer_id, status, idempotency_key, total_amount) VALUES (1, 'PENDING', $1, 56.00) RETURNING order_id",
            key
        )
        await conn.execute(
            "INSERT INTO order_items (order_id, sku_id, qty_requested, unit_price, status) VALUES ($1, 1, 2, 28.00, 'PENDING')",
            order_id
        )

        res_raw = await conn.fetchval("SELECT allocate_order_fefo($1, 1)", order_id)
        res = json.loads(res_raw) if isinstance(res_raw, str) else res_raw

        assert res["success"] is True
        assert res["status"] == "ALLOCATED"

        outbox_event = await conn.fetchrow(
            "SELECT * FROM outbox_events WHERE aggregate_id = $1 AND event_type = 'ORDER_ALLOCATED'",
            str(order_id)
        )
        assert outbox_event is not None
        assert outbox_event["aggregate_type"] == "ORDER"

@pytest.mark.asyncio
async def test_occ_engine_success():
    pool = await get_pool()
    async with pool.acquire() as conn:
        key = f"TEST-OCC-{uuid.uuid4()}"
        order_id = await conn.fetchval(
            "INSERT INTO orders (customer_id, status, idempotency_key, total_amount) VALUES (1, 'PENDING', $1, 28.00) RETURNING order_id",
            key
        )
        await conn.execute(
            "INSERT INTO order_items (order_id, sku_id, qty_requested, unit_price, status) VALUES ($1, 1, 1, 28.00, 'PENDING')",
            order_id
        )

        res_raw = await conn.fetchval("SELECT allocate_order_occ($1, 1)", order_id)
        res = json.loads(res_raw) if isinstance(res_raw, str) else res_raw

        assert res["success"] is True
        assert res["status"] == "ALLOCATED"
        assert res["strategy"] == "OCC"

@pytest.mark.asyncio
async def test_occ_multi_batch_draining():
    """Validates that OCC drains multiple batches across expiration dates."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Create a controlled 2-batch setup: Batch 1 has 2 units, Batch 2 has 3 units
        await conn.execute(
            "UPDATE inventory SET qty_available = 2, version = 1 WHERE store_id = 1 AND sku_id = 1 AND batch_id = 1"
        )
        await conn.execute(
            "UPDATE inventory SET qty_available = 3, version = 1 WHERE store_id = 1 AND sku_id = 1 AND batch_id = 2"
        )

        key = f"TEST-OCC-MULTIBATCH-{uuid.uuid4()}"
        order_id = await conn.fetchval(
            "INSERT INTO orders (customer_id, status, idempotency_key, total_amount) VALUES (1, 'PENDING', $1, 112.00) RETURNING order_id",
            key
        )
        # Request 4 units (drains 4 units across both batches based on FEFO expiry order)
        await conn.execute(
            "INSERT INTO order_items (order_id, sku_id, qty_requested, unit_price, status) VALUES ($1, 1, 4, 28.00, 'PENDING')",
            order_id
        )

        res_raw = await conn.fetchval("SELECT allocate_order_occ($1, 1)", order_id)
        res = json.loads(res_raw) if isinstance(res_raw, str) else res_raw

        assert res["success"] is True
        assert res["status"] == "ALLOCATED"
        assert len(res["allocations"]) == 2
        total_drained = sum(a["qty"] for a in res["allocations"])
        assert total_drained == 4

@pytest.mark.asyncio
async def test_advisory_lock_engine():
    pool = await get_pool()
    async with pool.acquire() as conn:
        key = f"TEST-ADVISORY-{uuid.uuid4()}"
        order_id = await conn.fetchval(
            "INSERT INTO orders (customer_id, status, idempotency_key, total_amount) VALUES (1, 'PENDING', $1, 28.00) RETURNING order_id",
            key
        )
        await conn.execute(
            "INSERT INTO order_items (order_id, sku_id, qty_requested, unit_price, status) VALUES ($1, 1, 1, 28.00, 'PENDING')",
            order_id
        )

        res_raw = await conn.fetchval("SELECT allocate_order_advisory($1, 1)", order_id)
        res = json.loads(res_raw) if isinstance(res_raw, str) else res_raw

        assert res["success"] is True
        assert res["status"] == "ALLOCATED"
        assert res["strategy"] == "ADVISORY_LOCK"

@pytest.mark.asyncio
async def test_api_dynamic_strategy_selection():
    """Validates that POST /api/v1/orders correctly respects the strategy payload."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. OCC Order targeting Store 1
        res_occ = await client.post(
            "/api/v1/orders",
            json={
                "customer_id": 1,
                "store_id": 1,
                "items": [{"sku_id": 1, "qty_requested": 1}],
                "strategy": "OCC"
            },
            headers={"Idempotency-Key": f"TEST-STRAT-OCC-{uuid.uuid4()}"}
        )
        assert res_occ.status_code == 201
        assert res_occ.json()["status"] == "ALLOCATED"

        # 2. Advisory Lock Order targeting Store 1
        res_adv = await client.post(
            "/api/v1/orders",
            json={
                "customer_id": 1,
                "store_id": 1,
                "items": [{"sku_id": 1, "qty_requested": 1}],
                "strategy": "ADVISORY"
            },
            headers={"Idempotency-Key": f"TEST-STRAT-ADV-{uuid.uuid4()}"}
        )
        assert res_adv.status_code == 201
        assert res_adv.json()["status"] == "ALLOCATED"

@pytest.mark.asyncio
async def test_dynamic_reorder_points_view():
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM v_dynamic_reorder_points LIMIT 10")
        assert len(rows) > 0
        first = rows[0]
        assert "dynamic_reorder_point" in first
        assert "safety_stock" in first
        assert "inventory_health_status" in first
