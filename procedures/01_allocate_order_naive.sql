-- =============================================================================
-- Dark-Store Inventory Allocation Engine: Naive (Broken) Allocation Baseline
-- File: procedures/01_allocate_order_naive.sql
-- Description: Deliberately omitted row locks (no FOR UPDATE) and simulated latency
--              to demonstrate race conditions and overselling under load.
-- =============================================================================

CREATE OR REPLACE FUNCTION allocate_order_naive(
    p_order_id INT,
    p_store_id INT
)
RETURNS JSONB AS $$
DECLARE
    r_item RECORD;
    v_available INT;
    v_batch_id INT;
BEGIN
    -- Loop through all items in the order
    FOR r_item IN 
        SELECT order_item_id, sku_id, qty_requested 
        FROM order_items 
        WHERE order_id = p_order_id AND status = 'PENDING'
    LOOP
        -- NAIVE READ: Plain SELECT without FOR UPDATE
        SELECT batch_id, qty_available INTO v_batch_id, v_available
        FROM inventory
        WHERE store_id = p_store_id AND sku_id = r_item.sku_id AND qty_available > 0
        ORDER BY batch_id ASC
        LIMIT 1;

        -- Artificially simulate application/network roundtrip latency (50ms)
        -- During this window, concurrent transactions read the exact same stock
        PERFORM pg_sleep(0.05);

        IF v_available IS NULL OR v_available < r_item.qty_requested THEN
            -- Insufficient stock: Mark failed
            UPDATE orders 
            SET status = 'FAILED', failure_reason = 'Stock depleted (Naive)' 
            WHERE order_id = p_order_id;
            
            RETURN jsonb_build_object(
                'success', FALSE, 
                'order_id', p_order_id, 
                'error', 'Insufficient stock for SKU ' || r_item.sku_id
            );
        END IF;

        -- NAIVE WRITE: Decrement stock based on stale read
        UPDATE inventory
        SET qty_available = qty_available - r_item.qty_requested,
            updated_at = CURRENT_TIMESTAMP
        WHERE store_id = p_store_id AND sku_id = r_item.sku_id AND batch_id = v_batch_id;

        UPDATE order_items
        SET store_id_allocated = p_store_id,
            batch_id_allocated = v_batch_id,
            status = 'ALLOCATED'
        WHERE order_item_id = r_item.order_item_id;
    END LOOP;

    -- Update order status
    UPDATE orders
    SET status = 'ALLOCATED', allocated_at = CURRENT_TIMESTAMP
    WHERE order_id = p_order_id;

    RETURN jsonb_build_object(
        'success', TRUE, 
        'order_id', p_order_id, 
        'store_id', p_store_id, 
        'mode', 'NAIVE_NO_LOCK'
    );
END;
$$ LANGUAGE plpgsql;
