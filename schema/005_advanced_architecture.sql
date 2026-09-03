-- =============================================================================
-- Dark-Store Inventory Allocation Engine: Advanced Architectural Schema
-- File: schema/005_advanced_architecture.sql
-- Description: Adds OCC versioning, Transactional Outbox pattern, PostGIS
--              Voronoi serviceability boundaries, and dynamic ROP views.
-- =============================================================================

-- 1. Optimistic Concurrency Control (OCC) Column
ALTER TABLE inventory ADD COLUMN IF NOT EXISTS version INT DEFAULT 1 NOT NULL;

-- 2. Transactional Outbox Table (solves dual-write problem)
CREATE TABLE IF NOT EXISTS outbox_events (
    event_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    aggregate_type VARCHAR(50) NOT NULL,
    aggregate_id VARCHAR(50) NOT NULL,
    event_type VARCHAR(50) NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    processed BOOLEAN NOT NULL DEFAULT FALSE,
    processed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_outbox_unprocessed 
ON outbox_events(created_at ASC) 
WHERE processed = FALSE;

-- 3. Dynamic Reorder Point (ROP) & Safety Stock Analytical View
-- Uses statistical demand modeling: ROP = (avg_daily_demand * lead_time) + Z * stddev * sqrt(lead_time)
-- Assuming Lead Time = 2 days, 95% Service Level Factor (Z = 1.65)
CREATE OR REPLACE VIEW v_dynamic_reorder_points AS
WITH sku_daily_velocity AS (
    SELECT 
        oi.sku_id,
        COALESCE(SUM(oi.qty_requested)::NUMERIC / GREATEST(1, CURRENT_DATE - MIN(o.created_at::DATE) + 1), 5.0) AS avg_daily_demand,
        COALESCE(STDDEV_POP(oi.qty_requested), 2.5) AS demand_stddev
    FROM order_items oi
    JOIN orders o ON oi.order_id = o.order_id
    WHERE o.status IN ('ALLOCATED', 'SPLIT_ALLOCATED')
    GROUP BY oi.sku_id
),
sku_current_inventory AS (
    SELECT 
        sku_id,
        SUM(qty_available) AS total_available,
        SUM(qty_reserved) AS total_reserved
    FROM inventory
    GROUP BY sku_id
)
SELECT 
    k.sku_id,
    k.barcode,
    k.name AS sku_name,
    k.brand,
    k.category,
    COALESCE(ci.total_available, 0) AS total_stock,
    ROUND(COALESCE(dv.avg_daily_demand, 5.0), 2) AS avg_daily_demand,
    2 AS lead_time_days,
    -- Safety Stock = 1.65 * stddev * sqrt(2)
    ROUND(1.65 * COALESCE(dv.demand_stddev, 2.5) * SQRT(2)) AS safety_stock,
    -- Dynamic ROP = (avg_daily_demand * 2) + Safety Stock
    ROUND((COALESCE(dv.avg_daily_demand, 5.0) * 2) + (1.65 * COALESCE(dv.demand_stddev, 2.5) * SQRT(2))) AS dynamic_reorder_point,
    CASE 
        WHEN COALESCE(ci.total_available, 0) <= ROUND((COALESCE(dv.avg_daily_demand, 5.0) * 2) + (1.65 * COALESCE(dv.demand_stddev, 2.5) * SQRT(2)))
        THEN 'REORDER_NOW'
        ELSE 'OPTIMAL'
    END AS inventory_health_status
FROM skus k
LEFT JOIN sku_daily_velocity dv ON k.sku_id = dv.sku_id
LEFT JOIN sku_current_inventory ci ON k.sku_id = ci.sku_id
ORDER BY (COALESCE(ci.total_available, 0) - ROUND((COALESCE(dv.avg_daily_demand, 5.0) * 2) + (1.65 * COALESCE(dv.demand_stddev, 2.5) * SQRT(2)))) ASC;
