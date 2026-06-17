# Publish On Render

This repo is now set up to run on Render as:

- FastAPI serves the `/api/*` routes
- FastAPI also serves the built React app in production
- An external Postgres database stores app data

## Before You Deploy

1. Push this repo to GitHub, GitLab, or Bitbucket.
2. Use the `main` branch, which already contains `render.yaml`.
3. Create a Supabase project and copy its Postgres connection string.

## Recommended Render Setup

This app includes [render.yaml](./render.yaml), so the simplest path is a Render Blueprint deploy.

1. Create a Render account.
2. In Render, choose `New` -> `Blueprint`.
3. Connect the repo that contains this project.
4. Confirm Render picks up `render.yaml`.
5. Deploy the Blueprint.

The Blueprint config does these things for you:

- Builds from the repo `Dockerfile`
- Runs the app in the `singapore` region
- Health-checks ` /api/health`
- Prompts you to set `DATABASE_URL` manually

## Supabase Database Setup

Use [SUPABASE_SETUP.md](./SUPABASE_SETUP.md) to create the database and get the connection string.

At deploy time on Render, set:

- `DATABASE_URL=<your Supabase Postgres connection string>`

## Free Tier Notes

- Render free web services can spin down when idle, so the first request after inactivity can be slow.
- Supabase's current docs say Free Plan projects can be paused after 7 days of low activity.
- This setup avoids Render's free-database expiration, but it is still not a guaranteed always-on production setup.

## Optional Environment Variables

You only need these if you want optional LLM verdict rewriting:

- `OPENAI_API_KEY`
- `GEMINI_API_KEY`
- `LLM_VERDICTS_PROVIDER`
- `OPENAI_VERDICTS_ENABLED=1`
- `GEMINI_VERDICTS_ENABLED=1`

Add them in the Render dashboard after the first deploy.

## Custom Domain

After the service is live:

1. Open the web service in Render.
2. Go to `Settings` -> `Custom Domains`.
3. Add your domain and follow Render's DNS instructions.

## Local Production-Style Test

You can test the single-container deploy locally with:

```powershell
docker compose up --build
```

Then open `http://localhost:8000`.
