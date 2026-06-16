-- Neoscona blog — posts table for Supabase
-- Run once in the Supabase dashboard → SQL Editor (for the same project Reva uses,
-- or a separate project — either works; the blog only needs this one table).
--
-- The Flask app reads/writes this table via DATABASE_URL (the Supabase Postgres
-- connection string). No Supabase client library required on the neoscona side.

create table if not exists public.posts (
    id          bigserial primary key,
    title       varchar(255) not null,
    category    varchar(100) not null,
    content     text,
    image_url   varchar(500),
    created_at  timestamptz not null default now()
);

create index if not exists posts_created_at_idx on public.posts (created_at desc);

-- Optional seed so /blog isn't empty on first load. Safe to delete/edit.
insert into public.posts (title, category, content, image_url)
select
    'Why we''re betting Africa runs on AI workers',
    'Manifesto',
    'Most businesses in Africa don''t lose to a bad product. They lose to a slow reply. Neoscona is the place you hire AI workers that never sleep, never forget, and never lie. This is our founding bet.',
    null
where not exists (select 1 from public.posts);

-- NOTE on Row Level Security (RLS):
-- The Flask app connects with the Postgres connection string (DB role), NOT the
-- anon/JWT key, so RLS does not block it. If you later enable RLS on this table
-- for other access paths, the direct DB connection still works because it uses
-- the database user, which bypasses RLS. Keep the connection string secret.
