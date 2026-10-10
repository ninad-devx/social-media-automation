# Deployment & migration guide

How to run this **now** on your PC, and how to move the same application to
hosted infrastructure **without editing a single line of application code**.

---

## 1. What changed, and why

Previously the n8n workflow owned the account registry: the Buffer channel ids
lived in a JavaScript block inside node `04 - Load Account Configuration`, and
the Buffer token lived in an n8n credential. Adding an account meant editing the
workflow.

Now:

| Concern | Before | After |
| --- | --- | --- |
| Buffer token | n8n credential | Backend, encrypted at rest |
| Channel → Buffer id mapping | hardcoded JS in the workflow | `channels.external_channel_id` in the database |
| Adding an account | edit workflow JSON + n8n credential | Settings → Buffer integrations |
| Adding a channel | edit workflow JSON | Settings → discover → import |
| AI provider key | backend (already) | unchanged, now shares one encryption key |

The backend is the single source of truth. The workflow became provider-blind:
it receives internal ids, calls the backend, and relays the result.

**Your existing local setup still works unchanged.** `PUBLISH_ENGINE` defaults to
`n8n`, which is the previous behaviour end to end.

---

## 2. Local development (what you run today)

Nothing here changes. From the repository root:

```bash
# Backend - http://localhost:8000
cd backend
.venv\Scripts\python -m uvicorn app.main:app --reload --port 8000

# Frontend - http://localhost:3000
cd frontend
npm run dev

# n8n - http://localhost:5678  (however you already run it)
```

Database stays SQLite at `backend/social_media.db`. Uploads stay in
`backend/media/`. Docker remains optional.

### Schema changes on first run

The backend adds tables and columns **in place** at startup
(`app/database/session.py`). It is additive only:

- new table `integrations`
- new columns on `channels`: `integration_id`, `external_channel_id`,
  `external_service`, `external_username`, `connection_status`, `is_locked`,
  `queue_paused`, `capabilities_json`, `last_synced_at`

Existing rows are untouched. Your current `company` account and its three
channels keep working and remain publishable through n8n exactly as before.

> SQLite cannot add a foreign key to an existing table via `ALTER TABLE`. The
> `channels.integration_id` column is therefore created without the FK
> constraint on a pre-existing database. The relationship is enforced in code
> (`integration_service.resolve_publish_target`) and the constraint **is**
> present on any database created from scratch. To get it on an old database,
> recreate it from a backup dump — no data conversion needed.

---

## 3. Adding Buffer accounts from the UI

No code, no environment edit, no workflow edit.

1. **Settings → Buffer Integrations → Add**
   - Name it (e.g. `SortPi workspace`)
   - Paste a Buffer access token
2. **Save and test connection.**
   The backend calls Buffer's `account { organizations }` query. Status becomes
   *Connected* only if the token actually works.
   One token may reach several workspaces; they are all listed.
3. **Discover channels.**
   Calls `channels(input: { organizationId })`. This is a live API call — the
   list is never guessed.
4. **Select channels → choose the brand → Import.**
   Each becomes a real channel row and immediately appears in Quick Publisher,
   AI Automation, the calendar and history.
5. **Repeat** for each additional account. Nothing else to do.

### Adding a brand

**Settings → Accounts & channels → Create a brand.** Type the name; the id is
derived from it. No seed edit, no restart.

The equivalent API is `POST /api/accounts`:

```json
{ "name": "Glaamora", "type": "brand" }
```

Then import channels into it with
`POST /api/integrations/{id}/sync` using that `account_id`. Brands can also be
renamed, deactivated and deleted (deletion is refused while channels remain,
so scheduled posts can never be silently re-pointed).

Note that `GET /api/accounts` intentionally lists only brands that already have
an active channel — that is the Quick Publisher contract and it is unchanged.
`GET /api/accounts/manage` returns every brand for administration.

### If discovery is unavailable

Use **Settings → Accounts & channels → Link a channel manually** and paste the
channel id Buffer shows for that profile. Buffer calls them *channels*; older
documentation calls them *profiles*. The id is never inferred from a display
name.

---

## 4. The generic n8n workflow

Canonical export: [`deploy/n8n/social-publishing-workflow.json`](n8n/social-publishing-workflow.json)

Regenerate it with:

```bash
python deploy/n8n/build_workflow.py
```

It contains **no** Buffer tokens, **no** channel ids, **no** account list, and
**no** credential references. Eight nodes, one outbound call.

### One-time setup on a fresh n8n

1. n8n → **Workflows → Import from File** → select the JSON.
2. Set two environment variables on the n8n instance
   (**Settings → Variables**, or `docker-compose` `environment:`):

   | Variable | Value | Meaning |
   | --- | --- | --- |
   | `SMA_BACKEND_URL` | `http://host.docker.internal:8000` | where the backend is **from n8n's point of view** |
   | `SMA_INTERNAL_SECRET` | same value as the backend's `INTERNAL_SERVICE_SECRET` | authenticates the callback |

   For **self-hosted n8n**, prefix `$env.` variables with `N8N_` instead
   (e.g. `N8N_SMA_BACKEND_URL`) or define them in your shell — check the n8n
   version you run.

3. In the backend `.env`:

   ```ini
   PUBLISH_ENGINE=n8n
   INTERNAL_SERVICE_SECRET=<same value>
   N8N_BACKEND_BASE_URL=http://host.docker.internal:8000
   ```

4. Activate the workflow.

That is the whole setup. Routine work — adding accounts, rotating tokens,
onboarding channels, changing AI providers — happens entirely in the app.

### `localhost` means the caller

The most common deployment mistake. `localhost` inside the n8n container is
*n8n's own loopback*, not your PC and not the backend.

| n8n runs | `SMA_BACKEND_URL` / `N8N_BACKEND_BASE_URL` |
| --- | --- |
| same host, no Docker | `http://localhost:8000` |
| Docker on your PC | `http://host.docker.internal:8000` |
| same compose network | `http://backend:8000` |
| hosted, separate host | `https://api.your-domain.com` |

### Choosing between the two engines

| | `PUBLISH_ENGINE=n8n` | `PUBLISH_ENGINE=direct` |
| --- | --- | --- |
| Path | backend → n8n → backend → Buffer | backend → Buffer |
| n8n needed to publish | yes | no |
| Buffer token in n8n | never | never |
| Latency | one extra hop | lowest |
| Good for | keeping orchestration, audit trail | reliability |

Both keep every credential in the backend. Switch by editing one env var and
restarting — no workflow change either way.

---

## 5. Moving to production

### 5.1 Prepare

```bash
cp deploy/.env.production.example deploy/.env.production
cp backend/.env.example backend/.env.production    # reference only
```

Fill in every value in `deploy/.env.production`. Nothing else changes: the same
image, the same code, different configuration.

### 5.2 Required values

| Variable | Notes |
| --- | --- |
| `CREDENTIAL_ENCRYPTION_KEY` | Encrypts all stored credentials. **Back this up.** |
| `API_KEY` | Required. `/api` is otherwise unauthenticated. |
| `INTERNAL_SERVICE_SECRET` | Backend ↔ n8n shared secret. |
| `N8N_ENCRYPTION_KEY` | n8n refuses to start without it. |
| `DATABASE_URL` | Point at the `db` service. |
| `N8N_BACKEND_BASE_URL` | **Reachable from n8n**, e.g. `http://backend:8000`. |
| `CORS_ORIGINS` | Your public frontend origin only. |
| `PUBLIC_API_BASE_URL`, `PREVIEW_MEDIA_BASE_URL` | Public HTTPS URLs. |
| `S3_*` | Strongly recommended for images — see below. |

### 5.3 Start

```bash
docker compose -f deploy/docker-compose.prod.yml --env-file deploy/.env.production up -d
```

Only the Caddy proxy publishes ports. The database, backend and n8n admin UI
are reachable only through it.

### 5.4 Re-enter credentials once

Credentials are **not** environment variables, so nothing secret is baked into
an image or a shell history. After the first production start:

1. Open the app → **Settings → Buffer Integrations** → add each Buffer token.
2. **Settings → AI providers** → add each AI provider key.
3. Discover and import channels, exactly as locally.

If you would rather migrate the data instead of re-entering, export and
re-encrypt — see [§7](#7-backup-restore-and-migrating-credentials).

### 5.5 Media is the one thing localhost cannot fake

Buffer downloads attachments **from the public internet**, usually long after
the post is created. A `localhost` or tunnel URL will silently produce posts
with no picture and no error.

Use object storage:

```ini
MEDIA_STORAGE_BACKEND=s3
S3_BUCKET=your-bucket
S3_ENDPOINT=https://<account>.r2.cloudflarestorage.com
S3_ACCESS_KEY_ID=...
S3_SECRET_ACCESS_KEY=...
S3_PUBLIC_BASE_URL=https://pub-<account>.r2.dev
```

**Settings → Overview** flags this automatically.

---

## 6. Verifying a deployment

| Check | Where |
| --- | --- |
| Backend healthy | `GET /api/health` |
| Everything wired up | **Settings → Overview** |
| n8n reachable | Overview → *Test n8n connection* |
| Credentials decryptable | Overview → *Credential encryption: On* |
| Channels publishable | Accounts & channels → no *Will not publish* |

`GET /api/system/status` returns the same data as JSON.

---

## 7. Backup, restore and migrating credentials

### What to back up

| Item | Why |
| --- | --- |
| `CREDENTIAL_ENCRYPTION_KEY` | **Without it, every stored credential is unreadable.** |
| `INTERNAL_SERVICE_SECRET` | Both sides must match. |
| `N8N_ENCRYPTION_KEY` | Decrypts n8n's own credentials. |
| The database | Accounts, channels, history, instructions. |
| The media volume | Uploaded files. |
| `social-publishing-workflow.json` | Reproducible, but cheap to keep. |

The encryption key is the one that cannot be regenerated. Losing it does not
corrupt data — it makes stored secrets unreadable, and they must be re-entered.

### Backup (Postgres)

```bash
docker compose -f deploy/docker-compose.prod.yml exec -T db \
  pg_dump -U sma social | gzip > backup-$(date +%F).sql.gz
```

### Restore

```bash
gunzip -c backup-2026-01-01.sql.gz | \
  docker compose -f deploy/docker-compose.prod.yml exec -T db psql -U sma social
```

The schema is additive, so a dump from an older version restores into a newer
one — the backend adds anything missing on startup.

### Moving credentials between environments

Do **not** copy `credential_encrypted` columns across hosts unless you also copy
the exact matching `CREDENTIAL_ENCRYPTION_KEY`. Re-entering them in the UI is
faster, safer and auditable.

---

## 8. Security checklist before going public

- [ ] `APP_ENV=production`, `DEBUG=false`
- [ ] `API_KEY` set (a weak value is not authentication — put real auth in front)
- [ ] `CORS_ORIGINS` lists only your real origins, never `*`
- [ ] `INTERNAL_SERVICE_SECRET` set (unset means `/internal/publish` 404s)
- [ ] `CREDENTIAL_ENCRYPTION_KEY` set, generated, backed up
- [ ] Postgres ports **not** published
- [ ] n8n admin behind the proxy; `/internal/publish` reachable only internally
- [ ] TLS terminated at the proxy; HSTS enabled (already in `Caddyfile`)
- [ ] Media on object storage, not a tunnel
- [ ] `docker-compose.yml` (dev) not used in production

### What is never exposed

- Buffer tokens, AI keys, encryption keys — backend only, encrypted, masked in
  every response
- No secret in any workflow export
- No raw API key or `Authorization` header in any log line
- The browser receives only status and a masked hint

---

## 9. Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| *Not authorised* from n8n | secrets mismatch | `SMA_INTERNAL_SECRET` must equal `INTERNAL_SERVICE_SECRET` |
| *Could not reach Buffer* from n8n | `localhost` in a container | use `host.docker.internal` or the service name |
| Channel shows *Disconnected* | Buffer reports it | reconnect the profile in Buffer, then **Refresh** |
| *Will not publish* | no integration bound | link it in Accounts & channels |
| Post published with no image | media URL not public | move to object storage |
| Nothing publishable after adding an account | channels not imported | discover → select → import |
| AI key rejected after a move | encryption key changed | re-enter the key in AI settings |

---

## 10. Repository map

```
backend/                      FastAPI
  app/models/entities.py      Account, Channel, Integration
  app/services/
    buffer_client.py          Buffer GraphQL client (the only Buffer knowledge)
    credentials.py            shared encryption for every secret
    integration_service.py    onboarding, discovery, sync, publish binding
    publish_service.py        validation, idempotency, engine routing
  app/api/
    accounts.py               brand CRUD
    integrations.py           integration + discovery REST API
    channels.py               channel enable/disable, manual bind, refresh
    internal.py               secured callback for n8n
    system.py                 status overview
  tests/test_integrations.py  dynamic onboarding, offline
  tests/test_account_management.py  brand lifecycle
  tests/test_workflow_portability.py  workflow contains no secrets
  tests/live_onboarding_check.py      full flow over real HTTP

frontend/src/
  components/settings/        Overview, Buffer integrations, Accounts & channels
  services/integrations.ts    API client
  types/integration.ts        types (no credential fields by design)

deploy/
  docker-compose.prod.yml     production stack
  Caddyfile                   TLS + routing
  .env.production.example     production configuration template
  n8n/social-publishing-workflow.json   portable workflow
  n8n/build_workflow.py       regenerates the export
  n8n/verify_workflow.js      executes the workflow's JS offline

social-media-automation.workflow.json   ORIGINAL workflow, kept as a backup
```

---

## 11. Verification summary

| Suite | Command | Result |
| --- | --- | --- |
| Backend | `pytest` (backend) | 289 passed |
| Live flow | `python tests/live_onboarding_check.py` | 66 passed |
| Workflow portability | `pytest tests/test_workflow_portability.py` | 22 passed |
| Workflow JS | `node deploy/n8n/verify_workflow.js` | 34 passed |
| Frontend | `npm run build`, `npm run lint` | clean |

No test performs real social publishing or makes a paid AI call.

