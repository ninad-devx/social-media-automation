"""Provider adapter interface.

Every provider is behind this one abstraction, so campaign generation never
contains provider-specific `if` statements.

Each adapter owns its own auth, request shape, response parsing, retry policy
and error normalisation. Adapters raise `ProviderError` subclasses, which the
campaign service turns into per-channel failures.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
class ProviderError(Exception):
    """Base error. `code` is stable and safe to surface."""

    code = "PROVIDER_ERROR"
    retryable = False

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class ProviderAuthError(ProviderError):
    code = "AI_AUTH_ERROR"


class ProviderRateLimitError(ProviderError):
    code = "AI_RATE_LIMIT"
    retryable = True


class ProviderTimeoutError(ProviderError):
    code = "AI_TIMEOUT"
    retryable = True


class ProviderUnavailableError(ProviderError):
    code = "AI_UNAVAILABLE"
    retryable = True


class ProviderCreditsError(ProviderError):
    code = "AI_OUT_OF_CREDITS"


class ProviderModelUnavailableError(ProviderError):
    """The model exists but this account cannot use it (retired or gated).

    Distinct from a generic failure because the fix is different: pick a
    different model rather than retrying.
    """

    code = "AI_MODEL_UNAVAILABLE"


class ProviderBadResponseError(ProviderError):
    code = "AI_INVALID_RESPONSE"


class ProviderNotConfiguredError(ProviderError):
    code = "AI_NOT_CONFIGURED"


# --------------------------------------------------------------------------- #
# Structured output contract
# --------------------------------------------------------------------------- #
@dataclass
class GeneratedPost:
    """What every adapter must return, normalised.

    Optional fields may legitimately be None/empty. Nothing is invented.
    """

    post_text: str
    hashtags: list[str] = field(default_factory=list)
    suggested_cta: str | None = None
    media_idea: str | None = None
    notes: str | None = None
    warnings: list[str] = field(default_factory=list)
    model: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


@dataclass
class ProviderInfo:
    """Result of a credentials/model-discovery probe."""

    ok: bool
    models: list[str] = field(default_factory=list)
    detail: str | None = None
    error_code: str | None = None


# --------------------------------------------------------------------------- #
# Adapter interface
# --------------------------------------------------------------------------- #
class AIProviderAdapter(ABC):
    """Contract every provider adapter implements."""

    # Stable registry key, e.g. "openai". Used in the database, never localised.
    slug: str = ""
    display_name: str = ""
    supports_model_discovery: bool = False
    default_base_url: str | None = None
    default_model: str | None = None

    def __init__(
        self,
        api_key: str,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float = 60.0,
        organization: str | None = None,
        project: str | None = None,
    ) -> None:
        self.api_key = api_key
        self.base_url = (base_url or self.default_base_url or "").rstrip("/")
        self.model = model or self.default_model or ""
        self.timeout = timeout
        self.organization = organization
        self.project = project

    # ---- capabilities ------------------------------------------------ #
    @abstractmethod
    def validate_credentials(self) -> ProviderInfo:
        """Cheapest possible call that proves the key works."""

    def list_models(self) -> ProviderInfo:
        return ProviderInfo(
            ok=False,
            detail="This provider does not support model discovery. Enter a model name manually.",
            error_code="AI_MODEL_DISCOVERY_UNSUPPORTED",
        )

    # ---- generation -------------------------------------------------- #
    @abstractmethod
    def generate_structured(self, system_prompt: str, user_prompt: str) -> GeneratedPost:
        """Return a validated, structured post. Raises ProviderError on failure."""

    # ---- helpers ------------------------------------------------------ #
    def _require(self) -> None:
        if not self.api_key:
            raise ProviderNotConfiguredError("No API key is configured for this provider.")
        if not self.model:
            raise ProviderNotConfiguredError(
                "No model is selected for this provider. Choose a model in Settings."
            )


# --------------------------------------------------------------------------- #
# JSON extraction helper shared by all adapters
# --------------------------------------------------------------------------- #
def extract_json_object(text: str) -> dict:
    """Pull a JSON object out of a model response.

    Models sometimes wrap JSON in prose or code fences even when told not to,
    so this is deliberately forgiving. Raises ProviderBadResponseError when
    nothing usable is present.
    """
    if not text or not text.strip():
        raise ProviderBadResponseError("The provider returned an empty response.")

    candidate = text.strip()

    # Strip markdown fences.
    if candidate.startswith("```"):
        candidate = candidate.strip("`")
        if candidate.lower().startswith("json"):
            candidate = candidate[4:]
        candidate = candidate.strip()

    import json

    try:
        parsed = json.loads(candidate)
        if isinstance(parsed, dict):
            return parsed
    except (json.JSONDecodeError, ValueError):
        pass

    # Fall back to the outermost {...} block.
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start != -1 and end > start:
        try:
            parsed = json.loads(candidate[start : end + 1])
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, ValueError):
            pass

    raise ProviderBadResponseError(
        "The provider response was not valid JSON. Try again, or switch to a model "
        "that follows structured output instructions reliably."
    )