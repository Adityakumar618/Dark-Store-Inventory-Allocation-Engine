-- =============================================================================
-- Dark-Store Inventory Allocation Engine: Geospatial PostGIS Layer
-- File: schema/003_postgis.sql
-- Description: PostGIS extension setup, spatial columns, GiST indexing,
--              and KNN distance routing for nearest dark store lookups.
-- =============================================================================

-- 1. ENABLE EXTENSION
CREATE EXTENSION IF NOT EXISTS postgis;

-- 2. ADD GEOGRAPHY COLUMNS
ALTER TABLE stores 
ADD COLUMN IF NOT EXISTS location GEOGRAPHY(Point, 4326);

ALTER TABLE customers 
ADD COLUMN IF NOT EXISTS location GEOGRAPHY(Point, 4326);

ALTER TABLE riders 
ADD COLUMN IF NOT EXISTS location GEOGRAPHY(Point, 4326);

-- 3. POPULATE GEOGRAPHY FROM LAT/LNG
UPDATE stores 
SET location = ST_SetSRID(ST_MakePoint(lng, lat), 4326)::geography
WHERE location IS NULL AND lat IS NOT NULL AND lng IS NOT NULL;

UPDATE customers 
SET location = ST_SetSRID(ST_MakePoint(lng, lat), 4326)::geography
WHERE location IS NULL AND lat IS NOT NULL AND lng IS NOT NULL;

UPDATE riders 
SET location = ST_SetSRID(ST_MakePoint(current_lng, current_lat), 4326)::geography
WHERE location IS NULL AND current_lat IS NOT NULL AND current_lng IS NOT NULL;

-- 4. CREATE SPATIAL GIST INDEXES
CREATE INDEX IF NOT EXISTS idx_stores_location_gist ON stores USING GIST (location);
CREATE INDEX IF NOT EXISTS idx_customers_location_gist ON customers USING GIST (location);
CREATE INDEX IF NOT EXISTS idx_riders_location_gist ON riders USING GIST (location);

-- 5. FUNCTION: FIND NEAREST STORES WITH SUFFICIENT INVENTORY
CREATE OR REPLACE FUNCTION find_nearest_stores_with_stock(
    p_customer_lat NUMERIC,
    p_customer_lng NUMERIC,
    p_sku_id INT,
    p_qty INT,
    p_radius_meters FLOAT DEFAULT 15000.0
)
RETURNS TABLE (
    store_id INT,
    store_name VARCHAR(150),
    distance_meters FLOAT,
    total_stock INT,
    can_fully_fulfill BOOLEAN
) AS $$
DECLARE
    v_cust_point GEOGRAPHY;
BEGIN
    v_cust_point := ST_SetSRID(ST_MakePoint(p_customer_lng, p_customer_lat), 4326)::geography;

    RETURN QUERY
    SELECT 
        s.store_id,
        s.name AS store_name,
        ROUND(ST_Distance(s.location, v_cust_point)::NUMERIC, 1)::FLOAT AS distance_meters,
        COALESCE(SUM(i.qty_available), 0)::INT AS total_stock,
        (COALESCE(SUM(i.qty_available), 0) >= p_qty) AS can_fully_fulfill
    FROM stores s
    LEFT JOIN inventory i ON s.store_id = i.store_id AND i.sku_id = p_sku_id
    WHERE s.is_active = TRUE
      AND ST_DWithin(s.location, v_cust_point, p_radius_meters)
    GROUP BY s.store_id, s.name, s.location
    ORDER BY s.location <-> v_cust_point ASC;
END;
$$ LANGUAGE plpgsql;
