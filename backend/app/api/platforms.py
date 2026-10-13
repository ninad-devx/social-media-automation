"""Platform capability endpoints.

The frontend used to keep its own copy of the limits, which had already drifted
from the backend in three places (different platform lists, different
`requiresMedia` values, different max-asset rules). These endpoints make the
backend the single source: the UI renders from what it is told, and the same
data drives the validation that runs before a publish is accepted.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import ApiKeyDep
from app.services.platform_capabilities import publishing_capability, text_warnings
from app.services.platforms import all_capabilities, media_capabilities

router = APIRouter(tags=["platforms"])


@router.get("/platforms", dependencies=[ApiKeyDep])
def list_platforms() -> list[dict]:
    """Every platform this deployment knows about, with its full capabilities."""
    return all_capabilities()


@router.get("/platforms/{platform}", dependencies=[ApiKeyDep])
def get_platform(platform: str) -> dict:
    """Capabilities for one platform, including every post type it supports."""
    return media_capabilities(platform)


@router.get("/platforms/{platform}/post-types", dependencies=[ApiKeyDep])
def list_post_types(platform: str) -> dict:
    """Which post types a platform can actually publish, and what each needs."""
    caps = media_capabilities(platform)
    return {
        "platform": caps["platform"],
        "label": caps["label"],
        "known": caps["known"],
        "default_post_type": caps["default_post_type"],
        "post_types": caps["post_types"],
    }


@router.get("/platforms/{platform}/check", dependencies=[ApiKeyDep])
def check_publishable(
    platform: str,
    media_count: Annotated[int, Query(ge=0, le=100)] = 0,
    post_type: Annotated[str | None, Query(max_length=32)] = None,
) -> dict:
    """Can this platform publish this draft? Used for live editor feedback.

    Advisory only: the same rules are enforced again inside PublishService, so a
    client that ignores this cannot get a bad request past the API.
    """
    ok, reason = publishing_capability(platform, media_count, post_type)
    return {"ok": ok, "reason": reason}


@router.get("/platforms/{platform}/text-warnings", dependencies=[ApiKeyDep])
def check_text(platform: str, text: Annotated[str, Query(max_length=65000)] = "") -> dict:
    """Length warnings for a draft. Never truncates."""
    return {"warnings": text_warnings(platform, text)}