# Deployment — GitHub → Render (+ Supabase)

Step-by-step deployment of this application to GitHub, Render and Supabase.

**Nothing here requires editing application code.** Local development and
production differ only in configuration.

> **Deploying from a different machine?** Read
> [What is safe to commit](#what-is-safe-to-commit) first. The repository must
> contain no credentials, and this project is arranged so that it does not.

---

## Contents

1. [What gets deployed where](#1-what-gets-deployed-where)
2. [Before you start](#2-before-you-start)
3. [Push to GitHub](#3-push-to-github)
4. [Supabase — the database](#4-supabase--the-database)
5. [Render — the backend](#5-render--the-backend)
6. [Render — the frontend](#6-render--the-frontend)
7. [Render — n8n](#7-render--n8n)
8. [First run — enter your credentials](#8-first-run--enter-your-credentials)
9. [Verify the deployment](#9-verify-the-deployment)
10. [Media storage](#10-media-storage)
11. [Migrating your existing local data](#11-migrating-your-existing-local-data)
12. [Rollbacks](#12-rollbacks)
13. [Troubleshooting](#13-troubleshooting)

---

## 1. What gets deployed where

Three services from one repository, plus one database.

| Service | Source | Type | Root directory |
| --- | --- | --- | --- |
| **Backend** | `backend/` | Web Service (Docker) | `backend` |
| **Frontend** | `frontend/` | Static Site | `frontend` |
| **n8n** | `deploy/n8n/Dockerfile` | Web Service (Docker) | `.` (context is `.`) |
| **Database** | — | Supabase PostgreSQL | — |

Why separate services: the task's requirement that n8n be deployed and managed
independently of the application. They are three different lifecycles — the
frontend rebuilds on every push, the backend redeploys on every push, and n8n
must *not* restart when the app changes, because restarting n8n without a
stable encryption key can invalidate its credential store.

```
Browser ──> Frontend (Render static) ──> Backend (Render web service)
                                            │            │
                                            │            └─> n8n ──> Buffer
                                            └─> Supabase PostgreSQL
```

---

## 2. Before you start

You need:

- A GitHub repository with this project pushed (§3)
- A Supabase project with a PostgreSQL database (§4)
- Three Render services (§5, §6, §7)
- A Buffer access token, entered through the UI after the first deploy (§8)

Generate the secrets **once** and store them somewhere safe. Two of them
cannot be recovered if lost:

```bash
# Credential encryption (backend) - encrypts every stored Buffer/AI secret
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# Backend <-> n8n shared secret
python -c "import secrets;print(secrets.token_urlsafe(32))"

# Backend API key
openssl rand -base64 36

# n8n credential vault key
openssl rand -base64 36
```

Keep all four in a password manager. Losing the first makes every stored
Buffer token and AI key unreadable; losing the last makes n8n unable to
decrypt its own stored credentials.

---

## 3. Push to GitHub

If you have already pushed, skip to §4.

```powershell
git status                       # confirm nothing sensitive is staged
git remote -v                    # confirm the origin is correct
git push -u origin main
```

Render connects to the repository over HTTPS using a GitHub token, so the
repository must be pushed **before** you create the services.

---

## 4. Supabase — the database

1. Supabase dashboard → **New project**.
2. **Project Settings → Database → Connection string → URI**.
3. Take the **Session Pooler** URI (port `5432`), not the transaction pooler.

Convert it to the form SQLAlchemy needs. The URI looks like:

```
postgresql://postgres.PROJECTREF:PASSWORD@aws-0-REGION.pooler.supabase.com:5432/postgres
```

The backend needs this exact form:

```
postgresql+psycopg://postgres.PROJECTREF:PASSWORD@aws-0-REGION.pooler.supabase.com:5432/postgres
```

The only change is `postgresql` → `postgresql+psycopg`.

### No manual migration step

There is none, and that is intentional. On startup the backend runs
`create_all` and then adds any missing columns
(`backend/app/database/session.py`). Both operations are **additive only** —
they never drop, rename or rewrite a table, so running them against an empty
database creates the schema and running them against an existing one is a
no-op. Never run `DROP TABLE` or delete the Supabase project to "start fresh".

If you need the schema on paper, connect with `psql` and run `\d`, or:

```bash
pg_dump --schema-only --dbname="postgresql://..." > schema.sql
```

---

## 5. Render — the backend

**New → Web Service → connect the repository.**

| Setting | Value |
| --- | --- |
| Root Directory | `backend` |
| Runtime | Docker |
| Health Check Path | `/api/health` |

Docker is used rather than Render's native Python runtime because the
repository already ships `backend/Dockerfile`; that keeps local and deployed
environments byte-identical.

### The port

`backend/Dockerfile` binds `${PORT:-8000}`, so Render's dynamically assigned
port is picked up automatically. Do not set `PORT` yourself.

### Environment variables

Required — the backend **refuses to start** without these:

| Variable | Value |
| --- | --- |
| `APP_ENV` | `production` |
| `DEBUG` | `false` |
| `DATABASE_URL` | The Supabase URI from §4, with `+psycopg` |
| `CREDENTIAL_ENCRYPTION_KEY` | From §2 |
| `API_KEY` | From §2 |
| `CORS_ORIGINS` | `https://<your-frontend>.onrender.com` |
| `INTERNAL_SERVICE_SECRET` | From §2 |
| `N8N_PUBLISH_WEBHOOK_URL` | `https://<your-n8n>.onrender.com/webhook/social/publish` |
| `N8N_BACKEND_BASE_URL` | `https://<your-backend>.onrender.com` |
| `PREVIEW_MEDIA_BASE_URL` | `https://<your-backend>.onrender.com/api/media` |
| `PUBLIC_API_BASE_URL` | `https://<your-backend>.onrender.com` |

Strongly recommended:

| Variable | Value |
| --- | --- |
| `MEDIA_STORAGE_BACKEND` | `s3` — see §10 |
| `S3_*` | Your bucket credentials, see §10 |
| `N8N_WEBHOOK_SECRET` | Any random string |
| `DEFAULT_TIMEZONE` | e.g. `Asia/Dhaka` |

Optional: `AI_ENCRYPTION_KEY` (legacy), `MAX_UPLOAD_BYTES`,
`AI_MAX_CONCURRENCY`, `BUFFER_API_BASE_URL`.

### Startup validation

On boot in `APP_ENV=production` the backend checks its own configuration and
**exits with a clear error** rather than starting in a broken state. This
catches the most damaging mistake — a deploy that starts happily against an
ephemeral SQLite file and loses every account on the next restart.

It rejects: SQLite `DATABASE_URL`, a missing encryption key, a missing
`API_KEY`, empty or wildcard `CORS_ORIGINS`, and `PUBLISH_ENGINE=n8n` with no
`INTERNAL_SERVICE_SECRET`.

Non-blocking warnings are logged for a loopback `PUBLIC_MEDIA_BASE_URL`,
`MEDIA_STORAGE_BACKEND=local`, and a missing `N8N_WEBHOOK_SECRET`.

> **Chicken-and-egg on CORS.** The frontend's URL is only known after you
> create it in §6. Either create the frontend first, or set `CORS_ORIGINS`
> afterwards and redeploy — both are one variable edit.

### `/docs` is disabled in production

Interactive API docs publish the entire route surface outside the API-key
gate. They are available in development (`APP_ENV != production`) and off in
production.

---

## 6. Render — the frontend

**New → Static Site → connect the repository.**

| Setting | Value |
| --- | --- |
| Root Directory | `frontend` |
| Build Command | `npm ci && npm run build` |
| Publish Path | `dist` |

### Build-time environment variables

Vite inlines `VITE_*` variables into the JavaScript bundle **at build time**.
On Render these belong under **Build** settings, not the runtime environment.
Changing one requires a new deploy to take effect.

| Variable | Value |
| --- | --- |
| `VITE_API_BASE_URL` | `https://<your-backend>.onrender.com` |
| `VITE_DEFAULT_TIMEZONE` | e.g. `Asia/Dhaka` |
| `VITE_API_KEY` | The same `API_KEY` as the backend — **only if** the backend requires it |

> `VITE_API_KEY` is compiled into a file anyone can download. It is an origin
> gate, not authentication: every browser gets the same value, so it does not
> identify a user. Treat the API key as semi-public and rotate it if the repo
> is ever compromised.

The frontend is a SPA. `frontend/nginx.conf` already contains the
`try_files … /index.html` fallback, so client-side routes such as `/settings`
survive a hard refresh.

---

## 7. Render — n8n

**New → Web Service → connect the repository.**

| Setting | Value |
| --- | --- |
| Root Directory | `.` (repository root) |
| Docker Context | `.` |
| Dockerfile Path | `deploy/n8n/Dockerfile` |

### Persistence — read this before the first deploy

Render's free web services have an **ephemeral** filesystem. `/home/node/.n8n`
is wiped on every redeploy and every restart. Choose exactly one:

**Option A — a PostgreSQL database (recommended, works on the free tier)**

Use a second Supabase project, or Render's own free PostgreSQL.

```
DB_TYPE=postgresdb
DB_POSTGRESDB_HOST=...
DB_POSTGRESDB_PORT=5432
DB_POSTGRESDB_DATABASE=...
DB_POSTGRESDB_USER=...
DB_POSTGRESDB_PASSWORD=...
```

**Option B — a Render Persistent Disk** mounted at `/home/node/.n8n`.
Disks are a paid feature and unavailable on the free tier.

With neither option, you lose every workflow and every credential on each
deploy. Losing workflows is recoverable by re-importing the JSON. Losing
credentials means re-entering every Buffer token.

### Environment variables

| Variable | Value | Why |
| --- | --- | --- |
| `N8N_ENCRYPTION_KEY` | From §2 | **Required.** Without it n8n generates a new key on every boot and every stored credential becomes undecryptable. |
| `SMA_BACKEND_URL` | `https://<your-backend>.onrender.com` | Where the backend is **from n8n's point of view**. `localhost` means the n8n container itself. |
| `SMA_INTERNAL_SECRET` | Same as the backend's `INTERNAL_SERVICE_SECRET` | Authenticates the callback to `/internal/publish`. |
| `N8N_HOST` | `<your-n8n>.onrender.com` | Makes n8n generate correct webhook URLs. |
| `WEBHOOK_URL` | `https://<your-n8n>.onrender.com/` | Same, for production webhooks. |
| `N8N_EDITOR_BASE_URL` | `https://<your-n8n>.onrender.com/` | Same, for the editor UI. |
| `N8N_PROTOCOL` | `https` | Render terminates TLS and proxies over HTTP. |
| `N8N_SECURE_COOKIE` | `false` | Required to log in to the editor behind Render's proxy. |
| `GENERIC_TIMEZONE`, `TZ` | e.g. `Asia/Dhaka` | Schedules resolve in this zone. |
| `N8N_DIAGNOSTICS_ENABLED` | `false` | No telemetry. |
| `DB_TYPE` + `DB_POSTGRESDB_*` | See Option A | Persistence. |

The two variables the **workflow** actually reads are `SMA_BACKEND_URL` and
`SMA_INTERNAL_SECRET`. That is the entire per-deployment workflow
configuration.

### Import and activate the workflow — once

1. Open `https://<your-n8n>.onrender.com` and create the owner account.
2. **Workflows → Import from File** → `deploy/n8n/social-publishing-workflow.json`.
3. **Activate** the workflow (the toggle in the top-right).

That is the whole setup. Accounts, channels, credentials, AI providers and
brands are all managed from the application UI afterwards — adding another
Buffer account or channel never requires editing a workflow node.

Full detail, including recovery: [`deploy/n8n/README.md`](deploy/n8n/README.md).

---

## 8. First run — enter your credentials

Credentials are **not** environment variables, so nothing secret is baked
into an image or a shell history.

1. Open the frontend → **Settings → Buffer Integrations → Add**.
   Name the integration, paste a Buffer access token, **Save and test
   connection**.
2. **Discover channels**, select the ones you want, choose the brand, **Import**.
3. **Settings → AI providers** → add each AI provider key.
4. **Settings → Accounts & channels** → create a brand if you need one.

Test **Quick Publisher** on a single channel before scheduling anything.

---

## 9. Verify the deployment

| Check | How |
| --- | --- |
| Backend is up | `GET https://<backend>/api/health` → `{"status":"ok"}` |
| Database reachable | Same response, `"database":"ok"` |
| Frontend loads | Open the static site; the dashboard renders |
| Frontend talks to the backend | Dashboard shows your accounts |
| API key enforced | `curl https://<backend>/api/accounts` → `401` |
| Everything wired | **Settings → Overview** in the UI |
| n8n reachable | Overview → *Test n8n connection* |
| Credentials decryptable | Overview → *Credential encryption: On* |
| Channels publishable | Accounts & channels → no *Will not publish* |

`GET /api/system/status` returns the same information as JSON and requires the
API key.

---

## 10. Media storage

Buffer downloads attachments from the **public internet**, usually long after
the post is created. If it cannot reach the URL, the post is still created —
it simply arrives with no image, and there is no error anywhere. This is the
single most common silent failure in deployment.

Render gives you no persistent disk on the free tier, so `MEDIA_STORAGE_BACKEND=local`
means uploaded media disappears on every redeploy and breaks already-scheduled
posts.

### Option A — Cloudflare R2 or any S3-compatible bucket (recommended)

```ini
MEDIA_STORAGE_BACKEND=s3
S3_BUCKET=your-bucket
S3_ENDPOINT=https://<account-id>.r2.cloudflarestorage.com
S3_REGION=auto
S3_ACCESS_KEY_ID=...
S3_SECRET_ACCESS_KEY=...
S3_PUBLIC_BASE_URL=https://pub-<account>.r2.dev
```

The bucket **must be publicly readable**, otherwise Buffer cannot download
from it. R2 has no egress fees, which matters for a service that hands out
download URLs to a third party on every published post.

### Option B — a Render Persistent Disk

Attach a disk mounted at `/data/media` and set:

```ini
MEDIA_STORAGE_BACKEND=local
MEDIA_STORAGE_DIR=/data/media
PREVIEW_MEDIA_BASE_URL=https://<your-backend>.onrender.com/api/media
```

This keeps local storage and requires no bucket. It is a paid Render feature,
and it only preserves media — the database still needs PostgreSQL.

### Option C — Supabase Storage

Not wired up. `backend/app/services/storage.py` has an S3 backend and a local
backend only; adding a third would mean new code. Use Option A or B.

---

## 11. Migrating your existing local data

You have two choices. **Option 1 is faster and safer** unless you specifically
need the post history.

### Option 1 — re-enter credentials (recommended)

Create the accounts, channels and providers through the UI as in §8.

Credentials entered before the move are encrypted with your **local**
`AI_ENCRYPTION_KEY`. Copying those encrypted rows to a different host with a
different `CREDENTIAL_ENCRYPTION_KEY` produces unreadable values, so copying
them is strictly worse than re-entering them.

### Option 2 — migrate accounts, channels and history

Only account/channel/post rows need to move; secrets never do.

1. On your machine, dump the **data**, not the schema:
   ```bash
   sqlite3 backend/social_media.db .dump > local-data.sql
   ```
   Inspect it and delete every INSERT for `integrations` and `ai_providers`
   (`credential_encrypted`, `api_key_encrypted`). These are the encrypted
   secrets and they are useless without your local key.
2. Load the remainder into Supabase:
   ```bash
   psql "postgresql://USER:PASSWORD@HOST:5432/postgres" < local-data.sql
   ```
3. Start the backend once so `create_all` adds anything missing.
4. Re-enter every Buffer token and AI provider key in the UI.

> SQLite and PostgreSQL are not interchangeable. The dump above is plain
> `INSERT` statements and transfers cleanly, but do **not** copy the schema
> (`sqlite3 .schema`) — types, autoincrement and boolean handling differ.
> Let `create_all` build the PostgreSQL schema instead.

---

## 12. Rollbacks

**Frontend and backend.** Render keeps every previous build. Roll back from
the **Deploys** tab → *Rollback to this deploy*. This redeploys an earlier
commit; environment variables are unchanged.

Rolling back the backend is safe: `create_all` and the column reconciliation
are additive, so an older commit runs fine against a newer schema.

**Database.** Restore from a `pg_dump` into a fresh Supabase project, then
point `DATABASE_URL` at it and redeploy. Additive schema changes mean a dump
from an older version restores into a newer one without conversion.

**n8n.** Not managed by Render deploys — it is a long-lived service whose
workflows live in its own database.

- *Lost workflows:* re-import `deploy/n8n/social-publishing-workflow.json`
  and activate it.
- *Lost credentials ("credential not found"):* `N8N_ENCRYPTION_KEY` changed
  or was not set. Restore the original key from your password manager and
  restart. **This is not recoverable otherwise** — the old ciphertext cannot
  be decrypted without it.

---

## 13. Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| `Refusing to start in production` | Startup validation | Read the log; it names the exact variable to set |
| Site loads, every request fails CORS | `CORS_ORIGINS` missing the frontend origin | Add the exact origin, including `https://`, and redeploy |
| Frontend talks to the old backend | `VITE_API_BASE_URL` is a build-time variable | Change it under **Build** settings and redeploy |
| `401` on every call | `API_KEY` set on one side only | `VITE_API_KEY` must equal the backend's `API_KEY` |
| n8n says `localhost` cannot connect | `SMA_BACKEND_URL` is a loopback address | Use the backend's public URL |
| n8n returns "Not authorised" to the workflow | Secrets do not match | `SMA_INTERNAL_SECRET` must equal `INTERNAL_SERVICE_SECRET` |
| `/internal/publish` returns 404 | `INTERNAL_SERVICE_SECRET` unset on the backend | Set it, or set `PUBLISH_ENGINE=direct` |
| Workflow inactive after a while | n8n restarted and lost its data | Attach a database or disk (§7) |
| Post published with no image | Media URL not publicly reachable | §10 |
| AI key rejected after the move | Encryption key differs | Re-enter the key in Settings |
| Everything reset after a redeploy | `DATABASE_URL` was SQLite | Point it at Supabase and re-enter credentials |
| Cannot log in to the n8n editor | Secure cookie behind a proxy | Set `N8N_SECURE_COOKIE=false` |

---

## What is safe to commit

This repository contains **no credentials**, by design:

| Never committed | Where it lives instead |
| --- | --- |
| Buffer access tokens | Encrypted in the database, entered via the UI |
| AI provider keys | Encrypted in the database, entered via the UI |
| `CREDENTIAL_ENCRYPTION_KEY` | Render environment variables |
| `INTERNAL_SERVICE_SECRET` | Render environment variables |
| `API_KEY` | Render environment variables |
| `N8N_ENCRYPTION_KEY` | Render environment variables |
| The local database and uploads | `.gitignore`, backed up separately |
| n8n credential vault | n8n's own database |

`backend/.env`, `frontend/.env`, `deploy/.env.production`, `*.db`, the media
directories and `.build/` are all excluded by `.gitignore`. `backend/.dockerignore`
and `frontend/.dockerignore` additionally keep them out of the Docker images,
which matters because `COPY . .` would otherwise bake `backend/.env` into an
image layer.

`social-media-automation.workflow.json` in the repository root has had its
Buffer organization id, channel ids and n8n credential id replaced with
`REPLACE_WITH_...` placeholders. It is kept for reference only — **do not
import it.** The supported artifact is
`deploy/n8n/social-publishing-workflow.json`.