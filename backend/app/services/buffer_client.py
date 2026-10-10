"""Buffer GraphQL API client.

Everything the application knows about Buffer's wire format lives here.

Why GraphQL: Buffer's supported API is a single GraphQL endpoint
(``POST https://api.buffer.com``) authenticated with a bearer token. The legacy
``api.bufferapp.com/1/`` REST API is deprecated and is not used. The query and
mutation text below follow Buffer's published schema
(https://developers.buffer.com/reference.html), so no endpoint is invented.

Two operations are supported by the official API and are therefore implemented
for real:

* ``account { organizations { id name } }`` - one API key can see several
  workspaces, which is what lets a single credential serve multiple brands.
* ``channels(input: { organizationId }) { ... }`` - automatic channel discovery.

Neither this module nor its caller ever logs the bearer token.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx

from app.core.config import Settings
from app.core.logging import get_logger

logger = get_logger("app.buffer")

# --------------------------------------------------------------------------- #
# Operations
# --------------------------------------------------------------------------- #

#: Read-only probe. Any success proves the token is valid and authenticated.
ORG_QUERY = """
query GetAccount {
  account {
    id
    organizations {
      id
      name
    }
  }
}
"""

#: Field set chosen to drive the UI: identity, platform, connection health and
#: what the channel can actually do. All of these exist in Buffer's schema.
CHANNELS_QUERY = """
query GetChannels($organizationId: OrganizationId!) {
  channels(input: { organizationId: $organizationId }) {
    id
    name
    displayName
    descriptor
    service
    type
    avatar
    externalLink
    isDisconnected
    isLocked
    isQueuePaused
    timezone
  }
}
"""

#: Identical to the mutation the existing working workflow uses, including the
#: `error` field. Buffer can return HTTP 200 and still fail the post later (for
#: example when it cannot download the media), so omitting this would report a
#: false success.
CREATE_POST_MUTATION = """
mutation CreatePost($input: CreatePostInput!) {
  createPost(input: $input) {
    __typename
    ... on PostActionSuccess {
      post {
        id
        text
        status
        dueAt
        channelId
        channelService
        externalLink
        isCustomScheduled
        via
        error {
          message
        }
      }
    }
    ... on MutationError {
      message
    }
  }
}
"""


# --------------------------------------------------------------------------- #
# Scheduling
# --------------------------------------------------------------------------- #
def _due_at_epoch(scheduled_local: str | None, timezone: str | None) -> int:
    """Convert a local wall-clock time to the Unix epoch Buffer expects.

    Buffer's `DateTime` scalar is epoch seconds, not an ISO string. Sending
    "2026-10-11T02:08:00" is rejected with
    `DateTime cannot represent ...`.

    The value arrives as a naive local time plus the IANA zone the operator
    chose, so it is localised here before being turned into an absolute
    instant. Naive datetimes are interpreted in `timezone`, defaulting to UTC
    rather than the machine's local zone, which on a container is never the
    operator's zone.
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo

    raw = (scheduled_local or "").strip()
    if not raw:
        raise BufferApiError("BUFFER_BAD_REQUEST", "A schedule time is required.")

    text = raw.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise BufferApiError(
            "BUFFER_BAD_REQUEST", f"Could not read the schedule time '{raw}'."
        ) from exc

    if parsed.tzinfo is None:
        try:
            parsed = parsed.replace(tzinfo=ZoneInfo(timezone or "UTC"))
        except Exception:
            parsed = parsed.replace(tzinfo=ZoneInfo("UTC"))

    return int(parsed.timestamp())


# --------------------------------------------------------------------------- #
# Results
# --------------------------------------------------------------------------- #
class BufferApiError(RuntimeError):
    """A safe, already-normalised Buffer failure.

    `message` is safe to show a user: it is built here and never contains the
    Authorization header or the token itself.
    """

    def __init__(self, code: str, message: str, *, http_status: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status


@dataclass(slots=True)
class BufferOrganization:
    id: str
    name: str


@dataclass(slots=True)
class BufferChannel:
    """A channel exactly as Buffer describes it. No local fields."""

    id: str
    service: str
    name: str
    display_name: str | None = None
    descriptor: str | None = None
    channel_type: str | None = None
    avatar: str | None = None
    external_link: str | None = None
    timezone: str | None = None
    disconnected: bool = False
    locked: bool = False
    queue_paused: bool = False

    @property
    def usable(self) -> bool:
        """Buffer refuses posts to disconnected or locked channels."""
        return not (self.disconnected or self.locked)


@dataclass(slots=True)
class BufferPostResult:
    success: bool
    post_id: str | None = None
    status: str | None = None
    due_at: str | None = None
    external_link: str | None = None
    error_message: str | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


# --------------------------------------------------------------------------- #
# Client
# --------------------------------------------------------------------------- #
class BufferClient:
    """Thin, synchronous GraphQL client for one API token."""

    def __init__(self, api_key: str, settings: Settings, *, client: httpx.Client | None = None) -> None:
        self._api_key = api_key
        self._settings = settings
        self._client = client

    # ------------------------------------------------------------------ #
    def _http(self) -> httpx.Client:
        if self._client is not None:
            return self._client
        return httpx.Client(
            timeout=self._settings.buffer_timeout_seconds,
            verify=self._settings.n8n_verify_tls,
            follow_redirects=False,
        )

    def _close(self, client: httpx.Client) -> None:
        if self._client is None:
            client.close()

    def _headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Accept": "application/json",
            # Buffer's documented scheme. The token lives only in this header.
            "Authorization": f"Bearer {self._api_key}",
        }

    # ------------------------------------------------------------------ #
    def execute(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        """Run one GraphQL operation and return `data`.

        Raises BufferApiError with a safe message on any failure. The token is
        never included in the exception, the log line or the returned body.
        """
        body = {"query": query, "variables": variables or {}}
        client = self._http()
        try:
            response = client.post(
                self._settings.buffer_api_base_url,
                json=body,
                headers=self._headers(),
            )
        except httpx.TimeoutException as exc:
            raise BufferApiError("BUFFER_TIMEOUT", "Buffer did not respond in time.") from exc
        except httpx.HTTPError as exc:
            # Deliberately logs the exception type only - the request object can
            # carry headers.
            logger.warning("buffer unreachable: %s", type(exc).__name__)
            raise BufferApiError(
                "BUFFER_NETWORK_ERROR", "Could not reach Buffer. Check network connectivity."
            ) from exc
        finally:
            self._close(client)

        status = response.status_code

        if status in (401, 403):
            raise BufferApiError(
                "BUFFER_AUTH_ERROR",
                "Buffer rejected this access token. It may be revoked or lack the required scope.",
                http_status=status,
            )
        if status == 429:
            raise BufferApiError(
                "BUFFER_RATE_LIMIT",
                "Buffer rate limit reached. Wait a few minutes and try again.",
                http_status=status,
            )
        if status >= 500:
            raise BufferApiError(
                "BUFFER_SERVER_ERROR", "Buffer returned a server error.", http_status=status
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise BufferApiError(
                "BUFFER_BAD_RESPONSE", "Buffer returned a response that was not valid JSON.", http_status=status
            ) from exc

        if not isinstance(payload, dict):
            raise BufferApiError("BUFFER_BAD_RESPONSE", "Unexpected response from Buffer.", http_status=status)

        # GraphQL reports request-level problems in `errors`.
        errors = payload.get("errors")
        if errors:
            first = errors[0] if isinstance(errors, list) and errors else {}
            message = ""
            if isinstance(first, dict):
                message = str(first.get("message") or "")
            lowered = message.lower()
            if status in (401, 403) or "unauthor" in lowered or "auth" in lowered:
                raise BufferApiError("BUFFER_AUTH_ERROR", "Buffer rejected this access token.", http_status=status)
            if "not found" in lowered and "organization" in lowered:
                raise BufferApiError(
                    "BUFFER_ORGANIZATION_NOT_FOUND",
                    "That Buffer workspace is not accessible with this token.",
                    http_status=status,
                )
            raise BufferApiError(
                "BUFFER_BAD_REQUEST",
                message[:300] or "Buffer rejected the request.",
                http_status=status,
            )

        data = payload.get("data")
        if not isinstance(data, dict):
            raise BufferApiError(
                "BUFFER_BAD_RESPONSE", "Buffer returned no data.", http_status=status
            )
        return data

    # ------------------------------------------------------------------ #
    # operations
    # ------------------------------------------------------------------ #
    def test_connection(self) -> list[BufferOrganization]:
        """Verify the token and return every workspace it can see."""
        data = self.execute(ORG_QUERY)
        account = data.get("account")
        if not isinstance(account, dict):
            raise BufferApiError("BUFFER_BAD_RESPONSE", "Buffer returned no account information.")
        organizations = account.get("organizations") or []
        return [
            BufferOrganization(id=str(o.get("id")), name=str(o.get("name") or "Unnamed workspace"))
            for o in organizations
            if isinstance(o, dict) and o.get("id")
        ]

    def list_channels(self, organization_id: str) -> list[BufferChannel]:
        """Automatic channel discovery for one workspace."""
        data = self.execute(CHANNELS_QUERY, {"organizationId": organization_id})
        channels = data.get("channels")
        if not isinstance(channels, list):
            raise BufferApiError("BUFFER_BAD_RESPONSE", "Buffer returned no channel list.")
        return [_map_channel(c) for c in channels if isinstance(c, dict) and c.get("id")]

    def get_channel(self, organization_id: str, channel_id: str) -> BufferChannel | None:
        """Fetch one channel. Returns None when Buffer does not know it."""
        data = self.execute(CHANNELS_QUERY, {"organizationId": organization_id})
        channels = data.get("channels") or []
        for raw in channels:
            if isinstance(raw, dict) and str(raw.get("id")) == channel_id:
                return _map_channel(raw)
        return None

    def create_post(
        self,
        *,
        channel_id: str,
        text: str,
        media: list[dict[str, Any]] | None = None,
        scheduled_local: str | None = None,
        timezone: str | None = None,
        source_suffix: str | None = None,
        platform_metadata: dict[str, Any] | None = None,
    ) -> BufferPostResult:
        """Publish or schedule one post.

        Mirrors the payload the existing n8n workflow already sends, so turning
        on direct publishing does not change what Buffer receives.
        """
        now_mode = scheduled_local is None

        assets: list[dict[str, Any]] = []
        for item in media or []:
            entry: dict[str, Any] = {}
            asset_type = item.get("type")
            if asset_type not in ("image", "video"):
                continue
            entry[asset_type] = {"url": item["url"]}
            # ImageMetadataInput.altText is non-null, so only send it when set.
            if asset_type == "image" and item.get("alt_text"):
                entry["image"]["metadata"] = {"altText": item["alt_text"]}
            assets.append(entry)

        post_input: dict[str, Any] = {
            "text": text,
            "channelId": channel_id,
            "schedulingType": "automatic",
            "mode": "shareNow" if now_mode else "customScheduled",
            "source": f"app:{source_suffix}" if source_suffix else "app",
        }
        if not now_mode:
            post_input["dueAt"] = _due_at_epoch(scheduled_local, timezone)
        if assets:
            post_input["assets"] = assets

        if platform_metadata:
            # Instagram switches to a carousel once more than one asset is
            # attached, exactly as the existing workflow does.
            meta = {k: dict(v) for k, v in platform_metadata.items()}
            for platform, value in meta.items():
                if len(assets) > 1 and platform == "instagram" and isinstance(value, dict):
                    value["type"] = "carousel"
            post_input["metadata"] = meta

        variables = {"input": post_input}
        data = self.execute(CREATE_POST_MUTATION, variables)

        payload = data.get("createPost")
        if not isinstance(payload, dict):
            raise BufferApiError("BUFFER_BAD_RESPONSE", "Buffer returned no result for the post.")

        typename = payload.get("__typename")

        if typename == "MutationError":
            raise BufferApiError(
                "BUFFER_POST_FAILED", str(payload.get("message") or "Buffer rejected the post.")[:300]
            )

        if typename != "PostActionSuccess":
            raise BufferApiError(
                "BUFFER_BAD_RESPONSE", f"Buffer returned an unexpected result: {typename or 'unknown'}."
            )

        post = payload.get("post")
        if not isinstance(post, dict):
            raise BufferApiError("BUFFER_BAD_RESPONSE", "Buffer returned no post object.")

        # Buffer can accept the request and still fail it afterwards. Reporting
        # success here would be a lie.
        error = post.get("error")
        if isinstance(error, dict) and error.get("message"):
            return BufferPostResult(
                success=False,
                error_message=str(error["message"])[:300],
                raw=post,
            )

        return BufferPostResult(
            success=True,
            post_id=str(post.get("id")) if post.get("id") else None,
            status=str(post.get("status")) if post.get("status") else None,
            due_at=str(post.get("dueAt")) if post.get("dueAt") else None,
            external_link=str(post.get("externalLink")) if post.get("externalLink") else None,
            raw=post,
        )


def _map_channel(raw: dict[str, Any]) -> BufferChannel:
    display_name = raw.get("displayName")
    return BufferChannel(
        id=str(raw.get("id")),
        service=str(raw.get("service") or "").lower(),
        name=str(raw.get("name") or ""),
        display_name=str(display_name) if display_name else None,
        descriptor=str(raw.get("descriptor")) if raw.get("descriptor") else None,
        channel_type=str(raw.get("type")) if raw.get("type") else None,
        avatar=str(raw.get("avatar")) if raw.get("avatar") else None,
        external_link=str(raw.get("externalLink")) if raw.get("externalLink") else None,
        timezone=str(raw.get("timezone")) if raw.get("timezone") else None,
        disconnected=bool(raw.get("isDisconnected")),
        locked=bool(raw.get("isLocked")),
        queue_paused=bool(raw.get("isQueuePaused")),
    )


# --------------------------------------------------------------------------- #
# Platform metadata
# --------------------------------------------------------------------------- #
#: Service-specific metadata Buffer's createPost mutation requires. Kept in one
#: place so adding a platform is a data change, not a code branch. Mirrors the
#: `platform_metadata` block the n8n workflow already used.
PLATFORM_METADATA: dict[str, dict[str, Any]] = {
    "instagram": {"type": "post", "shouldShareToFeed": True},
    "facebook": {"type": "post"},
}

#: Platforms this engine will publish to.
SUPPORTED_PLATFORMS: tuple[str, ...] = (
    "instagram",
    "facebook",
    "linkedin",
    "twitter",
    "threads",
)


def platform_metadata_for(platform: str) -> dict[str, Any] | None:
    key = platform.strip().lower()
    if key not in PLATFORM_METADATA:
        return None
    return {key: dict(PLATFORM_METADATA[key])}
