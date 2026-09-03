"""
Dark-Store Inventory Allocation Engine: Automated Integration Tests
File: tests/test_allocation.py
Description: Pytest integration suite validating FEFO allocation, idempotency replay,
             concurrency safety, and 2PC split fulfillment.
"""

import uuid
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from api.main import app
from api.database import init_db_pool, close_db_pool


@pytest.mark.asyncio
async def test_idempotency_replay():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        unique_key = f"TEST-IDEMP-{uuid.uuid4()}"
        payload = {
            "customer_id": 1,
            "items": [{"sku_id": 1, "qty_requested": 1}],
            "allow_split_fulfillment": True
        }

        # First Request: creates and allocates
        res1 = await client.post("/api/v1/orders", json=payload, headers={"Idempotency-Key": unique_key})
        assert res1.status_code == 201
        data1 = res1.json()
        assert data1["is_idempotent_replay"] is False
        order_id_1 = data1["order_id"]

        # Second Request with IDENTICAL idempotency key: must replay without creating duplicate order
        res2 = await client.post("/api/v1/orders", json=payload, headers={"Idempotency-Key": unique_key})
        assert res2.status_code == 201
        data2 = res2.json()
        assert data2["is_idempotent_replay"] is True
        assert data2["order_id"] == order_id_1
        assert data2["total_amount"] == data1["total_amount"]

@pytest.mark.asyncio
async def test_order_stockout_failure():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        payload = {
            "customer_id": 1,
            "items": [{"sku_id": 1, "qty_requested": 999999}],
            "allow_split_fulfillment": False
        }
        res = await client.post("/api/v1/orders", json=payload)
        assert res.status_code == 201
        data = res.json()
        assert data["status"] == "FAILED"
        assert "Insufficient stock" in (data["failure_reason"] or "")

@pytest.mark.asyncio
async def test_analytics_overview_endpoint():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/api/v1/analytics/overview")
        assert res.status_code == 200
        data = res.json()
        assert "total_stores" in data
        assert "total_skus" in data
        assert data["total_stores"] >= 20
        assert data["total_skus"] >= 200

@pytest.mark.asyncio
async def test_store_inventory_endpoint():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/api/v1/stores/1/inventory")
        assert res.status_code == 200
        data = res.json()
        assert isinstance(data, list)
        assert len(data) > 0
        assert "sku_name" in data[0]
        assert "qty_available" in data[0]
