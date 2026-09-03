"""
Shared Pytest Fixtures for Dark-Store Inventory Allocation Engine
File: tests/conftest.py
"""

import pytest_asyncio
from api.database import init_db_pool, close_db_pool, get_pool

@pytest_asyncio.fixture(autouse=True)
async def manage_db_pool():
    await init_db_pool()
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Guarantee Store 1 has stock for test cases
        await conn.execute(
            "UPDATE inventory SET qty_available = 50, version = 1 WHERE store_id = 1 AND sku_id = 1 AND batch_id = 1"
        )
    yield
    await close_db_pool()
