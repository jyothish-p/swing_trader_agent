-- Fix Supabase Security Advisor: "RLS Disabled in Public" for public.evaluations.
--
-- This app uses the backend DATABASE_URL directly and does not query
-- public.evaluations from the browser. The safest default is therefore:
-- enable RLS and grant no public Data API access policies.

alter table if exists public.evaluations enable row level security;

revoke all on table public.evaluations from anon;
revoke all on table public.evaluations from authenticated;

-- Keep backend/service access available if Supabase granted it previously.
grant select, insert, update, delete on table public.evaluations to service_role;

-- No RLS policies are created intentionally. With RLS enabled and no policies,
-- anon/authenticated clients cannot read or write rows through Supabase APIs.
