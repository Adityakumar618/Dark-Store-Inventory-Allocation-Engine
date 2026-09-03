-- =============================================================================
-- Dark-Store Inventory Allocation Engine: Triggers & Auditing
-- File: schema/002_triggers.sql
-- Description: Automated audit logging and proactive reorder threshold alerts.
-- =============================================================================

-- 1. AUDIT TRIGGER FUNCTION
CREATE OR REPLACE FUNCTION func_inventory_audit()
RETURNS TRIGGER AS $$
BEGIN
    IF (TG_OP = 'UPDATE') THEN
        -- Log whenever quantity changes
        IF (OLD.qty_available <> NEW.qty_available OR OLD.qty_reserved <> NEW.qty_reserved) THEN
            INSERT INTO inventory_audit (
                store_id,
                sku_id,
                batch_id,
                old_qty,
                new_qty,
                operation,
                changed_by,
                changed_at
            ) VALUES (
                OLD.store_id,
                OLD.sku_id,
                OLD.batch_id,
                OLD.qty_available,
                NEW.qty_available,
                'UPDATE',
                CURRENT_USER,
                CURRENT_TIMESTAMP
            );
        END IF;
        RETURN NEW;
    ELSIF (TG_OP = 'DELETE') THEN
        INSERT INTO inventory_audit (
            store_id,
            sku_id,
            batch_id,
            old_qty,
            new_qty,
            operation,
            changed_by,
            changed_at
        ) VALUES (
            OLD.store_id,
            OLD.sku_id,
            OLD.batch_id,
            OLD.qty_available,
            0,
            'DELETE',
            CURRENT_USER,
            CURRENT_TIMESTAMP
        );
        RETURN OLD;
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

-- Attach Audit Trigger
DROP TRIGGER IF EXISTS trg_inventory_audit ON inventory;
CREATE TRIGGER trg_inventory_audit
BEFORE UPDATE OR DELETE ON inventory
FOR EACH ROW
EXECUTE FUNCTION func_inventory_audit();


-- 2. REORDER THRESHOLD ALERT TRIGGER FUNCTION
CREATE OR REPLACE FUNCTION func_reorder_alert()
RETURNS TRIGGER AS $$
DECLARE
    v_threshold INT;
    v_total_stock INT;
    v_existing_alert_count INT;
BEGIN
    -- Fetch SKU threshold
    SELECT reorder_threshold INTO v_threshold
    FROM skus
    WHERE sku_id = NEW.sku_id;

    IF v_threshold IS NULL THEN
        RETURN NEW;
    END IF;

    -- Calculate current total available stock across all batches for this store and SKU
    SELECT COALESCE(SUM(qty_available), 0) INTO v_total_stock
    FROM inventory
    WHERE store_id = NEW.store_id AND sku_id = NEW.sku_id;

    -- Check if stock has breached threshold
    IF (v_total_stock <= v_threshold) THEN
        -- Check if an active unresolved alert already exists to prevent alert spam
        SELECT COUNT(*) INTO v_existing_alert_count
        FROM reorder_alerts
        WHERE store_id = NEW.store_id 
          AND sku_id = NEW.sku_id 
          AND resolved = FALSE;

        IF v_existing_alert_count = 0 THEN
            INSERT INTO reorder_alerts (
                store_id,
                sku_id,
                current_qty,
                threshold,
                alerted_at,
                resolved
            ) VALUES (
                NEW.store_id,
                NEW.sku_id,
                v_total_stock,
                v_threshold,
                CURRENT_TIMESTAMP,
                FALSE
            );
        END IF;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Attach Reorder Alert Trigger
DROP TRIGGER IF EXISTS trg_reorder_alert ON inventory;
CREATE TRIGGER trg_reorder_alert
AFTER UPDATE ON inventory
FOR EACH ROW
EXECUTE FUNCTION func_reorder_alert();
