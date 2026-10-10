# Social Media Automation

A social media management application. React UI, FastAPI backend, n8n
publishing engine, Buffer.

```
React (UI)  ->  FastAPI (source of truth)  ->  n8n (optional)  ->  Buffer  ->  Social platforms
```

The backend owns accounts, channels, integrations and every credential. Buffer
tokens are encrypted at rest in the backend and are **never** placed in n8n, in
a workflow export, or in the browser. The n8n workflow is generic: it receives
internal ids and asks the backend which credential and which external channel id
to use.

> **New here?** Accounts, channels and credentials are configured from
> **Settings**, not from code. See
> [`deploy/README.md`](deploy/README.md) for the migration guide and
> [`deploy/n8n/README.md`](deploy/n8n/README.md) for the one-time n8n setup.

The boundaries are strict and deliberate:

| Layer          | Owns                                                            |
| -------------- | --------------------------------------------------------------- |
| **React**      | User interface only. Never sees a Buffer key or the n8n URL.    |
| **FastAPI**    | Accounts, channels, validation, idempotency, error normalisation. |
| **n8n**        | Buffer configuration, timezone conversion, retry, publishing.    |
| **Buffer**     | The actual social publishing infrastructure.                     |

**FastAPI never calls Buffer. React never talks to n8n.**

---

## Quick start

### 1. Prerequisites

- Node.js 20+
- Python 3.11+ (3.12/3.13 work; **3.14 does not** — `pydantic-core` has no prebuilt wheel)
- n8n running with the `social media automation` workflow **active**

### 2. Backend

```powershell
cd backend

py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

Copy-Item .env.example .env      # then edit N8N_PUBLISH_WEBHOOK_URL
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
```

Check it: <http://localhost:8000/api/health> → `{"status":"ok", ...}`
API docs: <http://localhost:8000/docs>

### 3. Frontend

```powershell
cd frontend
npm install
Copy-Item .env.example .env
npm run dev
```

Open **<http://localhost:3000>**

### 4. Run the tests

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest        # 289 tests, no real posts created
```

The suite mocks the n8n engine, so it is safe to run at any time.

The n8n workflow can also be verified without an n8n instance:

```powershell
node deploy/n8n/verify_workflow.js          # runs every Code node offline
```

---

## Configuration

Every environment variable the project reads is listed, with placeholders and
explanations, in **[`.env.example`](.env.example)**. The two files you actually
copy are:

| File | Template | Copy with |
| --- | --- | --- |
| Backend | `backend/.env.example` | `Copy-Item backend/.env.example backend/.env` |
| Frontend | `frontend/.env.example` | `Copy-Item frontend/.env.example frontend/.env` |

Both real `.env` files are gitignored. Buffer tokens and AI provider keys are
**not** environment variables — they are entered in the UI and encrypted in the
database (see [What is deliberately not configurable here](#what-is-deliberately-not-configurable-here)).

### Backend (`backend/.env`)

| Variable                  | Purpose                                                        |
| ------------------------- | -------------------------------------------------------------- |
| `N8N_PUBLISH_WEBHOOK_URL` | **Required.** Production webhook URL of the n8n workflow.      |
| `N8N_WEBHOOK_SECRET`      | Optional shared secret, sent as `X-N8N-Secret`.                |
| `N8N_TIMEOUT_SECONDS`     | Raise if your n8n instance is slow. Default `60`.              |
| `DATABASE_URL`            | SQLite by default; point at PostgreSQL for production.         |
| `CORS_ORIGINS`            | Comma-separated. Never use `*`.                                 |
| `API_KEY`                 | Set to require `X-API-Key` on every `/api` route.               |
| `DEFAULT_TIMEZONE`        | Default schedule timezone.                                      |

### Frontend (`frontend/.env`)

| Variable                | Purpose                        |
| ----------------------- | ------------------------------ |
| `VITE_API_BASE_URL`     | Backend base URL.              |
| `VITE_DEFAULT_TIMEZONE` | Pre-selected timezone.         |

### What is deliberately **not** configurable here

- **Buffer access tokens** — entered in Settings, encrypted in the backend, and
  never returned to the browser (only a masked hint).
- **Buffer channel ids** — resolved server-side from `channels.external_channel_id`.
- **The n8n webhook URL** — backend only, never shipped to the browser.

---

## Adding an account or channel

**Do it from the UI. No code, no environment edit, no workflow edit.**

1. **Settings → Buffer Integrations → Add**
   Name it and paste a Buffer access token, then *Save and test connection*.
   The backend calls Buffer's `account { organizations }` query, so the status
   shown is a real result. One token may reach several workspaces.
2. **Discover channels.** A live `channels(input: { organizationId })` call.
3. **Select the channels you want → choose the brand → Import.**

They immediately appear in Quick Publisher, AI Automation, the calendar and
publishing history. Repeat for every additional account.

To add a **brand**, create the account record, then import channels into it.
Brands are rows, not code.

<details>
<summary>Where this used to live (kept for reference)</summary>

Previously this required editing the `CONFIG` object in n8n node
`04 - Load Account Configuration` (including each `buffer_profile_id`) **and**
adding matching rows to `app/database/seed.py`. Both steps are gone: the
original workflow is preserved as a backup and still functions if you keep
using it.

</details>


**No frontend change is required.** The Quick Publish channel selector calls
`GET /api/accounts/{id}/channels` whenever the account changes.

The identifiers (`company`, `company_instagram`, …) must match exactly in both
places, because n8n is the component that resolves them to Buffer profiles.

---

## API

| Method | Path                                  | Purpose                            |
| ------ | ------------------------------------- | ---------------------------------- |
| GET    | `/api/health`                         | Liveness + engine configuration    |
| GET    | `/api/accounts`                       | Active accounts with channel counts |
| GET    | `/api/accounts/{id}`                  | One account + its channels         |
| GET    | `/api/accounts/{id}/channels`         | Channels for one account           |
| GET    | `/api/channels`                       | All channels across accounts       |
| POST   | `/api/publish`                        | Publish or schedule a post         |
| GET    | `/api/publish/history`                | Publishing history                 |

### `POST /api/publish`

```json
{
  "account_id": "company",
  "channel_id": "company_instagram",
  "content": "Hello from our social media system!",
  "media": [],
  "publish_mode": "NOW",
  "scheduled_at": null,
  "timezone": "Asia/Dhaka"
}
```

Success:

```json
{
  "success": true,
  "request_id": "req_01M4EAQ...",
  "status": "published",
  "account_id": "company",
  "channel_id": "company_instagram",
  "platform": "instagram",
  "buffer_publication_id": "6ac7d52c...",
  "external_link": "https://facebook.com/…",
  "n8n_request_id": "pub_01M4EAQ..."
}
```

Failure — always this shape, never a traceback:

```json
{
  "success": false,
  "request_id": "req_01M4EAQ...",
  "error": { "code": "CONTENT_EMPTY", "message": "Your post is empty. Please write something first." },
  "errors": [{ "code": "CONTENT_EMPTY", "message": "…", "field": "content" }]
}
```

### Idempotency

Send an `Idempotency-Key` header. Replaying the same key returns the original
result instead of publishing twice. The React client sends one automatically
per attempt.

```powershell
curl.exe -X POST http://localhost:8000/api/publish `
  -H "Content-Type: application/json" `
  -H "Idempotency-Key: my-unique-key" `
  --data-binary "@post.json"
```

---

## Testing publishing manually

**Publish now (Facebook, text-only):**

```powershell
$body = '{
  "account_id":"company","channel_id":"company_facebook",
  "content":"Hello from our social media system!","media":[],
  "publish_mode":"NOW","scheduled_at":null,"timezone":"Asia/Dhaka"}'
Set-Content -Path post.json -Value $body -NoNewline
curl.exe -X POST http://localhost:8000/api/publish -H "Content-Type: application/json" --data-binary "@post.json"
```

**Instagram requires media.** A text-only Instagram post is rejected — by
Buffer itself — so always attach an image URL:

```json
"media": [{ "type": "image", "url": "https://…/image.jpg", "alt_text": "…" }]
```

The UI enforces this before the request leaves the browser.

**Scheduling** returns immediately; Buffer owns the delivery time. The
backend never sleeps waiting for a scheduled post.

---

## Timezone handling

The UI sends a **naive local datetime plus an explicit IANA timezone**. The
conversion to UTC happens once, inside n8n, using `Intl` with proper
DST handling (gaps rejected, overlaps resolved to the first occurrence). A `Z`
is never appended blindly.

All date maths in the frontend uses **Luxon**; no browser-local zone is ever
assumed.

---

## Duplicate-post safety

- **Frontend** disables the button and guards re-entrant calls with a ref.
- **Backend** honours `Idempotency-Key` and replays the stored response.
- **n8n** retries only HTTP 5xx, at most once. It deliberately does **not**
  retry transport timeouts, because Buffer has no idempotency key and a retry
  after an ambiguous timeout could duplicate a post.

If you see a timeout, check Buffer before retrying.

---

## Desktop media upload

Pick or drop a file in Quick Publish. It uploads to **our backend**, which
stores it and hands Buffer a URL it can download.

```
desktop file → FastAPI (stores) → public URL → n8n → Buffer downloads it
```

### The one thing you must configure

**Buffer runs in the cloud, so it downloads media from the public internet.**
A `localhost` URL can never work. Set a public address:

```bash
# terminal 1 — expose the API
ngrok http 8000
# -> https://<subdomain>.ngrok-free.dev

# terminal 2 — point the backend at it
PUBLIC_MEDIA_BASE_URL=https://<subdomain>.ngrok-free.dev/api/media
PREVIEW_MEDIA_BASE_URL=http://localhost:8000/api/media
```

- `PUBLIC_MEDIA_BASE_URL` → what **Buffer** downloads.
- `PREVIEW_MEDIA_BASE_URL` → what your **browser** loads for thumbnails.

They are deliberately different. A tunnel serves an interstitial HTML page to
browser requests while serving the real file to server-side clients, so
previews must bypass the tunnel or thumbnails appear broken.

If `PUBLIC_MEDIA_BASE_URL` is empty, uploads still work but the UI shows a
warning and Buffer will reject the post.

For production, implement the `MediaStorage` interface in
`backend/app/services/storage.py` against S3 or Cloudflare R2 and set
`PUBLIC_MEDIA_BASE_URL` to that bucket. Nothing else changes.

### Endpoints

| Method | Path                 | Purpose                          |
| ------ | -------------------- | -------------------------------- |
| POST   | `/api/media/upload`  | Upload a file (multipart)        |
| GET    | `/api/media`         | List uploads                     |
| GET    | `/api/media/{id}`    | Serve a file                     |
| DELETE | `/api/media/{id}`    | Remove a file                    |

**Security measures applied**

- Content type detected by **magic bytes**, not the filename or the browser's
  claimed MIME type — a PHP file renamed `.png` is rejected.
- Stored names are **generated**, so a filename like `../../../evil.png` cannot
  influence the path.
- `X-Content-Type-Options: nosniff` and a sandboxing CSP on every response.
- 25 MB ceiling enforced while streaming, so oversized files never fill memory.
- Path traversal and unknown ids return 404.

---

## Known issue in this environment

The n8n container is currently showing task-runner instability:

```
Task (…) rejected by Runner with reason "Offer expired - not accepted within validity window"
Runner (…) took too long to acknowledge acceptance of task
Database ping failed: Database connection timed out
```

This makes publishing slow (10–35s) and occasionally fails outright. It is a
pre-existing n8n/host problem, not caused by this application. Mitigations:

- `N8N_TIMEOUT_SECONDS` is raised to 60.
- Failures surface as a clean, user-safe error rather than a hang.

Recommended fixes on the n8n side: give the container more CPU/memory, move
the database off SQLite, and set `N8N_RUNNERS_MODE=external` with a separate
task-runner launcher.

---

## AI campaigns

Generate a draft for every selected channel at once, review them, then publish only what
you approve. **Nothing is ever published automatically.**

### 1. Encrypt provider keys first

AI provider keys are encrypted at rest with Fernet. Generate a key:

```bash
cd backend
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Add it to `backend/.env`:

```
AI_ENCRYPTION_KEY=<the value you just generated>
```

Restart the backend. Until this is set, AI Settings will refuse to save any key.

> This is **not** your provider key. It only encrypts and decrypts the keys you enter in
> the UI. If you lose it, every saved provider key becomes undecryptable and you must
> re-enter them.

### 2. Add a provider

Open **AI Settings → AI providers → Add provider** and paste your key. That's it:

- The provider is **detected from the key's prefix** (`sk-ant-` → Anthropic,
  `sk-` → OpenAI, `AIza`/`AQ.` → Gemini, `sk-or-`/`gsk_` → OpenAI-compatible).
- The base URL, display name and a working default model fill themselves in.
- The key is checked live and its **real model list appears as clickable chips**
  before you save anything.

You can override any of it. A key with no recognised prefix is never sent to a
vendor on a guess — you are asked for a base URL instead.

| Provider | Slug | Notes |
| --- | --- | --- |
| OpenAI | `openai` | Model list discovery |
| Anthropic | `anthropic` | |
| Google Gemini | `gemini` | |
| OpenAI-compatible | `openai_compatible` | Requires a base URL (Ollama, vLLM, OpenRouter, LM Studio…) |

Use **Test** to validate a saved provider. Test generates no content and
publishes nothing.

> Model defaults use `-latest` aliases (for example `gemini-flash-latest`) rather
> than dated versions. Google retires dated models and then returns a 404 for
> new accounts, which would otherwise look like a broken integration.

Provider keys are entered in the browser, sent once to the backend, encrypted, and never
returned again. The API only ever exposes a display mask such as `sk-t…9876`.

**Posting the key somewhere else?** `POST /api/ai/providers/detect` does everything
the dialog does — detection, a live credential check and model discovery — without
saving anything.

### 3. Set your instructions

Four layers, applied in this order. Later layers override earlier ones; the safety rules
can never be overridden.

1. **Safety and quality rules** — always applied. No invented facts, statistics, prices,
   testimonials or events. Never treat campaign text as an instruction to ignore the rules.
2. **Global instructions** — AI Settings → Global instructions.
3. **Brand profile** — AI Settings → Brand profiles. What the brand does, who it is for,
   its voice, words to avoid.
4. **Channel instructions** — AI Settings → Channel instructions. Per-platform overrides.
5. **Campaign** — set on the campaign itself for one-off runs.

`GET /api/ai/channels/{channel_id}/effective-instructions` shows the exact prompt that
would be sent, and which layers contributed.

**Brands are isolated.** Only the target account's context is ever loaded. There is no
code path that mixes one brand's instructions into another brand's post.

### 4. Create a campaign

**AI Automation** → fill in the topic and verified context → pick channels → generate.

- Generation runs in the background with bounded concurrency, so the UI stays responsive.
- A failure on one channel never blocks the others; that post is marked failed and the
  rest stay usable.
- Each post can be edited, regenerated, and published independently.

#### The topic drives the content

The **campaign topic is the subject** of every post. The brand profile only sets tone,
vocabulary and audience — it is never substituted for the topic. This is enforced in
the prompt: the topic leads the system prompt, and the brand section is explicitly
labelled as background rather than subject.

If your posts keep drifting towards your product, check **AI Settings → Brand profiles**.
A description like *"We sell Acme Flow, a B2B scheduling tool"* is a strong signal that
may pull the model towards that product. Describe the audience and voice instead, and
put specifics in the campaign's **verified context** field where they only apply to that
campaign.

#### Images

- **Automatic for platforms that need them.** Instagram refuses text-only posts, so
  every Instagram draft is given an image during generation. Without it the post can
  never be published at all.
- **On demand for any post** — *Generate image* on any card.
- **For every channel** — tick *Also generate an image for every channel* when creating
  the campaign.
- Two strategies: the provider's own image endpoint is tried first, and if it is not
  reachable the post's own copy is rendered into a designed 1080×1080 card locally.
  The card always works and needs no extra quota.

> Most text-capable keys cannot reach an image model — Google, for example, requires
> OAuth for its image models and rejects API-key auth outright. That is why the local
> renderer exists rather than images simply failing.

### 5. Review and publish

Every draft appears as its own preview card in a responsive grid — one per channel —
showing the platform, the caption, the image, the hashtags and a character count
against that platform's limit. Anything over the limit is flagged in red.

Each card can be:

- **Edited**. Your edit is what gets published, and the original is kept.
- **Undo edit** — step back one version of your own edits.
- **Discard edits** — go back to the AI draft.
- **Regenerated** — a fresh draft. If you edited the post, your edit is preserved and
  regeneration is skipped, so work is never silently lost.
- **Regenerated over edit** — explicitly replace your edit.
- **Given a new image** — regenerate or add a visual at any time.
- **Published** individually, or selected several and published in bulk.

Publishing goes through the same `PublishService` and the same n8n workflow as Quick
Publish, with the same idempotency protection. A post that has already been published or
scheduled is locked against further edits and cannot be published twice.

---

## Project structure

```
.
├── backend/
│   ├── app/
│   │   ├── main.py                 # app factory, middleware, error normalisation
│   │   ├── api/                    # accounts, channels, publishing, health, ai/*
│   │   ├── schemas/                # Pydantic contracts
│   │   ├── services/               # n8n adapter, publish orchestration, errors
│   │   │   └── ai/                 # adapters + key detection (detect.py)
│   │   ├── models/                 # SQLAlchemy entities
│   │   ├── database/               # engine, session, seed
│   │   └── core/                   # config + production startup validation
│   ├── tests/                      # 289 tests
│   ├── Dockerfile                  # binds ${PORT:-8000} (Render assigns PORT)
│   └── requirements.txt
├── frontend/
│   └── src/
│       ├── components/
│       │   ├── ui/                 # Button, Card, Field, Select, Badge…
│       │   ├── layout/             # AppShell, Sidebar
│       │   └── quick-publish/      # selectors, editor, media, schedule, preview
│       ├── pages/                  # QuickPublish, Dashboard, Posts, Calendar, AI…
│       ├── hooks/                  # useAccounts, useChannels, usePublish
│       ├── services/               # api client, accounts, publishing, ai
│       ├── lib/                    # platform metadata, formatting
│       ├── styles/                 # tokens, global, components, layout, pages, ai
│       └── types/
├── deploy/
│   ├── README.md                   # Docker Compose production stack
│   ├── docker-compose.prod.yml
│   ├── Caddyfile
│   ├── .env.production.example
│   └── n8n/
│       ├── social-publishing-workflow.json   # the importable, secret-free workflow
│       ├── Dockerfile                      # n8n as a Render web service
│       ├── build_workflow.py               # regenerates the export
│       └── verify_workflow.js               # runs the workflow's JS offline
├── docker-compose.yml              # local development stack
├── .env.example                    # every variable the project reads, placeholders only
├── DEPLOYMENT.md                   # GitHub -> Render -> Supabase
└── README.md
```

---

## Deployment

Local development and production differ **only in configuration** — no
application code changes.

- **[`DEPLOYMENT.md`](DEPLOYMENT.md)** — GitHub → Render → Supabase, step by
  step: the three services, required environment variables, the production
  startup gate, media storage, rollback and troubleshooting.
- **[`.env.example`](.env.example)** — every environment variable the project
  reads, with placeholders only and no secrets.
- **[`deploy/README.md`](deploy/README.md)** — the Docker Compose production
  stack.
- **[`deploy/n8n/README.md`](deploy/n8n/README.md)** — the n8n workflow:
  import, configure, activate, recover.

| Service | Source | Type |
| --- | --- | --- |
| Backend | `backend/` | Render Web Service (Docker) |
| Frontend | `frontend/` | Render Static Site |
| n8n | `deploy/n8n/Dockerfile` | Render Web Service (Docker) |
| Database | — | Supabase PostgreSQL |

In production the backend validates its own configuration on boot and **refuses
to start** rather than run against an ephemeral SQLite file. It also disables
`/docs` and `/openapi.json`, which sit outside the API-key gate.

---

## Extending later

The seams are already in place:

- **AI content generation** — implement a service that calls
  `POST /api/publish` with the same payload. The publishing path does not care
  whether content came from a human or a model. `PublishRequest` already
  accepts `source`, `campaign_id`, `approval_status`, `created_by`,
  `ai_generated` and `content_id`.
- **Approval workflow** — the `approval_status` field is reserved.
- **Media library** — `MediaPicker` already emits the shape the API expects;
  swap the URL input for an upload control.
- **Per-platform content** — `platforms.ts` centralises limits and identity.
- **Authentication** — set `API_KEY` to turn on header auth, or replace
  `require_api_key` in `app/api/deps.py` with real user auth. Routes already
  depend on it centrally. In production the backend refuses to start without
  `API_KEY`.