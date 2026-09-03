-- =============================================================================
-- Dark-Store Inventory Allocation Engine: Row-Level Security (RLS)
-- File: schema/004_rls.sql
-- Description: Multi-tenant / Store-Manager isolation enforcing that dark-store
--              managers can only view and mutate inventory belonging to their
--              assigned store (via session parameter app.current_store_id).
-- =============================================================================

-- 1. CREATE STORE MANAGER ROLE
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'store_manager_role') THEN
        CREATE ROLE store_manager_role WITH LOGIN PASSWORD 'managerpass';
    END IF;
END
$$;

-- Grant base table permissions
GRANT CONNECT ON DATABASE darkstore_db TO store_manager_role;
GRANT USAGE ON SCHEMA public TO store_manager_role;
GRANT SELECT, UPDATE ON inventory TO store_manager_role;
GRANT SELECT ON skus, batches, stores TO store_manager_role;

-- 2. ENABLE ROW LEVEL SECURITY ON INVENTORY
ALTER TABLE inventory ENABLE ROW LEVEL SECURITY;

-- Drop existing policies if re-applying
DROP POLICY IF EXISTS store_manager_inventory_policy ON inventory;
DROP POLICY IF EXISTS system_admin_inventory_bypass ON inventory;

-- 3. STORE MANAGER ISOLATION POLICY
-- A store manager can ONLY SELECT and UPDATE rows matching their active session store ID
CREATE POLICY store_manager_inventory_policy ON inventory
    FOR ALL
    TO store_manager_role
    USING (
        store_id = NULLIF(current_setting('app.current_store_id', true), '')::INT
    )
    WITH CHECK (
        store_id = NULLIF(current_setting('app.current_store_id', true), '')::INT
    );

-- 4. SYSTEM ADMIN / SUPERUSER BYPASS POLICY
-- Allows backend order allocation coordinator and admin jobs full access
CREATE POLICY system_admin_inventory_bypass ON inventory
    FOR ALL
    TO postgres
    USING (TRUE)
    WITH CHECK (TRUE);
