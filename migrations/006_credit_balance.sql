-- Atomic credit to tenant balance (handles NULL safely)
CREATE OR REPLACE FUNCTION credit_balance(p_tenant UUID, p_amount NUMERIC)
RETURNS VOID AS $$
BEGIN
  UPDATE tenants
  SET 
    balance = COALESCE(balance, 0) + p_amount,
    updated_at = NOW()
  WHERE id = p_tenant;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Add tracking for dunning retries
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS charge_attempts INT DEFAULT 0;
