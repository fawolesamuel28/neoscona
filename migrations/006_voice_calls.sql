-- Phase 6 — Voice Calls (full per-tenant call log for the Neoscona Voice console).
-- Run once in Supabase SQL Editor, or: scripts/apply_migration_supabase.sh migrations/006_voice_calls.sql
--
-- Provides:
--   • voice_calls — every receptionist call: caller, status, duration, full transcript
--     and summary. Written by the ElevenLabs post-call webhook (service role, resolves
--     the tenant from the channel registry) and read by the console API as the
--     authenticated user. Complements elevenlabs_leads (the qualified-capture subset).
--
-- Depends on 002_phase3_tenant_isolation.sql (public.user_tenant_ids()) and
-- 004_voice_receptionist.sql (public.voice_agents).
--
-- RLS deliberately matches the public.user_tenant_ids() membership model used by
-- voice_agents / elevenlabs_leads. The current_setting('app.current_tenant_id') GUC
-- referenced elsewhere is NOT wired in this app (TenantMiddleware is not mounted), so a
-- GUC-based policy would deny-all; the membership model is the one that actually works.
-- No literal tenant UUID appears here. Idempotent / replay-safe (see 005 convention).

-- ─── 1. voice_calls — one row per inbound receptionist call ────────────────────
CREATE TABLE IF NOT EXISTS public.voice_calls (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id            UUID NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
    -- The receptionist that took the call. elevenlabs_agent_id is the webhook's
    -- attribution key; voice_agent_id is a soft link to the provisioned row.
    elevenlabs_agent_id  TEXT,
    voice_agent_id       UUID REFERENCES public.voice_agents(id) ON DELETE SET NULL,
    conversation_id      TEXT NOT NULL,
    caller_number        TEXT,
    e164                 TEXT,                                   -- receptionist (callee) number
    direction            TEXT NOT NULL DEFAULT 'inbound',
    -- completed | failed | no-data | unknown
    status               TEXT NOT NULL DEFAULT 'completed',
    started_at           TIMESTAMPTZ,
    ended_at             TIMESTAMPTZ,
    duration_secs        INTEGER,
    has_audio            BOOLEAN NOT NULL DEFAULT false,         -- drives the on-demand audio proxy
    recording_url        TEXT,                                   -- usually NULL (post-call webhook ships no URL)
    transcript           JSONB NOT NULL DEFAULT '[]'::jsonb,     -- the turn array, as received
    summary              TEXT,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- A conversation is unique within a tenant — the webhook upsert target (idempotent on retry).
ALTER TABLE public.voice_calls
    DROP CONSTRAINT IF EXISTS voice_calls_tenant_conversation_key;
ALTER TABLE public.voice_calls
    ADD CONSTRAINT voice_calls_tenant_conversation_key UNIQUE (tenant_id, conversation_id);

CREATE INDEX IF NOT EXISTS idx_voice_calls_tenant
    ON public.voice_calls(tenant_id);
-- Every console list is tenant-scoped + newest-first; serve it from the index.
CREATE INDEX IF NOT EXISTS idx_voice_calls_tenant_created
    ON public.voice_calls(tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_voice_calls_conversation
    ON public.voice_calls(conversation_id);

-- ─── 2. RLS — tenant_id ∈ public.user_tenant_ids() (same model as 004) ─────────
-- Authenticated reads isolate per tenant. The service-role client (webhook) has
-- BYPASSRLS and relies on explicit .eq("tenant_id") filters in application code.
ALTER TABLE public.voice_calls ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS voice_calls_tenant ON public.voice_calls;
CREATE POLICY voice_calls_tenant ON public.voice_calls
    FOR ALL TO authenticated
    USING (tenant_id IN (SELECT public.user_tenant_ids()))
    WITH CHECK (tenant_id IN (SELECT public.user_tenant_ids()));
