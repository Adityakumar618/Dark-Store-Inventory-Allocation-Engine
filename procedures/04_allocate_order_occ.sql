-- =============================================================================
-- Dark-Store Inventory Allocation Engine: Optimistic Concurrency Control (OCC)
-- File: procedures/04_allocate_order_occ.sql
-- Description: PL/pgSQL allocation using version-number checking.
--              No row-level locks (zero lock contention).
--              Supports multi-batch FEFO draining.
--              Aborts or retries on version conflict.
-- =============================================================================

CREATE OR REPLACE FUNCTION allocate_order_occ(
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
    v_rows_updated INT;
    v_allocations JSONB := '[]'::JSONB;
    v_total_stock INT;
BEGIN
    SELECT status INTO v_order_status FROM orders WHERE order_id = p_order_id;
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

    FOR r_item IN
        SELECT oi.order_item_id, oi.sku_id, oi.qty_requested, k.name AS sku_name
        FROM order_items oi
        JOIN skus k ON oi.sku_id = k.sku_id
        WHERE oi.order_id = p_order_id AND oi.status = 'PENDING'
        ORDER BY oi.sku_id ASC
    LOOP
        v_needed := r_item.qty_requested;

        -- OCC Read Phase (NO LOCKS HELD)
        SELECT COALESCE(SUM(qty_available), 0) INTO v_total_stock
        FROM inventory
        WHERE store_id = p_store_id AND sku_id = r_item.sku_id;

        IF v_total_stock < v_needed THEN
            UPDATE orders
            SET status = 'FAILED',
                failure_reason = FORMAT('Stock depleted for SKU %s', r_item.sku_name)
            WHERE order_id = p_order_id;

            UPDATE order_items SET status = 'UNFULFILLED' WHERE order_id = p_order_id;

            RETURN jsonb_build_object(
                'success', FALSE,
                'order_id', p_order_id,
                'status', 'FAILED',
                'reason', 'Insufficient inventory'
            );
        END IF;

        -- OCC Validate & Write Phase (Multi-batch FEFO draining)
        FOR r_batch IN
            SELECT i.store_id, i.sku_id, i.batch_id, i.qty_available, i.version, b.expiry_date
            FROM inventory i
            JOIN batches b ON i.sku_id = b.sku_id AND i.batch_id = b.batch_id
            WHERE i.store_id = p_store_id 
              AND i.sku_id = r_item.sku_id 
              AND i.qty_available > 0
            ORDER BY b.expiry_date ASC, i.batch_id ASC
        LOOP
            IF v_needed <= 0 THEN
                EXIT;
            END IF;

            v_allocated := LEAST(r_batch.qty_available, v_needed);

            -- Attempt atomic update with version match
            UPDATE inventory
            SET qty_available = qty_available - v_allocated,
                version = version + 1
            WHERE store_id = p_store_id
              AND sku_id = r_item.sku_id
              AND batch_id = r_batch.batch_id
              AND version = r_batch.version;

            GET DIAGNOSTICS v_rows_updated = ROW_COUNT;

            IF v_rows_updated = 0 THEN
                -- CONCURRENCY CONFLICT: Another worker modified this row concurrently!
                UPDATE orders
                SET status = 'FAILED',
                    failure_reason = FORMAT('OCC Conflict: Version mismatch on SKU %s batch %s', r_item.sku_name, r_batch.batch_id)
                WHERE order_id = p_order_id;

                RETURN jsonb_build_object(
                    'success', FALSE,
                    'order_id', p_order_id,
                    'status', 'OCC_CONFLICT',
                    'reason', 'Concurrent version mutation detected'
                );
            END IF;

            UPDATE order_items
            SET store_id_allocated = p_store_id,
                batch_id_allocated = r_batch.batch_id,
                status = 'ALLOCATED'
            WHERE order_item_id = r_item.order_item_id;

            v_allocations := v_allocations || jsonb_build_object(
                'sku_id', r_item.sku_id,
                'batch_id', r_batch.batch_id,
                'qty', v_allocated,
                'strategy', 'OCC'
            );

            v_needed := v_needed - v_allocated;
        END LOOP;

        IF v_needed > 0 THEN
            UPDATE orders SET status = 'FAILED', failure_reason = 'Partial depletion under OCC' WHERE order_id = p_order_id;
            RETURN jsonb_build_object('success', FALSE, 'order_id', p_order_id, 'status', 'FAILED');
        END IF;
    END LOOP;

    -- Finalize Order and write to Transactional Outbox
    UPDATE orders
    SET status = 'ALLOCATED',
        allocated_at = CLOCK_TIMESTAMP()
    WHERE order_id = p_order_id;

    INSERT INTO outbox_events (aggregate_type, aggregate_id, event_type, payload)
    VALUES (
        'ORDER',
        p_order_id::TEXT,
        'ORDER_ALLOCATED',
        jsonb_build_object('order_id', p_order_id, 'store_id', p_store_id, 'strategy', 'OCC', 'allocations', v_allocations)
    );

    RETURN jsonb_build_object(
        'success', TRUE,
        'order_id', p_order_id,
        'status', 'ALLOCATED',
        'strategy', 'OCC',
        'allocations', v_allocations
    );
END;
$$ LANGUAGE plpgsql;
