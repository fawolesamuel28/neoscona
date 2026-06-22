-- Migration 007: Billing Perfection
-- Robust transaction tracking and automated billing metadata.

-- 1. Create billing_transactions table
CREATE TABLE IF NOT EXISTS billing_transactions (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    amount       NUMERIC NOT NULL,
    currency     TEXT NOT NULL DEFAULT 'NGN',
    type         TEXT NOT NULL, -- 'subscription', 'topup', 'adjustment', 'refund'
    status       TEXT NOT NULL DEFAULT 'pending', -- 'pending', 'successful', 'failed'
    flw_ref      TEXT UNIQUE, -- Flutterwave tx_ref or id
    description  TEXT,
    metadata     JSONB DEFAULT '{}'::jsonb,
    created_at   TIMESTAMPTZ DEFAULT NOW(),
    updated_at   TIMESTAMPTZ DEFAULT NOW()
);

-- 2. Add indexes for performance
CREATE INDEX IF NOT EXISTS idx_billing_transactions_tenant ON billing_transactions(tenant_id);
CREATE INDEX IF NOT EXISTS idx_billing_transactions_tenant_status ON billing_transactions(tenant_id, status);

-- 3. Enable RLS
ALTER TABLE billing_transactions ENABLE ROW LEVEL SECURITY;

-- 4. Create policy: users can view their own tenant's transactions
DROP POLICY IF EXISTS "tenant_select_transactions" ON billing_transactions;
CREATE POLICY "tenant_select_transactions" ON billing_transactions
    FOR SELECT TO authenticated
    USING (tenant_id IN (SELECT user_tenant_ids()));

-- 5. Add balance to tenants if not already present (it should be, but let's be safe)
DO $$ 
BEGIN 
    IF NOT EXISTS (SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME='tenants' AND COLUMN_NAME='balance') THEN
        ALTER TABLE tenants ADD COLUMN balance NUMERIC DEFAULT 0;
    END IF;
END $$;

-- 6. Add trigger for updated_at
-- 7. Function to atomic increment balance
CREATE OR REPLACE FUNCTION increment_tenant_balance(p_tenant UUID, p_amount NUMERIC)
RETURNS VOID AS $$
BEGIN
    UPDATE tenants
    SET balance = COALESCE(balance, 0) + p_amount,
        updated_at = NOW()
    WHERE id = p_tenant;
END;
$$ LANGUAGE plpgsql;
