# Deploying on a Free Tier

This project can be deployed for demo use on a free web-service tier.

## Recommended host

Use **Render free web service** for the best chance of success with this stack.

Why:
- Docker deployment is supported.
- Environment variables can be set in the dashboard or via `render.yaml`.
- A public `onrender.com` URL is assigned automatically.
- The app already includes a `/healthz` endpoint.

## Important MVP limitations on free hosting

This MVP intentionally uses:
- local file storage
- SQLite
- an in-process worker

On Render free web services, the filesystem is **ephemeral**. Uploaded files, source PDFs, revised PDFs, and the SQLite DB can disappear whenever the service restarts, redeploys, or spins down. This is acceptable for demos, but not for durable production.

## One-time setup

1. Create a GitHub repository and push this project.
2. Sign in to Render.
3. Create a new Blueprint from the repo, or create a Docker web service from the repo.
4. Set the required secret:
   - `CLOUDCONVERT_API_KEYS`
5. Deploy.

## Required environment variables

Only one variable is mandatory:

- `CLOUDCONVERT_API_KEYS=key1,key2,key3`

Recommended values already declared in `render.yaml`:

- `APP_ENV=production`
- `API_PREFIX=/v1`
- `STORAGE_ROOT=/tmp/cad-text-mover/storage`
- `SQLITE_PATH=/tmp/cad-text-mover/jobs.sqlite3`
- `MAX_UPLOAD_SIZE_MB=50`
- `CLOUDCONVERT_FAILOVER_ENABLED=true`
- `CLOUDCONVERT_KEY_COOLDOWN_SECONDS=900`
- `WORKER_POLL_INTERVAL_SECONDS=2.0`
- `CLOUDCONVERT_POLL_INTERVAL_SECONDS=2.0`
- `CLOUDCONVERT_TIMEOUT_SECONDS=120.0`
- `TESSERACT_CMD=/usr/bin/tesseract`

## Render deploy flow

### Option A: Blueprint using `render.yaml`

1. Push the repository to GitHub.
2. In Render, click **New > Blueprint**.
3. Connect the GitHub repository.
4. Render will detect `render.yaml`.
5. When prompted for secrets, set:
   - `CLOUDCONVERT_API_KEYS`
6. Click **Apply**.
7. Wait for the Docker build and deploy to finish.
8. Open the assigned `https://<your-service>.onrender.com` URL.

### Option B: Manual Docker web service

1. Push the repository to GitHub.
2. In Render, click **New > Web Service**.
3. Connect the repository.
4. Choose:
   - Runtime: `Docker`
   - Plan: `Free`
5. Set the health check path to:
   - `/healthz`
6. Add the same environment variables listed above.
7. Deploy.

## After deploy

The frontend is available at `/`.

The API remains available under `/v1`.

Examples:
- `https://<your-service>.onrender.com/`
- `https://<your-service>.onrender.com/healthz`
- `https://<your-service>.onrender.com/v1/jobs`

## Keeping it reliable on free tier

- Keep the browser open while a job runs so the app keeps receiving requests.
- Use smaller CAD files first.
- Expect old jobs to disappear after restarts or redeploys.
- Upgrade later to persistent storage + Postgres if you need durable jobs.
