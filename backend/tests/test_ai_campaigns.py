"""Campaign generation, review, editing and publishing.

Every publish path here must go through PublishService, so these tests also
assert the n8n payload looks like the Quick Publisher one.
"""
from __future__ import annotations

import pytest

# Instagram cannot publish text-only posts, so publishing tests use the
# text-capable channels and Instagram's media rule is tested on its own.
from tests.conftest import make_campaign, wait_for_generation

GEN_TARGETS = [
    {"account_id": "company", "channel_id": "company_linkedin"},
    {"account_id": "company", "channel_id": "company_instagram"},
]

PUBLISH_TARGETS = [
    {"account_id": "company", "channel_id": "company_linkedin"},
    {"account_id": "company", "channel_id": "company_facebook"},
]


def _create(client, **overrides):
    payload = {"topic": "Launch of our scheduling tool", "targets": GEN_TARGETS}
    payload.update(overrides)
    return client.post("/api/ai/campaigns", json=payload)


def _generated(client, targets=None, topic="Launch of our scheduling tool"):
    campaign = make_campaign(client, targets or GEN_TARGETS, topic=topic)
    client.post(f"/api/ai/campaigns/{campaign['id']}/generate", json={})
    wait_for_generation()
    return client.get(f"/api/ai/campaigns/{campaign['id']}").json()


def _post_for(campaign, channel_id):
    return next(p for p in campaign["posts"] if p["channel_id"] == channel_id)



@pytest.fixture(autouse=True)
def _bind_seeded_channels(bound_company_channels) -> None:
    """The seeded demo channels are bound to a mocked Buffer integration.

    Publishing refuses a channel with no Buffer profile - that refusal is the
    fix this work is verifying - so the fixtures these tests publish through
    have to be genuinely bound. The integration is a throwaway row with an
    encrypted dummy credential; no socket is opened and no real token exists.
    """



# --------------------------------------------------------------------------- #
# create / list / validate
# --------------------------------------------------------------------------- #
def test_create_campaign_creates_no_publishable_content(client) -> None:
    r = _create(client)
    assert r.status_code == 201
    body = r.json()

    assert body["status"] == "draft"
    assert body["target_count"] == 2
    # Placeholder slots may exist, but nothing is written or published yet.
    assert body["generated_count"] == 0
    assert body["published_count"] == 0
    assert all(p["generation_status"] == "pending" for p in body["posts"])
    assert all(p["effective_content"] == "" for p in body["posts"])


def test_campaign_requires_a_topic(client) -> None:
    r = client.post("/api/ai/campaigns", json={"topic": "", "targets": GEN_TARGETS})
    assert r.status_code == 422


def test_campaign_rejects_an_unknown_channel(client) -> None:
    r = _create(client, targets=[{"account_id": "company", "channel_id": "nope"}])
    assert r.status_code in (400, 404)
    assert "error" in r.json()


def test_campaign_rejects_a_channel_from_another_account(client) -> None:
    """The account/channel pairing must actually exist."""
    r = _create(client, targets=[{"account_id": "nope", "channel_id": "company_linkedin"}])
    assert r.status_code in (400, 404)


def test_duplicate_targets_are_collapsed(client) -> None:
    r = _create(client, targets=GEN_TARGETS + [GEN_TARGETS[0]])
    assert r.status_code == 201
    assert r.json()["target_count"] == 2


def test_list_and_filter_campaigns(client) -> None:
    _create(client, topic="First")
    _create(client, topic="Second")
    assert len(client.get("/api/ai/campaigns").json()["items"]) == 2

    by_status = client.get("/api/ai/campaigns", params={"status": "draft"}).json()
    assert len(by_status["items"]) == 2
    assert len(client.get("/api/ai/campaigns", params={"status": "published"}).json()["items"]) == 0


def test_delete_campaign(client) -> None:
    campaign = _create(client).json()
    assert client.delete(f"/api/ai/campaigns/{campaign['id']}").status_code == 204
    assert client.get(f"/api/ai/campaigns/{campaign['id']}").status_code == 404


# --------------------------------------------------------------------------- #
# generation
# --------------------------------------------------------------------------- #
def test_generate_creates_one_draft_post_per_channel(client, fake_adapter, provider_row) -> None:
    campaign = _generated(client)

    assert campaign["generated_count"] == 2
    assert campaign["failed_count"] == 0
    assert campaign["status"] == "ready"

    assert {p["channel_id"] for p in campaign["posts"]} == {
        "company_linkedin", "company_instagram"
    }

    for post in campaign["posts"]:
        # Generation never publishes; everything waits for a human.
        assert post["publishing_status"] == "draft"
        assert post["generation_status"] == "generated"
        assert post["effective_content"]
        assert post["is_edited"] is False


def test_generated_posts_carry_platform_and_account(client, fake_adapter, provider_row) -> None:
    campaign = _generated(client)
    linkedin = _post_for(campaign, "company_linkedin")

    assert linkedin["platform"] == "linkedin"
    assert linkedin["account_id"] == "company"
    assert linkedin["platform_label"]


def test_generation_is_bounded_and_reports_progress(client, fake_adapter, provider_row) -> None:
    campaign = make_campaign(client, GEN_TARGETS)
    client.post(f"/api/ai/campaigns/{campaign['id']}/generate", json={})
    wait_for_generation()

    status = client.get(f"/api/ai/campaigns/{campaign['id']}/generation-status").json()
    assert status["generated"] == 2
    assert status["pending"] == 0
    assert status["generating"] == 0
    # Nothing is ever left stranded mid-flight.
    assert status["status"] == "ready"


def test_one_channel_failure_does_not_block_the_other(client, fake_adapter, provider_row) -> None:
    """Partial failure is expected; the healthy post must still be usable."""
    from app.services.ai.base import ProviderAuthError

    fake_adapter.fail_platforms = {"instagram"}

    campaign = make_campaign(client, GEN_TARGETS)
    client.post(f"/api/ai/campaigns/{campaign['id']}/generate", json={})
    wait_for_generation()

    body = client.get(f"/api/ai/campaigns/{campaign['id']}").json()
    assert body["generated_count"] == 1
    assert body["failed_count"] == 1

    instagram = _post_for(body, "company_instagram")
    assert instagram["generation_status"] == "failed"
    assert instagram["error_code"] == "AI_AUTH_ERROR"

    # The healthy post is a normal, publishable draft.
    linkedin = _post_for(body, "company_linkedin")
    assert linkedin["generation_status"] == "generated"
    assert linkedin["effective_content"]


def test_generation_needs_a_provider(client) -> None:
    campaign = make_campaign(client, GEN_TARGETS)
    r = client.post(f"/api/ai/campaigns/{campaign['id']}/generate", json={})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "AI_NO_PROVIDER"


# --------------------------------------------------------------------------- #
# review and editing
# --------------------------------------------------------------------------- #
def test_editing_a_post_updates_content_and_flags_it(client, fake_adapter, provider_row) -> None:
    campaign = _generated(client)
    post = campaign["posts"][0]

    r = client.patch(
        f"/api/ai/campaign-posts/{post['id']}",
        json={"content": "Human edited this caption.", "hashtags": ["scheduling", "saas"]},
    )
    assert r.status_code == 200
    body = r.json()

    assert body["effective_content"] == "Human edited this caption."
    assert body["is_edited"] is True
    assert body["hashtags"] == ["scheduling", "saas"]
    # The original is preserved so an edit is never destructive.
    assert body["generated_content"] != "Human edited this caption."


def test_edit_can_be_undone_one_version_at_a_time(client, fake_adapter, provider_row) -> None:
    campaign = _generated(client)
    post = campaign["posts"][0]

    client.patch(f"/api/ai/campaign-posts/{post['id']}", json={"content": "Version 2."})
    client.patch(f"/api/ai/campaign-posts/{post['id']}", json={"content": "Version 3."})

    undone = client.post(f"/api/ai/campaign-posts/{post['id']}/restore")
    assert undone.status_code == 200
    assert undone.json()["effective_content"] == "Version 2."


def test_edits_can_be_discarded_entirely(client, fake_adapter, provider_row) -> None:
    """'Revert to AI draft' is a different action from 'undo last edit'."""
    campaign = _generated(client)
    post = campaign["posts"][0]

    client.patch(f"/api/ai/campaign-posts/{post['id']}", json={"content": "Human copy."})
    discarded = client.post(f"/api/ai/campaign-posts/{post['id']}/discard-edits")

    assert discarded.status_code == 200
    assert discarded.json()["is_edited"] is False
    assert discarded.json()["effective_content"] == post["generated_content"]


def test_publishing_an_edited_post_uses_the_edited_text(
    client, fake_adapter, provider_row, fake_n8n
) -> None:
    """The single most important guarantee in this feature."""
    campaign = _generated(client, PUBLISH_TARGETS)
    post = _post_for(campaign, "company_linkedin")

    client.patch(f"/api/ai/campaign-posts/{post['id']}", json={"content": "EDITED CAPTION"})
    client.post(f"/api/ai/campaign-posts/{post['id']}/publish", json={})

    assert len(fake_n8n.calls) == 1
    assert fake_n8n.calls[0]["content"] == "EDITED CAPTION"


def test_regenerate_preserves_a_human_edit_by_default(client, fake_adapter, provider_row) -> None:
    campaign = _generated(client)
    post = campaign["posts"][0]

    client.patch(f"/api/ai/campaign-posts/{post['id']}", json={"content": "Do not lose me."})
    client.post(f"/api/ai/campaign-posts/{post['id']}/regenerate", json={})
    wait_for_generation()

    after = client.get(f"/api/ai/campaigns/{campaign['id']}").json()
    same = next(p for p in after["posts"] if p["id"] == post["id"])
    assert same["effective_content"] == "Do not lose me."
    assert same["is_edited"] is True


def test_regenerate_can_overwrite_when_asked(client, fake_adapter, provider_row) -> None:
    campaign = _generated(client)
    post = campaign["posts"][0]

    client.patch(f"/api/ai/campaign-posts/{post['id']}", json={"content": "Old text."})
    client.post(
        f"/api/ai/campaign-posts/{post['id']}/regenerate",
        json={"overwrite_edited": True},
    )
    wait_for_generation()

    after = client.get(f"/api/ai/campaigns/{campaign['id']}").json()
    same = next(p for p in after["posts"] if p["id"] == post["id"])
    assert same["effective_content"] != "Old text."
    assert same["is_edited"] is False


def test_a_post_can_be_removed_from_a_campaign(client, fake_adapter, provider_row) -> None:
    campaign = _generated(client)
    post = campaign["posts"][0]

    assert client.delete(f"/api/ai/campaign-posts/{post['id']}").status_code == 204
    assert len(client.get(f"/api/ai/campaigns/{campaign['id']}").json()["posts"]) == 1


def test_a_failed_post_can_never_be_published(
    client, fake_adapter, provider_row, fake_n8n
) -> None:
    """The real safety net: a generation failure must not become a blank post."""
    from app.services.ai.base import ProviderAuthError

    fake_adapter.fail_with = ProviderAuthError("invalid key")
    campaign = make_campaign(client, PUBLISH_TARGETS)
    client.post(f"/api/ai/campaigns/{campaign['id']}/generate", json={})
    wait_for_generation()

    posts = client.get(f"/api/ai/campaigns/{campaign['id']}").json()["posts"]
    assert posts and all(p["generation_status"] == "failed" for p in posts)

    for post in posts:
        r = client.post(f"/api/ai/campaign-posts/{post['id']}/publish", json={})
        assert r.status_code in (200, 400)
        body = r.json()
        if r.status_code == 200:
            assert body["ok"] is False

    assert fake_n8n.calls == []


def test_whitespace_only_edit_falls_back_to_the_draft(
    client, fake_adapter, provider_row, fake_n8n
) -> None:
    """Blanking the text must not blank the published post."""
    campaign = _generated(client, PUBLISH_TARGETS)
    post = _post_for(campaign, "company_linkedin")

    assert client.patch(
        f"/api/ai/campaign-posts/{post['id']}", json={"content": "   "}
    ).status_code == 400

    client.post(f"/api/ai/campaign-posts/{post['id']}/publish", json={})
    assert fake_n8n.calls[0]["content"] == post["generated_content"]


# --------------------------------------------------------------------------- #
# publishing
# --------------------------------------------------------------------------- #
def test_publish_sends_a_normalised_payload_to_n8n(
    client, fake_adapter, provider_row, fake_n8n
) -> None:
    campaign = _generated(client, PUBLISH_TARGETS)
    post = _post_for(campaign, "company_linkedin")

    r = client.post(f"/api/ai/campaign-posts/{post['id']}/publish", json={})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True, body
    assert body["status"] == "published"
    assert body["publication_id"] == "buf_test_123"

    call = fake_n8n.calls[0]
    assert call["account_id"] == "company"
    assert call["channel_id"] == "company_linkedin"
    assert call["media"] == []
    # Same shape Quick Publish sends, plus campaign traceability.
    assert "content" in call and "publish_mode" in call


def test_instagram_posts_are_publishable_because_they_get_an_image(
    client, fake_adapter, provider_row, fake_n8n
) -> None:
    """Instagram refuses text-only posts, so generation attaches an image.

    This is the behaviour that previously made an Instagram draft impossible
    to send.
    """
    campaign = _generated(client)
    post = _post_for(campaign, "company_instagram")

    assert len(post["media"]) == 1

    r = client.post(f"/api/ai/campaign-posts/{post['id']}/publish", json={})
    assert r.status_code == 200
    assert r.json()["ok"] is True, r.json()
    assert fake_n8n.calls[0]["media"][0]["type"] == "image"


def test_instagram_is_refused_when_its_media_is_removed(
    client, fake_adapter, provider_row, fake_n8n
) -> None:
    """Fail loudly rather than let Buffer reject it later."""
    campaign = _generated(client)
    post = _post_for(campaign, "company_instagram")

    client.patch(f"/api/ai/campaign-posts/{post['id']}", json={"media": []})
    r = client.post(f"/api/ai/campaign-posts/{post['id']}/publish", json={})

    assert r.json()["ok"] is False
    assert r.json()["error_code"] == "MEDIA_INVALID"
    assert fake_n8n.calls == []


def test_published_post_is_locked_and_counted(
    client, fake_adapter, provider_row, fake_n8n
) -> None:
    campaign = _generated(client, PUBLISH_TARGETS)
    post = _post_for(campaign, "company_linkedin")
    client.post(f"/api/ai/campaign-posts/{post['id']}/publish", json={})

    after = client.get(f"/api/ai/campaigns/{campaign['id']}").json()
    same = next(p for p in after["posts"] if p["id"] == post["id"])
    assert same["publishing_status"] == "published"
    assert after["published_count"] == 1

    # A published post must not be editable any more.
    assert client.patch(
        f"/api/ai/campaign-posts/{post['id']}", json={"content": "too late"}
    ).status_code in (400, 409)


def test_publishing_twice_is_rejected(client, fake_adapter, provider_row, fake_n8n) -> None:
    campaign = _generated(client, PUBLISH_TARGETS)
    post = _post_for(campaign, "company_linkedin")

    client.post(f"/api/ai/campaign-posts/{post['id']}/publish", json={})
    second = client.post(f"/api/ai/campaign-posts/{post['id']}/publish", json={})
    assert second.status_code in (400, 409)
    assert len(fake_n8n.calls) == 1


def test_idempotency_key_prevents_a_double_publish(
    client, fake_adapter, provider_row, fake_n8n
) -> None:
    campaign = _generated(client, PUBLISH_TARGETS)
    post = _post_for(campaign, "company_linkedin")

    body = {"idempotency_key": "campaign-run-abc"}
    first = client.post(f"/api/ai/campaign-posts/{post['id']}/publish", json=body)
    assert first.status_code == 200

    second = client.post(f"/api/ai/campaign-posts/{post['id']}/publish", json=body)
    assert second.status_code in (200, 400, 409)
    assert len(fake_n8n.calls) == 1


def test_upstream_failure_is_reported_not_swallowed(
    client, fake_adapter, provider_row, fake_n8n
) -> None:
    campaign = _generated(client, PUBLISH_TARGETS)
    post = _post_for(campaign, "company_linkedin")

    fake_n8n.fail(code="BUFFER_SERVER_ERROR", message="upstream exploded")
    r = client.post(f"/api/ai/campaign-posts/{post['id']}/publish", json={})

    body = r.json()
    assert body["ok"] is False
    assert body["error_code"] == "BUFFER_SERVER_ERROR"

    # It stays retryable rather than being marked done.
    after = client.get(f"/api/ai/campaigns/{campaign['id']}").json()
    same = next(p for p in after["posts"] if p["id"] == post["id"])
    assert same["publishing_status"] == "failed"
    assert client.patch(
        f"/api/ai/campaign-posts/{post['id']}", json={"content": "retry me"}
    ).status_code == 200


def test_schedule_requires_a_future_time(client, fake_adapter, provider_row, fake_n8n) -> None:
    from datetime import datetime, timedelta, timezone

    campaign = _generated(client, PUBLISH_TARGETS)
    post = _post_for(campaign, "company_linkedin")

    past = client.post(
        f"/api/ai/campaign-posts/{post['id']}/schedule",
        json={"scheduled_at": "2020-01-01T00:00:00", "timezone": "Asia/Dhaka"},
    )
    # The route reports per-post outcomes, so a refusal is ok=false, not a 4xx.
    assert past.json()["ok"] is False
    assert past.json()["error_code"] == "SCHEDULE_IN_PAST"
    assert fake_n8n.calls == []

    soon = (datetime.now(timezone.utc) + timedelta(days=2)).replace(microsecond=0)
    future = client.post(
        f"/api/ai/campaign-posts/{post['id']}/schedule",
        json={"scheduled_at": soon.isoformat(), "timezone": "Asia/Dhaka"},
    )
    assert future.status_code == 200
    assert future.json()["status"] == "scheduled", future.json()

    after = client.get(f"/api/ai/campaigns/{campaign['id']}").json()
    same = next(p for p in after["posts"] if p["id"] == post["id"])
    assert same["publishing_status"] == "scheduled"


def test_schedule_beyond_the_horizon_is_refused(
    client, fake_adapter, provider_row, fake_n8n
) -> None:
    campaign = _generated(client, PUBLISH_TARGETS)
    post = _post_for(campaign, "company_linkedin")

    r = client.post(
        f"/api/ai/campaign-posts/{post['id']}/schedule",
        json={"scheduled_at": "2099-01-01T09:00:00", "timezone": "Asia/Dhaka"},
    )
    assert r.json()["ok"] is False
    assert r.json()["error_code"] == "SCHEDULE_TOO_FAR"
    assert fake_n8n.calls == []


def test_bulk_publish_publishes_every_selection(
    client, fake_adapter, provider_row, fake_n8n
) -> None:
    campaign = _generated(client, PUBLISH_TARGETS)
    ids = [p["id"] for p in campaign["posts"]]

    r = client.post("/api/ai/campaign-posts/bulk-publish", json={"post_ids": ids})
    assert r.status_code == 200
    body = r.json()
    assert body["requested"] == 2
    assert body["succeeded"] == 2
    assert body["failed"] == 0
    assert len(fake_n8n.calls) == 2


def test_bulk_publish_reports_per_post_results(
    client, fake_adapter, provider_row, fake_n8n
) -> None:
    """Each row gets its own result, including any that refuse."""
    campaign = _generated(client)
    ids = [p["id"] for p in campaign["posts"]]

    r = client.post("/api/ai/campaign-posts/bulk-publish", json={"post_ids": ids})
    assert r.status_code == 200
    body = r.json()
    assert len(body["results"]) == 2
    assert all("ok" in item for item in body["results"])

    by_channel = {item["channel_id"]: item for item in body["results"]}
    assert by_channel["company_instagram"]["ok"] is True
    assert by_channel["company_linkedin"]["ok"] is True
    assert body["succeeded"] == 2
    assert body["failed"] == 0


def test_bulk_publish_marks_one_failure_without_blocking_the_rest(
    client, fake_adapter, provider_row, fake_n8n
) -> None:
    """A post that cannot send must not stop the others going out."""
    campaign = _generated(client)
    ids = [p["id"] for p in campaign["posts"]]
    instagram = _post_for(campaign, "company_instagram")

    client.patch(f"/api/ai/campaign-posts/{instagram['id']}", json={"media": []})

    r = client.post("/api/ai/campaign-posts/bulk-publish", json={"post_ids": ids})
    body = r.json()
    assert body["succeeded"] == 1
    assert body["failed"] == 1

    # The healthy post still reached the network.
    assert len(fake_n8n.calls) == 1
    assert fake_n8n.calls[0]["channel_id"] == "company_linkedin"


def test_bulk_publish_ignores_unselected_posts(
    client, fake_adapter, provider_row, fake_n8n, db, bound_company_channels
) -> None:
    """Selecting two of three must publish exactly two."""
    from app.models.entities import Channel

    db.add(
        Channel(
            id="company_facebook_2",
            account_id="company",
            platform="facebook",
            display_name="Company Facebook (page 2)",
            # A brand-new channel has to be bound before it can publish. The
            # second page is added here to make the bulk selection three wide,
            # so it is bound the same way a real import would bind it.
            external_channel_id="buf_sma_fb_2",
            integration_id=bound_company_channels,
            connection_status="connected",
        )
    )
    db.commit()

    campaign = _generated(
        client,
        targets=[
            {"account_id": "company", "channel_id": "company_linkedin"},
            {"account_id": "company", "channel_id": "company_facebook"},
            {"account_id": "company", "channel_id": "company_facebook_2"},
        ],
    )
    ids = [p["id"] for p in campaign["posts"]]
    assert len(ids) == 3

    client.post("/api/ai/campaign-posts/bulk-publish", json={"post_ids": ids[:2]})
    assert len(fake_n8n.calls) == 2

    after = client.get(f"/api/ai/campaigns/{campaign['id']}").json()
    untouched = next(p for p in after["posts"] if p["id"] == ids[2])
    assert untouched["publishing_status"] == "draft"


def test_bulk_publish_rejects_an_empty_selection(
    client, fake_adapter, provider_row, fake_n8n
) -> None:
    campaign = _generated(client, PUBLISH_TARGETS)
    r = client.post("/api/ai/campaign-posts/bulk-publish", json={"post_ids": []})
    assert r.status_code in (400, 422)
    assert fake_n8n.calls == []


def test_campaign_history_records_published_campaigns(
    client, fake_adapter, provider_row, fake_n8n
) -> None:
    campaign = _generated(client, PUBLISH_TARGETS)
    client.post("/api/ai/campaign-posts/bulk-publish", json={"post_ids": [p["id"] for p in campaign["posts"]]})

    history = client.get("/api/ai/campaigns", params={"status": "published"}).json()
    assert len(history["items"]) == 1
    assert history["items"][0]["published_count"] == 2


def test_partially_published_campaign_is_labelled_distinctly(
    client, fake_adapter, provider_row, fake_n8n
) -> None:
    """Publishing one of two must not claim the campaign is done."""
    campaign = _generated(client, PUBLISH_TARGETS)
    client.post(f"/api/ai/campaign-posts/{campaign['posts'][0]['id']}/publish", json={})

    body = client.get(f"/api/ai/campaigns/{campaign['id']}").json()
    assert body["published_count"] == 1
    assert body["status"] == "partially_published"
    assert len(client.get("/api/ai/campaigns", params={"status": "published"}).json()["items"]) == 0