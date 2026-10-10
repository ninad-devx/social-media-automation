"""Guess which provider a pasted API key belongs to.

This exists so a user can paste one key and have everything else fill itself
in. It is a *guess* from the key's shape, never a security decision, and the
user can always override it. Detection never talks to the network - it only
looks at the string - so it is instant and cannot leak the key.
"""
from __future__ import annotations

from dataclasses import dataclass

# (prefix, slug). Ordered: the first match wins, so longer/more specific
# prefixes must come first (sk-ant- before sk-).
_PREFIXES: tuple[tuple[str, str], ...] = (
    ("sk-ant-", "anthropic"),
    ("sk-ant-api", "anthropic"),
    ("AIza", "gemini"),
    ("AQ.", "gemini"),  # Google AI Studio keys issued in the newer format
    ("sk-or-", "openai_compatible"),  # OpenRouter
    ("sk-", "openai"),
    ("gsk_", "openai_compatible"),  # Groq and other OpenAI-shaped hosts
)

# Substrings that identify an OpenAI-shaped custom endpoint. Only consulted
# for keys that carry no recognisable prefix of their own.
_ENDPOINT_HINTS: tuple[tuple[str, str], ...] = (
    ("openrouter.ai", "openai_compatible"),
    ("together.xyz", "openai_compatible"),
    ("groq.com", "openai_compatible"),
    ("fireworks.ai", "openai_compatible"),
    ("localhost", "openai_compatible"),
    ("127.0.0.1", "openai_compatible"),
    ("ollama", "openai_compatible"),
)


@dataclass(frozen=True)
class KeyDetection:
    """What we believe a pasted key is."""

    #: A slug from the registry, or "unknown".
    slug: str
    #: True when we matched a known key shape rather than falling back.
    confident: bool
    #: Human-readable reason, shown in the UI so the guess is never silent.
    reason: str


def detect_slug(api_key: str | None, base_url: str | None = None) -> KeyDetection:
    """Identify a provider from a key's prefix, falling back to the base URL.

    Unknown keys become `openai_compatible` rather than guessing a vendor, and
    `confident=False` so the UI asks the user to confirm instead of silently
    sending their key to the wrong API.
    """
    key = (api_key or "").strip()
    url = (base_url or "").strip().lower()

    for prefix, slug in _PREFIXES:
        if key.lower().startswith(prefix.lower()):
            return KeyDetection(
                slug=slug,
                confident=True,
                reason=f"This key starts with '{prefix}', so it looks like a {label(slug)} key.",
            )

    for needle, slug in _ENDPOINT_HINTS:
        if needle in url:
            return KeyDetection(
                slug=slug,
                confident=True,
                reason=f"The base URL looks like a {label(slug)} host.",
            )

    return KeyDetection(
        slug="openai_compatible",
        confident=False,
        reason=(
            "This key has no recognised prefix. It may be for a custom or "
            "OpenAI-compatible endpoint - add the base URL and test the connection."
        ),
    )


def label(slug: str) -> str:
    return {
        "openai": "OpenAI",
        "anthropic": "Anthropic",
        "gemini": "Google Gemini",
        "openai_compatible": "OpenAI-compatible",
    }.get(slug, slug)