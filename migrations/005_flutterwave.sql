-- Flutterwave state on tenants
ALTER TABLE tenants
  ADD COLUMN IF NOT EXISTS flw_customer_id    TEXT,
  ADD COLUMN IF NOT EXISTS flw_tx_ref         TEXT,
  ADD COLUMN IF NOT EXISTS flw_card_token     TEXT,
  ADD COLUMN IF NOT EXISTS flw_token_email    TEXT,
  ADD COLUMN IF NOT EXISTS token_expires_at   TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS next_billing_date  TIMESTAMPTZ;

-- Dedup / audit table
CREATE TABLE IF NOT EXISTS flutterwave_events (
  id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  flw_id       TEXT UNIQUE NOT NULL,
  event_type   TEXT NOT NULL,
  payload      JSONB NOT NULL,
  created_at   TIMESTAMPTZ DEFAULT NOW()
);

-- RLS: service role only
ALTER TABLE flutterwave_events ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "service-only" ON flutterwave_events;
CREATE POLICY "service-only" ON flutterwave_events USING (false);

-- Keep paystack_* columns — read-only history, drop in v2
