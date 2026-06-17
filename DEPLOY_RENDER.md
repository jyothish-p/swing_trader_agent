# Publish On Render

This repo is now set up to run as a single Docker web service:

- FastAPI serves the `/api/*` routes
- FastAPI also serves the built React app in production
- App data can live on a persistent disk at `/data`

## Before You Deploy

1. Push this repo to GitHub, GitLab, or Bitbucket.
2. Decide whether this is:
   - A demo deployment: keep SQLite and the persistent disk.
   - A long-term production deployment: consider moving `DATABASE_URL` to managed Postgres later so you can scale beyond one instance.

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
- Mounts a persistent disk at `/data`

## Important Note About SQLite

This project uses SQLite by default. That works on Render only if you keep the persistent disk attached, because Render's normal filesystem is ephemeral.

For the current app shape, SQLite is fine when:

- You run one web instance
- You mainly want a personal or small-team deployment

You should plan a Postgres migration if you want:

- Multiple app instances
- Stronger database durability
- Easier growth beyond a single machine

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
