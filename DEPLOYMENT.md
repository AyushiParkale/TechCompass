# TechCompass deployment

TechCompass uses a static chat UI on Vercel, a persistent FastAPI service on
Render, and the managed FalkorDB Cloud instance. The browser talks only to the
API; FalkorDB and Groq credentials stay on the Render service.

## Before deploying

1. **Enable TLS on the FalkorDB Cloud instance.** Render is configured with
   `FALKORDB_TLS=true`. Do not deploy production traffic while the cloud
   instance still has TLS disabled; otherwise the database credentials and
   graph traffic are not protected in transit.
2. Rotate the Groq key if the previously exposed key has not already been
   revoked. Put the replacement directly into Render's secret environment
   variable form, never into the frontend or a committed file.
3. Push this project to a GitHub repository. Keep `.env` out of the repository.

## Deploy the API on Render

1. In Render, create a **Blueprint** from the repository and apply
   [`render.yaml`](./render.yaml).
2. Fill the prompted environment variables in Render:
   - `FALKORDB_HOST`, `FALKORDB_PORT`, `FALKORDB_USERNAME`,
     `FALKORDB_PASSWORD`: values from the cloud instance dashboard.
   - `GROQ_API_KEY`: the active Groq key.
   - `TECHCOMPASS_ACCESS_PASSWORD`: a strong password for the private chat.
   - `TECHCOMPASS_AUTH_SECRET`: a separate random secret, at least 32
     characters.
   - `FRONTEND_ORIGINS`: the exact production Vercel origin, for example
     `https://your-project.vercel.app` (no trailing slash).
3. Keep `FALKORDB_GRAPH=techcompass` and `FALKORDB_TLS=true`.
4. Wait for the Render service to deploy and verify
   `https://<render-service>/health/ready` returns status `ready`.

Generate an auth signing secret in PowerShell without saving it into the
repository:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Run it separately for `TECHCOMPASS_ACCESS_PASSWORD` and
`TECHCOMPASS_AUTH_SECRET`; store the results in a password manager and enter
them directly in Render.

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

The UI keeps its signed access token in page memory only. Refreshing the page
requires signing in again. The API limits failed login attempts and requires a
valid signed token for graph and agent routes. Health endpoints remain public
for the hosting provider's health checks.

## Verify the deployed chat

1. Open the Vercel URL and sign in with `TECHCOMPASS_ACCESS_PASSWORD`.
2. Ask a question such as “What agent-related capabilities are documented?”
3. Confirm an answer appears and expand **Evidence tools used** to inspect the
   graph tools called.
4. Confirm an unauthenticated request to the API's `/api/agent/ask` returns
   `401`, and `/health/ready` returns `ready`.

The Render service is configured on a paid always-on plan to avoid sleeping
during chat use. FalkorDB remains a separate managed service. Do not place
database credentials, the Groq key, or the shared password in Vercel
environment variables or frontend source.
