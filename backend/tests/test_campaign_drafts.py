"""Campaign drafts built in Quick Publish.

The flow: compose a post for a channel, add it to a draft, name the campaign,
save. The campaign then appears in Campaigns and stays fully editable.
"""
from __future__ import annotations

import pytest

INSTAGRAM = "company_instagram"
LINKEDIN = "company_linkedin"
FACEBOOK = "company_facebook"


def _draft(client, drafts, name="October launch", topic=None):
    return client.post(
        "/api/ai/campaigns",
        json={
            "name": name,
            "topic": topic or name,
            "drafts": drafts,
            "targets": [],
        },
    )


def test_a_named_draft_campaign_is_created_from_quick_publish(client) -> None:
    r = _draft(
        client,
        [
            {"account_id": "company", "channel_id": INSTAGRAM, "content": "Instagram copy"},
            {"account_id": "company", "channel_id": LINKEDIN, "content": "LinkedIn copy"},
        ],
    )
    assert r.status_code == 201, r.text
    body = r.json()

    # The user's chosen name is kept, not replaced by the topic.
    assert body["name"] == "October launch"
    assert body["status"] == "draft"
    assert body["source"] == "quick_publish"
    assert body["target_count"] == 2


def test_each_draft_keeps_its_own_text_and_media(client) -> None:
    r = _draft(
        client,
        [
            {
                "account_id": "company",
                "channel_id": INSTAGRAM,
                "content": "Instagram copy",
                "media": [{"type": "image", "url": "https://cdn.example.com/a.png"}],
            },
            {"account_id": "company", "channel_id": LINKEDIN, "content": "LinkedIn copy"},
        ],
    )
    posts = {p["channel_id"]: p for p in r.json()["posts"]}

    assert posts[INSTAGRAM]["effective_content"] == "Instagram copy"
    assert len(posts[INSTAGRAM]["media"]) == 1
    assert posts[LINKEDIN]["effective_content"] == "LinkedIn copy"
    assert posts[LINKEDIN]["media"] == []


def test_draft_content_is_editable_and_publishable(client, fake_n8n) -> None:
    """The whole point of the feature: edit, add media, then publish."""
    campaign = _draft(
        client,
        [{"account_id": "company", "channel_id": LINKEDIN, "content": "First version"}],
    ).json()
    post = campaign["posts"][0]

    # Edit the copy.
    edited = client.patch(
        f"/api/ai/campaign-posts/{post['id']}", json={"content": "Second version"}
    )
    assert edited.json()["effective_content"] == "Second version"

    # Add media.
    with_media = client.patch(
        f"/api/ai/campaign-posts/{post['id']}",
        json={"media": [{"type": "image", "url": "https://cdn.example.com/b.png"}]},
    )
    assert len(with_media.json()["media"]) == 1

    # Publish.
    result = client.post(f"/api/ai/campaign-posts/{post['id']}/publish", json={})
    assert result.json()["ok"] is True, result.json()
    assert fake_n8n.calls[0]["content"] == "Second version"
    assert fake_n8n.calls[0]["media"][0]["url"] == "https://cdn.example.com/b.png"


def test_a_draft_does_not_publish_itself(client, fake_n8n) -> None:
    _draft(client, [{"account_id": "company", "channel_id": LINKEDIN, "content": "Wait"}])
    assert fake_n8n.calls == []


def test_draft_campaign_appears_in_history(client) -> None:
    _draft(client, [{"account_id": "company", "channel_id": LINKEDIN, "content": "Hi"}], name="Saved one")

    page = client.get("/api/ai/campaigns").json()
    assert len(page["items"]) == 1
    assert page["items"][0]["source"] == "quick_publish"
    assert page["items"][0]["name"] == "Saved one"


def test_drafts_can_be_filtered_apart_from_ai_campaigns(client, fake_adapter, provider_row) -> None:
    from tests.conftest import make_campaign

    _draft(client, [{"account_id": "company", "channel_id": LINKEDIN, "content": "Hand written"}])
    make_campaign(client, [{"account_id": "company", "channel_id": FACEBOOK}])

    quick = client.get("/api/ai/campaigns", params={"source": "quick_publish"}).json()
    assert len(quick["items"]) == 1

    ai = client.get("/api/ai/campaigns", params={"source": "ai_campaign"}).json()
    assert len(ai["items"]) == 1

    everything = client.get("/api/ai/campaigns", params={"source": "all"}).json()
    assert len(everything["items"]) == 2


def test_an_empty_campaign_name_falls_back_to_the_topic(client) -> None:
    r = _draft(
        client,
        [{"account_id": "company", "channel_id": LINKEDIN, "content": "x"}],
        name="   ",
        topic="Seasonal offer",
    )
    assert r.status_code == 201
    assert r.json()["name"] == "Seasonal offer"


def test_a_campaign_with_nothing_to_do_is_refused(client) -> None:
    """An empty draft would be a silent no-op, so it must be an error."""
    r = client.post(
        "/api/ai/campaigns", json={"topic": "Nothing", "drafts": [], "targets": []}
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "AI_NO_CHANNELS_SELECTED"


def test_blank_draft_content_is_rejected(client) -> None:
    r = _draft(client, [{"account_id": "company", "channel_id": LINKEDIN, "content": "  "}])
    assert r.status_code == 422


def test_a_draft_for_an_unknown_channel_is_rejected(client) -> None:
    r = _draft(client, [{"account_id": "company", "channel_id": "nope", "content": "x"}])
    assert r.status_code in (400, 404)


def test_duplicate_channels_collapse_to_one_post(client) -> None:
    r = _draft(
        client,
        [
            {"account_id": "company", "channel_id": LINKEDIN, "content": "first"},
            {"account_id": "company", "channel_id": LINKEDIN, "content": "second"},
        ],
    )
    assert r.status_code == 201
    # One post per channel, so the last write wins rather than duplicating.
    assert r.json()["target_count"] == 1


def test_a_draft_with_three_channels(client) -> None:
    r = _draft(
        client,
        [
            {"account_id": "company", "channel_id": ch, "content": f"copy for {ch}"}
            for ch in (INSTAGRAM, LINKEDIN, FACEBOOK)
        ],
    )
    assert r.status_code == 201
    assert r.json()["target_count"] == 3
    assert all(p["generation_status"] == "generated" for p in r.json()["posts"])


def test_generation_can_still_be_run_on_a_draft(client, fake_adapter, provider_row) -> None:
    """A saved draft is a normal campaign: it can be regenerated too."""
    campaign = _draft(
        client, [{"account_id": "company", "channel_id": LINKEDIN, "content": "hand written"}]
    ).json()

    r = client.post(f"/api/ai/campaigns/{campaign['id']}/generate", json={})
    assert r.status_code == 200

    import time

    from tests.conftest import wait_for_generation

    wait_for_generation()
    body = client.get(f"/api/ai/campaigns/{campaign['id']}").json()
    assert body["generated_count"] == 1
    # The hand-written copy is replaced by the generated one.
    assert body["posts"][0]["effective_content"] != "hand written"