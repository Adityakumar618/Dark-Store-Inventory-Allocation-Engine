"""
Dark-Store Inventory Allocation Engine: Database Pool Manager
File: api/database.py
Description: Asynchronous connection pool management with asyncpg and psycopg.
"""

import os
import asyncpg
from dotenv import load_dotenv

load_dotenv()

DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = int(os.getenv("DB_PORT", "5432"))
DB_NAME = os.getenv("DB_NAME", "darkstore_db")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "postgrespassword")

db_pool: asyncpg.Pool = None

async def init_db_pool() -> asyncpg.Pool:
    global db_pool
    if db_pool is None:
        db_pool = await asyncpg.create_pool(
            host=DB_HOST,
            port=DB_PORT,
            database=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD,
            min_size=10,
            max_size=60,
            command_timeout=60
        )
    return db_pool

async def close_db_pool():
    global db_pool
    if db_pool:
        await db_pool.close()
        db_pool = None

async def get_pool() -> asyncpg.Pool:
    global db_pool
    if db_pool is None:
        await init_db_pool()
    return db_pool
