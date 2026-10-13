"""The single authoritative platform capability model.

Everything platform-specific lives here: label, limits, which media kinds are
accepted, how many assets fit in one post, and - the part that was missing
entirely - which *post types* the platform and the publishing engine can
actually produce.

Design rules:

* One source of truth. The backend validates from this module; the frontend
  reads the same facts through ``GET /api/ai/media-capabilities`` and
  ``GET /api/platforms`` rather than keeping its own copy.
* Nothing here claims a capability the publishing engine cannot deliver. A post
  type is only listed when the Buffer ``createPost`` metadata we send can
  express it (see :mod:`app.services.platform_capabilities` for the verified
  matrix and the evidence behind each entry).
* Limits are advisory in the sense that they never truncate content. They are
  enforced as refusals before a request is ever accepted.

Why a dedicated module rather than extending ``platform_limits``:
``platform_limits`` was already one of several drifting copies, and post-type
support needs a richer shape (per-type media rules, minimum asset counts,
whether a type can be scheduled). One module, one import path.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# --------------------------------------------------------------------------- #
# Post types
# --------------------------------------------------------------------------- #
#: A feed image / album entry with no format directive.
POST = "post"
#: A video uploaded directly, published to the feed.
VIDEO = "video"
#: Short-form vertical video (Instagram Reel, Facebook Reel, YouTube Short).
REEL = "reel"
#: A full-screen vertical image or video that disappears after 24 hours.
STORY = "story"
#: More than one image presented as an album/carousel.
CAROUSEL = "carousel"
#: A link preview card.
LINK = "link"
#: A long-form upload published to a channel (YouTube video).
LONG_FORM = "long_form"

ALL_POST_TYPES = (POST, VIDEO, REEL, STORY, CAROUSEL, LINK, LONG_FORM)


@dataclass(frozen=True)
class PostTypeSpec:
    """One post type on one platform."""

    key: str
    label: str
    #: Key sent in Buffer's `metadata[<service>].type`, or None when the
    #: platform needs no directive at all (a plain feed post).
    buffer_type: str | None = None
    #: Buffer metadata flags applied alongside the type.
    buffer_flags: dict[str, bool] = field(default_factory=dict)
    #: Minimum number of assets. `None` means the platform may accept none.
    min_assets: int | None = None
    max_assets: int | None = None
    #: Which media kinds this type accepts.
    allows_image: bool = True
    allows_video: bool = True
    #: True when the type must be scheduled rather than shared immediately.
    requires_schedule: bool = False
    #: Why this type is unavailable, when it is not supported.
    unsupported_reason: str | None = None


# --------------------------------------------------------------------------- #
# Platform spec
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class PlatformSpec:
    key: str
    label: str
    #: Maximum characters. None means "not enforced here".
    limit: int | None
    supports_hashtags: bool = True
    #: The platform refuses a text-only post.
    requires_media: bool = False
    supports_image: bool = True
    supports_video: bool = True
    supports_carousel: bool = False
    max_assets: int | None = None
    #: The default post type when the caller does not choose one.
    default_post_type: str = POST
    #: platform -> post type -> spec. Only *available* types appear here.
    post_types: dict[str, PostTypeSpec] = field(default_factory=dict)

    # -- convenience ------------------------------------------------------- #
    def available_post_types(self) -> tuple[str, ...]:
        return tuple(self.post_types)

    def post_type(self, key: str) -> PostTypeSpec | None:
        return self.post_types.get((key or "").strip().lower())

    def supports_post_type(self, key: str) -> bool:
        return self.post_type(key) is not None


FALLBACK = PlatformSpec(
    "social",
    "Social",
    None,
    requires_media=False,
    default_post_type=POST,
    # No post types at all. An unrecognised platform must produce an empty
    # capability list so the composer shows no format selector, rather than
    # offering a feed post on a network we know nothing about.
    post_types={},
)


# --------------------------------------------------------------------------- #
# The matrix
# --------------------------------------------------------------------------- #
# NOTE ON EVIDENCE: the `buffer_type` / `buffer_flags` values below are exactly
# the ones the existing, working integration already sends (a plain feed `post`
# for Instagram and Facebook, with `shouldShareToFeed` on Instagram). Types
# beyond that are only listed where Buffer's documented `metadata` shape for
# the service accepts them. Anything unproven is expressed as an absent entry,
# which `resolve_post_type` turns into an explicit, actionable refusal rather
# than a guess. See app/services/platform_capabilities.py for the full record.
def _x_post_types() -> dict[str, PostTypeSpec]:
    return {
        POST: PostTypeSpec(POST, "Post", None, {}, min_assets=None, max_assets=4),
        VIDEO: PostTypeSpec(
            VIDEO, "Video", None, {}, min_assets=1, max_assets=4, allows_image=False
        ),
    }


def _x_spec() -> PlatformSpec:
    # Buffer reports X profiles with `service: "twitter"`. The "x" alias
    # resolves to the same rules so a channel stored either way is validated
    # identically instead of silently falling back to "no limits at all".
    return PlatformSpec("twitter", "X", 280, max_assets=4, default_post_type=POST,
                        post_types=_x_post_types())


PLATFORMS: dict[str, PlatformSpec] = {
    "instagram": PlatformSpec(
        "instagram", "Instagram", 2200,
        requires_media=True,
        supports_carousel=True,
        max_assets=10,
        default_post_type=POST,
        post_types={
            POST: PostTypeSpec(
                POST, "Feed post", "post", {"shouldShareToFeed": True},
                min_assets=1, max_assets=10,
            ),
            CAROUSEL: PostTypeSpec(
                CAROUSEL, "Carousel / album", "carousel", {"shouldShareToFeed": True},
                min_assets=2, max_assets=10,
            ),
            REEL: PostTypeSpec(
                REEL, "Reel", "reel", {"shouldShareToFeed": True},
                min_assets=1, max_assets=1, allows_image=False,
            ),
            STORY: PostTypeSpec(
                STORY, "Story", "story", {"shouldShareToFeed": False},
                min_assets=1, max_assets=1,
            ),
        },
    ),
    "facebook": PlatformSpec(
        "facebook", "Facebook", 63206,
        supports_carousel=True,
        max_assets=10,
        default_post_type=POST,
        post_types={
            # PostTypeFacebook is exactly post | reel | story. Facebook natively
            # supports albums and link posts, but Buffer's enum has no value
            # for either, so they are not offered rather than being guessed at.
            POST: PostTypeSpec(POST, "Feed post", "post", {}, min_assets=None, max_assets=10),
            REEL: PostTypeSpec(
                REEL, "Reel", "reel", {}, min_assets=1, max_assets=1, allows_image=False
            ),
            STORY: PostTypeSpec(STORY, "Story", "story", {}, min_assets=1, max_assets=1),
        },
    ),
    # No `type` field exists on these services, so the format is expressed
    # purely through the assets array and buffer_type stays None.
    "linkedin": PlatformSpec(
        "linkedin", "LinkedIn", 3000,
        max_assets=1,
        default_post_type=POST,
        post_types={
            POST: PostTypeSpec(
                POST, "Post", None, {},
                min_assets=None, max_assets=1, allows_image=True, allows_video=False,
            ),
            VIDEO: PostTypeSpec(
                VIDEO, "Video", None, {},
                min_assets=1, max_assets=1, allows_image=False,
            ),
        },
    ),
    "twitter": _x_spec(),
    "threads": PlatformSpec(
        "threads", "Threads", 500,
        supports_carousel=True,
        max_assets=20,
        default_post_type=POST,
        post_types={
            # Threads accepts a nullable PostType, but Buffer never documents
            # which values it honours, so only the default is offered.
            POST: PostTypeSpec(POST, "Post", None, {}, min_assets=None, max_assets=20),
        },
    ),
    "youtube": PlatformSpec(
        "youtube", "YouTube", 5000,
        requires_media=True,
        max_assets=1,
        default_post_type=LONG_FORM,
        post_types={
            LONG_FORM: PostTypeSpec(
                LONG_FORM, "Video", None, {},
                min_assets=1, max_assets=1, allows_image=False, requires_schedule=True,
            ),
        },
    ),
    "pinterest": PlatformSpec(
        "pinterest", "Pinterest", 500,
        requires_media=True,
        max_assets=5,
        default_post_type=POST,
        post_types={
            POST: PostTypeSpec(
                POST, "Pin", None, {},
                min_assets=1, max_assets=1, allows_image=True, allows_video=False,
            ),
        },
    ),
    "mastodon": PlatformSpec(
        "mastodon", "Mastodon", 500,
        max_assets=4,
        default_post_type=POST,
        post_types={POST: PostTypeSpec(POST, "Post", None, {}, min_assets=None, max_assets=4)},
    ),
    "bluesky": PlatformSpec(
        "bluesky", "Bluesky", 300,
        max_assets=4,
        default_post_type=POST,
        post_types={POST: PostTypeSpec(POST, "Post", None, {}, min_assets=None, max_assets=4)},
    ),
    # Buffer reports X profiles with `service: "twitter"`. The "x" alias
    # resolves to the same rules so a channel stored either way is validated
    # identically instead of falling back to "no limits at all".
    "x": _x_spec(),
}

#: Buffer services whose input type actually declares a `type` field. Sending
#: one anywhere else is a GraphQL validation error.
SERVICES_WITH_TYPE: frozenset[str] = frozenset({"instagram", "facebook", "google", "threads"})

PLATFORM_KEYS: tuple[str, ...] = tuple(PLATFORMS)


def spec_for(platform: str | None) -> PlatformSpec:
    """Rules for a platform. Unknown platforms get the permissive fallback."""
    if not platform:
        return FALLBACK
    return PLATFORMS.get(platform.strip().lower(), FALLBACK)


def known_platform(platform: str | None) -> bool:
    return bool(platform) and platform.strip().lower() in PLATFORMS


def platform_label(platform: str | None) -> str:
    return spec_for(platform).label


def text_limit(platform: str | None) -> int | None:
    return spec_for(platform).limit


def limit_hint(label_or_platform: str) -> str | None:
    """A short instruction appended to an AI generation prompt."""
    spec = PLATFORMS.get(label_or_platform.strip().lower())
    if spec is None:
        for candidate in PLATFORMS.values():
            if candidate.label.lower() == label_or_platform.strip().lower():
                spec = candidate
                break
    if spec is None or spec.limit is None:
        return None
    return f"- **Length:** keep the post under {spec.limit:,} characters."


def requires_media(platform: str | None) -> bool:
    """True when the platform refuses a post with no media at all."""
    return spec_for(platform).requires_media


def media_capabilities(platform: str | None) -> dict:
    """Serializable capability record for the UI. The frontend renders from this."""
    spec = spec_for(platform)
    return {
        "platform": spec.key,
        "label": spec.label,
        "known": known_platform(platform),
        "requires_media": spec.requires_media,
        "supports_image": spec.supports_image,
        "supports_video": spec.supports_video,
        "supports_carousel": spec.supports_carousel,
        "max_assets": spec.max_assets,
        "text_limit": spec.limit,
        "default_post_type": spec.default_post_type,
        "post_types": [
            {
                "key": t.key,
                "label": t.label,
                "min_assets": t.min_assets,
                "max_assets": t.max_assets,
                "allows_image": t.allows_image,
                "allows_video": t.allows_video,
                "requires_schedule": t.requires_schedule,
            }
            for t in spec.post_types.values()
        ],
    }


def all_capabilities() -> list[dict]:
    return [media_capabilities(key) for key in PLATFORM_KEYS]