"""Compatibility shim.

The limits used to live here, which was the origin of the drift the audit found:
a second hardcoded media-required list in ``publish_service`` disagreed with
this module about Pinterest and YouTube, and the frontend kept a third copy of
the platform table.

There is now exactly one capability model,
:mod:`app.services.platforms`, and this module re-exports it so existing imports
keep working. Everything delegates; nothing is defined here.

New code should import from :mod:`app.services.platforms` directly.
"""
from __future__ import annotations

from app.services.platform_capabilities import (
    buffer_metadata_for,
    media_kinds,
    publishing_capability,
    resolve_post_type,
    text_warnings,
    validate_for_publish,
)
from app.services.platforms import (
    PLATFORMS,
    PlatformSpec,
    PostTypeSpec,
    all_capabilities,
    known_platform,
    limit_hint,
    media_capabilities,
    platform_label,
    requires_media,
    spec_for,
    text_limit,
)

__all__ = [
    "PLATFORMS",
    "PlatformSpec",
    "PostTypeSpec",
    "all_capabilities",
    "buffer_metadata_for",
    "known_platform",
    "limit_hint",
    "media_capabilities",
    "media_kinds",
    "platform_label",
    "publishing_capability",
    "requires_media",
    "resolve_post_type",
    "spec_for",
    "text_limit",
    "text_warnings",
    "validate_for_publish",
    "validate_media_for_platform",
    "validate_platform_text",
]


def validate_platform_text(platform: str | None, text: str) -> list[str]:
    """Deprecated: use ``text_warnings``."""
    return text_warnings(platform, text)


def validate_media_for_platform(platform: str | None, media: list) -> list[str]:
    """Non-raising media validation. Returns human-readable problems.

    Wraps :func:`app.services.platform_capabilities.validate_for_publish` and
    returns its reasons instead of raising, so the existing callers that expect
    a list keep working against the single rule set.
    """
    from app.services.errors import AppError

    try:
        validate_for_publish(
            platform=platform, text="", media=media, post_type=None
        )
    except AppError as exc:
        return [str(d.get("reason", "")) for d in exc.details if d.get("reason")]
    return []