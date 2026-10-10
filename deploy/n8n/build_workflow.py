"""Generate the canonical, portable n8n workflow.

Run:  python deploy/n8n/build_workflow.py

Produces social-publishing-workflow.json with no account registry, no channel
ids and no credentials. Kept as a generator (rather than a hand-edited blob) so
the export stays reproducible and reviewable in a diff.

Design:
  webhook -> validate -> call the backend -> normalise -> respond

The workflow holds NO provider knowledge. It sends internal ids to the backend,
which resolves the credential and the external channel id itself. Adding an
account, a channel or rotating a token is therefore a UI action that never
touches this file.
"""
from __future__ import annotations

import json
from pathlib import Path

OUT = Path(__file__).with_name("social-publishing-workflow.json")

# --------------------------------------------------------------------------- #
# Code nodes
# --------------------------------------------------------------------------- #
ULID = """
// ULID: sortable, collision-resistant, no dependencies.
const CROCKFORD = '0123456789ABCDEFGHJKMNPQRSTVWXYZ';
function makeUlid(now) {
  let t = now, ts = '';
  for (let i = 9; i >= 0; i--) { ts = CROCKFORD[t % 32] + ts; t = Math.floor(t / 32); }
  let bits = '';
  while (bits.length < 80) bits += Math.floor(Math.random() * 2).toString(2);
  let rand = '';
  for (let i = 0; i < 80; i += 5) rand += CROCKFORD[parseInt(bits.slice(i, i + 5), 2)];
  return ts + rand;
}
function logEvent(fields) {
  console.log(JSON.stringify(Object.assign({ ts: new Date().toISOString() }, fields)));
}
"""

VALIDATE = (
    ULID
    + """
// ===========================================================================
// Validates the request contract ONLY.
//
// This node intentionally knows nothing about accounts, channels, brands or
// providers. It checks that the caller sent well-formed internal ids and that
// the content and schedule are usable. Whether the channel exists, whether it
// belongs to the account, whether its integration is healthy and which external
// id to use are ALL decided by the backend, which owns that data.
// ===========================================================================
const MAX_CONTENT = 5000;

return $input.all().map((item) => {
  const header = item.json.headers || {};
  const body = (item.json.body !== undefined && item.json.body !== null) ? item.json.body : item.json;

  const errors = [];
  const fail = (code, message) => errors.push({ code, message });

  // A request id may be supplied by the caller (the backend always does) so a
  // single operation stays traceable from the UI through to the provider.
  const supplied =
    (typeof body.request_id === 'string' && body.request_id.trim()) ||
    (typeof header['x-request-id'] === 'string' && header['x-request-id'].trim()) ||
    '';
  const requestId = supplied || makeUlid(Date.now());

  const accountId = typeof body.account_id === 'string' ? body.account_id.trim() : '';
  const channelId = typeof body.channel_id === 'string' ? body.channel_id.trim() : '';
  if (!accountId) fail('MISSING_ACCOUNT_ID', 'account_id is required.');
  if (!channelId) fail('MISSING_CHANNEL_ID', 'channel_id is required.');

  const content = typeof body.content === 'string' ? body.content.trim() : '';
  if (!content) fail('CONTENT_EMPTY', 'content is required and cannot be empty.');
  else if (content.length > MAX_CONTENT) fail('INVALID_REQUEST', 'content is too long.');

  const publishMode = (typeof body.publish_mode === 'string' ? body.publish_mode : 'NOW')
    .trim().toUpperCase();
  if (publishMode !== 'NOW' && publishMode !== 'SCHEDULE') {
    fail('INVALID_PUBLISH_MODE', 'publish_mode must be NOW or SCHEDULE.');
  }

  const timezone = (typeof body.timezone === 'string' && body.timezone.trim()) || 'UTC';
  const scheduledAt = body.scheduled_at === undefined ? null : body.scheduled_at;
  if (publishMode === 'SCHEDULE' && !scheduledAt) {
    fail('INVALID_SCHEDULE', 'scheduled_at is required when publish_mode is SCHEDULE.');
  }

  const media = Array.isArray(body.media) ? body.media : [];
  for (let i = 0; i < media.length; i++) {
    const m = media[i] || {};
    if (m.type !== 'image' && m.type !== 'video') {
      fail('MEDIA_INVALID', 'media[' + i + '].type must be image or video.');
    }
    if (typeof m.url !== 'string' || !/^https?:\\/\\//.test(m.url)) {
      fail('MEDIA_INVALID', 'media[' + i + '].url must be an absolute http(s) URL.');
    }
  }

  const normalized = {
    request_id: requestId,
    account_id: accountId,
    channel_id: channelId,
    content,
    publish_mode: publishMode,
    scheduled_at: scheduledAt,
    timezone,
    media: media.map((m) => ({
      type: m.type,
      url: m.url,
      ...(m.alt_text ? { alt_text: String(m.alt_text).slice(0, 1000) } : {}),
    })),
  };

  logEvent({
    event: 'request_validated',
    request_id: requestId,
    valid: errors.length === 0,
    error_count: errors.length,
    media_count: media.length,
  });

  return {
    json: {
      request_id: requestId,
      payload: normalized,
      valid: errors.length === 0,
      errors,
    },
  };
});
"""
)

BUILD_ERROR = (
    ULID
    + """
// Shaped exactly like a successful response so the caller never has to branch
// on transport details, only on `success`.
return $input.all().map((item) => {
  const j = item.json;
  const first = (j.errors && j.errors[0]) || {};
  logEvent({ event: 'request_rejected', request_id: j.request_id, code: first.code || 'INVALID_REQUEST' });
  return {
    json: {
      success: false,
      request_id: j.request_id,
      error: {
        code: first.code || 'INVALID_REQUEST',
        message: first.message || 'The request was rejected.',
      },
    },
  };
});
"""
)

NORMALIZE = (
    ULID
    + """
// ===========================================================================
// Turns whatever the backend returned into the application's response shape.
//
// The backend owns the outcome: this node only relays it. A non-2xx HTTP status
// is turned into the same envelope rather than a raw provider error, and the
// response body is never logged verbatim because it can contain internal ids.
// ===========================================================================
  const SUCCESS_STATUSES = ['published', 'scheduled'];

return $input.all().map((item) => {
  const j = item.json;
  const httpStatus = typeof j.statusCode === 'number' ? j.statusCode : 0;
  let body = j.body;

  if (typeof body === 'string') {
    try { body = JSON.parse(body); } catch (e) { body = null; }
  }
  if (!body || typeof body !== 'object') {
    body = null;
  }

  // Prefer the backend's id, then the header this workflow sent (which the
  // backend echoes), so a failure is traceable even when the body is not ours.
  const requestId =
    (body && body.request_id) ||
    ((j.headers || {})['x-request-id']) ||
    j.request_id ||
    '';


  if (body && body.success === true) {
    logEvent({
      event: 'publish_success',
      request_id: requestId,
      status: body.status,
      account_id: body.account_id,
      channel_id: body.channel_id,
    });
    return {
      json: {
        success: true,
        request_id: requestId,
        status: SUCCESS_STATUSES.indexOf(body.status) >= 0 ? body.status : 'published',
        account_id: body.account_id || null,
        account_name: body.account_name || null,
        channel_id: body.channel_id || null,
        channel_name: body.channel_name || null,
        platform: body.platform || null,
        buffer_publication_id: body.buffer_publication_id || null,
        external_link: body.external_link || null,
        scheduled_at: body.scheduled_at || null,
        timezone: body.timezone || null,
        content_length: typeof body.content_length === 'number' ? body.content_length : null,
        media_count: typeof body.media_count === 'number' ? body.media_count : 0,
      },
    };
  }

  // Failure. Prefer the backend's own code/message; fall back to the status.
  let code = 'PUBLISHING_FAILED';
  let message = 'The publishing service could not complete this request.';
  if (body && body.error && typeof body.error === 'object') {
    code = body.error.code || code;
    message = body.error.message || message;
  } else if (httpStatus === 0) {
    code = 'BUFFER_NETWORK_ERROR';
    message = 'The backend could not be reached from n8n. Check SMA_BACKEND_URL.';
  } else if (httpStatus === 401 || httpStatus === 403) {
    code = 'INTERNAL_SERVICE_UNAUTHORIZED';
    message = 'n8n was rejected by the backend. Check SMA_INTERNAL_SECRET matches INTERNAL_SERVICE_SECRET.';
  } else if (httpStatus === 429) {
    code = 'BUFFER_RATE_LIMIT';
  } else if (httpStatus >= 500) {
    code = 'BUFFER_SERVER_ERROR';
  }

  logEvent({
    event: 'publish_failure',
    request_id: requestId,
    code,
    http_status: httpStatus,
  });

  return {
    json: {
      success: false,
      request_id: requestId,
      error: { code, message },
    },
  };
});
"""
)


def code_node(name: str, js: str, position: list[int]) -> dict:
    return {
        "parameters": {"mode": "runOnceForAllItems", "jsCode": js.strip()},
        "id": name.lower().replace(" ", "-").replace("&", "and"),
        "name": name,
        "type": "n8n-nodes-base.code",
        "typeVersion": 2,
        "position": position,
    }


def build() -> dict:
    nodes = [
        {
            "parameters": {
                "httpMethod": "POST",
                "path": "social/publish",
                "responseMode": "responseNode",
                "options": {},
            },
            "id": "sma-webhook",
            "name": "01 - Webhook - Publish Request",
            "type": "n8n-nodes-base.webhook",
            "typeVersion": 2,
            "position": [-360, 0],
            "webhookId": "sma-social-publish",
        },
        code_node("02 - Validate Request Contract", VALIDATE, [-140, 0]),
        {
            "parameters": {
                "conditions": {
                    "options": {"caseSensitive": True, "leftValue": "", "typeValidation": "strict"},
                    "conditions": [
                        {
                            "id": "is-valid",
                            "leftValue": "={{ $json.valid }}",
                            "rightValue": True,
                            "operator": {"type": "boolean", "operation": "true", "singleValue": True},
                        }
                    ],
                    "combinator": "and",
                },
                "options": {},
            },
            "id": "sma-gate",
            "name": "03 - Route - Valid Request",
            "type": "n8n-nodes-base.if",
            "typeVersion": 2.2,
            "position": [60, 0],
        },
        code_node("04 - Build Validation Error Response", BUILD_ERROR, [60, 200]),
        {
            "parameters": {
                "method": "POST",
                # Both values come from n8n instance environment variables, set
                # once at deployment. Nothing environment-specific is baked in,
                # which is what makes this file importable anywhere.
                "url": "={{ ($env.SMA_BACKEND_URL || 'http://localhost:8000').replace(/\\/$/, '') + '/internal/publish' }}",
                "authentication": "none",
                "sendHeaders": True,
                "headerParameters": {
                    "parameters": [
                        {"name": "X-Internal-Secret", "value": "={{ $env.SMA_INTERNAL_SECRET }}"},
                        {"name": "Content-Type", "value": "application/json"},
                        {"name": "X-Request-Id", "value": "={{ $json.payload.request_id }}"},
                    ]
                },
                "sendBody": True,
                "contentType": "raw",
                "rawContentType": "application/json",
                "body": "={{ JSON.stringify($json.payload) }}",
                "options": {
                    "timeout": 60000,
                    "redirect": {"redirect": {"followRedirects": False}},
                    "response": {
                        "response": {
                            "fullResponse": True,
                            # Never throw: a failure has to be relayed to the
                            # caller as data, not raised as an n8n exception.
                            "neverError": True,
                            "responseFormat": "json",
                        }
                    },
                },
            },
            "id": "sma-backend-call",
            "name": "05 - Call Backend Publishing Service",
            "type": "n8n-nodes-base.httpRequest",
            "typeVersion": 4.2,
            "position": [280, 0],
            # Continue on error so node 06 can normalise it into the envelope.
            "onError": "continueRegularOutput",
            "retryOnFail": False,
            "notes": "Resolves the integration credential and the external channel id. Retries are deliberately OFF: publishing is not idempotent, so a blind retry can publish twice.",
        },
        code_node("06 - Normalize Response", NORMALIZE, [500, 0]),
        {
            "parameters": {
                "respondWith": "json",
                "responseBody": "={{ JSON.stringify($json) }}",
                "options": {"responseCode": 200},
            },
            "id": "sma-respond-ok",
            "name": "07 - Respond - Success",
            "type": "n8n-nodes-base.respondToWebhook",
            "typeVersion": 1.1,
            "position": [720, -80],
        },
        {
            "parameters": {
                "respondWith": "json",
                "responseBody": "={{ JSON.stringify($json) }}",
                "options": {"responseCode": 200},
            },
            "id": "sma-respond-err",
            "name": "08 - Respond - Error",
            "type": "n8n-nodes-base.respondToWebhook",
            "typeVersion": 1.1,
            "position": [720, 200],
        },
    ]

    return {
        "name": "SMA - Generic Social Publishing",
        "nodes": nodes,
        "connections": {
            "01 - Webhook - Publish Request": {
                "main": [[{"node": "02 - Validate Request Contract", "type": "main", "index": 0}]]
            },
            "02 - Validate Request Contract": {
                "main": [[{"node": "03 - Route - Valid Request", "type": "main", "index": 0}]]
            },
            "03 - Route - Valid Request": {
                "main": [
                    [{"node": "05 - Call Backend Publishing Service", "type": "main", "index": 0}],
                    [{"node": "04 - Build Validation Error Response", "type": "main", "index": 0}],
                ]
            },
            "04 - Build Validation Error Response": {
                "main": [[{"node": "08 - Respond - Error", "type": "main", "index": 0}]]
            },
            "05 - Call Backend Publishing Service": {
                "main": [[{"node": "06 - Normalize Response", "type": "main", "index": 0}]]
            },
            "06 - Normalize Response": {
                "main": [
                    [
                        {"node": "07 - Respond - Success", "type": "main", "index": 0},
                        {"node": "08 - Respond - Error", "type": "main", "index": 0},
                    ]
                ]
            },
        },
        "settings": {
            "executionOrder": "v1",
            "saveManualExecutions": True,
            "callerPolicy": "workflowsFromSameOwner",
        },
        "pinData": {},
        "meta": {"instanceId": "sma-portable-workflow"},
        "tags": [],
    }


if __name__ == "__main__":
    workflow = build()
    OUT.write_text(json.dumps(workflow, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes, {len(workflow['nodes'])} nodes)")
