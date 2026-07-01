# Supabase Setup

This app can use Supabase Postgres as its production database with no code changes beyond setting `DATABASE_URL`.

## 1. Create the Supabase project

1. Sign in to Supabase.
2. Create a new project.
3. Wait for the database to finish provisioning.

## 2. Copy the connection string

In the Supabase dashboard:

1. Open your project.
2. Click `Connect`.
3. Copy a Postgres connection string.

Recommended choice for this app:

- Start with the `Session pooler` connection string.

Why:

- Supabase's docs say direct connections are best for long-lived backends.
- Supabase also says `Session pooler` is the right alternative for persistent backends on IPv4-only networks.

If the direct connection string works from Render, you can use it. If not, use the Session pooler string.

## 3. Set it in Render

When creating the Render Blueprint or web service, set:

```text
DATABASE_URL=postgres://...
```

This app automatically rewrites Supabase `postgres://` or `postgresql://` URLs into the SQLAlchemy driver format it needs.

## 4. First startup

On startup, the app automatically creates its database tables with SQLAlchemy, so you do not need to run a separate migration step for the initial deploy.

## Notes

- Supabase's current docs say Free Plan projects can be paused after 7 days of low activity.
- Supabase's current pricing page says Free Plan projects are paused after 1 week of inactivity.
- If the project is paused, you can restore it from the Supabase dashboard.
- If Supabase Security Advisor reports `RLS Disabled in Public`, run the matching SQL fix from this repo. For `public.evaluations`, use `supabase_security_fix.sql` in the Supabase SQL Editor, then click `Refresh` in Security Advisor.
