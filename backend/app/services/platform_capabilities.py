"""Post-type resolution and media validation.

One entry point, :func:`validate_for_publish`, is what the publishing path calls
before a request is ever accepted. It is deliberately pure: it takes plain
values and returns plain values, so it can be unit-tested without a database, a
network or a publishing engine.

Every rule here comes from :mod:`app.services.platforms`. There is no second
copy of the limits, and no separate "media required" list - the audit found
`publish_service.MEDIA_REQUIRED_PLATFORMS` and `platform_limits` disagreeing
about Pinterest and YouTube, which is exactly the drift this module removes.
"""
from __future__ import annotations

from typing import Any

from app.services import platforms as plat
from app.services.errors import AppError, ErrorCode


class Problem:
    """One reason a request cannot be published, phrased for the user."""

    __slots__ = ("field", "message")

    def __init__(self, field: str, message: str) -> None:
        self.field = field
        self.message = message

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Problem(field={self.field!r}, message={self.message!r})"

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, Problem)
            and other.field == self.field
            and other.message == self.message
        )


def _media_field(item: Any, name: str) -> str:
    """Read a field from either a plain dict or a Pydantic-style object."""
    if isinstance(item, dict):
        return str(item.get(name) or "")
    return str(getattr(item, name, "") or "")


def media_kinds(media: list | None) -> list[str]:
    return [(_media_field(m, "type").lower() or "image") for m in (media or [])]


def resolve_post_type(platform: str | None, requested: str | None) -> plat.PostTypeSpec:
    """Pick the post type, or refuse.

    * No type requested -> the platform default.
    * Requested but unknown -> refuse with the list of what IS available.

    Never falls back silently: a caller asking for a reel on a platform that
    cannot publish one gets an error, not a feed post.
    """
    spec = plat.spec_for(platform)
    key = (requested or "").strip().lower()

    if not key:
        chosen = spec.post_type(spec.default_post_type)
        if chosen is None:
            # A default that the matrix does not define is a modelling error,
            # not a user error. Fail loudly rather than publishing blind.
            raise AppError(
                ErrorCode.INVALID_REQUEST,
                500,
                field="post_type",
                details=[{"reason": f"No default post type defined for '{spec.key}'."}],
            )
        return chosen

    chosen = spec.post_type(key)
    if chosen is None:
        available = ", ".join(spec.available_post_types()) or "none"
        reason = chosen.unsupported_reason if chosen else None
        raise AppError(
            ErrorCode.POST_TYPE_UNSUPPORTED,
            400,
            field="post_type",
            details=[
                {
                    "requested": key,
                    "platform": spec.key,
                    "available": available,
                    "reason": reason,
                }
            ],
        )
    return chosen


def validate_for_publish(
    *,
    platform: str | None,
    text: str,
    media: list | None,
    post_type: str | None = None,
) -> plat.PostTypeSpec:
    """Validate one publishable unit. Raises :class:`AppError` on any problem.

    Returns the resolved post type so the caller can use it for the Buffer
    metadata without resolving twice.
    """
    spec = plat.spec_for(platform)
    resolved = resolve_post_type(platform, post_type)
    media = list(media or [])
    problems: list[Problem] = []

    # --- text ------------------------------------------------------------
    length = len(text or "")
    if spec.limit is not None and length > spec.limit:
        problems.append(
            Problem(
                "content",
                f"This draft is {length:,} characters, over the {spec.label} limit of "
                f"{spec.limit:,}. It will be rejected as-is - shorten it before publishing.",
            )
        )

    # --- required media --------------------------------------------------
    required_by_platform = spec.requires_media
    required_by_type = resolved.min_assets
    minimum = max(required_by_platform and 1 or 0, required_by_type or 0)

    if not media and minimum > 0:
        problems.append(
            Problem(
                "media",
                f"{spec.label} will not accept this without media: a "
                f"{resolved.label.lower()} needs at least {minimum} attachment"
                f"{'s' if minimum != 1 else ''}.",
            )
        )

    # --- asset counts ----------------------------------------------------
    if media and resolved.max_assets is not None and len(media) > resolved.max_assets:
        problems.append(
            Problem(
                "media",
                f"A {spec.label} {resolved.label.lower()} accepts at most "
                f"{resolved.max_assets} attachment{'s' if resolved.max_assets != 1 else ''}; "
                f"you have {len(media)}.",
            )
        )

    # --- media kinds -----------------------------------------------------
    kinds = media_kinds(media)
    if media and "image" in kinds and not resolved.allows_image:
        problems.append(
            Problem("media", f"A {spec.label} {resolved.label.lower()} cannot contain images.")
        )
    if media and "video" in kinds and not resolved.allows_video:
        problems.append(
            Problem("media", f"A {spec.label} {resolved.label.lower()} cannot contain videos.")
        )

    # --- a reel or a story is a single asset -----------------------------
    if resolved.key in (plat.REEL, plat.STORY, plat.LONG_FORM) and len(media) > 1:
        problems.append(
            Problem(
                "media",
                f"A {spec.label} {resolved.label.lower()} is a single item, so it takes "
                f"exactly one attachment. You have {len(media)}.",
            )
        )

    # --- carousel --------------------------------------------------------
    if resolved.key == plat.CAROUSEL and len(media) < 2:
        problems.append(
            Problem(
                "media",
                f"A {spec.label} {resolved.label.lower()} needs at least 2 attachments; "
                f"you have {len(media)}.",
            )
        )

    # --- never mix video and images in one album -------------------------
    if len(set(kinds)) > 1:
        problems.append(Problem("media", "Do not mix videos and images in the same post."))

    # --- every attachment must be usable ---------------------------------
    if any(not _media_field(m, "url").strip() for m in media):
        problems.append(Problem("media", "Every attachment needs a working file URL."))

    if problems:
        raise AppError(
            ErrorCode.MEDIA_INVALID if problems[0].field == "media" else ErrorCode.INVALID_REQUEST,
            400,
            field=problems[0].field,
            details=[{"reason": p.message, "field": p.field} for p in problems],
        )
    return resolved


def text_warnings(platform: str | None, text: str) -> list[str]:
    """Non-blocking notes for the generator/editor. Never truncates."""
    from app.services.platforms import spec_for

    spec = spec_for(platform)
    limit = spec.limit
    length = len(text or "")
    if limit is None:
        return []
    if length > limit:
        return [
            f"This draft is {length:,} characters, over the {spec.label} limit of "
            f"{limit:,}. It will be rejected as-is - shorten it before publishing."
        ]
    if length > limit * 0.95:
        return [f"This draft is close to the {spec.label} limit ({length:,} of {limit:,})."]
    return []


def buffer_metadata_for(
    platform: str | None, post_type: plat.PostTypeSpec, asset_count: int
) -> dict[str, Any] | None:
    """Buffer `metadata` for one post, or None when the platform needs none.

    Mirrors the payload the working integration already sends. Instagram becomes
    a carousel when more than one asset is attached and no explicit type was
    chosen, exactly as before.

    Two safety properties, both learned from Buffer's published schema:

    * a ``type`` is only emitted for a service whose input type actually
      declares one (instagram, facebook, google, threads). Sending one anywhere
      else is a GraphQL validation error, not a soft failure;
    * the value must be a member of that service's enum. ``"video"``,
      ``"album"`` and ``"link"`` look like reasonable post types but are NOT
      valid `type` values - a video is expressed through ``assets[].video``.
      Anything not in the enum is dropped rather than sent and rejected.
    """
    spec = plat.spec_for(platform)
    key = spec.key

    body: dict[str, Any] = {}
    if post_type.buffer_type is not None and key in plat.SERVICES_WITH_TYPE:
        if post_type.buffer_type in VALID_BUFFER_TYPES.get(key, ()):
            body["type"] = post_type.buffer_type
    body.update(post_type.buffer_flags if key in plat.SERVICES_WITH_TYPE else {})

    if post_type.key == plat.POST and key == "instagram" and asset_count > 1:
        body["type"] = "carousel"
        body.pop("shouldShareToFeed", None)

    if not body:
        return None
    # Buffer keys the metadata block by the service name it reports.
    return {key: body}


#: Enum members from Buffer's published schema.
#: `PostType` is shared by Instagram and Threads; Facebook and Google Business
#: have their own narrower enums. Anything outside these sets is not sent.
VALID_BUFFER_TYPES: dict[str, tuple[str, ...]] = {
    "instagram": (
        "post", "carousel", "reel", "story", "short", "thread",
        "event", "offer", "whats_new", "ghost_post",
    ),
    "threads": ("post", "carousel", "reel", "story", "short", "thread"),
    "facebook": ("post", "reel", "story"),
    "google": ("event", "offer", "whats_new"),
}


def publishing_capability(
    platform: str | None, media_count: int, post_type: str | None = None
) -> tuple[bool, str | None]:
    """Non-raising variant used by read endpoints and the UI."""
    try:
        resolved = resolve_post_type(platform, post_type)
    except AppError as exc:
        return False, exc.details[0].get("reason") if exc.details else exc.user_message

    spec = plat.spec_for(platform)
    minimum = max((spec.requires_media and 1) or 0, resolved.min_assets or 0)
    if media_count < minimum:
        plural = "s" if minimum != 1 else ""
        noun = "attachment" + plural if minimum else ""
        return False, (
            f"{spec.label} needs at least {minimum} {noun} for a "
            f"{resolved.label.lower()}. Add media to this post before publishing."
        )
    if resolved.requires_schedule and minimum:
        return False, (
            f"A {spec.label} {resolved.label.lower()} can only be scheduled, not shared "
            "immediately. Pick Schedule instead of Publish now."
        )
    return True, None