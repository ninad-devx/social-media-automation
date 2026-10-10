"""Application error taxonomy.

`AppError` is the only exception type the API layer converts into a response.
Every message defined here is safe to render in the UI: no tracebacks, no n8n
internals, no Buffer payloads.
"""
from __future__ import annotations

from typing import Any


class ErrorCode:
    # --- request validation ------------------------------------------------
    INVALID_REQUEST = "INVALID_REQUEST"
    MISSING_ACCOUNT_ID = "MISSING_ACCOUNT_ID"
    MISSING_CHANNEL_ID = "MISSING_CHANNEL_ID"
    MISSING_CONTENT = "MISSING_CONTENT"
    CONTENT_EMPTY = "CONTENT_EMPTY"
    INVALID_PUBLISH_MODE = "INVALID_PUBLISH_MODE"
    INVALID_TIMEZONE = "INVALID_TIMEZONE"
    INVALID_SCHEDULE = "INVALID_SCHEDULE"
    SCHEDULE_IN_PAST = "SCHEDULE_IN_PAST"
    SCHEDULE_TOO_FAR = "SCHEDULE_TOO_FAR"
    MEDIA_INVALID = "MEDIA_INVALID"
    MEDIA_NOT_ACCESSIBLE = "MEDIA_NOT_ACCESSIBLE"
    BUFFER_POST_FAILED = "BUFFER_POST_FAILED"

    # --- configuration resolution ------------------------------------------
    ACCOUNT_NOT_FOUND = "ACCOUNT_NOT_FOUND"
    ACCOUNT_INACTIVE = "ACCOUNT_INACTIVE"
    CHANNEL_NOT_FOUND = "CHANNEL_NOT_FOUND"
    CHANNEL_NOT_BELONG_TO_ACCOUNT = "CHANNEL_NOT_BELONG_TO_ACCOUNT"
    CHANNEL_INACTIVE = "CHANNEL_INACTIVE"

    # --- publishing engine -------------------------------------------------
    PUBLISHING_UNAVAILABLE = "PUBLISHING_UNAVAILABLE"
    PUBLISHING_TIMEOUT = "PUBLISHING_TIMEOUT"
    PUBLISHING_FAILED = "PUBLISHING_FAILED"
    BUFFER_AUTH_ERROR = "BUFFER_AUTH_ERROR"
    BUFFER_RATE_LIMIT = "BUFFER_RATE_LIMIT"
    BUFFER_BAD_REQUEST = "BUFFER_BAD_REQUEST"
    BUFFER_SERVER_ERROR = "BUFFER_SERVER_ERROR"
    BUFFER_NETWORK_ERROR = "BUFFER_NETWORK_ERROR"
    BUFFER_ORGANIZATION_NOT_FOUND = "BUFFER_ORGANIZATION_NOT_FOUND"
    BUFFER_BAD_RESPONSE = "BUFFER_BAD_RESPONSE"
    BUFFER_NO_ORGANIZATIONS = "BUFFER_NO_ORGANIZATIONS"
    # ---- integrations (credentials, discovery, sync) ------------------
    INTEGRATION_NOT_FOUND = "INTEGRATION_NOT_FOUND"
    INTEGRATION_DUPLICATE = "INTEGRATION_DUPLICATE"
    INTEGRATION_DISABLED = "INTEGRATION_DISABLED"
    INTEGRATION_NOT_TESTED = "INTEGRATION_NOT_TESTED"
    INTEGRATION_CREDENTIAL_REQUIRED = "INTEGRATION_CREDENTIAL_REQUIRED"
    INTEGRATION_CREDENTIAL_UNREADABLE = "INTEGRATION_CREDENTIAL_UNREADABLE"
    INTEGRATION_REAUTH_REQUIRED = "INTEGRATION_REAUTH_REQUIRED"
    INTEGRATION_NO_CHANNELS_SELECTED = "INTEGRATION_NO_CHANNELS_SELECTED"
    # ---- channel binding -------------------------------------------------
    CHANNEL_NOT_BOUND = "CHANNEL_NOT_BOUND"
    CHANNEL_REAUTH_REQUIRED = "CHANNEL_REAUTH_REQUIRED"
    CHANNEL_LOCKED = "CHANNEL_LOCKED"
    CHANNEL_ALREADY_ASSIGNED = "CHANNEL_ALREADY_ASSIGNED"
    # ---- accounts (brands) ------------------------------------------------
    ACCOUNT_ID_EXHAUSTED = "ACCOUNT_ID_EXHAUSTED"
    ACCOUNT_STILL_HAS_CHANNELS = "ACCOUNT_STILL_HAS_CHANNELS"
    # ---- AI providers / generation ------------------------------------
    AI_NOT_CONFIGURED = "AI_NOT_CONFIGURED"
    AI_NO_PROVIDER = "AI_NO_PROVIDER"
    AI_PROVIDER_DISABLED = "AI_PROVIDER_DISABLED"
    AI_PROVIDER_UNKNOWN = "AI_PROVIDER_UNKNOWN"
    AI_PROVIDER_KEY_REQUIRED = "AI_PROVIDER_KEY_REQUIRED"
    AI_PROVIDER_BASE_URL_REQUIRED = "AI_PROVIDER_BASE_URL_REQUIRED"
    AI_ENCRYPTION_NOT_CONFIGURED = "AI_ENCRYPTION_NOT_CONFIGURED"
    AI_AUTH_ERROR = "AI_AUTH_ERROR"
    AI_RATE_LIMIT = "AI_RATE_LIMIT"
    AI_TIMEOUT = "AI_TIMEOUT"
    AI_UNAVAILABLE = "AI_UNAVAILABLE"
    AI_OUT_OF_CREDITS = "AI_OUT_OF_CREDITS"
    AI_MODEL_UNAVAILABLE = "AI_MODEL_UNAVAILABLE"
    AI_INVALID_RESPONSE = "AI_INVALID_RESPONSE"
    AI_QUOTA_EXCEEDED = "AI_QUOTA_EXCEEDED"
    AI_NO_CHANNELS_SELECTED = "AI_NO_CHANNELS_SELECTED"
    AI_TOPIC_REQUIRED = "AI_TOPIC_REQUIRED"
    AI_GENERATION_FAILED = "AI_GENERATION_FAILED"

    DUPLICATE_SUBMISSION = "DUPLICATE_SUBMISSION"
    UNAUTHORIZED = "UNAUTHORIZED"
    NOT_FOUND = "NOT_FOUND"
    INTERNAL_ERROR = "INTERNAL_ERROR"


# User-facing copy. Keyed by code. Unknown codes fall back to a safe default.
USER_MESSAGES: dict[str, str] = {
    ErrorCode.INVALID_REQUEST: "We couldn't read that request. Please check the form and try again.",
    ErrorCode.MISSING_ACCOUNT_ID: "Please choose an account.",
    ErrorCode.MISSING_CHANNEL_ID: "Please choose a channel.",
    ErrorCode.MISSING_CONTENT: "Please write something to publish.",
    ErrorCode.CONTENT_EMPTY: "Your post is empty. Please write something first.",
    ErrorCode.INVALID_PUBLISH_MODE: "Please choose either Publish now or Schedule.",
    ErrorCode.INVALID_TIMEZONE: "Please choose a valid timezone.",
    ErrorCode.INVALID_SCHEDULE: "Please choose a future date and time.",
    ErrorCode.SCHEDULE_IN_PAST: "Please choose a future date and time.",
    ErrorCode.SCHEDULE_TOO_FAR: "That date is too far in the future. Please choose an earlier date.",
    ErrorCode.MEDIA_INVALID: "One of your media attachments is not valid. Please check the links.",
    ErrorCode.MEDIA_NOT_ACCESSIBLE: (
        "Buffer could not download your image. The media link has to be publicly "
        "reachable - a localhost or tunnel address changes or is blocked. Point "
        "PUBLIC_MEDIA_BASE_URL at stable public storage and upload again."
    ),
    ErrorCode.BUFFER_POST_FAILED: "The post was created but could not be delivered. Please check Buffer and retry.",
    ErrorCode.ACCOUNT_NOT_FOUND: "That account no longer exists.",
    ErrorCode.ACCOUNT_INACTIVE: "That account is currently unavailable.",
    ErrorCode.CHANNEL_NOT_FOUND: "This social channel is currently unavailable.",
    ErrorCode.CHANNEL_NOT_BELONG_TO_ACCOUNT: "That channel doesn't belong to the selected account.",
    ErrorCode.CHANNEL_INACTIVE: "This social channel is currently unavailable.",
    ErrorCode.PUBLISHING_UNAVAILABLE: "Publishing is temporarily unavailable. Please try again in a moment.",
    ErrorCode.PUBLISHING_TIMEOUT: "Publishing took longer than expected and we couldn't confirm the result. Check Buffer before retrying so you don't post twice.",
    ErrorCode.PUBLISHING_FAILED: "We couldn't publish your post right now. Please try again.",
    ErrorCode.BUFFER_AUTH_ERROR: "The publishing connection needs to be re-authorised. Please contact an administrator.",
    ErrorCode.BUFFER_RATE_LIMIT: "We've hit the publishing limit for now. Please try again shortly.",
    ErrorCode.BUFFER_BAD_REQUEST: "That post couldn't be sent to this channel. Please review the content and media.",
    ErrorCode.BUFFER_SERVER_ERROR: "The social platform is having trouble right now. Please try again.",
    ErrorCode.BUFFER_NETWORK_ERROR: "We couldn't reach the publishing service. Please try again.",
    ErrorCode.BUFFER_ORGANIZATION_NOT_FOUND: "That Buffer workspace is not reachable with this access token.",
    ErrorCode.BUFFER_BAD_RESPONSE: "Buffer returned an unexpected response. Please try again.",
    ErrorCode.BUFFER_NO_ORGANIZATIONS: "This token cannot see any Buffer workspace. Check its permissions.",
    ErrorCode.INTEGRATION_NOT_FOUND: "That integration no longer exists.",
    ErrorCode.INTEGRATION_DUPLICATE: (
        "An integration with this name already exists. Rename it or edit the existing one."
    ),
    ErrorCode.INTEGRATION_DISABLED: "This integration is disabled. Enable it in Settings to publish.",
    ErrorCode.INTEGRATION_NOT_TESTED: "Test this connection in Settings before using it.",
    ErrorCode.INTEGRATION_CREDENTIAL_REQUIRED: "Enter an access token for this integration.",
    ErrorCode.INTEGRATION_CREDENTIAL_UNREADABLE: (
        "The stored credential could not be decrypted. Re-enter the token in Settings."
    ),
    ErrorCode.INTEGRATION_REAUTH_REQUIRED: "This connection needs to be re-authorised. Reconnect it in Settings.",
    ErrorCode.INTEGRATION_NO_CHANNELS_SELECTED: "Select at least one channel to import.",
    ErrorCode.CHANNEL_NOT_BOUND: (
        "This channel isn't linked to a publishing connection yet. Connect it in Settings."
    ),
    ErrorCode.CHANNEL_REAUTH_REQUIRED: "Buffer reports this channel as disconnected. Reconnect it in Buffer.",
    ErrorCode.CHANNEL_LOCKED: "Buffer has this channel locked, so it cannot receive posts.",
    ErrorCode.CHANNEL_ALREADY_ASSIGNED: (
        "That social profile is already linked to a different account. Remove it there first."
    ),
    ErrorCode.ACCOUNT_ID_EXHAUSTED: "Could not generate a unique id for that brand. Enter one manually.",
    ErrorCode.ACCOUNT_STILL_HAS_CHANNELS: (
        "This brand still has channels. Remove or disable them before deleting it."
    ),
    ErrorCode.DUPLICATE_SUBMISSION: "This post was already submitted. Please wait before trying again.",
    ErrorCode.AI_NOT_CONFIGURED: "The AI provider is not fully configured. Check Settings.",
    ErrorCode.AI_NO_PROVIDER: "No AI provider is configured. Add one in Settings to generate content.",
    ErrorCode.AI_PROVIDER_DISABLED: "That AI provider is disabled. Enable it in Settings.",
    ErrorCode.AI_PROVIDER_UNKNOWN: "That AI provider is not supported.",
    ErrorCode.AI_PROVIDER_KEY_REQUIRED: "Enter an API key for this provider.",
    ErrorCode.AI_PROVIDER_BASE_URL_REQUIRED: "Enter the base URL for this OpenAI-compatible provider.",
    ErrorCode.AI_ENCRYPTION_NOT_CONFIGURED: (
        "Provider API keys cannot be stored yet. Add AI_ENCRYPTION_KEY to backend/.env and restart."
    ),
    ErrorCode.AI_AUTH_ERROR: "The AI provider rejected this API key. Check it in Settings.",
    ErrorCode.AI_RATE_LIMIT: "The AI provider is rate limiting requests. Wait a moment and retry.",
    ErrorCode.AI_TIMEOUT: "The AI provider took too long to respond. Please retry.",
    ErrorCode.AI_UNAVAILABLE: "The AI provider is having trouble right now. Please retry.",
    ErrorCode.AI_OUT_OF_CREDITS: "This AI provider account is out of credits.",
    ErrorCode.AI_MODEL_UNAVAILABLE: "That model is not available for this account. Choose another model.",
    ErrorCode.AI_INVALID_RESPONSE: "The AI provider returned an unexpected response. Try regenerating.",
    ErrorCode.AI_QUOTA_EXCEEDED: "You've reached the generation limit for this hour. Please try again later.",
    ErrorCode.AI_NO_CHANNELS_SELECTED: "Select at least one channel to generate content for.",
    ErrorCode.AI_TOPIC_REQUIRED: "Enter a campaign topic before generating.",
    ErrorCode.AI_GENERATION_FAILED: "We couldn't generate content for this channel. You can retry it.",
    ErrorCode.UNAUTHORIZED: "You don't have access to this action.",
    ErrorCode.NOT_FOUND: "We couldn't find what you were looking for.",
    ErrorCode.INTERNAL_ERROR: "Something went wrong on our side. Please try again.",
}

# n8n / Buffer error code -> our code. Anything unmapped becomes PUBLISHING_FAILED.
UPSTREAM_CODE_MAP: dict[str, str] = {
    "MISSING_ACCOUNT_ID": ErrorCode.MISSING_ACCOUNT_ID,
    "MISSING_CHANNEL_ID": ErrorCode.MISSING_CHANNEL_ID,
    "MISSING_CONTENT": ErrorCode.MISSING_CONTENT,
    "CONTENT_EMPTY": ErrorCode.CONTENT_EMPTY,
    "INVALID_REQUEST": ErrorCode.INVALID_REQUEST,
    "INVALID_PUBLISH_MODE": ErrorCode.INVALID_PUBLISH_MODE,
    "INVALID_TIMEZONE": ErrorCode.INVALID_TIMEZONE,
    "INVALID_SCHEDULE": ErrorCode.INVALID_SCHEDULE,
    "SCHEDULE_IN_PAST": ErrorCode.SCHEDULE_IN_PAST,
    "SCHEDULE_TOO_FAR": ErrorCode.SCHEDULE_TOO_FAR,
    "MEDIA_INVALID": ErrorCode.MEDIA_INVALID,
    "MEDIA_NOT_ACCESSIBLE": ErrorCode.MEDIA_NOT_ACCESSIBLE,
    "BUFFER_POST_FAILED": ErrorCode.BUFFER_POST_FAILED,
    "ACCOUNT_NOT_FOUND": ErrorCode.ACCOUNT_NOT_FOUND,
    "ACCOUNT_INACTIVE": ErrorCode.ACCOUNT_INACTIVE,
    "CHANNEL_NOT_FOUND": ErrorCode.CHANNEL_NOT_FOUND,
    "CHANNEL_NOT_BELONG_TO_ACCOUNT": ErrorCode.CHANNEL_NOT_BELONG_TO_ACCOUNT,
    "CHANNEL_INACTIVE": ErrorCode.CHANNEL_INACTIVE,
    "BUFFER_PROFILE_NOT_CONFIGURED": ErrorCode.CHANNEL_INACTIVE,
    "BUFFER_AUTH_ERROR": ErrorCode.BUFFER_AUTH_ERROR,
    "BUFFER_RATE_LIMIT": ErrorCode.BUFFER_RATE_LIMIT,
    "BUFFER_BAD_REQUEST": ErrorCode.BUFFER_BAD_REQUEST,
    "BUFFER_SERVER_ERROR": ErrorCode.BUFFER_SERVER_ERROR,
    "BUFFER_NETWORK_ERROR": ErrorCode.BUFFER_NETWORK_ERROR,
    "BUFFER_ORGANIZATION_NOT_FOUND": ErrorCode.BUFFER_ORGANIZATION_NOT_FOUND,
    "BUFFER_BAD_RESPONSE": ErrorCode.BUFFER_BAD_RESPONSE,
    "BUFFER_NO_ORGANIZATIONS": ErrorCode.BUFFER_NO_ORGANIZATIONS,
    "BUFFER_TIMEOUT": ErrorCode.PUBLISHING_TIMEOUT,
    "CHANNEL_NOT_BOUND": ErrorCode.CHANNEL_NOT_BOUND,
    "INTEGRATION_DISABLED": ErrorCode.INTEGRATION_DISABLED,
    # transport-level codes produced by our own adapter
    "PUBLISHING_UNAVAILABLE": ErrorCode.PUBLISHING_UNAVAILABLE,
    "PUBLISHING_TIMEOUT": ErrorCode.PUBLISHING_TIMEOUT,
    "PUBLISHING_FAILED": ErrorCode.PUBLISHING_FAILED,
}

# Pydantic request field -> app error code, so schema failures stay specific
# instead of collapsing into a generic INVALID_REQUEST.
FIELD_ERROR_CODES: dict[str, str] = {
    "account_id": ErrorCode.MISSING_ACCOUNT_ID,
    "channel_id": ErrorCode.MISSING_CHANNEL_ID,
    "content": ErrorCode.MISSING_CONTENT,
    "publish_mode": ErrorCode.INVALID_PUBLISH_MODE,
    "scheduled_at": ErrorCode.INVALID_SCHEDULE,
    "timezone": ErrorCode.INVALID_TIMEZONE,
    "media": ErrorCode.MEDIA_INVALID,
    "api_key": ErrorCode.INTEGRATION_CREDENTIAL_REQUIRED,
    "credential": ErrorCode.INTEGRATION_CREDENTIAL_UNREADABLE,
    "integration_id": ErrorCode.INTEGRATION_NOT_FOUND,
    "external_channel_ids": ErrorCode.INTEGRATION_NO_CHANNELS_SELECTED,
}


def code_for_field(field: str | None) -> str:
    if not field:
        return ErrorCode.INVALID_REQUEST
    head = field.split(".", 1)[0]
    return FIELD_ERROR_CODES.get(head, ErrorCode.INVALID_REQUEST)


class AppError(Exception):
    """Raised anywhere in the app; rendered as a normalized JSON response."""

    def __init__(
        self,
        code: str,
        http_status: int = 400,
        *,
        field: str | None = None,
        details: list[dict[str, Any]] | None = None,
        log_context: dict[str, Any] | None = None,
    ) -> None:
        self.code = code
        self.http_status = http_status
        self.field = field
        self.details = details or []
        self.log_context = log_context or {}
        super().__init__(USER_MESSAGES.get(code, USER_MESSAGES[ErrorCode.INTERNAL_ERROR]))

    @property
    def user_message(self) -> str:
        return USER_MESSAGES.get(self.code, USER_MESSAGES[ErrorCode.INTERNAL_ERROR])


def normalize_upstream_code(code: str | None) -> str:
    if not code:
        return ErrorCode.PUBLISHING_FAILED
    return UPSTREAM_CODE_MAP.get(code, ErrorCode.PUBLISHING_FAILED)