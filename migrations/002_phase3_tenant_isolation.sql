-- Phase 3 — Activate multi-tenant isolation at the database.
-- Run once in Supabase SQL Editor, or: python scripts/apply_migration.py
--
-- Provides:
--   • public.user_tenant_ids()  — the RLS primitive (tenants the current user belongs to)
--   • channels                — channel→tenant registry consumed by app/services/channels.py
--   • leads uniqueness        — per-tenant (tenant_id, phone_number) instead of global phone
--   • agents.tenant_id        — so hot-lead routing can scope to one workspace
--   • consistent RLS          — every tenant-scoped table keyed on public.user_tenant_ids()
--
-- This supersedes the intent of the (never-shipped) 008_default_tenant_rls.sql referenced
-- in CLAUDE.md. Order matters: backfill before NOT NULL / unique.

-- The seeded default/system tenant ("Atlantic Horizons"); see app/core/tenant.py.
-- Used only to backfill pre-existing rows that predate per-row tenancy.

-- ─── 1. Backfill NULL tenant_ids so the NOT NULL / unique steps below succeed ──
UPDATE public.leads             SET tenant_id = 'a0000000-0000-4000-8000-000000000001' WHERE tenant_id IS NULL;
UPDATE public.conversation_logs SET tenant_id = 'a0000000-0000-4000-8000-000000000001' WHERE tenant_id IS NULL;

-- ─── 2. public.user_tenant_ids() — the policy primitive ─────────────────────────
-- SECURITY DEFINER so it can read memberships regardless of that table's own RLS.
CREATE OR REPLACE FUNCTION public.user_tenant_ids()
    RETURNS SETOF UUID
    LANGUAGE sql
    STABLE
    SECURITY DEFINER
    SET search_path = public
AS $$
    SELECT tenant_id FROM public.memberships WHERE user_id = auth.uid()
$$;

-- ─── 3. channels — the channel→tenant registry ────────────────────────────────
-- external_id is the provider's business-account identifier (WhatsApp Cloud
-- phone_number_id, Evolution instance, IG account id, Vapi phoneNumberId, …).
CREATE TABLE IF NOT EXISTS public.channels (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    UUID NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
    provider     TEXT NOT NULL,
    external_id  TEXT NOT NULL,
    active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (provider, external_id)
);
CREATE INDEX IF NOT EXISTS idx_channels_tenant ON public.channels(tenant_id);

-- ─── 4. leads — swap global phone uniqueness for per-tenant uniqueness ─────────
-- A phone number is unique WITHIN a tenant, not globally; two businesses may both
-- talk to the same lead. This also gives the Phase 2 upsert its required
-- on_conflict="tenant_id,phone_number" index.
ALTER TABLE public.leads ALTER COLUMN tenant_id SET NOT NULL;
ALTER TABLE public.leads DROP CONSTRAINT IF EXISTS leads_phone_number_key;
ALTER TABLE public.leads ADD CONSTRAINT leads_tenant_id_phone_number_key UNIQUE (tenant_id, phone_number);

-- ─── 5. agents — add tenancy so routing can scope to a workspace ──────────────
ALTER TABLE public.agents ADD COLUMN IF NOT EXISTS tenant_id UUID REFERENCES public.tenants(id);
UPDATE public.agents SET tenant_id = 'a0000000-0000-4000-8000-000000000001' WHERE tenant_id IS NULL;
ALTER TABLE public.agents ALTER COLUMN tenant_id SET NOT NULL;
CREATE INDEX IF NOT EXISTS idx_agents_tenant ON public.agents(tenant_id);

-- ─── 6. One consistent RLS model: tenant_id ∈ public.user_tenant_ids() ──────────
-- The user-facing path (app/db/supabase.py get_request_client) runs as the
-- `authenticated` role, so these policies isolate per tenant. The service-role
-- client (webhooks/jobs/provisioning) has BYPASSRLS and relies on explicit
-- .eq(tenant_id) filters in application code.

-- Drop the old, broken policies (hardcoded default UUID + the GUC the app never sets).
DROP POLICY IF EXISTS lead_tenant_access     ON public.leads;
DROP POLICY IF EXISTS log_tenant_access      ON public.conversation_logs;
DROP POLICY IF EXISTS agent_configs_tenant   ON public.agent_configs;
DROP POLICY IF EXISTS lead_notes_tenant      ON public.lead_notes;
DROP POLICY IF EXISTS usage_counters_tenant  ON public.usage_counters;
DROP POLICY IF EXISTS usage_events_tenant    ON public.usage_events;

ALTER TABLE public.leads             ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.conversation_logs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.agent_configs     ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.lead_notes        ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.usage_counters    ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.usage_events      ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.agents            ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.channels          ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS leads_tenant             ON public.leads;
DROP POLICY IF EXISTS conversation_logs_tenant ON public.conversation_logs;
DROP POLICY IF EXISTS agent_configs_tenant     ON public.agent_configs;
DROP POLICY IF EXISTS lead_notes_tenant        ON public.lead_notes;
DROP POLICY IF EXISTS usage_counters_tenant    ON public.usage_counters;
DROP POLICY IF EXISTS usage_events_tenant      ON public.usage_events;
DROP POLICY IF EXISTS agents_tenant            ON public.agents;
DROP POLICY IF EXISTS channels_tenant          ON public.channels;

CREATE POLICY leads_tenant ON public.leads
    FOR ALL TO authenticated
    USING (tenant_id IN (SELECT public.user_tenant_ids()))
    WITH CHECK (tenant_id IN (SELECT public.user_tenant_ids()));

CREATE POLICY conversation_logs_tenant ON public.conversation_logs
    FOR ALL TO authenticated
    USING (tenant_id IN (SELECT public.user_tenant_ids()))
    WITH CHECK (tenant_id IN (SELECT public.user_tenant_ids()));

CREATE POLICY agent_configs_tenant ON public.agent_configs
    FOR ALL TO authenticated
    USING (tenant_id IN (SELECT public.user_tenant_ids()))
    WITH CHECK (tenant_id IN (SELECT public.user_tenant_ids()));

CREATE POLICY lead_notes_tenant ON public.lead_notes
    FOR ALL TO authenticated
    USING (tenant_id IN (SELECT public.user_tenant_ids()))
    WITH CHECK (tenant_id IN (SELECT public.user_tenant_ids()));

CREATE POLICY usage_counters_tenant ON public.usage_counters
    FOR ALL TO authenticated
    USING (tenant_id IN (SELECT public.user_tenant_ids()))
    WITH CHECK (tenant_id IN (SELECT public.user_tenant_ids()));

CREATE POLICY usage_events_tenant ON public.usage_events
    FOR ALL TO authenticated
    USING (tenant_id IN (SELECT public.user_tenant_ids()))
    WITH CHECK (tenant_id IN (SELECT public.user_tenant_ids()));

CREATE POLICY agents_tenant ON public.agents
    FOR ALL TO authenticated
    USING (tenant_id IN (SELECT public.user_tenant_ids()))
    WITH CHECK (tenant_id IN (SELECT public.user_tenant_ids()));

CREATE POLICY channels_tenant ON public.channels
    FOR ALL TO authenticated
    USING (tenant_id IN (SELECT public.user_tenant_ids()))
    WITH CHECK (tenant_id IN (SELECT public.user_tenant_ids()));

-- ─── 7. Seed the LIVE tenant's channels ───────────────────────────────────────
-- REQUIRED for inbound to flow after this migration: the gateway strictly rejects
-- any message whose channel isn't registered. Fill in the production identifiers
-- for the default/live tenant, then run. (Resolution is by (provider, external_id).)
--
--   • Cloud API : external_id = the WhatsApp Business phone_number_id (Meta dashboard)
--   • Evolution : external_id = the Evolution instance name (WHATSAPP_EVOLUTION_INSTANCE)
--   • 360dialog : external_id = the channel's phone_number_id (if used)
--
-- INSERT INTO public.channels (tenant_id, provider, external_id) VALUES
--   ('a0000000-0000-4000-8000-000000000001', 'cloud',     '<META_PHONE_NUMBER_ID>'),
--   ('a0000000-0000-4000-8000-000000000001', 'evolution', '<EVOLUTION_INSTANCE_NAME>')
-- ON CONFLICT (provider, external_id) DO NOTHING;
