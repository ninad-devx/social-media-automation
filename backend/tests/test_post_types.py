"""Post type behaviour, verified against Buffer's published GraphQL schema.

These tests never contact Buffer. They assert the *request we build* matches the
documented contract, which is the only way to be confident about a post type
without publishing to a real channel.

Evidence used:
  PostType enum  - carousel, event, ghost_post, offer, post, reel, short,
                   story, thread, whats_new
  PostTypeFacebook - post, reel, story
  PostInputMetaData - only instagram, facebook, google and threads declare a
                      `type` field at all
"""
from __future__ import annotations

import pytest

from app.services import platforms as plat
from app.services.errors import AppError, ErrorCode
from app.services.platform_capabilities import (
    VALID_BUFFER_TYPES,
    buffer_metadata_for,
    publishing_capability,
    resolve_post_type,
    validate_for_publish,
)
from app.services.platforms import media_capabilities

IMG = {"type": "image", "url": "https://cdn.example/a.png"}
VID = {"type": "video", "url": "https://cdn.example/a.mp4"}


# --------------------------------------------------------------------------- #
# The enum itself
# --------------------------------------------------------------------------- #
def test_only_instagram_and_facebook_advertise_reels_and_stories() -> None:
    assert plat.spec_for("instagram").supports_post_type(plat.REEL)
    assert plat.spec_for("instagram").supports_post_type(plat.STORY)
    assert plat.spec_for("facebook").supports_post_type(plat.REEL)
    assert plat.spec_for("facebook").supports_post_type(plat.STORY)


def test_no_platform_advertises_a_format_buffer_cannot_express() -> None:
    """Every emitted buffer_type must be a real enum member.

    This is the check that would have caught the provisional matrix, which used
    "video", "album" and "pin" as type values - none of which exist in Buffer's
    schema and all of which would have been rejected at request time.
    """
    for key in plat.PLATFORM_KEYS:
        spec = plat.spec_for(key)
        if key not in plat.SERVICES_WITH_TYPE:
            for post_type in spec.post_types.values():
                assert post_type.buffer_type is None, (
                    f"{key}/{post_type.key} sends a type to a service that has no "
                    f"`type` field"
                )
            continue
        allowed = VALID_BUFFER_TYPES[key]
        for post_type in spec.post_types.values():
            if post_type.buffer_type is not None:
                assert post_type.buffer_type in allowed, (
                    f"{key}/{post_type.key} -> {post_type.buffer_type!r} is not a "
                    f"valid Buffer enum value"
                )


def test_facebook_does_not_offer_album_or_link() -> None:
    """PostTypeFacebook is post | reel | story only."""
    spec = plat.spec_for("facebook")
    assert not spec.supports_post_type(plat.LINK)
    assert not spec.supports_post_type(plat.VIDEO)
    assert set(spec.available_post_types()) == {"post", "reel", "story"}


def test_non_meta_services_never_receive_a_metadata_block() -> None:
    for platform in ("linkedin", "twitter", "threads", "youtube", "pinterest", "mastodon", "bluesky"):
        spec = plat.spec_for(platform)
        resolved = resolve_post_type(platform, None)
        assert buffer_metadata_for(platform, resolved, asset_count=1) is None, platform


# --------------------------------------------------------------------------- #
# Metadata construction
# --------------------------------------------------------------------------- #
class TestBufferMetadata:
    def test_instagram_feed_post_keeps_the_existing_payload(self) -> None:
        resolved = resolve_post_type("instagram", "post")
        meta = buffer_metadata_for("instagram", resolved, asset_count=1)
        # Identical to what the working integration already sends.
        assert meta == {"instagram": {"type": "post", "shouldShareToFeed": True}}

    def test_instagram_multi_image_becomes_a_carousel(self) -> None:
        resolved = resolve_post_type("instagram", "post")
        meta = buffer_metadata_for("instagram", resolved, asset_count=3)
        assert meta == {"instagram": {"type": "carousel"}}

    def test_instagram_reel_uses_the_reel_type(self) -> None:
        resolved = resolve_post_type("instagram", "reel")
        meta = buffer_metadata_for("instagram", resolved, asset_count=1)
        assert meta["instagram"]["type"] == "reel"

    def test_instagram_story_uses_the_story_type_and_is_not_shared_to_feed(self) -> None:
        resolved = resolve_post_type("instagram", "story")
        meta = buffer_metadata_for("instagram", resolved, asset_count=1)
        assert meta["instagram"]["type"] == "story"
        # A story must not also appear in the feed.
        assert meta["instagram"]["shouldShareToFeed"] is False

    def test_facebook_reel_and_story(self) -> None:
        for post_type, expected in (("reel", "reel"), ("story", "story")):
            resolved = resolve_post_type("facebook", post_type)
            meta = buffer_metadata_for("facebook", resolved, asset_count=1)
            assert meta["facebook"]["type"] == expected

    def test_pinterest_pin_sends_no_type(self) -> None:
        """Pinterest's input type has no `type` field at all."""
        resolved = resolve_post_type("pinterest", None)
        assert buffer_metadata_for("pinterest", resolved, asset_count=1) is None


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #
class TestPostTypeValidation:
    def test_unsupported_type_is_refused_not_downgraded(self) -> None:
        with pytest.raises(AppError) as exc:
            resolve_post_type("facebook", "short")
        assert exc.value.code == ErrorCode.POST_TYPE_UNSUPPORTED

    def test_link_post_is_refused_on_facebook(self) -> None:
        with pytest.raises(AppError):
            resolve_post_type("facebook", "link")

    def test_unknown_type_is_refused(self) -> None:
        with pytest.raises(AppError):
            resolve_post_type("instagram", "space_post")

    def test_omitting_the_type_uses_the_platform_default(self) -> None:
        assert resolve_post_type("instagram", None).key == "post"
        assert resolve_post_type("youtube", None).key == "long_form"

    def test_a_reel_takes_exactly_one_video(self) -> None:
        validate_for_publish(
            platform="instagram", text="hi", media=[VID], post_type="reel"
        )
        with pytest.raises(AppError) as two:
            validate_for_publish(
                platform="instagram", text="hi", media=[VID, VID], post_type="reel"
            )
        assert "at most 1 attachment" in two.value.details[0]["reason"]

    def test_a_reel_rejects_an_image(self) -> None:
        with pytest.raises(AppError) as exc:
            validate_for_publish(
                platform="instagram", text="hi", media=[IMG], post_type="reel"
            )
        assert "cannot contain images" in exc.value.details[0]["reason"]

    def test_a_story_takes_exactly_one_asset(self) -> None:
        validate_for_publish(
            platform="facebook", text="hi", media=[IMG], post_type="story"
        )
        with pytest.raises(AppError):
            validate_for_publish(
                platform="facebook", text="hi", media=[IMG, IMG], post_type="story"
            )

    def test_a_carousel_needs_at_least_two_assets(self) -> None:
        with pytest.raises(AppError):
            validate_for_publish(
                platform="instagram", text="hi", media=[IMG], post_type="carousel"
            )
        validate_for_publish(
            platform="instagram", text="hi", media=[IMG, IMG], post_type="carousel"
        )

    def test_instagram_still_requires_media_for_every_type(self) -> None:
        for post_type in plat.spec_for("instagram").available_post_types():
            with pytest.raises(AppError):
                validate_for_publish(
                    platform="instagram", text="hi", media=[], post_type=post_type
                )

    def test_linkedin_still_caps_at_one_attachment(self) -> None:
        with pytest.raises(AppError) as exc:
            validate_for_publish(
                platform="linkedin", text="hi", media=[IMG, IMG], post_type="post"
            )
        assert "at most 1" in exc.value.details[0]["reason"]


# --------------------------------------------------------------------------- #
# Serialisation for the UI
# --------------------------------------------------------------------------- #
class TestCapabilitySerialisation:
    def test_instagram_reports_every_supported_type(self) -> None:
        caps = media_capabilities("instagram")
        keys = {t["key"] for t in caps["post_types"]}
        assert keys == {"post", "carousel", "reel", "story"}
        assert caps["default_post_type"] == "post"
        assert caps["known"] is True

    def test_reel_reports_its_asset_rules(self) -> None:
        caps = media_capabilities("instagram")
        reel = next(t for t in caps["post_types"] if t["key"] == "reel")
        assert reel["max_assets"] == 1
        assert reel["allows_video"] is True
        assert reel["allows_image"] is False

    def test_unknown_platform_is_reported_as_unknown(self) -> None:
        caps = media_capabilities("tiktok")
        assert caps["known"] is False
        assert caps["post_types"] == []

    def test_publishing_capability_reports_schedule_only_types(self) -> None:
        ok, reason = publishing_capability("youtube", media_count=1, post_type="long_form")
        assert ok is False
        assert "scheduled" in (reason or "")

    def test_every_platform_has_a_default_that_exists(self) -> None:
        for key in plat.PLATFORM_KEYS:
            spec = plat.spec_for(key)
            assert spec.default_post_type in spec.available_post_types(), key