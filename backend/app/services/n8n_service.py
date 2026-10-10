"""n8n publishing engine adapter.

This is the ONLY place that knows the n8n webhook exists. React never sees it;
the backend never talks to Buffer directly.

Contract note (deliberate adapter behaviour):
The existing n8n workflow generates its own request id internally (node
"02 - Generate Request ID") and does not accept one from the caller. So the
backend generates a request id for its own tracing and separately surfaces
n8n's id as `n8n_request_id`. Both end up in the logs, which makes a single
publishing request traceable end to end without modifying the working workflow.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx

from app.core.config import Settings
from app.core.logging import get_logger
from app.services.errors import ErrorCode, normalize_upstream_code

logger = get_logger("app.n8n")


@dataclass(slots=True)
class N8nResult:
    """Normalized view of whatever the publishing engine returned."""

    success: bool
    upstream_code: str | None = None
    upstream_message: str | None = None
    status: str | None = None                 # published | scheduled
    buffer_publication_id: str | None = None
    external_link: str | None = None
    scheduled_at: datetime | None = None
    timezone: str | None = None
    n8n_request_id: str | None = None
    http_status: int | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


class N8nService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._url = settings.n8n_publish_webhook_url
        self._timeout = settings.n8n_timeout_seconds

    # ------------------------------------------------------------------ #
    def is_configured(self) -> bool:
        return bool(self._url)

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self._settings.n8n_webhook_secret:
            headers["X-N8N-Secret"] = self._settings.n8n_webhook_secret
        return headers

    # ------------------------------------------------------------------ #
    def publish(self, payload: dict[str, Any]) -> N8nResult:
        """POST to the n8n webhook and normalize the outcome.

        Never raises for upstream failures; transport problems come back as a
        result with `success=False` so the caller can log uniformly.
        """
        if not self.is_configured():
            return N8nResult(success=False, upstream_code=ErrorCode.PUBLISHING_UNAVAILABLE)

        try:
            with httpx.Client(
                timeout=self._timeout,
                verify=self._settings.n8n_verify_tls,
                follow_redirects=False,
            ) as client:
                response = client.post(self._url, json=payload, headers=self._headers())
        except httpx.TimeoutException:
            logger.warning("n8n request timed out")
            return N8nResult(success=False, upstream_code=ErrorCode.PUBLISHING_TIMEOUT)
        except httpx.HTTPError as exc:
            # Connection refused, DNS failure, TLS error, etc.
            logger.warning("n8n unreachable: %s", type(exc).__name__)
            return N8nResult(success=False, upstream_code=ErrorCode.BUFFER_NETWORK_ERROR)

        http_status = response.status_code

        try:
            body = response.json()
        except (json.JSONDecodeError, ValueError):
            body = {}

        if not isinstance(body, dict):
            body = {"raw": str(body)[:2000]}

        # n8n answers 200 with a normalized body on success and on domain errors.
        if http_status >= 500 and not body.get("error"):
            return N8nResult(success=False, upstream_code=ErrorCode.BUFFER_SERVER_ERROR, http_status=http_status, raw=body)

        if not body.get("success"):
            err = body.get("error") or {}
            upstream_code = err.get("code") if isinstance(err, dict) else None
            upstream_message = err.get("message") if isinstance(err, dict) else None

            if upstream_code is None:
                if http_status == 429:
                    upstream_code = ErrorCode.BUFFER_RATE_LIMIT
                elif http_status in (401, 403):
                    upstream_code = ErrorCode.BUFFER_AUTH_ERROR
                elif http_status >= 500:
                    upstream_code = ErrorCode.BUFFER_SERVER_ERROR
                elif http_status >= 400:
                    upstream_code = ErrorCode.PUBLISHING_FAILED
                else:
                    upstream_code = ErrorCode.PUBLISHING_FAILED

            return N8nResult(
                success=False,
                upstream_code=upstream_code,
                upstream_message=upstream_message,
                n8n_request_id=body.get("request_id"),
                http_status=http_status,
                raw=body,
            )

        scheduled_at = None
        if body.get("scheduled_at"):
            try:
                scheduled_at = datetime.fromisoformat(str(body["scheduled_at"]))
            except ValueError:
                scheduled_at = None

        return N8nResult(
            success=True,
            upstream_code=None,
            status=body.get("status"),
            buffer_publication_id=body.get("buffer_publication_id"),
            external_link=body.get("external_link"),
            scheduled_at=scheduled_at,
            timezone=body.get("timezone"),
            n8n_request_id=body.get("request_id"),
            http_status=http_status,
            raw=body,
        )

    # ------------------------------------------------------------------ #
    def health(self) -> dict[str, Any]:
        """Lightweight reachability probe used by /api/health."""
        if not self.is_configured():
            return {"configured": False, "reachable": False}
        try:
            with httpx.Client(timeout=5.0, verify=self._settings.n8n_verify_tls) as client:
                # n8n answers 405/404 for GET on a POST webhook, which still
                # proves the host is up and reachable.
                response = client.get(self._url, headers=self._headers())
            return {"configured": True, "reachable": response.status_code < 500, "status_code": response.status_code}
        except httpx.HTTPError as exc:
            return {"configured": True, "reachable": False, "error": type(exc).__name__}


def build_n8n_payload(request: dict[str, Any]) -> dict[str, Any]:
    """Translate our request into the n8n workflow's contract.

    The field names are identical; this function exists so that any future
    divergence is handled in exactly one place.
    """
    return {
        "account_id": request["account_id"],
        "channel_id": request["channel_id"],
        "content": request["content"],
        "media": [
            {"type": m["type"], "url": m["url"], **({"alt_text": m["alt_text"]} if m.get("alt_text") else {})}
            for m in request.get("media", [])
        ],
        "publish_mode": request["publish_mode"],
        "scheduled_at": request.get("scheduled_at"),
        "timezone": request["timezone"],
    }


def mapped_error_code(result: N8nResult) -> str:
    return normalize_upstream_code(result.upstream_code)