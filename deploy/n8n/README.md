# The generic n8n workflow

[`social-publishing-workflow.json`](social-publishing-workflow.json) is the
single workflow that serves **every** account and channel.

## What it is

Eight nodes:

| # | Node | Role |
| --- | --- | --- |
| 01 | Webhook | `POST /webhook/social/publish` |
| 02 | Validate Request Contract | shape checks only |
| 03 | Route — Valid Request | gate |
| 04 | Build Validation Error Response | error envelope |
| 05 | Call Backend Publishing Service | **the only outbound call** |
| 06 | Normalize Response | consistent shape |
| 07 / 08 | Respond — Success / Error | reply |

## What it deliberately does not contain

- Buffer access tokens
- Buffer channel or organisation ids
- an account or brand list
- a branch per brand or channel
- n8n credential references
- any environment-specific host

`backend/tests/test_workflow_portability.py` fails the build if any of these
reappear.

## Setup

1. **Import from File** → `social-publishing-workflow.json`
2. Set the instance variables:

   | Variable | Value |
   | --- | --- |
   | `SMA_BACKEND_URL` | Backend base URL **as seen from the n8n runtime** |
   | `SMA_INTERNAL_SECRET` | Same value as the backend's `INTERNAL_SERVICE_SECRET` |

   > **Self-hosted n8n** reads environment variables prefixed with `N8N_`. Use
   > `N8N_SMA_BACKEND_URL` / `N8N_SMA_INTERNAL_SECRET`, or define them in the
   > shell before starting n8n.

3. **Activate.**

That is the entire installation. Afterwards, accounts, channels, credentials and
AI providers are managed from the app.

## Configuration

| Variable | Required | Description |
| --- | --- | --- |
| `SMA_BACKEND_URL` | yes | Backend base URL **as seen from the n8n runtime** |
| `SMA_INTERNAL_SECRET` | yes | Must match the backend's `INTERNAL_SERVICE_SECRET` |

---

## Hosting on Render

`Dockerfile` in this directory deploys the official n8n image with Render's
`$PORT` wired up. Full service-by-service instructions are in
[`../../DEPLOYMENT.md`](../../DEPLOYMENT.md#7-render--n8n); this section is the
n8n-specific summary.

| Render setting | Value |
| --- | --- |
| Root Directory | `.` |
| Docker Context | `.` |
| Dockerfile Path | `deploy/n8n/Dockerfile` |

### Persistence

Render's free web services have an **ephemeral** filesystem:
`/home/node/.n8n` is wiped on every redeploy and every restart. Choose one:

- **PostgreSQL** (recommended, free tier works) — set `DB_TYPE=postgresdb` and
  the `DB_POSTGRESDB_*` variables.
- **Render Persistent Disk** mounted at `/home/node/.n8n` (paid).

Neither set → every workflow and credential is lost on each deploy.

### N8N_ENCRYPTION_KEY — keep it stable

n8n encrypts its credential store with this key. If it is unset, n8n generates
a **new** one on every boot and every previously stored credential becomes
undecryptable — the symptom is "credential not found" after a restart.

Set it once, record it in a password manager, and never rotate it casually.
Restoring a backed-up n8n database **requires** the same key; there is no
recovery path without it.

### Environment variables

```
N8N_ENCRYPTION_KEY=<generated>
SMA_BACKEND_URL=https://<backend>.onrender.com
SMA_INTERNAL_SECRET=<same as backend INTERNAL_SERVICE_SECRET>
N8N_HOST=<n8n>.onrender.com
WEBHOOK_URL=https://<n8n>.onrender.com/
N8N_EDITOR_BASE_URL=https://<n8n>.onrender.com/
N8N_PROTOCOL=https
N8N_SECURE_COOKIE=false
GENERIC_TIMEZONE=Asia/Dhaka
TZ=Asia/Dhaka
N8N_DIAGNOSTICS_ENABLED=false
```

`N8N_SECURE_COOKIE=false` is required because Render terminates TLS and proxies
over HTTP; with the flag on you cannot log in to the editor.

---

## Recovery

| Situation | What to do |
| --- | --- |
| Workflows gone after a redeploy | No database or disk was attached. Attach one, then re-import this JSON and activate. |
| "credential not found" after a restart | `N8N_ENCRYPTION_KEY` changed or was unset. Restore the original key and restart — otherwise the credentials are unrecoverable. |
| Workflow inactive | Open it and toggle **Active**. Nothing dispatches while inactive. |
| "Not authorised" from the workflow | `SMA_INTERNAL_SECRET` ≠ backend `INTERNAL_SERVICE_SECRET`. |
| "Could not reach Buffer" | `SMA_BACKEND_URL` is a loopback address. Inside a container, `localhost` is n8n itself. Use the backend's public URL. |

### `localhost` means the caller

The most common deployment mistake. `localhost` inside the n8n container is
*n8n's own loopback*, not your PC and not the backend.

| n8n runs | `SMA_BACKEND_URL` / `N8N_BACKEND_BASE_URL` |
| --- | --- |
| same host, no Docker | `http://localhost:8000` |
| Docker on your PC | `http://host.docker.internal:8000` |
| same compose network | `http://backend:8000` |
| hosted, separate host | `https://api.your-domain.com` |

---

## Regenerating

```bash
python deploy/n8n/build_workflow.py
```

Edit `build_workflow.py`, never the JSON — the JSON is generated output, and
`test_workflow_portability.py` verifies the two match.

## Verifying without n8n

```bash
node deploy/n8n/verify_workflow.js
```

Executes every Code node against real payloads — valid requests, malformed
requests, provider failures, an unreachable backend — and asserts the
behaviour. No n8n instance and no network access needed.

## Design notes

**Retries are off.** Publishing is not idempotent: a blind retry can publish the
same post twice. The backend records the request id and guards with an
idempotency record instead.

**The request id is preserved.** If the caller supplies `request_id` (the
backend always does) it is reused; otherwise a ULID is generated. It travels to
the backend, into Buffer's `source` field, and back out in the response, so one
publish is traceable end to end.

**Validation knows nothing about accounts.** The workflow checks that
`account_id` and `channel_id` are present and well-formed, then asks the
backend. Existence, ownership, integration health and the external channel id
are all decided by the backend, which owns that data. That is what lets a new
brand be onboarded without touching this file.

**Errors are data, not exceptions.** Node 05 uses `neverError` and
`onError: continueRegularOutput`, so an HTTP failure is normalised into the
same envelope as a domain failure instead of becoming an n8n stack trace.

## The original workflow

`../../social-media-automation.workflow.local.json` is your original 22-node
workflow. It is **gitignored and never committed**, because it contains real
Buffer organization and channel ids.

The committed `../../social-media-automation.workflow.json` is the same file
with those ids and the n8n credential id replaced by `REPLACE_WITH_...`
placeholders. It is a structural reference only — **import
`social-publishing-workflow.json` instead.** The original still works on your
own n8n instance and is still selected by `PUBLISH_ENGINE=n8n` if you prefer it.
