# Publish On Render

This repo is now set up to run on Render as:

- FastAPI serves the `/api/*` routes
- FastAPI also serves the built React app in production
- A free Render Postgres database stores app data

## Before You Deploy

1. Push this repo to GitHub, GitLab, or Bitbucket.
2. Use the `main` branch, which already contains `render.yaml`.

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
- Creates a free Render Postgres database
- Connects the web app to that database with `DATABASE_URL`

## Free Tier Notes

- This Blueprint now targets Render free instances so it should not require a paid web service or persistent disk.
- Render's free Postgres offering is time-limited. Render's docs say free Postgres databases expire 30 days after creation.
- Free web services can spin down when idle, so the first request after inactivity can be slow.

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
