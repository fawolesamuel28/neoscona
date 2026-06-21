-- Phase 4 — Voice Receptionist (KrosAI numbers + ElevenLabs ConvAI).
-- Run once in Supabase SQL Editor, or: python scripts/apply_migration.py
--
-- Provides:
--   • voice_agents            — per-tenant receptionist (ElevenLabs agent + KrosAI number)
--   • elevenlabs_leads        — hardened to real per-tenant isolation (was default-stamped)
--   • RLS on both, keyed on public.user_tenant_ids() (see 002_phase3_tenant_isolation.sql)
--
-- Depends on 002_phase3_tenant_isolation.sql (public.user_tenant_ids(), channels table).
-- Order matters: backfill before NOT NULL.

-- ─── 1. voice_agents — one provisioned receptionist per tenant ─────────────────
-- A tenant's ElevenLabs ConvAI agent is the conversational brain; the KrosAI number
-- routes inbound SIP to it. external attribution of post-call webhooks is by
-- elevenlabs_agent_id, registered in `channels` (provider='elevenlabs').
CREATE TABLE IF NOT EXISTS public.voice_agents (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id            UUID NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
    provider             TEXT NOT NULL DEFAULT 'elevenlabs',
    elevenlabs_agent_id  TEXT,
    krosai_phone_id      TEXT,
    e164                 TEXT,
    label                TEXT,
    -- provisioning | active | disabled | failed
    status               TEXT NOT NULL DEFAULT 'provisioning',
    -- reva | dedicated  (where the persona/prompt came from)
    persona_source       TEXT NOT NULL DEFAULT 'dedicated',
    config               JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_voice_agents_tenant ON public.voice_agents(tenant_id);
-- One agent id is globally unique (it identifies exactly one tenant on inbound).
CREATE UNIQUE INDEX IF NOT EXISTS uq_voice_agents_el_agent
    ON public.voice_agents(elevenlabs_agent_id)
    WHERE elevenlabs_agent_id IS NOT NULL;

-- ─── 2. elevenlabs_leads — swap default-tenant stamping for real isolation ─────
-- Backfill any pre-existing NULLs to the seeded default tenant, then forbid the
-- silent default going forward (writes must carry the resolved tenant_id).
UPDATE public.elevenlabs_leads
   SET tenant_id = 'a0000000-0000-4000-8000-000000000001'
 WHERE tenant_id IS NULL;
ALTER TABLE public.elevenlabs_leads ALTER COLUMN tenant_id DROP DEFAULT;
ALTER TABLE public.elevenlabs_leads ALTER COLUMN tenant_id SET NOT NULL;
CREATE INDEX IF NOT EXISTS idx_elevenlabs_leads_tenant ON public.elevenlabs_leads(tenant_id);
-- Idempotent post-call webhook upsert target: a call is unique within a tenant.
ALTER TABLE public.elevenlabs_leads
    DROP CONSTRAINT IF EXISTS elevenlabs_leads_tenant_call_key;
ALTER TABLE public.elevenlabs_leads
    ADD CONSTRAINT elevenlabs_leads_tenant_call_key UNIQUE (tenant_id, call_id);

-- ─── 3. RLS — tenant_id ∈ public.user_tenant_ids() (same model as Phase 3) ───────
-- User-facing reads run as the `authenticated` role (get_request_client), so these
-- isolate per tenant. The service-role client (webhook/provisioning) has BYPASSRLS
-- and relies on explicit .eq("tenant_id") filters in application code.
ALTER TABLE public.voice_agents     ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.elevenlabs_leads ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS voice_agents_tenant     ON public.voice_agents;
DROP POLICY IF EXISTS elevenlabs_leads_tenant ON public.elevenlabs_leads;

CREATE POLICY voice_agents_tenant ON public.voice_agents
    FOR ALL TO authenticated
    USING (tenant_id IN (SELECT public.user_tenant_ids()))
    WITH CHECK (tenant_id IN (SELECT public.user_tenant_ids()));

CREATE POLICY elevenlabs_leads_tenant ON public.elevenlabs_leads
    FOR ALL TO authenticated
    USING (tenant_id IN (SELECT public.user_tenant_ids()))
    WITH CHECK (tenant_id IN (SELECT public.user_tenant_ids()));
