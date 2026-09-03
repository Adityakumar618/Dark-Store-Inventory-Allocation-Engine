"""
Dark-Store Inventory Allocation Engine: Distributed 2PC Allocation Coordinator
File: api/allocation_coordinator.py
Description: Orchestrates single-store FEFO allocation and multi-store 2PC split fulfillment.
"""

import json
import asyncpg
from typing import Dict, Any, List

class AllocationCoordinator:
    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool

    async def find_nearest_store_for_customer(self, conn: asyncpg.Connection, customer_id: int) -> int:
        """Finds closest dark store using PostGIS KNN or zone matching."""
        store_id = await conn.fetchval(
            """
            SELECT s.store_id
            FROM stores s
            JOIN customers c ON c.customer_id = $1
            WHERE s.is_active = TRUE
            ORDER BY 
                CASE 
                    WHEN s.location IS NOT NULL AND c.location IS NOT NULL 
                    THEN s.location <-> c.location 
                    ELSE NULL 
                END ASC,
                CASE WHEN s.zone_id = c.zone_id THEN 0 ELSE 1 END ASC
            LIMIT 1;
            """,
            customer_id
        )
        if not store_id:
            store_id = await conn.fetchval("SELECT store_id FROM stores WHERE is_active = TRUE LIMIT 1")
        return store_id

    async def execute_allocation(
        self, 
        order_id: int, 
        customer_id: int, 
        explicit_store_id: int = None,
        allow_split: bool = True,
        strategy: str = "PESSIMISTIC"
    ) -> Dict[str, Any]:
        """Orchestrates order allocation with automatic fallback to 2PC split fulfillment."""
        async with self.pool.acquire() as conn:
            target_store_id = explicit_store_id
            if not target_store_id:
                target_store_id = await self.find_nearest_store_for_customer(conn, customer_id)

            func = "allocate_order_fefo"
            strat_upper = (strategy or "PESSIMISTIC").upper()
            if "OCC" in strat_upper:
                func = "allocate_order_occ"
            elif "ADV" in strat_upper:
                func = "allocate_order_advisory"

            # 1. Attempt single-store allocation first with chosen strategy
            single_result = await conn.fetchval(
                f"SELECT {func}($1, $2)",
                order_id, target_store_id
            )
            single_data = json.loads(single_result) if isinstance(single_result, str) else single_result

            if single_data.get("success") is True:
                return single_data

            # If single store failed and split fulfillment is disabled, return failure
            if not allow_split:
                return single_data

            # 2. Split Fulfillment via Two-Phase Commit (2PC)
            return await self._execute_2pc_split(conn, order_id, customer_id)

    async def _execute_2pc_split(self, conn: asyncpg.Connection, order_id: int, customer_id: int) -> Dict[str, Any]:
        """Coordinates 2PC multi-store reservation across nearest dark stores."""
        # Fetch unfulfilled order items
        items = await conn.fetch(
            "SELECT order_item_id, sku_id, qty_requested FROM order_items WHERE order_id = $1",
            order_id
        )

        # Get top 3 nearest stores
        stores = await conn.fetch(
            """
            SELECT s.store_id, s.name
            FROM stores s
            JOIN customers c ON c.customer_id = $1
            WHERE s.is_active = TRUE
            ORDER BY 
                CASE 
                    WHEN s.location IS NOT NULL AND c.location IS NOT NULL 
                    THEN s.location <-> c.location 
                    ELSE NULL 
                END ASC
            LIMIT 3;
            """,
            customer_id
        )
        candidate_store_ids = [s["store_id"] for s in stores]

        prepared_reservations: List[Dict[str, Any]] = []
        split_commit_plan: List[Dict[str, Any]] = []

        try:
            # PHASE 1: PREPARE (Reserve Stock across stores)
            for item in items:
                sku_id = item["sku_id"]
                needed = item["qty_requested"]

                for s_id in candidate_store_ids:
                    if needed <= 0:
                        break

                    # Check available stock at this store
                    available = await conn.fetchval(
                        "SELECT COALESCE(SUM(qty_available), 0) FROM inventory WHERE store_id = $1 AND sku_id = $2",
                        s_id, sku_id
                    )
                    
                    if available > 0:
                        reserve_qty = min(available, needed)
                        prepare_res_raw = await conn.fetchval(
                            "SELECT reserve_stock_partition($1, $2, $3, $4)",
                            order_id, s_id, sku_id, reserve_qty
                        )
                        prep_data = json.loads(prepare_res_raw) if isinstance(prepare_res_raw, str) else prepare_res_raw

                        if prep_data.get("success"):
                            prepared_reservations.append(prep_data)
                            for r in prep_data.get("reservations", []):
                                split_commit_plan.append({
                                    "sku_id": sku_id,
                                    "store_id": s_id,
                                    "batch_id": r["batch_id"],
                                    "qty": r["qty"]
                                })
                            needed -= reserve_qty

                if needed > 0:
                    # Inability to fulfill complete SKU across all candidate stores -> Abort
                    raise Exception(f"Split fulfillment failed: SKU {sku_id} still short by {needed} units across network.")

            # PHASE 2: COMMIT (All participants successfully prepared)
            commit_res_raw = await conn.fetchval(
                "SELECT commit_split_allocation($1, $2::jsonb)",
                order_id, json.dumps(split_commit_plan)
            )
            commit_data = json.loads(commit_res_raw) if isinstance(commit_res_raw, str) else commit_res_raw
            commit_data["plan"] = split_commit_plan
            return commit_data

        except Exception as e:
            # PHASE 2: ABORT / ROLLBACK (Clean up any prepared reservations)
            for res in prepared_reservations:
                await conn.fetchval(
                    "SELECT rollback_split_reservation($1, $2, $3::jsonb)",
                    res["store_id"], res["sku_id"], json.dumps(res.get("reservations", []))
                )

            await conn.execute(
                "UPDATE orders SET status = 'FAILED', failure_reason = $1 WHERE order_id = $2",
                str(e), order_id
            )
            return {
                "success": False,
                "status": "FAILED",
                "phase": "ABORTED",
                "order_id": order_id,
                "error": str(e)
            }
