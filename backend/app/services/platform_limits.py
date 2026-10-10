"""Platform limits and validation.

Limits are configurable per platform rather than hardcoded into logic, because
they change over time and can differ by account type. These values are
advisory: they drive warnings, never silent truncation.

IMPORTANT: Buffer rejects a text-only Instagram post outright, so a capability
check is included rather than letting generation produce something that can
never publish.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PlatformSpec:
    key: str
    label: str
    #: Maximum characters. None means "not enforced here".
    limit: int | None
    supports_hashtags: bool = True
    #: Buffer requires at least one asset for this platform.
    requires_media: bool = False
    #: Character limit is enforced by the platform itself, not by us.
    soft_limit: bool = False
    #: Media kinds this platform accepts.
    supports_image: bool = True
    supports_video: bool = True
    #: Multi-asset carousel / album support.
    supports_carousel: bool = False
    #: Highest number of assets in one post. None means unlimited here.
    max_assets: int | None = None


PLATFORMS: dict[str, PlatformSpec] = {
    "instagram": PlatformSpec(
        "instagram", "Instagram", 2200, True,
        requires_media=True,
        supports_carousel=True,
        max_assets=10,
    ),
    "facebook": PlatformSpec(
        "facebook", "Facebook", 63206, True,
        supports_carousel=True,
        max_assets=10,
    ),
    "linkedin": PlatformSpec(
        "linkedin", "LinkedIn", 3000, True,
        # LinkedIn takes one document or one image; albums are not supported.
        supports_video=True,
        supports_carousel=False,
        max_assets=1,
    ),
    "twitter": PlatformSpec(
        "twitter", "X", 280, True,
        supports_video=True, max_assets=4,
    ),
    "x": PlatformSpec(
        "x", "X", 280, True,
        supports_video=True, max_assets=4,
    ),
    "threads": PlatformSpec(
        "threads", "Threads", 500, True,
        supports_carousel=True, max_assets=20,
    ),
    "mastodon": PlatformSpec("mastodon", "Mastodon", 500, True),
    "pinterest": PlatformSpec(
        "pinterest", "Pinterest", 500, True,
        requires_media=True, supports_video=True, max_assets=5,
    ),
    "youtube": PlatformSpec(
        "youtube", "YouTube", 5000, True,
        requires_media=True, supports_video=True, max_assets=1,
    ),
    "bluesky": PlatformSpec("bluesky", "Bluesky", 300, True, max_assets=4),
}

FALLBACK = PlatformSpec("social", "Social", None, True)


def spec_for(platform: str | None) -> PlatformSpec:
    if not platform:
        return FALLBACK
    return PLATFORMS.get(platform.strip().lower(), FALLBACK)


def platform_label(platform: str | None) -> str:
    return spec_for(platform).label


def limit_hint(label_or_platform: str) -> str | None:
    """A short instruction appended to the prompt."""
    spec = PLATFORMS.get(label_or_platform.strip().lower())
    if spec is None:
        for candidate in PLATFORMS.values():
            if candidate.label.lower() == label_or_platform.strip().lower():
                spec = candidate
                break
    if spec is None or spec.limit is None:
        return None
    return f"- **Length:** keep the post under {spec.limit:,} characters."


def validate_platform_text(platform: str | None, text: str) -> list[str]:
    """Non-blocking warnings. Content is never truncated."""
    warnings: list[str] = []
    spec = spec_for(platform)
    length = len(text or "")

    if spec.limit is not None and length > spec.limit:
        warnings.append(
            f"This draft is {length:,} characters, over the {spec.label} limit of "
            f"{spec.limit:,}. It will be rejected as-is - shorten it before publishing."
        )
    elif spec.limit is not None and length > spec.limit * 0.95:
        warnings.append(
            f"This draft is close to the {spec.label} limit ({length:,} of {spec.limit:,})."
        )

    if spec.requires_media:
        warnings.append(
            f"{spec.label} will not accept a text-only post. Attach an image or video before publishing."
        )

    return warnings


def publishing_capability(platform: str | None, media_count: int) -> tuple[bool, str | None]:
    """Can this platform actually publish what we currently have?"""
    spec = spec_for(platform)
    if spec.requires_media and media_count == 0:
        return False, (
            f"{spec.label} requires at least one image or video. Add media to this post "
            "before publishing."
        )
    return True, None


def requires_media(platform: str | None) -> bool:
    """True when this platform refuses a text-only post.

    Used to decide whether a campaign must generate an image, otherwise the
    post can never be published at all.
    """
    return spec_for(platform).requires_media


def _media_field(item, name: str) -> str:
    """Read a field from either a plain dict or a MediaItem-style object."""
    if isinstance(item, dict):
        return str(item.get(name) or "")
    return str(getattr(item, name, "") or "")


def validate_media_for_platform(platform: str | None, media: list) -> list[str]:
    """Check a proposed attachment list against what the platform accepts.

    Returns human-readable problems. An empty list means the set is fine.

    This runs BEFORE the publish so the user is told what to change, instead
    of Buffer rejecting it asynchronously and the post going out wrong.
    """
    spec = spec_for(platform)
    problems: list[str] = []
    media = media or []

    if not media:
        if spec.requires_media:
            problems.append(
                f"{spec.label} needs at least one image or video before it can publish."
            )
        return problems

    kinds = [_media_field(m, "type").lower() or "image" for m in media]

    if any(not _media_field(m, "url").strip() for m in media):
        problems.append("Every attachment needs a working file URL.")

    if "image" not in kinds and not spec.supports_image:
        problems.append(f"{spec.label} does not accept images.")

    if "video" in kinds and not spec.supports_video:
        problems.append(f"{spec.label} does not accept videos.")

    if len(media) > 1 and not spec.supports_carousel:
        problems.append(
            f"{spec.label} accepts one attachment only - it has no carousel or album."
        )

    if spec.max_assets is not None and len(media) > spec.max_assets:
        problems.append(
            f"{spec.label} accepts at most {spec.max_assets} attachment"
            f"{'s' if spec.max_assets != 1 else ''}; you have {len(media)}."
        )

    # Mixing videos and images in one album is rejected by several networks.
    if len(set(kinds)) > 1:
        problems.append("Do not mix videos and images in the same post.")

    return problems


def media_capabilities(platform: str | None) -> dict:
    """What this platform can do with media, for the UI to render from."""
    spec = spec_for(platform)
    return {
        "platform": spec.key,
        "label": spec.label,
        "requires_media": spec.requires_media,
        "supports_image": spec.supports_image,
        "supports_video": spec.supports_video,
        "supports_carousel": spec.supports_carousel,
        "max_assets": spec.max_assets,
        "text_limit": spec.limit,
    }