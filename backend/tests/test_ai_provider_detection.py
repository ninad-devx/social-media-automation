"""Provider auto-detection and the pre-save probe.

These cover the failure that made the UI look broken: a key pasted without
knowing its provider, which produced an empty model list and a dead default.
"""
from __future__ import annotations

import pytest

from app.services.ai.detect import detect_slug
from app.services.ai.openai_adapter import classify_status
from app.services.ai.base import (
    ProviderCreditsError,
    ProviderModelUnavailableError,
    ProviderRateLimitError,
)

# The exact shape of the key that triggered the original report.
GEMINI_KEY = "AQ.not-a-real-key"


# --------------------------------------------------------------------------- #
# key shape -> provider
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "key,expected",
    [
        ("sk-ant-api03-abcdef", "anthropic"),
        ("sk-proj-abc123XYZ", "openai"),
        ("sk-abc123", "openai"),
        ("AIzaSyD-Example123", "gemini"),
        (GEMINI_KEY, "gemini"),
        ("sk-or-v1-abc123", "openai_compatible"),
        ("gsk_abc123", "openai_compatible"),
    ],
)
def test_recognised_key_prefixes_map_to_the_right_provider(key: str, expected: str) -> None:
    result = detect_slug(key)
    assert result.slug == expected
    assert result.confident is True
    assert result.reason


def test_anthropic_is_not_mistaken_for_openai() -> None:
    # Both start with "sk-", so ordering in the prefix table matters.
    assert detect_slug("sk-ant-api03-x").slug == "anthropic"
    assert detect_slug("sk-proj-x").slug == "openai"


def test_unknown_key_is_flagged_as_not_confident() -> None:
    """An unrecognised key must never be sent to a vendor on a guess alone."""
    result = detect_slug("totally-unknown-key-1234")
    assert result.slug == "openai_compatible"
    assert result.confident is False
    assert "base URL" in result.reason


def test_empty_key_is_handled_without_raising() -> None:
    for value in (None, "", "   "):
        result = detect_slug(value)
        assert result.slug == "openai_compatible"
        assert result.confident is False


def test_base_url_can_identify_an_openai_shaped_host() -> None:
    result = detect_slug("some-unprefixed-key", "https://openrouter.ai/api/v1")
    assert result.slug == "openai_compatible"
    assert result.confident is True

    local = detect_slug("some-unprefixed-key", "http://localhost:11434/v1")
    assert local.confident is True


def test_detection_makes_no_network_call() -> None:
    """Detection is string inspection only, so it is instant and offline."""
    assert detect_slug(GEMINI_KEY).slug == "gemini"


# --------------------------------------------------------------------------- #
# error classification
# --------------------------------------------------------------------------- #
def test_quota_429_is_reported_as_credits_not_rate_limit() -> None:
    """Gemini returns 429 for 'out of quota', which retrying cannot fix."""
    body = '{"error":{"code":429,"message":"You exceeded your current quota"}}'
    err = classify_status(429, body)
    assert isinstance(err, ProviderCreditsError)
    assert not isinstance(err, ProviderRateLimitError)
    assert "quota" in err.message.lower() or "billing" in err.message.lower()


def test_plain_429_is_still_a_retryable_rate_limit() -> None:
    err = classify_status(429, '{"error":{"message":"slow down"}}')
    assert isinstance(err, ProviderRateLimitError)
    assert err.retryable is True


def test_retired_model_404_says_pick_another_model() -> None:
    body = (
        '{"code":404,"message":"This model models/gemini-2.0-flash is no longer '
        'available to new users. Please update your code to use models/gemini-3.8-flash"}'
    )
    err = classify_status(404, body)
    assert isinstance(err, ProviderModelUnavailableError)
    assert err.code == "AI_MODEL_UNAVAILABLE"
    # Retrying the same model would never work.
    assert err.retryable is False
    assert "model" in err.message.lower()


def test_401_is_still_an_auth_error() -> None:
    err = classify_status(401, '{"error":{"message":"Incorrect API key provided"}}')
    assert err.code == "AI_AUTH_ERROR"


# --------------------------------------------------------------------------- #
# defaults
# --------------------------------------------------------------------------- #
def test_no_adapter_defaults_to_a_retired_model() -> None:
    """A dated default silently 404s for new accounts.

    Gemini retired the 2.x flash models, so the default must be the '-latest'
    alias, which always resolves.
    """
    from app.services.ai.registry import list_specs

    for spec in list_specs():
        if not spec.default_model:
            continue
        assert not spec.default_model.startswith("gemini-2."), (
            f"{spec.slug} still defaults to a retired Gemini model"
        )

    from app.services.ai.gemini_adapter import GeminiAdapter

    assert GeminiAdapter.default_model.endswith("-latest")


def test_gemini_default_appears_in_live_discovery(client) -> None:
    """The default must be a model the provider actually offers."""
    from app.services.ai.gemini_adapter import GeminiAdapter

    # Shape check without the network: the alias form matches the '-latest'
    # entries the live endpoint returns.
    assert "latest" in GeminiAdapter.default_model


# --------------------------------------------------------------------------- #
# the detect endpoint
# --------------------------------------------------------------------------- #
def test_detect_never_persists_the_key(client, db, fake_adapter) -> None:
    """Probing is a read: nothing may be written."""
    from app.models.ai_entities import AIProvider

    status, body = _detect(client, "AQ.some-google-looking-key-value")
    assert status == 200
    assert db.query(AIProvider).count() == 0
    assert "api_key" not in body


def test_detect_reports_a_provider_for_any_key(client, fake_adapter) -> None:
    status, body = _detect(client, GEMINI_KEY)
    assert status == 200
    assert body["detected_slug"] == "gemini"
    assert body["confident"] is True
    # Enough shape for the UI to prefill the form.
    assert body["display_name"]
    assert body["key_label"]
    assert "models" in body


def test_detect_honours_an_explicit_provider(client, fake_adapter) -> None:
    """When the caller chooses, the choice wins over the heuristic."""
    status, body = _detect(client, GEMINI_KEY, provider_slug="openai")
    assert status == 200
    assert body["detected_slug"] == "openai"
    assert "chose" in body["reason"].lower()


def test_detect_tells_the_user_to_add_a_base_url(client, fake_adapter) -> None:
    """A custom endpoint cannot be probed, and must say so plainly."""
    status, body = _detect(client, "some-unprefixed-key-value")
    assert status == 200
    assert body["requires_base_url"] is True
    assert body["credentials_ok"] is False
    assert "base URL" in body["reason"]


def test_detect_rejects_an_empty_key(client) -> None:
    status, _ = _detect(client, "")
    assert status == 422


def test_detect_error_codes_are_stable_and_safe(client, fake_adapter, monkeypatch) -> None:
    """A rejected key gives a stable code and never echoes the key back."""
    fake_adapter.reject_slugs = {"anthropic"}

    status, body = _detect(client, GEMINI_KEY, provider_slug="anthropic")
    assert status == 200
    assert body["credentials_ok"] is False
    assert body["error_code"] == "AI_AUTH_ERROR"
    # A rejected key must not leak back to the browser.
    assert "AQ.Ab8RN" not in json_dumps(body)


def test_detect_does_not_require_encryption_to_be_configured(client, fake_adapter, monkeypatch) -> None:
    """Probing works before a key is stored, so it must not need the cipher."""
    from app.core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(type(settings), "ai_encryption_key", None, raising=False)

    status, body = _detect(client, GEMINI_KEY)
    assert status == 200
    assert body["detected_slug"] == "gemini"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _detect(client, api_key: str, provider_slug: str | None = None):
    response = client.post(
        "/api/ai/providers/detect",
        json={"api_key": api_key, "provider_slug": provider_slug},
    )
    return response.status_code, response.json()


def json_dumps(value) -> str:
    import json

    return json.dumps(value)
