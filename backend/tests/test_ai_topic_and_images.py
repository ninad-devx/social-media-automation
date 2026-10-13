"""Topic primacy and image generation.

Two regressions this guards against:

1. The model writing about the brand's product instead of the campaign topic.
   The brand profile used to be framed as commanding subject matter, and the
   topic only appeared at the very bottom of the user prompt.
2. Posts for platforms that cannot publish text-only (Instagram) having no
   media, which made them permanently unpublishable.
"""
from __future__ import annotations

import json

import pytest

from app.services.ai.image_service import ImageGenerationError, image_prompt_for, render_card
from app.services.prompt_service import build_system_prompt, build_user_prompt, effective_for_channel
from app.services.platform_limits import requires_media
from tests.conftest import make_campaign, wait_for_generation

GEN_TARGETS = [
    {"account_id": "company", "channel_id": "company_linkedin"},
    {"account_id": "company", "channel_id": "company_instagram"},
]


def _prompt(client, topic: str, brand: str | None = None, context: str | None = None) -> str:
    campaign = client.post(
        "/api/ai/campaigns",
        json={
            "topic": topic,
            "context": context,
            "targets": GEN_TARGETS,
        },
    ).json()
    body = client.get(
        f"/api/ai/campaigns/{campaign['id']}"
    ).json()
    return body["posts"][0]["id"]


# --------------------------------------------------------------------------- #
# prompt construction
# --------------------------------------------------------------------------- #
def _system_prompt(db, topic: str, brand: str | None = None, context: str | None = None) -> str:
    from app.models.ai_entities import Campaign
    from app.models.entities import Account, Channel

    account = Account(id="company", name="Acme")
    channel = Channel(id="company_linkedin", account_id="company", platform="linkedin")
    campaign = Campaign(id="c", name="c", topic=topic, context=context, language="en", status="draft")

    class _Instr:
        def __init__(self, values):
            for k, v in values.items():
                setattr(self, k, v)

    account_instr = _Instr({"description": brand, "instructions": None}) if brand else None
    effective = effective_for_channel(account_instr, None, None)
    return build_system_prompt(effective, account, channel, "LinkedIn", campaign, None)



@pytest.fixture(autouse=True)
def _bind_seeded_channels(bound_company_channels) -> None:
    """The seeded demo channels are bound to a mocked Buffer integration.

    Publishing refuses a channel with no Buffer profile - that refusal is the
    fix this work is verifying - so the fixtures these tests publish through
    have to be genuinely bound. The integration is a throwaway row with an
    encrypted dummy credential; no socket is opened and no real token exists.
    """



def test_the_topic_appears_in_the_system_prompt() -> None:
    """It used to live only at the bottom of the user prompt."""
    text = _system_prompt(None, "Why cities should plant more trees")
    assert "Why cities should plant more trees" in text


def test_the_topic_comes_before_the_brand_profile() -> None:
    text = _system_prompt(None, "Why cities should plant more trees", brand="Acme Flow, a B2B scheduling tool")
    assert text.index("Why cities should plant more trees") < text.index("Acme Flow")


def test_the_brand_profile_is_labelled_as_voice_not_subject() -> None:
    text = _system_prompt(None, "Why cities should plant more trees", brand="Acme Flow, a B2B scheduling tool")
    assert "Voice and audience" in text
    # It must not tell the model to write about the brand's products.
    assert "NOT the subject" in text


def test_system_rules_state_the_topic_wins() -> None:
    text = _system_prompt(None, "Anything")
    assert "CAMPAIGN TOPIC IS THE SUBJECT" in text
    assert "The campaign topic below is what the post is ABOUT" in text


def test_user_prompt_repeats_the_topic() -> None:
    from app.models.ai_entities import Campaign

    campaign = Campaign(id="c", name="c", topic="Why cities should plant more trees", language="en")
    user = build_user_prompt(campaign)
    assert "Why cities should plant more trees" in user
    assert "must be about" in user.lower()


def test_context_is_carried_into_the_system_prompt() -> None:
    text = _system_prompt(None, "Trees", context="The UK planted 2 million trees since 2020.")
    assert "2 million trees" in text


def test_a_missing_brand_profile_does_not_break_the_prompt() -> None:
    text = _system_prompt(None, "Just a topic")
    assert "Just a topic" in text


# --------------------------------------------------------------------------- #
# image rendering
# --------------------------------------------------------------------------- #
def test_rendered_card_is_a_real_png() -> None:
    result = render_card(headline="Scheduling made simple", caption="One calendar for your team.", brand="Acme")
    assert result.data[:8] == b"\x89PNG\r\n\x1a\n"
    assert result.mime_type == "image/png"
    assert result.source == "card"
    assert len(result.data) > 1000


def test_card_survives_empty_and_very_long_input() -> None:
    assert render_card(headline="", caption="").data[:4] == b"\x89PNG"
    long = "word " * 200
    assert render_card(headline=long, caption=long).data[:4] == b"\x89PNG"


def test_image_prompt_does_not_ask_for_text_in_the_image() -> None:
    """Models add garbled lettering when asked to render words."""
    prompt = image_prompt_for("Tree planting", "Some caption", "Instagram")
    assert "no text" in prompt.lower()
    assert "no words" in prompt.lower()
    assert "Tree planting" in prompt


def test_image_prompt_falls_back_to_the_caption() -> None:
    prompt = image_prompt_for("", "A quiet morning in the city park", "LinkedIn")
    assert "quiet morning" in prompt


# --------------------------------------------------------------------------- #
# platform media requirements
# --------------------------------------------------------------------------- #
def test_instagram_is_known_to_require_media() -> None:
    assert requires_media("instagram") is True
    assert requires_media("linkedin") is False
    assert requires_media("facebook") is False


# --------------------------------------------------------------------------- #
# generation attaches images
# --------------------------------------------------------------------------- #
def test_instagram_posts_are_generated_with_media(client, fake_adapter, provider_row) -> None:
    """Without this an Instagram draft can never be published at all."""
    campaign = make_campaign(client, GEN_TARGETS)
    client.post(f"/api/ai/campaigns/{campaign['id']}/generate", json={})
    wait_for_generation()

    body = client.get(f"/api/ai/campaigns/{campaign['id']}").json()
    instagram = next(p for p in body["posts"] if p["platform"] == "instagram")
    linkedin = next(p for p in body["posts"] if p["platform"] == "linkedin")

    assert instagram["generation_status"] == "generated"
    assert len(instagram["media"]) == 1
    assert instagram["media"][0]["type"] == "image"
    assert instagram["media"][0]["url"].startswith("http")

    # A platform that does not require media is left alone.
    assert len(linkedin["media"]) == 0


def test_generate_images_flag_covers_every_platform(
    client, fake_adapter, provider_row
) -> None:
    campaign = client.post(
        "/api/ai/campaigns",
        json={
            "topic": "Tree planting",
            "generate_images": True,
            "targets": GEN_TARGETS,
        },
    ).json()
    assert campaign["generate_images"] is True

    client.post(f"/api/ai/campaigns/{campaign['id']}/generate", json={})
    wait_for_generation()

    body = client.get(f"/api/ai/campaigns/{campaign['id']}").json()
    assert all(len(p["media"]) >= 1 for p in body["posts"])


def test_image_can_be_added_to_any_post_on_demand(
    client, fake_adapter, provider_row, fake_n8n
) -> None:
    campaign = make_campaign(client, GEN_TARGETS)
    client.post(f"/api/ai/campaigns/{campaign['id']}/generate", json={})
    wait_for_generation()

    body = client.get(f"/api/ai/campaigns/{campaign['id']}").json()
    linkedin = next(p for p in body["posts"] if p["platform"] == "linkedin")
    assert len(linkedin["media"]) == 0

    r = client.post(f"/api/ai/campaign-posts/{linkedin['id']}/generate-image")
    assert r.status_code == 200
    assert len(r.json()["media"]) == 1


def test_generated_image_makes_instagram_publishable(
    client, fake_adapter, provider_row, fake_n8n
) -> None:
    """The whole point: Instagram goes from unpublishable to publishable."""
    campaign = make_campaign(client, GEN_TARGETS)
    client.post(f"/api/ai/campaigns/{campaign['id']}/generate", json={})
    wait_for_generation()

    body = client.get(f"/api/ai/campaigns/{campaign['id']}").json()
    instagram = next(p for p in body["posts"] if p["platform"] == "instagram")

    r = client.post(f"/api/ai/campaign-posts/{instagram['id']}/publish", json={})
    assert r.json()["ok"] is True, r.json()

    # The image URL must reach n8n.
    call = fake_n8n.calls[0]
    assert len(call["media"]) == 1
    assert call["media"][0]["type"] == "image"
    assert call["media"][0]["url"]


def test_image_generation_is_refused_after_publishing(
    client, fake_adapter, provider_row, fake_n8n
) -> None:
    campaign = make_campaign(client, [{"account_id": "company", "channel_id": "company_linkedin"}])
    client.post(f"/api/ai/campaigns/{campaign['id']}/generate", json={})
    wait_for_generation()
    post = client.get(f"/api/ai/campaigns/{campaign['id']}").json()["posts"][0]

    client.post(f"/api/ai/campaign-posts/{post['id']}/publish", json={})
    r = client.post(f"/api/ai/campaign-posts/{post['id']}/generate-image")
    assert r.status_code == 409


def test_image_failure_does_not_fail_the_caption(client, fake_adapter, provider_row, monkeypatch) -> None:
    """A missing image model must never mark good copy as failed."""
    import app.services.campaign_service as cs

    monkeypatch.setattr(cs, "render_card", lambda **kw: (_ for _ in ()).throw(RuntimeError("boom")))

    campaign = make_campaign(client, GEN_TARGETS)
    client.post(f"/api/ai/campaigns/{campaign['id']}/generate", json={})
    wait_for_generation()

    body = client.get(f"/api/ai/campaigns/{campaign['id']}").json()
    assert all(p["generation_status"] == "generated" for p in body["posts"])
    # ...just without media.
    assert all(len(p["media"]) == 0 for p in body["posts"])


def test_regenerating_does_not_stack_images(client, fake_adapter, provider_row) -> None:
    campaign = make_campaign(client, GEN_TARGETS)
    client.post(f"/api/ai/campaigns/{campaign['id']}/generate", json={})
    wait_for_generation()

    body = client.get(f"/api/ai/campaigns/{campaign['id']}").json()
    instagram = next(p for p in body["posts"] if p["platform"] == "instagram")

    client.post(f"/api/ai/campaign-posts/{instagram['id']}/regenerate", json={})
    wait_for_generation()

    after = client.get(f"/api/ai/campaigns/{campaign['id']}").json()
    same = next(p for p in after["posts"] if p["id"] == instagram["id"])
    assert len(same["media"]) == 1


# --------------------------------------------------------------------------- #
# schema reconciliation
# --------------------------------------------------------------------------- #
def test_new_columns_are_added_to_existing_tables(tmp_path, monkeypatch) -> None:
    """`create_all` never alters an existing table, so new columns must be
    reconciled in place or every query touching them fails at runtime."""
    import os
    import sqlite3

    from app.database import session as session_module

    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """CREATE TABLE campaigns (
            id VARCHAR(64) PRIMARY KEY,
            name VARCHAR(200) NOT NULL,
            topic TEXT NOT NULL,
            status VARCHAR(32) NOT NULL,
            created_at DATETIME,
            updated_at DATETIME
        )"""
    )
    conn.execute(
        "INSERT INTO campaigns (id,name,topic,status) VALUES ('c1','Existing','Old','ready')"
    )
    conn.commit()
    conn.close()

    from sqlalchemy import create_engine, inspect, text

    engine = create_engine(f"sqlite:///{db_path}")
    monkeypatch.setattr(session_module, "engine", engine)

    session_module.init_db()

    cols = [c["name"] for c in inspect(engine).get_columns("campaigns")]
    assert "generate_images" in cols

    with engine.connect() as c:
        # Existing data survives and the new column has a usable value.
        assert c.execute(text("SELECT topic FROM campaigns")).scalar() == "Old"
        assert c.execute(text("SELECT generate_images FROM campaigns")).scalar() in (0, False)

    # Idempotent: running again must not raise.
    session_module.init_db()


def test_reconciliation_never_drops_data(tmp_path, monkeypatch) -> None:
    import sqlite3

    from sqlalchemy import create_engine, inspect, text

    from app.database import session as session_module

    db_path = tmp_path / "keep.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE campaigns (id VARCHAR(64) PRIMARY KEY, name VARCHAR(200) NOT NULL,"
        " topic TEXT NOT NULL, status VARCHAR(32) NOT NULL)"
    )
    conn.executemany(
        "INSERT INTO campaigns (id,name,topic,status) VALUES (?,?,?,?)",
        [(f"c{i}", f"N{i}", f"Topic {i}", "draft") for i in range(5)],
    )
    conn.commit()
    conn.close()

    engine = create_engine(f"sqlite:///{db_path}")
    monkeypatch.setattr(session_module, "engine", engine)
    session_module.init_db()

    with engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM campaigns")).scalar() == 5
    # Columns are only ever added.
    assert "topic" in [c["name"] for c in inspect(engine).get_columns("campaigns")]