"""Instruction storage, inheritance and isolation between brands."""
from __future__ import annotations

import pytest

from app.services.prompt_service import effective_for_channel

GLOBAL_TEXT = "Always write in British English. Never invent statistics."
ACCOUNT_TEXT = "We sell a B2B scheduling tool called Acme Flow."
CHANNEL_TEXT = "Keep it under 900 characters and lead with the CTA."


def _put_global(client, content: str):
    return client.put("/api/ai/instructions", json={"content": content})


def _put_account(client, account_id: str, **fields):
    payload = {"description": fields.pop("description", "")}
    payload.update(fields)
    return client.put(f"/api/ai/accounts/{account_id}/instructions", json=payload)


def test_global_instructions_round_trip(client) -> None:
    assert client.get("/api/ai/instructions").json()["content"] == ""

    saved = _put_global(client, GLOBAL_TEXT)
    assert saved.status_code == 200
    assert saved.json()["content"] == GLOBAL_TEXT
    assert client.get("/api/ai/instructions").json()["content"] == GLOBAL_TEXT

    # Overwriting replaces rather than appends.
    _put_global(client, "Only one set of global rules.")
    assert client.get("/api/ai/instructions").json()["content"] == "Only one set of global rules."


def test_account_instructions_round_trip(client) -> None:
    r = _put_account(
        client,
        "company",
        description="B2B scheduling",
        industry="SaaS",
        voice="direct and calm",
        avoid_words="synergy, revolutionary",
    )
    assert r.status_code == 200
    body = r.json()
    assert body["configured"] is True
    assert body["industry"] == "SaaS"
    assert body["avoid_words"] == "synergy, revolutionary"

    assert client.get("/api/ai/accounts/company/instructions").json()["voice"] == "direct and calm"


def test_unknown_account_and_channel_are_rejected(client) -> None:
    assert _put_account(client, "nope").status_code == 404
    assert client.put(
        "/api/ai/channels/nope/instructions", json={"instructions": "x"}
    ).status_code == 404
    assert client.get("/api/ai/channels/nope/instructions").status_code == 404


def test_blank_fields_are_stored_as_null_not_empty_strings(client) -> None:
    _put_account(client, "company", description="   ")
    body = client.get("/api/ai/accounts/company/instructions").json()
    # A whitespace-only brand description counts as "not configured".
    assert body["description"] is None
    assert body["configured"] is False


def test_channel_instructions_can_be_reset_to_inherited(client) -> None:
    put = client.put(
        "/api/ai/channels/company_linkedin/instructions",
        json={"instructions": CHANNEL_TEXT},
    )
    assert put.status_code == 200
    assert put.json()["configured"] is True
    assert put.json()["platform"] == "linkedin"

    reset = client.delete("/api/ai/channels/company_linkedin/instructions")
    assert reset.status_code == 200
    assert reset.json()["reset"] is True
    assert client.get("/api/ai/channels/company_linkedin/instructions").json()["configured"] is False


def test_effective_instructions_merge_all_three_layers(client) -> None:
    _put_global(client, GLOBAL_TEXT)
    _put_account(client, "company", description=ACCOUNT_TEXT)
    client.put("/api/ai/channels/company_linkedin/instructions", json={"instructions": CHANNEL_TEXT})

    r = client.get("/api/ai/channels/company_linkedin/effective-instructions")
    assert r.status_code == 200
    body = r.json()

    text = body["text"]
    # All three layers must be present.
    assert GLOBAL_TEXT in text
    assert ACCOUNT_TEXT in text
    assert CHANNEL_TEXT in text

    # The absolute safety rules are always applied first and cannot be skipped.
    assert body["layers"]
    assert body["layers"][0]["key"] == "system"
    assert "system_rules_preview"


def test_system_rules_precede_every_user_layer(client) -> None:
    _put_global(client, GLOBAL_TEXT)
    _put_account(client, "company", description=ACCOUNT_TEXT)

    text = client.get(
        "/api/ai/channels/company_instagram/effective-instructions"
    ).json()["text"]

    # Match unwrapped phrases: SYSTEM_RULES hard-wraps its own lines.
    safety_pos = text.find("ABSOLUTE RULES")
    global_pos = text.find("British English")
    brand_pos = text.find("Acme Flow")
    assert safety_pos != -1 and global_pos != -1 and brand_pos != -1
    assert safety_pos < global_pos < brand_pos


def test_sibling_channels_do_not_leak_into_each_other(client) -> None:
    """A LinkedIn-only rule must never reach an Instagram post."""
    client.put(
        "/api/ai/channels/company_linkedin/instructions",
        json={"instructions": "LINKEDIN_ONLY_MARKER end with a question."},
    )

    instagram = client.get(
        "/api/ai/channels/company_instagram/effective-instructions"
    ).json()["text"]
    assert "LINKEDIN_ONLY_MARKER" not in instagram


def test_channel_of_a_different_account_does_not_leak(client, db) -> None:
    """Only the target brand's context is loaded."""
    from app.models.ai_entities import ChannelInstructions
    from app.models.entities import Account, Channel

    other_account = Account(id="rival", name="Rival Co")
    db.add(other_account)
    db.add(Channel(id="rival_ig", account_id="rival", platform="instagram", display_name="Rival IG"))
    db.flush()
    db.add(ChannelInstructions(channel_id="rival_ig", instructions="RIVAL_ONLY_MARKER"))
    db.commit()

    text = client.get(
        "/api/ai/channels/company_instagram/effective-instructions"
    ).json()["text"]
    assert "RIVAL_ONLY_MARKER" not in text


def test_test_generation_requires_a_provider(client) -> None:
    r = client.post(
        "/api/ai/channels/company_linkedin/test-generation",
        json={"topic": "Our new scheduling feature"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert body["error_code"] == "AI_NO_PROVIDER"
    assert "Settings" in body["message"]


def test_test_generation_returns_a_sample_post(client, fake_adapter, provider_row) -> None:
    r = client.post(
        "/api/ai/channels/company_linkedin/test-generation",
        json={"topic": "Our new scheduling feature"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["post_text"]
    assert isinstance(body["hashtags"], list)


def test_effective_instruction_layers_report_which_layer_won(client) -> None:
    _put_account(client, "company", description="Brand level voice.")
    layers = client.get(
        "/api/ai/channels/company_instagram/effective-instructions"
    ).json()["layers"]

    keys = [layer["key"] for layer in layers]
    assert keys == ["system", "global", "brand", "account", "channel", "campaign"]
    # Ordered weakest -> strongest.
    assert [layer["rank"] for layer in layers] == sorted(layer["rank"] for layer in layers)

    configured = {layer["key"]: layer["configured"] for layer in layers}
    assert configured["system"] is True
    assert configured["brand"] is True
    # A channel with no override of its own must not claim a channel layer.
    assert configured["channel"] is False


def test_effective_for_channel_is_pure() -> None:
    """The resolver must not depend on a database session."""
    class _Account:
        description = "Brand voice"
        instructions = "Account voice"

    class _Channel:
        instructions = "Channel voice"

    class _Global:
        content = "Global voice"

    effective = effective_for_channel(_Account(), _Channel(), _Global())
    rendered = "\n".join(
        [effective.global_text or "", effective.account_text or "", effective.channel_text or ""]
    )
    assert "Account voice" in rendered
    assert "Channel voice" in rendered
    assert "Global voice" in rendered
    assert "Brand voice" in effective.brand_fields.values()
    assert effective.sources == {"global": True, "account": True, "channel": True}


def test_missing_rows_are_handled_without_a_session(client) -> None:
    """No instructions anywhere must still produce a usable prompt."""
    effective = effective_for_channel(None, None, None)
    assert effective.sources == {"global": False, "account": False, "channel": False}
    assert effective.brand_fields == {}