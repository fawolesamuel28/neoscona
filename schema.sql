--
-- PostgreSQL database dump
--

-- Dumped from database version 17.6
-- Dumped by pg_dump version 17.5

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: public; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA public;


--
-- Name: SCHEMA public; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON SCHEMA public IS 'standard public schema';


--
-- Name: increment_usage_counter(uuid, date, date, integer, numeric, integer); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.increment_usage_counter(p_tenant uuid, p_period_start date, p_period_end date, p_messages integer DEFAULT 0, p_voice numeric DEFAULT 0, p_seats integer DEFAULT 0) RETURNS void
    LANGUAGE sql
    AS $$
    INSERT INTO usage_counters (tenant_id, period_start, period_end, messages, voice_minutes, seats, updated_at)
    VALUES (p_tenant, p_period_start, p_period_end, p_messages, p_voice, p_seats, now())
    ON CONFLICT (tenant_id, period_start) DO UPDATE SET
        messages      = usage_counters.messages + EXCLUDED.messages,
        voice_minutes = usage_counters.voice_minutes + EXCLUDED.voice_minutes,
        seats         = GREATEST(usage_counters.seats, EXCLUDED.seats),
        updated_at    = now();
$$;


--
-- Name: rollup_usage_counters(date, date); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.rollup_usage_counters(p_start date, p_end date) RETURNS void
    LANGUAGE sql
    AS $$
    INSERT INTO usage_counters (tenant_id, period_start, period_end, messages, voice_minutes, seats, updated_at)
    SELECT e.tenant_id, p_start, p_end,
           COALESCE(SUM(e.quantity) FILTER (WHERE e.event_type = 'message'), 0)::int,
           COALESCE(SUM(e.quantity) FILTER (WHERE e.event_type = 'voice_minute'), 0),
           COALESCE(MAX(e.quantity) FILTER (WHERE e.event_type = 'seat'), 0)::int,
           now()
    FROM usage_events e
    WHERE e.created_at::date BETWEEN p_start AND p_end
    GROUP BY e.tenant_id
    ON CONFLICT (tenant_id, period_start) DO UPDATE SET
        messages      = EXCLUDED.messages,
        voice_minutes = EXCLUDED.voice_minutes,
        seats         = GREATEST(usage_counters.seats, EXCLUDED.seats),
        updated_at    = now();
$$;


--
-- Name: search_inventory_fts(text, numeric, integer); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.search_inventory_fts(search_query text, max_budget numeric DEFAULT NULL::numeric, target_bedrooms integer DEFAULT NULL::integer) RETURNS TABLE(id uuid, name text, location text, property_type text, bedrooms integer, price numeric, highlights text, source text, rank real)
    LANGUAGE plpgsql
    AS $$
DECLARE
    formatted_query tsquery;
    budget_stretch NUMERIC;
BEGIN
    -- Format query for FTS (e.g., "lekki apartment" -> "lekki" & "apartment")
    -- We use plainto_tsquery to safely handle user input
    formatted_query := plainto_tsquery('english', COALESCE(search_query, ''));
    
    -- Allow a 20% stretch on the budget (industry standard fuzziness)
    IF max_budget IS NOT NULL THEN
        budget_stretch := max_budget * 1.2;
    END IF;

    RETURN QUERY
    SELECT 
        v.id,
        v.title as name,
        v.location,
        v.property_type,
        v.bedrooms,
        v.price_naira as price,
        v.highlights,
        v.source_table as source,
        CASE 
            WHEN formatted_query::text = '' THEN 1.0::REAL
            ELSE ts_rank(
                setweight(to_tsvector('english', COALESCE(v.location, '')), 'A') ||
                setweight(to_tsvector('english', COALESCE(v.property_type, '')), 'A') ||
                setweight(to_tsvector('english', COALESCE(v.title, '')), 'B') ||
                setweight(to_tsvector('english', COALESCE(v.highlights, '')), 'C'),
                formatted_query
            )::REAL 
        END as rank
    FROM v_available_inventory v
    WHERE 
        (max_budget IS NULL OR v.price_naira <= budget_stretch) AND
        (target_bedrooms IS NULL OR v.bedrooms = target_bedrooms OR v.bedrooms IS NULL) AND
        (formatted_query::text = '' OR (
            setweight(to_tsvector('english', COALESCE(v.location, '')), 'A') ||
            setweight(to_tsvector('english', COALESCE(v.property_type, '')), 'A') ||
            setweight(to_tsvector('english', COALESCE(v.title, '')), 'B') ||
            setweight(to_tsvector('english', COALESCE(v.highlights, '')), 'C')
        ) @@ formatted_query)
    ORDER BY 
        rank DESC, 
        (CASE WHEN max_budget IS NOT NULL THEN abs(v.price_naira - max_budget) ELSE v.price_naira END) ASC
    LIMIT 3;
END;
$$;


--
-- Name: update_updated_at(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.update_updated_at() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: agent_configs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.agent_configs (
    tenant_id uuid NOT NULL,
    agent_name text,
    company_name text,
    tone text,
    languages text[],
    qualifying_fields text[],
    guardrails text,
    custom_instructions text,
    greeting text,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: agent_developments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.agent_developments (
    agent_id uuid NOT NULL,
    development_id uuid NOT NULL
);


--
-- Name: agents; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.agents (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name text NOT NULL,
    whatsapp text NOT NULL,
    email text,
    active boolean DEFAULT true,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: audit_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.audit_log (
    id bigint NOT NULL,
    tenant_id uuid,
    actor_id uuid,
    actor_email text,
    action text NOT NULL,
    target text,
    ip text,
    metadata jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: audit_log_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.audit_log ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.audit_log_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: conversation_logs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.conversation_logs (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    phone_number text NOT NULL,
    role text NOT NULL,
    message text NOT NULL,
    created_at timestamp with time zone DEFAULT now(),
    tenant_id uuid,
    author_user_id uuid
);


--
-- Name: developers; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.developers (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name text NOT NULL,
    slug text NOT NULL,
    tagline text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: developments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.developments (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    developer_id uuid NOT NULL,
    name text NOT NULL,
    phase text,
    location text NOT NULL,
    area_tags text[] DEFAULT '{}'::text[] NOT NULL,
    description text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    price_min numeric,
    price_max numeric,
    available_types text[],
    tenant_id uuid DEFAULT 'a0000000-0000-4000-8000-000000000001'::uuid
);


--
-- Name: elevenlabs_leads; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.elevenlabs_leads (
    id bigint NOT NULL,
    call_id text NOT NULL,
    phone_number text,
    budget text,
    location text,
    property_type text,
    timeline text,
    whatsapp_number text,
    ai_summary text,
    created_at timestamp with time zone DEFAULT now(),
    name text,
    viewed_at timestamp with time zone,
    tenant_id uuid DEFAULT 'a0000000-0000-4000-8000-000000000001'::uuid
);


--
-- Name: elevenlabs_leads_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.elevenlabs_leads ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.elevenlabs_leads_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: lead_notes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.lead_notes (
    id bigint NOT NULL,
    tenant_id uuid NOT NULL,
    lead_phone text NOT NULL,
    author_user_id uuid,
    author_email text,
    body text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: lead_notes_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.lead_notes ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.lead_notes_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: lead_unit_matches; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.lead_unit_matches (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    phone_number text NOT NULL,
    unit_id uuid NOT NULL,
    match_score real DEFAULT 0 NOT NULL,
    rank integer DEFAULT 1 NOT NULL,
    offered_at timestamp with time zone DEFAULT now() NOT NULL,
    tenant_id uuid DEFAULT 'a0000000-0000-4000-8000-000000000001'::uuid
);


--
-- Name: leads; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.leads (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    phone_number text NOT NULL,
    name text,
    budget text,
    location text,
    property_type text,
    timeline text,
    language text DEFAULT 'english'::text,
    seriousness_score integer DEFAULT 0,
    stage text DEFAULT 'new'::text,
    meeting_booked boolean DEFAULT false,
    meeting_url text,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    assigned_agent_id uuid,
    source text DEFAULT 'whatsapp_organic'::text,
    first_response_at timestamp with time zone,
    qualified_at timestamp with time zone,
    utm_campaign text,
    tenant_id uuid,
    development_id uuid,
    attribution text DEFAULT 'reva'::text,
    closing_revenue numeric,
    is_paused boolean DEFAULT false,
    taken_over_by uuid,
    taken_over_at timestamp with time zone,
    assigned_user_id uuid,
    tags text[],
    sla_notified_at timestamp with time zone
);


--
-- Name: memberships; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.memberships (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    role text DEFAULT 'viewer'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT memberships_role_check CHECK ((role = ANY (ARRAY['owner'::text, 'admin'::text, 'agent'::text, 'viewer'::text])))
);


--
-- Name: paystack_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.paystack_events (
    id bigint NOT NULL,
    paystack_id text,
    event_type text NOT NULL,
    payload jsonb,
    processed_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: paystack_events_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.paystack_events ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.paystack_events_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: properties; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.properties (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name text NOT NULL,
    type text NOT NULL,
    location text NOT NULL,
    bedrooms integer,
    bathrooms integer,
    price numeric NOT NULL,
    highlights text,
    images text[],
    available boolean DEFAULT true,
    payment_plan boolean DEFAULT false,
    payment_plan_details text,
    created_at timestamp with time zone DEFAULT now(),
    tenant_id uuid,
    development_id uuid
);


--
-- Name: revenue_intelligence; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.revenue_intelligence AS
 SELECT tenant_id,
    count(*) FILTER (WHERE (stage = 'qualified'::text)) AS hot_pipeline,
    count(*) FILTER (WHERE (stage = 'confirmed'::text)) AS meetings_booked,
    count(*) FILTER (WHERE (stage = 'closed'::text)) AS conversions,
    avg(seriousness_score) AS avg_lead_quality,
    sum(closing_revenue) AS actual_revenue,
    (COALESCE(sum(closing_revenue), (0)::numeric) + (((count(*) FILTER (WHERE ((stage = 'qualified'::text) AND (closing_revenue IS NULL))))::numeric * 0.20) * (15000000)::numeric)) AS projected_revenue_naira
   FROM public.leads
  WHERE (created_at > (now() - '30 days'::interval))
  GROUP BY tenant_id;


--
-- Name: revenue_projection; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.revenue_projection AS
 SELECT count(*) FILTER (WHERE (stage = 'qualified'::text)) AS hot_pipeline,
    count(*) FILTER (WHERE (stage = 'done'::text)) AS meetings_booked,
    count(*) FILTER (WHERE (meeting_booked = true)) AS conversions,
    avg(seriousness_score) AS avg_lead_quality,
    (((count(*) FILTER (WHERE (meeting_booked = true)))::numeric * 0.20) * (15000000)::numeric) AS projected_revenue_naira
   FROM public.leads
  WHERE (created_at > (now() - '30 days'::interval));


--
-- Name: tenants; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tenants (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name text NOT NULL,
    company_name text NOT NULL,
    active boolean DEFAULT true,
    created_at timestamp with time zone DEFAULT now(),
    plan text DEFAULT 'trial'::text NOT NULL,
    subscription_status text DEFAULT 'trialing'::text NOT NULL,
    trial_ends_at timestamp with time zone,
    paystack_customer_code text,
    paystack_subscription_code text,
    billing_email text,
    onboarding_step text DEFAULT 'created'::text NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT tenants_subscription_status_check CHECK ((subscription_status = ANY (ARRAY['trialing'::text, 'active'::text, 'past_due'::text, 'canceled'::text])))
);


--
-- Name: units; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.units (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    development_id uuid NOT NULL,
    unit_code text NOT NULL,
    title text NOT NULL,
    property_type text NOT NULL,
    bedrooms integer,
    price_naira bigint NOT NULL,
    status text DEFAULT 'available'::text NOT NULL,
    size_sqm integer,
    highlights text,
    payment_plan_notes text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    tenant_id uuid DEFAULT 'a0000000-0000-4000-8000-000000000001'::uuid,
    CONSTRAINT units_status_check CHECK ((status = ANY (ARRAY['available'::text, 'reserved'::text, 'sold'::text])))
);


--
-- Name: usage_counters; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.usage_counters (
    tenant_id uuid NOT NULL,
    period_start date NOT NULL,
    period_end date NOT NULL,
    messages integer DEFAULT 0 NOT NULL,
    voice_minutes numeric DEFAULT 0 NOT NULL,
    seats integer DEFAULT 0 NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: usage_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.usage_events (
    id bigint NOT NULL,
    tenant_id uuid NOT NULL,
    event_type text NOT NULL,
    quantity numeric DEFAULT 1 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: usage_events_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.usage_events ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.usage_events_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: usage_intelligence; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.usage_intelligence AS
 SELECT t.id AS tenant_id,
    t.plan,
    t.subscription_status,
    t.trial_ends_at,
    COALESCE(c.messages, 0) AS messages_used,
    COALESCE(c.voice_minutes, (0)::numeric) AS voice_minutes_used,
    COALESCE(c.seats, 0) AS seats_used,
    c.period_start,
    c.period_end
   FROM (public.tenants t
     LEFT JOIN public.usage_counters c ON (((c.tenant_id = t.id) AND ((CURRENT_DATE >= c.period_start) AND (CURRENT_DATE <= c.period_end)))));


--
-- Name: v_available_inventory; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_available_inventory AS
 SELECT properties.id,
    properties.name AS title,
    properties.location,
    properties.type AS property_type,
    properties.bedrooms,
    properties.price AS price_naira,
    properties.highlights,
    'properties'::text AS source_table
   FROM public.properties
  WHERE (properties.available = true)
UNION ALL
 SELECT u.id,
    ((dev.name || ' - '::text) || u.title) AS title,
    dev.location,
    u.property_type,
    u.bedrooms,
    u.price_naira,
    u.highlights,
    'units'::text AS source_table
   FROM (public.units u
     JOIN public.developments dev ON ((u.development_id = dev.id)))
  WHERE (u.status = 'available'::text)
UNION ALL
 SELECT developments.id,
    developments.name AS title,
    developments.location,
    array_to_string(developments.available_types, ', '::text) AS property_type,
    NULL::integer AS bedrooms,
    developments.price_min AS price_naira,
    developments.description AS highlights,
    'developments'::text AS source_table
   FROM public.developments
  WHERE (developments.price_min IS NOT NULL);


--
-- Name: agent_configs agent_configs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_configs
    ADD CONSTRAINT agent_configs_pkey PRIMARY KEY (tenant_id);


--
-- Name: agent_developments agent_developments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_developments
    ADD CONSTRAINT agent_developments_pkey PRIMARY KEY (agent_id, development_id);


--
-- Name: agents agents_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agents
    ADD CONSTRAINT agents_pkey PRIMARY KEY (id);


--
-- Name: audit_log audit_log_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_log
    ADD CONSTRAINT audit_log_pkey PRIMARY KEY (id);


--
-- Name: conversation_logs conversation_logs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.conversation_logs
    ADD CONSTRAINT conversation_logs_pkey PRIMARY KEY (id);


--
-- Name: developers developers_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.developers
    ADD CONSTRAINT developers_pkey PRIMARY KEY (id);


--
-- Name: developers developers_slug_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.developers
    ADD CONSTRAINT developers_slug_key UNIQUE (slug);


--
-- Name: developments developments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.developments
    ADD CONSTRAINT developments_pkey PRIMARY KEY (id);


--
-- Name: elevenlabs_leads elevenlabs_leads_call_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.elevenlabs_leads
    ADD CONSTRAINT elevenlabs_leads_call_id_key UNIQUE (call_id);


--
-- Name: elevenlabs_leads elevenlabs_leads_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.elevenlabs_leads
    ADD CONSTRAINT elevenlabs_leads_pkey PRIMARY KEY (id);


--
-- Name: lead_notes lead_notes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.lead_notes
    ADD CONSTRAINT lead_notes_pkey PRIMARY KEY (id);


--
-- Name: lead_unit_matches lead_unit_matches_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.lead_unit_matches
    ADD CONSTRAINT lead_unit_matches_pkey PRIMARY KEY (id);


--
-- Name: leads leads_phone_number_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.leads
    ADD CONSTRAINT leads_phone_number_key UNIQUE (phone_number);


--
-- Name: leads leads_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.leads
    ADD CONSTRAINT leads_pkey PRIMARY KEY (id);


--
-- Name: memberships memberships_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.memberships
    ADD CONSTRAINT memberships_pkey PRIMARY KEY (id);


--
-- Name: memberships memberships_user_id_tenant_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.memberships
    ADD CONSTRAINT memberships_user_id_tenant_id_key UNIQUE (user_id, tenant_id);


--
-- Name: paystack_events paystack_events_paystack_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.paystack_events
    ADD CONSTRAINT paystack_events_paystack_id_key UNIQUE (paystack_id);


--
-- Name: paystack_events paystack_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.paystack_events
    ADD CONSTRAINT paystack_events_pkey PRIMARY KEY (id);


--
-- Name: properties properties_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.properties
    ADD CONSTRAINT properties_pkey PRIMARY KEY (id);


--
-- Name: tenants tenants_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenants
    ADD CONSTRAINT tenants_pkey PRIMARY KEY (id);


--
-- Name: units units_development_id_unit_code_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.units
    ADD CONSTRAINT units_development_id_unit_code_key UNIQUE (development_id, unit_code);


--
-- Name: units units_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.units
    ADD CONSTRAINT units_pkey PRIMARY KEY (id);


--
-- Name: usage_counters usage_counters_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.usage_counters
    ADD CONSTRAINT usage_counters_pkey PRIMARY KEY (tenant_id, period_start);


--
-- Name: usage_events usage_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.usage_events
    ADD CONSTRAINT usage_events_pkey PRIMARY KEY (id);


--
-- Name: idx_audit_tenant_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_audit_tenant_time ON public.audit_log USING btree (tenant_id, created_at DESC);


--
-- Name: idx_developments_area_tags; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_developments_area_tags ON public.developments USING gin (area_tags);


--
-- Name: idx_developments_tenant; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_developments_tenant ON public.developments USING btree (tenant_id);


--
-- Name: idx_elevenlabs_leads_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_elevenlabs_leads_created_at ON public.elevenlabs_leads USING btree (created_at DESC);


--
-- Name: idx_elevenlabs_leads_tenant; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_elevenlabs_leads_tenant ON public.elevenlabs_leads USING btree (tenant_id);


--
-- Name: idx_elevenlabs_leads_unviewed; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_elevenlabs_leads_unviewed ON public.elevenlabs_leads USING btree (viewed_at) WHERE (viewed_at IS NULL);


--
-- Name: idx_lead_notes_tenant_phone; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_lead_notes_tenant_phone ON public.lead_notes USING btree (tenant_id, lead_phone, created_at);


--
-- Name: idx_lead_unit_matches_phone; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_lead_unit_matches_phone ON public.lead_unit_matches USING btree (phone_number);


--
-- Name: idx_lead_unit_matches_tenant; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_lead_unit_matches_tenant ON public.lead_unit_matches USING btree (tenant_id);


--
-- Name: idx_leads_phone; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_leads_phone ON public.leads USING btree (phone_number);


--
-- Name: idx_leads_stage; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_leads_stage ON public.leads USING btree (stage);


--
-- Name: idx_logs_phone; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_logs_phone ON public.conversation_logs USING btree (phone_number);


--
-- Name: idx_memberships_tenant; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_memberships_tenant ON public.memberships USING btree (tenant_id);


--
-- Name: idx_memberships_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_memberships_user ON public.memberships USING btree (user_id);


--
-- Name: idx_units_price; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_units_price ON public.units USING btree (price_naira);


--
-- Name: idx_units_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_units_status ON public.units USING btree (status);


--
-- Name: idx_units_tenant; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_units_tenant ON public.units USING btree (tenant_id);


--
-- Name: idx_usage_events_tenant_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_usage_events_tenant_time ON public.usage_events USING btree (tenant_id, created_at);


--
-- Name: leads leads_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER leads_updated_at BEFORE UPDATE ON public.leads FOR EACH ROW EXECUTE FUNCTION public.update_updated_at();


--
-- Name: agent_configs agent_configs_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_configs
    ADD CONSTRAINT agent_configs_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--
-- Name: agent_developments agent_developments_agent_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_developments
    ADD CONSTRAINT agent_developments_agent_id_fkey FOREIGN KEY (agent_id) REFERENCES public.agents(id);


--
-- Name: agent_developments agent_developments_development_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_developments
    ADD CONSTRAINT agent_developments_development_id_fkey FOREIGN KEY (development_id) REFERENCES public.developments(id);


--
-- Name: audit_log audit_log_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_log
    ADD CONSTRAINT audit_log_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE SET NULL;


--
-- Name: conversation_logs conversation_logs_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.conversation_logs
    ADD CONSTRAINT conversation_logs_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id);


--
-- Name: developments developments_developer_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.developments
    ADD CONSTRAINT developments_developer_id_fkey FOREIGN KEY (developer_id) REFERENCES public.developers(id) ON DELETE CASCADE;


--
-- Name: developments developments_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.developments
    ADD CONSTRAINT developments_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id);


--
-- Name: elevenlabs_leads elevenlabs_leads_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.elevenlabs_leads
    ADD CONSTRAINT elevenlabs_leads_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id);


--
-- Name: lead_notes lead_notes_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.lead_notes
    ADD CONSTRAINT lead_notes_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--
-- Name: lead_unit_matches lead_unit_matches_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.lead_unit_matches
    ADD CONSTRAINT lead_unit_matches_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id);


--
-- Name: lead_unit_matches lead_unit_matches_unit_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.lead_unit_matches
    ADD CONSTRAINT lead_unit_matches_unit_id_fkey FOREIGN KEY (unit_id) REFERENCES public.units(id) ON DELETE CASCADE;


--
-- Name: leads leads_assigned_agent_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.leads
    ADD CONSTRAINT leads_assigned_agent_id_fkey FOREIGN KEY (assigned_agent_id) REFERENCES public.agents(id);


--
-- Name: leads leads_development_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.leads
    ADD CONSTRAINT leads_development_id_fkey FOREIGN KEY (development_id) REFERENCES public.developments(id);


--
-- Name: leads leads_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.leads
    ADD CONSTRAINT leads_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id);


--
-- Name: memberships memberships_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.memberships
    ADD CONSTRAINT memberships_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--
-- Name: memberships memberships_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.memberships
    ADD CONSTRAINT memberships_user_id_fkey FOREIGN KEY (user_id) REFERENCES auth.users(id) ON DELETE CASCADE;


--
-- Name: properties properties_development_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.properties
    ADD CONSTRAINT properties_development_id_fkey FOREIGN KEY (development_id) REFERENCES public.developments(id);


--
-- Name: properties properties_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.properties
    ADD CONSTRAINT properties_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id);


--
-- Name: units units_development_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.units
    ADD CONSTRAINT units_development_id_fkey FOREIGN KEY (development_id) REFERENCES public.developments(id) ON DELETE CASCADE;


--
-- Name: units units_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.units
    ADD CONSTRAINT units_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id);


--
-- Name: usage_counters usage_counters_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.usage_counters
    ADD CONSTRAINT usage_counters_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--
-- Name: usage_events usage_events_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.usage_events
    ADD CONSTRAINT usage_events_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--
-- Name: agent_configs; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.agent_configs ENABLE ROW LEVEL SECURITY;

--
-- Name: agent_configs agent_configs_tenant; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY agent_configs_tenant ON public.agent_configs USING ((tenant_id = (current_setting('app.current_tenant_id'::text, true))::uuid));


--
-- Name: agents; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.agents ENABLE ROW LEVEL SECURITY;

--
-- Name: conversation_logs; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.conversation_logs ENABLE ROW LEVEL SECURITY;

--
-- Name: developments; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.developments ENABLE ROW LEVEL SECURITY;

--
-- Name: lead_notes; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.lead_notes ENABLE ROW LEVEL SECURITY;

--
-- Name: lead_notes lead_notes_tenant; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY lead_notes_tenant ON public.lead_notes USING ((tenant_id = (current_setting('app.current_tenant_id'::text, true))::uuid));


--
-- Name: leads lead_tenant_access; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY lead_tenant_access ON public.leads USING ((tenant_id = 'a0000000-0000-4000-8000-000000000001'::uuid)) WITH CHECK ((tenant_id = 'a0000000-0000-4000-8000-000000000001'::uuid));


--
-- Name: leads; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.leads ENABLE ROW LEVEL SECURITY;

--
-- Name: conversation_logs log_tenant_access; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY log_tenant_access ON public.conversation_logs USING ((tenant_id = 'a0000000-0000-4000-8000-000000000001'::uuid)) WITH CHECK ((tenant_id = 'a0000000-0000-4000-8000-000000000001'::uuid));


--
-- Name: memberships membership_self_read; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY membership_self_read ON public.memberships FOR SELECT USING ((user_id = auth.uid()));


--
-- Name: memberships; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.memberships ENABLE ROW LEVEL SECURITY;

--
-- Name: properties; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.properties ENABLE ROW LEVEL SECURITY;

--
-- Name: usage_counters; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.usage_counters ENABLE ROW LEVEL SECURITY;

--
-- Name: usage_counters usage_counters_tenant; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY usage_counters_tenant ON public.usage_counters USING ((tenant_id = (current_setting('app.current_tenant_id'::text, true))::uuid));


--
-- Name: usage_events; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.usage_events ENABLE ROW LEVEL SECURITY;

--
-- Name: usage_events usage_events_tenant; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY usage_events_tenant ON public.usage_events USING ((tenant_id = (current_setting('app.current_tenant_id'::text, true))::uuid));


--
-- PostgreSQL database dump complete
--

