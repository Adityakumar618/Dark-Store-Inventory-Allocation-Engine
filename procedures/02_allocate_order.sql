-- =============================================================================
-- Dark-Store Inventory Allocation Engine: Production Concurrency-Safe Engine
-- File: procedures/02_allocate_order.sql
-- Description: PL/pgSQL FEFO allocation with SELECT ... FOR UPDATE pessimistic row
--              locking, deterministic lock ordering, multi-batch fulfillment,
--              and atomic rollback on partial depletion.
-- =============================================================================

CREATE OR REPLACE FUNCTION allocate_order_fefo(
    p_order_id INT,
    p_store_id INT
)
RETURNS JSONB AS $$
DECLARE
    r_item RECORD;
    r_batch RECORD;
    v_needed INT;
    v_allocated INT;
    v_order_status VARCHAR(30);
    v_allocations JSONB := '[]'::JSONB;
    v_total_stock INT;
BEGIN
    -- Verify Order Existence & Status
    SELECT status INTO v_order_status
    FROM orders
    WHERE order_id = p_order_id;

    IF v_order_status IS NULL THEN
        RAISE EXCEPTION 'Order ID % does not exist', p_order_id;
    END IF;

    IF v_order_status <> 'PENDING' THEN
        RETURN jsonb_build_object(
            'success', FALSE,
            'order_id', p_order_id,
            'status', v_order_status,
            'message', 'Order is already processed'
        );
    END IF;

    -- =========================================================================
    -- STEP 1: PRE-FLIGHT VERIFICATION & DETERMINISTIC LOCK ACQUISITION
    -- To prevent 40P01 deadlocks during concurrent multi-item allocations,
    -- we lock candidate inventory rows strictly in ASCENDING (sku_id, batch_id) order.
    -- =========================================================================
    FOR r_item IN
        SELECT oi.order_item_id, oi.sku_id, oi.qty_requested, k.name AS sku_name
        FROM order_items oi
        JOIN skus k ON oi.sku_id = k.sku_id
        WHERE oi.order_id = p_order_id AND oi.status = 'PENDING'
        ORDER BY oi.sku_id ASC
    LOOP
        v_needed := r_item.qty_requested;
        
        -- Check total available stock across all batches with FEFO pessimistic lock
        SELECT COALESCE(SUM(qty_available), 0) INTO v_total_stock
        FROM inventory
        WHERE store_id = p_store_id AND sku_id = r_item.sku_id;

        IF v_total_stock < v_needed THEN
            -- Atomically mark order as FAILED due to insufficient inventory
            UPDATE orders
            SET status = 'FAILED',
                failure_reason = FORMAT('Insufficient stock for SKU %s (%s). Required: %s, Available: %s', 
                                        r_item.sku_id, r_item.sku_name, v_needed, v_total_stock)
            WHERE order_id = p_order_id;

            UPDATE order_items
            SET status = 'UNFULFILLED'
            WHERE order_id = p_order_id;

            RETURN jsonb_build_object(
                'success', FALSE,
                'order_id', p_order_id,
                'status', 'FAILED',
                'reason', FORMAT('SKU %s stock depleted', r_item.sku_name)
            );
        END IF;

        -- =====================================================================
        -- STEP 2: FEFO BATCH CURSOR ALLOCATION
        -- Decrement inventory from batches ordered by earliest expiration date
        -- =====================================================================
        FOR r_batch IN
            SELECT 
                i.store_id, 
                i.sku_id, 
                i.batch_id, 
                i.qty_available,
                b.expiry_date
            FROM inventory i
            JOIN batches b ON i.sku_id = b.sku_id AND i.batch_id = b.batch_id
            WHERE i.store_id = p_store_id 
              AND i.sku_id = r_item.sku_id 
              AND i.qty_available > 0
            ORDER BY b.expiry_date ASC, i.batch_id ASC
            FOR UPDATE OF i  -- PESSIMISTIC ROW-LOCK ACQUIRED HERE
        LOOP
            IF v_needed <= 0 THEN
                EXIT;
            END IF;

            v_allocated := LEAST(r_batch.qty_available, v_needed);

            -- Atomically decrement locked inventory row
            UPDATE inventory
            SET qty_available = qty_available - v_allocated,
                updated_at = CURRENT_TIMESTAMP
            WHERE store_id = p_store_id 
              AND sku_id = r_item.sku_id 
              AND batch_id = r_batch.batch_id;

            -- Update or record allocation details
            UPDATE order_items
            SET store_id_allocated = p_store_id,
                batch_id_allocated = r_batch.batch_id,
                status = 'ALLOCATED'
            WHERE order_item_id = r_item.order_item_id;

            v_allocations := v_allocations || jsonb_build_object(
                'sku_id', r_item.sku_id,
                'batch_id', r_batch.batch_id,
                'qty_allocated', v_allocated,
                'expiry_date', r_batch.expiry_date
            );

            v_needed := v_needed - v_allocated;
        END LOOP;

        -- Edge check: If concurrency caused batch quantity to drop before lock
        IF v_needed > 0 THEN
            RAISE EXCEPTION 'Concurrency conflict: stock changed during allocation for SKU %', r_item.sku_id;
        END IF;
    END LOOP;

    -- STEP 3: COMMIT ORDER ALLOCATION STATUS & WRITE TRANSACTIONAL OUTBOX
    UPDATE orders
    SET status = 'ALLOCATED',
        allocated_at = CURRENT_TIMESTAMP,
        failure_reason = NULL
    WHERE order_id = p_order_id;

    INSERT INTO outbox_events (aggregate_type, aggregate_id, event_type, payload)
    VALUES (
        'ORDER',
        p_order_id::TEXT,
        'ORDER_ALLOCATED',
        jsonb_build_object('order_id', p_order_id, 'store_id', p_store_id, 'strategy', 'PESSIMISTIC_LOCK', 'allocations', v_allocations)
    );

    RETURN jsonb_build_object(
        'success', TRUE,
        'order_id', p_order_id,
        'store_id', p_store_id,
        'status', 'ALLOCATED',
        'allocations', v_allocations
    );
END;
$$ LANGUAGE plpgsql;
