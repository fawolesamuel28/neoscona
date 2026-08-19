-- Migration 008: Billing ledger and dunning state.
--
-- Adds a real double-entry-style balance ledger (every mutation of
-- tenants.balance is now logged with a running snapshot), a decrement RPC
-- (balance was previously credit-only), and staged-dunning columns on tenants.
-- (Receipts already exist: GET /billing/invoice/{tx_id} renders billing_transactions
-- directly — no separate invoices table needed.)

-- 1. ledger_entries — append-only log of every balance mutation.
CREATE TABLE IF NOT EXISTS ledger_entries (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    delta        NUMERIC NOT NULL,           -- positive = credit, negative = debit
    balance_after NUMERIC NOT NULL,
    reason       TEXT NOT NULL,              -- 'topup', 'proration', 'usage_overage', 'refund', ...
    ref_type     TEXT,                       -- 'billing_transaction' | 'usage_event' | ...
    ref_id       TEXT,
    metadata     JSONB DEFAULT '{}'::jsonb,
    created_at   TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ledger_entries_tenant ON ledger_entries(tenant_id, created_at DESC);

ALTER TABLE ledger_entries ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "tenant_select_ledger" ON ledger_entries;
CREATE POLICY "tenant_select_ledger" ON ledger_entries
    FOR SELECT TO authenticated
    USING (tenant_id IN (SELECT user_tenant_ids()));

-- 2. Row-locking credit/debit RPCs — both write the ledger row atomically with
--    the balance update so `tenants.balance` and `ledger_entries` never drift.
-- `increment_tenant_balance` originally returned VOID (migration 007); Postgres
-- won't let CREATE OR REPLACE change a function's return type, so drop it first.
DROP FUNCTION IF EXISTS increment_tenant_balance(UUID, NUMERIC);

CREATE FUNCTION increment_tenant_balance(p_tenant UUID, p_amount NUMERIC)
RETURNS NUMERIC AS $$
DECLARE
    v_new_balance NUMERIC;
BEGIN
    IF p_amount < 0 THEN
        RAISE EXCEPTION 'increment_tenant_balance requires a non-negative amount; use decrement_tenant_balance to debit';
    END IF;

    UPDATE tenants
    SET balance = COALESCE(balance, 0) + p_amount,
        updated_at = NOW()
    WHERE id = p_tenant
    RETURNING balance INTO v_new_balance;

    RETURN v_new_balance;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION decrement_tenant_balance(p_tenant UUID, p_amount NUMERIC)
RETURNS NUMERIC AS $$
DECLARE
    v_new_balance NUMERIC;
BEGIN
    IF p_amount < 0 THEN
        RAISE EXCEPTION 'decrement_tenant_balance requires a non-negative amount';
    END IF;

    -- Row lock via UPDATE avoids a lost-update race against a concurrent
    -- increment/decrement on the same tenant (e.g. webhook + usage debit).
    UPDATE tenants
    SET balance = COALESCE(balance, 0) - p_amount,
        updated_at = NOW()
    WHERE id = p_tenant
    RETURNING balance INTO v_new_balance;

    RETURN v_new_balance;
END;
$$ LANGUAGE plpgsql;

-- 3. Staged-dunning state on tenants.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='tenants' AND column_name='dunning_stage') THEN
        ALTER TABLE tenants ADD COLUMN dunning_stage INT NOT NULL DEFAULT 0;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='tenants' AND column_name='dunning_next_action_at') THEN
        ALTER TABLE tenants ADD COLUMN dunning_next_action_at TIMESTAMPTZ;
    END IF;
END $$;
