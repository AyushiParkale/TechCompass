# TechCompass deployment

TechCompass uses a static update-report UI on Vercel, a persistent FastAPI service on
Render, and the managed FalkorDB Cloud instance. The browser talks only to the
API; FalkorDB and Gemini credentials stay on the Render service.

**The application has no authentication.** Every graph and agent endpoint is
public on the internet when deployed this way. Anyone can query your graph,
consume Gemini quota, trigger external-source syncs, and submit article URLs.
Only deploy behind an access-controlled private network or trusted gateway.
If that boundary is not available, do not deploy this configuration publicly.

## Before deploying

1. **Enable TLS on the FalkorDB Cloud instance.** Render is configured with
   `FALKORDB_TLS=true`. Do not deploy production traffic while the cloud
   instance still has TLS disabled; otherwise the database credentials and
   graph traffic are not protected in transit.
2. Rotate any LLM API key if it was previously exposed and has not already been
   revoked. Put the replacement directly into Render's secret environment
   variable form, never into the frontend or a committed file.
3. Push this project to a GitHub repository. Keep `.env` out of the repository.

## Deploy the API on Render

1. In Render, create a **Blueprint** from the repository and apply
   [`render.yaml`](./render.yaml).
2. Fill the prompted environment variables in Render:
   - `FALKORDB_HOST`, `FALKORDB_PORT`, `FALKORDB_USERNAME`,
     `FALKORDB_PASSWORD`: values from the cloud instance dashboard.
   - `GEMINI_API_KEY`: the active Gemini API key.
   - `GEMINI_MODEL`: defaults to `gemini-3.8-flash` in the Render Blueprint.
   - `FRONTEND_ORIGINS`: the exact production Vercel origin, for example
     `https://your-project.vercel.app` (no trailing slash).
3. Keep `FALKORDB_GRAPH=techcompass` and `FALKORDB_TLS=true`.
4. Wait for the Render service to deploy and verify
   `https://<render-service>/health/ready` returns status `ready`.

## Deploy the UI on Vercel

1. Import the same GitHub repository as a new Vercel project.
2. Set **Root Directory** to `frontend`.
3. Set the Vercel environment variable `TECHCOMPASS_API_URL` to the Render
   service's HTTPS origin, such as `https://<render-service>.onrender.com`.
4. Build command and output directory are already defined in
   [`frontend/vercel.json`](./frontend/vercel.json). Deploy, then copy the
   production Vercel origin.
5. Set Render's `FRONTEND_ORIGINS` to that exact Vercel origin and redeploy the
   Render service if needed. Redeploy the Vercel project after changing its
   environment variable.

The UI and API require no login. CORS only restricts browser-origin requests;
it does not prevent direct API access and is not a security boundary.

## Verify the deployed report

1. Open the Vercel URL, set a lookback of up to 90 days, and select
   **Check for updates**.
2. Confirm live check statuses appear, then review the segmented report and
   its source links and graph evidence.
3. Confirm `/health/ready` returns `ready`.

The Render service is configured on a paid always-on plan to avoid sleeping
during report generation. FalkorDB remains a separate managed service. Do not place
database credentials or the Gemini key in Vercel environment variables or
frontend source.
