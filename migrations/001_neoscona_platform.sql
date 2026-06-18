-- Neoscona platform bootstrap for Supabase (oruazksvjbmfkuwriocb)
-- Run once in Supabase SQL Editor, or: python scripts/apply_migration.py
--
-- Provides: blog posts + auth/org tables for self-serve signup (/api/signup).

-- ─── Blog (marketing) ────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.posts (
    id          BIGSERIAL PRIMARY KEY,
    title       VARCHAR(255) NOT NULL,
    category    VARCHAR(100) NOT NULL,
    content     TEXT,
    image_url   VARCHAR(500),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS posts_created_at_idx ON public.posts (created_at DESC);

-- ─── Organizations (tenants) ───────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.tenants (
    id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name                        TEXT NOT NULL,
    company_name                TEXT NOT NULL,
    active                      BOOLEAN NOT NULL DEFAULT TRUE,
    plan                        TEXT NOT NULL DEFAULT 'trial',
    subscription_status         TEXT NOT NULL DEFAULT 'trialing'
        CHECK (subscription_status IN ('trialing', 'active', 'past_due', 'canceled')),
    trial_ends_at               TIMESTAMPTZ,
    paystack_customer_code      TEXT,
    paystack_subscription_code  TEXT,
    billing_email               TEXT,
    onboarding_step             TEXT NOT NULL DEFAULT 'created',
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ─── User ↔ org memberships (Supabase Auth users) ───────────────────────────
CREATE TABLE IF NOT EXISTS public.memberships (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    tenant_id   UUID NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
    role        TEXT NOT NULL DEFAULT 'viewer'
                CHECK (role IN ('owner', 'admin', 'agent', 'viewer')),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, tenant_id)
);
CREATE INDEX IF NOT EXISTS idx_memberships_user ON public.memberships(user_id);
CREATE INDEX IF NOT EXISTS idx_memberships_tenant ON public.memberships(tenant_id);

ALTER TABLE public.memberships ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS membership_self_read ON public.memberships;
CREATE POLICY membership_self_read ON public.memberships
    FOR SELECT USING (user_id = auth.uid());

-- ─── Usage metering (billing soft limits) ────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.usage_events (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id   UUID NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
    event_type  TEXT NOT NULL,
    quantity    NUMERIC NOT NULL DEFAULT 1,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_usage_events_tenant_time ON public.usage_events (tenant_id, created_at);

CREATE TABLE IF NOT EXISTS public.usage_counters (
    tenant_id      UUID NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
    period_start   DATE NOT NULL,
    period_end     DATE NOT NULL,
    messages       INTEGER NOT NULL DEFAULT 0,
    voice_minutes  NUMERIC NOT NULL DEFAULT 0,
    seats          INTEGER NOT NULL DEFAULT 0,
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, period_start)
);
