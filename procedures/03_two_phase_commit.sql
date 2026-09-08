-- =============================================================================
-- Dark-Store Inventory Allocation Engine: Multi-Store Split & 2PC Logic
-- File: procedures/03_two_phase_commit.sql
-- Description: Two-Phase Commit (2PC) reservation protocol for multi-store
--              split order fulfillment.
-- =============================================================================

-- 1. PREPARE PHASE: RESERVE STOCK AT TARGET STORE
CREATE OR REPLACE FUNCTION reserve_stock_partition(
    p_order_id INT,
    p_store_id INT,
    p_sku_id INT,
    p_qty INT
)
RETURNS JSONB AS $$
DECLARE
    r_batch RECORD;
    v_needed INT := p_qty;
    v_allocated INT;
    v_total_available INT;
    v_batch_reservations JSONB := '[]'::JSONB;
BEGIN
    -- Check available stock
    SELECT COALESCE(SUM(qty_available), 0) INTO v_total_available
    FROM inventory
    WHERE store_id = p_store_id AND sku_id = p_sku_id;

    IF v_total_available < p_qty THEN
        RETURN jsonb_build_object(
            'success', FALSE,
            'phase', 'PREPARE',
            'store_id', p_store_id,
            'sku_id', p_sku_id,
            'error', FORMAT('Store %s has insufficient stock (%s available, %s needed)', p_store_id, v_total_available, p_qty)
        );
    END IF;

    -- Lock candidate rows and shift qty_available -> qty_reserved
    FOR r_batch IN
        SELECT batch_id, qty_available
        FROM inventory
        WHERE store_id = p_store_id AND sku_id = p_sku_id AND qty_available > 0
        ORDER BY batch_id ASC
        FOR UPDATE
    LOOP
        IF v_needed <= 0 THEN
            EXIT;
        END IF;

        v_allocated := LEAST(r_batch.qty_available, v_needed);

        UPDATE inventory
        SET qty_available = qty_available - v_allocated,
            qty_reserved = qty_reserved + v_allocated,
            updated_at = CURRENT_TIMESTAMP
        WHERE store_id = p_store_id AND sku_id = p_sku_id AND batch_id = r_batch.batch_id;

        v_batch_reservations := v_batch_reservations || jsonb_build_object(
            'batch_id', r_batch.batch_id,
            'qty', v_allocated
        );

        v_needed := v_needed - v_allocated;
    END LOOP;

    RETURN jsonb_build_object(
        'success', TRUE,
        'phase', 'PREPARED',
        'store_id', p_store_id,
        'sku_id', p_sku_id,
        'qty_reserved', p_qty,
        'reservations', v_batch_reservations
    );
END;
$$ LANGUAGE plpgsql;


-- 2. ABORT / ROLLBACK PHASE: RELEASE RESERVED STOCK
CREATE OR REPLACE FUNCTION rollback_split_reservation(
    p_store_id INT,
    p_sku_id INT,
    p_reservations JSONB
)
RETURNS JSONB AS $$
DECLARE
    r RECORD;
BEGIN
    FOR r IN SELECT * FROM jsonb_to_recordset(p_reservations) AS x(batch_id INT, qty INT)
    LOOP
        UPDATE inventory
        SET qty_available = qty_available + r.qty,
            qty_reserved = GREATEST(0, qty_reserved - r.qty),
            updated_at = CURRENT_TIMESTAMP
        WHERE store_id = p_store_id AND sku_id = p_sku_id AND batch_id = r.batch_id;
    END LOOP;

    RETURN jsonb_build_object(
        'success', TRUE,
        'phase', 'ABORTED_ROLLED_BACK',
        'store_id', p_store_id,
        'sku_id', p_sku_id
    );
END;
$$ LANGUAGE plpgsql;


-- 3. COMMIT PHASE: FINALIZE SPLIT ALLOCATIONS
CREATE OR REPLACE FUNCTION commit_split_allocation(
    p_order_id INT,
    p_split_plan JSONB
)
RETURNS JSONB AS $$
DECLARE
    r RECORD;
BEGIN
    FOR r IN SELECT * FROM jsonb_to_recordset(p_split_plan) AS x(
        sku_id INT, 
        store_id INT, 
        batch_id INT, 
        qty INT
    )
    LOOP
        -- Decrement reservation bucket
        UPDATE inventory
        SET qty_reserved = GREATEST(0, qty_reserved - r.qty),
            updated_at = CURRENT_TIMESTAMP
        WHERE store_id = r.store_id AND sku_id = r.sku_id AND batch_id = r.batch_id;

        -- Update order item mapping
        UPDATE order_items
        SET store_id_allocated = r.store_id,
            batch_id_allocated = r.batch_id,
            status = 'ALLOCATED'
        WHERE order_id = p_order_id AND sku_id = r.sku_id;
    END LOOP;

    UPDATE orders
    SET status = 'SPLIT_ALLOCATED',
        allocated_at = CURRENT_TIMESTAMP,
        failure_reason = NULL
    WHERE order_id = p_order_id;

    INSERT INTO outbox_events (aggregate_type, aggregate_id, event_type, payload)
    VALUES (
        'ORDER',
        p_order_id::TEXT,
        'ORDER_SPLIT_ALLOCATED',
        jsonb_build_object('order_id', p_order_id, 'status', 'SPLIT_ALLOCATED', 'strategy', '2PC_SPLIT', 'plan', p_split_plan)
    );

    RETURN jsonb_build_object(
        'success', TRUE,
        'phase', 'COMMITTED',
        'order_id', p_order_id,
        'status', 'SPLIT_ALLOCATED'
    );
END;
$$ LANGUAGE plpgsql;
