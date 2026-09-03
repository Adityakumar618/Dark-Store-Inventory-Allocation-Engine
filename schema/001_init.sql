-- =============================================================================
-- Dark-Store Inventory Allocation Engine: Phase 1 DDL
-- File: schema/001_init.sql
-- Description: Core relational schema with strict integrity constraints,
--              weak entity identification, FEFO index optimization,
--              and real-world OpenStreetMap & Barcode catalog support.
-- =============================================================================

-- Clean up old objects if re-running
DROP TABLE IF EXISTS reorder_alerts CASCADE;
DROP TABLE IF EXISTS inventory_audit CASCADE;
DROP TABLE IF EXISTS order_items CASCADE;
DROP TABLE IF EXISTS orders CASCADE;
DROP TABLE IF EXISTS riders CASCADE;
DROP TABLE IF EXISTS customers CASCADE;
DROP TABLE IF EXISTS inventory CASCADE;
DROP TABLE IF EXISTS batches CASCADE;
DROP TABLE IF EXISTS skus CASCADE;
DROP TABLE IF EXISTS stores CASCADE;
DROP TABLE IF EXISTS zones CASCADE;

-- 1. ZONES (Geographic Delivery Pincode Clusters)
CREATE TABLE zones (
    zone_id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    pincode VARCHAR(10) NOT NULL UNIQUE,
    city VARCHAR(100) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- 2. STORES (Real Dark Stores & Supermarket Micro-Fulfillment Hubs)
CREATE TABLE stores (
    store_id SERIAL PRIMARY KEY,
    osm_id BIGINT,
    name VARCHAR(200) NOT NULL,
    brand VARCHAR(100),
    address TEXT,
    zone_id INT NOT NULL REFERENCES zones(zone_id) ON DELETE RESTRICT,
    lat NUMERIC(9,6) NOT NULL,
    lng NUMERIC(9,6) NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_store_lat CHECK (lat BETWEEN -90.000000 AND 90.000000),
    CONSTRAINT chk_store_lng CHECK (lng BETWEEN -180.000000 AND 180.000000)
);

-- 3. SKUS (Authentic FMCG Products with Universal EAN-13 Barcodes)
CREATE TABLE skus (
    sku_id SERIAL PRIMARY KEY,
    barcode VARCHAR(32) UNIQUE,
    name VARCHAR(250) NOT NULL,
    brand VARCHAR(100),
    category VARCHAR(100) NOT NULL,
    is_perishable BOOLEAN NOT NULL DEFAULT FALSE,
    price NUMERIC(10,2) NOT NULL CHECK (price >= 0.00),
    reorder_threshold INT NOT NULL DEFAULT 10 CHECK (reorder_threshold >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- 4. BATCHES (Weak Entity keyed on (sku_id, batch_id))
-- FEFO (First-Expiry-First-Out) uses expiry_date
CREATE TABLE batches (
    sku_id INT NOT NULL REFERENCES skus(sku_id) ON DELETE CASCADE,
    batch_id INT NOT NULL,
    expiry_date DATE NOT NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (sku_id, batch_id),
    CONSTRAINT chk_batch_expiry CHECK (expiry_date >= received_at::DATE)
);

-- 5. INVENTORY (Composite PK: store_id, sku_id, batch_id)
-- Strict CHECK constraint: qty_available can NEVER be negative (prevent overselling)
CREATE TABLE inventory (
    store_id INT NOT NULL REFERENCES stores(store_id) ON DELETE RESTRICT,
    sku_id INT NOT NULL,
    batch_id INT NOT NULL,
    qty_available INT NOT NULL CHECK (qty_available >= 0),
    qty_reserved INT NOT NULL DEFAULT 0 CHECK (qty_reserved >= 0),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (store_id, sku_id, batch_id),
    FOREIGN KEY (sku_id, batch_id) REFERENCES batches(sku_id, batch_id) ON DELETE RESTRICT
);

-- 6. CUSTOMERS
CREATE TABLE customers (
    customer_id SERIAL PRIMARY KEY,
    name VARCHAR(150) NOT NULL,
    phone VARCHAR(20) NOT NULL UNIQUE,
    email VARCHAR(150),
    zone_id INT NOT NULL REFERENCES zones(zone_id) ON DELETE RESTRICT,
    address TEXT,
    lat NUMERIC(9,6) NOT NULL,
    lng NUMERIC(9,6) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_customer_lat CHECK (lat BETWEEN -90.000000 AND 90.000000),
    CONSTRAINT chk_customer_lng CHECK (lng BETWEEN -180.000000 AND 180.000000)
);

-- 7. ORDERS
CREATE TABLE orders (
    order_id SERIAL PRIMARY KEY,
    customer_id INT NOT NULL REFERENCES customers(customer_id) ON DELETE RESTRICT,
    status VARCHAR(30) NOT NULL DEFAULT 'PENDING' 
        CHECK (status IN ('PENDING', 'ALLOCATED', 'SPLIT_ALLOCATED', 'FAILED', 'CANCELLED', 'DELIVERED')),
    idempotency_key VARCHAR(128) NOT NULL UNIQUE,
    total_amount NUMERIC(10,2) NOT NULL DEFAULT 0.00 CHECK (total_amount >= 0.00),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    allocated_at TIMESTAMPTZ,
    failure_reason TEXT
);

-- 8. ORDER_ITEMS
CREATE TABLE order_items (
    order_item_id SERIAL PRIMARY KEY,
    order_id INT NOT NULL REFERENCES orders(order_id) ON DELETE CASCADE,
    sku_id INT NOT NULL REFERENCES skus(sku_id) ON DELETE RESTRICT,
    qty_requested INT NOT NULL CHECK (qty_requested > 0),
    store_id_allocated INT REFERENCES stores(store_id) ON DELETE SET NULL,
    batch_id_allocated INT,
    unit_price NUMERIC(10,2) NOT NULL DEFAULT 0.00 CHECK (unit_price >= 0.00),
    status VARCHAR(30) NOT NULL DEFAULT 'PENDING'
        CHECK (status IN ('PENDING', 'ALLOCATED', 'UNFULFILLED')),
    FOREIGN KEY (sku_id, batch_id_allocated) REFERENCES batches(sku_id, batch_id) ON DELETE SET NULL
);

-- 9. RIDERS (Fleet in Delivery Zones)
CREATE TABLE riders (
    rider_id SERIAL PRIMARY KEY,
    name VARCHAR(150) NOT NULL,
    phone VARCHAR(20) NOT NULL UNIQUE,
    current_zone_id INT NOT NULL REFERENCES zones(zone_id) ON DELETE RESTRICT,
    status VARCHAR(30) NOT NULL DEFAULT 'IDLE' 
        CHECK (status IN ('IDLE', 'ASSIGNED', 'DELIVERING', 'OFFLINE')),
    current_lat NUMERIC(9,6),
    current_lng NUMERIC(9,6),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- 10. INVENTORY_AUDIT (Audit Trail for Compliance & Debugging)
CREATE TABLE inventory_audit (
    audit_id BIGSERIAL PRIMARY KEY,
    store_id INT NOT NULL,
    sku_id INT NOT NULL,
    batch_id INT NOT NULL,
    old_qty INT,
    new_qty INT,
    operation VARCHAR(20) NOT NULL,
    changed_by VARCHAR(100) NOT NULL DEFAULT CURRENT_USER,
    changed_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- 11. REORDER_ALERTS (Automated Supply Chain Threshold Triggers)
CREATE TABLE reorder_alerts (
    alert_id BIGSERIAL PRIMARY KEY,
    store_id INT NOT NULL REFERENCES stores(store_id) ON DELETE CASCADE,
    sku_id INT NOT NULL REFERENCES skus(sku_id) ON DELETE CASCADE,
    current_qty INT NOT NULL,
    threshold INT NOT NULL,
    alerted_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    resolved BOOLEAN NOT NULL DEFAULT FALSE
);

-- =============================================================================
-- PERFORMANCE INDEXES
-- =============================================================================

CREATE INDEX idx_batches_fefo ON batches (sku_id, expiry_date ASC, batch_id ASC);
CREATE INDEX idx_inventory_store_sku ON inventory (store_id, sku_id);
CREATE INDEX idx_orders_customer_created ON orders (customer_id, created_at DESC);
CREATE INDEX idx_order_items_order ON order_items (order_id);
CREATE INDEX idx_audit_store_sku ON inventory_audit (store_id, sku_id, changed_at DESC);
CREATE INDEX idx_skus_barcode ON skus (barcode);
CREATE INDEX idx_skus_brand ON skus (brand);
