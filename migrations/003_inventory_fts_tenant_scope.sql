-- Phase 3 (cont.) — Tenant-scope the LLM inventory catalog path.
-- Run once in Supabase SQL Editor, or: python scripts/apply_migration.py 003_inventory_fts_tenant_scope.sql
-- Apply alongside / after 002_phase3_tenant_isolation.sql.
--
-- Why: search_inventory_fts() is the catalog tool the Amara agent calls mid-chat.
-- It runs under the service-role client (BYPASSRLS), so RLS does NOT scope it — and
-- the old function selected from v_available_inventory with no tenant filter. That
-- let one tenant's lead be shown another tenant's listings. This adds an explicit
-- tenant filter the application MUST pass (app/services/inventory.py search_properties).
--
-- properties / units / developments all already carry tenant_id; we only need to
-- surface it through the view and require it in the function.

-- ─── 1. Backfill NULL tenant_ids so the view filter can't silently hide seeded rows ──
-- (properties.tenant_id is nullable with no default; units/developments default to the
-- system tenant but pre-column rows may be NULL.)
UPDATE public.properties   SET tenant_id = 'a0000000-0000-4000-8000-000000000001' WHERE tenant_id IS NULL;
UPDATE public.units        SET tenant_id = 'a0000000-0000-4000-8000-000000000001' WHERE tenant_id IS NULL;
UPDATE public.developments SET tenant_id = 'a0000000-0000-4000-8000-000000000001' WHERE tenant_id IS NULL;

-- ─── 2. Expose tenant_id through the unified inventory view ────────────────────
CREATE OR REPLACE VIEW public.v_available_inventory AS
 SELECT properties.id,
    properties.name AS title,
    properties.location,
    properties.type AS property_type,
    properties.bedrooms,
    properties.price AS price_naira,
    properties.highlights,
    'properties'::text AS source_table,
    properties.tenant_id
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
    'units'::text AS source_table,
    u.tenant_id
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
    'developments'::text AS source_table,
    developments.tenant_id
   FROM public.developments
  WHERE (developments.price_min IS NOT NULL);

-- ─── 3. Tenant-filtered FTS function ──────────────────────────────────────────
-- Signature changes (adds the leading p_tenant_id arg), so drop the old one first.
-- The arg name p_tenant_id is what the PostgREST RPC call passes by name.
DROP FUNCTION IF EXISTS public.search_inventory_fts(text, numeric, integer);

CREATE FUNCTION public.search_inventory_fts(
    p_tenant_id uuid,
    search_query text,
    max_budget numeric DEFAULT NULL::numeric,
    target_bedrooms integer DEFAULT NULL::integer
) RETURNS TABLE(id uuid, name text, location text, property_type text, bedrooms integer, price numeric, highlights text, source text, rank real)
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
        -- Tenant isolation: only this workspace's catalog is searchable.
        v.tenant_id = p_tenant_id AND
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
