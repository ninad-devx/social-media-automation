"""AI provider configuration: storage, masking, probes, selection."""
from __future__ import annotations

from app.services.ai_provider_service import ProviderService
from cryptography.fernet import Fernet

from app.services.crypto import SecretCipher, mask_secret

SECRET = "sk-super-secret-value-do-not-leak-987654"


def _create(client, **overrides):
    payload = {
        "provider_slug": "openai",
        "display_name": "My OpenAI",
        "api_key": SECRET,
        "model": "gpt-4o",
    }
    payload.update(overrides)
    return client.post("/api/ai/providers", json=payload)


def test_catalogue_lists_supported_providers(client) -> None:
    r = client.get("/api/ai/providers/catalogue")
    assert r.status_code == 200
    slugs = {item["slug"] for item in r.json()}
    assert {"openai", "anthropic", "gemini", "openai_compatible"} <= slugs


def test_create_provider_encrypts_and_never_returns_the_key(client, db) -> None:
    r = _create(client)
    assert r.status_code == 201, r.text
    body = r.json()

    assert body["id"].startswith("aip_")
    assert body["provider_slug"] == "openai"
    assert body["model"] == "gpt-4o"
    assert body["has_api_key"] is True

    # The response must not leak the key in any form.
    assert SECRET not in r.text
    assert body["api_key_masked"] == mask_secret(SECRET)
    assert SECRET[-4:] in body["api_key_masked"]

    # ...and the stored value must be ciphertext, not plaintext.
    row = db.get(__import__("app.models", fromlist=["AIProvider"]).AIProvider, body["id"])
    assert row.api_key_encrypted != SECRET
    assert row.api_key_encrypted.startswith("enc:v1:")
    assert SECRET not in row.api_key_encrypted


def test_key_can_be_decrypted_by_the_service_but_not_by_the_api(client, db, settings) -> None:
    created = _create(client).json()
    service = ProviderService(db, settings)
    row = service.get(created["id"])
    assert service.decrypt(row) == SECRET


def test_secret_never_appears_in_error_bodies(client, settings) -> None:
    # A failure must not echo the key back.
    r = _create(client, provider_slug="nope-not-real")
    assert r.status_code in (400, 422, 500)
    assert SECRET not in r.text


def test_get_list_and_update(client) -> None:
    created = _create(client).json()

    listed = client.get("/api/ai/providers").json()
    assert len(listed) == 1
    assert SECRET not in client.get("/api/ai/providers").text

    updated = client.patch(f"/api/ai/providers/{created['id']}", json={"model": "gpt-4.1", "enabled": False})
    assert updated.status_code == 200
    assert updated.json()["model"] == "gpt-4.1"
    assert updated.json()["enabled"] is False


def test_rotating_the_key_replaces_ciphertext(client, db, settings) -> None:
    created = _create(client).json()
    service = ProviderService(db, settings)
    before = service.get(created["id"]).api_key_encrypted

    client.patch(f"/api/ai/providers/{created['id']}", json={"api_key": "sk-rotated-value-000111222"})
    after = service.get(created["id"]).api_key_encrypted

    assert after != before
    assert service.decrypt(service.get(created["id"])) == "sk-rotated-value-000111222"


def test_default_provider_selection(client) -> None:
    a = _create(client, display_name="First").json()
    b = _create(client, display_name="Second").json()

    listed = {p["id"]: p for p in client.get("/api/ai/providers").json()}
    # Exactly one default.
    assert sum(1 for p in listed.values() if p["is_default"]) == 1
    assert listed[a["id"]]["is_default"] is True

    client.patch(f"/api/ai/providers/{b['id']}", json={"make_default": True})
    listed = {p["id"]: p for p in client.get("/api/ai/providers").json()}
    assert listed[b["id"]]["is_default"] is True
    assert listed[a["id"]]["is_default"] is False


def test_delete_promotes_another_default(client) -> None:
    a = _create(client, display_name="A").json()
    b = _create(client, display_name="B").json()

    assert client.delete(f"/api/ai/providers/{a['id']}").status_code == 204
    remaining = client.get("/api/ai/providers").json()
    assert len(remaining) == 1
    assert remaining[0]["id"] == b["id"]
    assert remaining[0]["is_default"] is True


def test_openai_compatible_requires_base_url(client) -> None:
    r = _create(client, provider_slug="openai_compatible", model="local-model")
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "AI_PROVIDER_BASE_URL_REQUIRED"

    ok = _create(
        client,
        provider_slug="openai_compatible",
        model="local-model",
        base_url="http://localhost:11434/v1",
    )
    assert ok.status_code == 201
    assert ok.json()["base_url"] == "http://localhost:11434/v1"


def test_missing_api_key_is_rejected(client) -> None:
    r = client.post("/api/ai/providers", json={"provider_slug": "openai"})
    assert r.status_code == 422


def test_test_connection_reports_invalid_credentials(client, monkeypatch) -> None:
    created = _create(client).json()

    from app.services.ai.base import ProviderInfo
    from app.services.ai.openai_adapter import OpenAIAdapter

    def boom(self):
        return ProviderInfo(ok=False, detail="The provider rejected this API key.", error_code="AI_AUTH_ERROR")

    monkeypatch.setattr(OpenAIAdapter, "validate_credentials", boom)

    r = client.post(f"/api/ai/providers/{created['id']}/test")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert body["error_code"] == "AI_AUTH_ERROR"
    assert SECRET not in r.text

    # Status is persisted for the Settings UI.
    after = client.get("/api/ai/providers").json()[0]
    assert after["last_status"] == "error"
    assert after["last_tested_at"] is not None


def test_test_connection_success(client, monkeypatch) -> None:
    created = _create(client).json()

    from app.services.ai.base import ProviderInfo
    from app.services.ai.base import ProviderInfo
    from app.services.ai.openai_adapter import OpenAIAdapter

    monkeypatch.setattr(
        OpenAIAdapter, "validate_credentials", lambda self: ProviderInfo(ok=True, detail="Connected.")
    )
    r = client.post(f"/api/ai/providers/{created['id']}/test")
    assert r.json()["ok"] is True
    assert client.get("/api/ai/providers").json()[0]["last_status"] == "ok"


def test_model_discovery(client, fake_adapter, monkeypatch) -> None:
    created = _create(client).json()
    r = client.get(f"/api/ai/providers/{created['id']}/models")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert "fake-model-1" in body["models"]
    # Manual entry is always allowed.
    assert body["manual_entry_allowed"] is True


def test_no_provider_configured_is_a_clean_error(client, db, settings) -> None:
    from app.services.errors import AppError

    service = ProviderService(db, settings)
    try:
        service.adapter_for_campaign()
        raise AssertionError("should have raised")
    except AppError as exc:
        assert exc.code == "AI_NO_PROVIDER"
        assert "Settings" in exc.user_message


def test_generation_refuses_when_no_provider_configured(client) -> None:
    r = client.post("/api/ai/campaigns", json={
        "topic": "Something",
        "targets": [{"account_id": "company", "channel_id": "company_linkedin"}],
    })
    assert r.status_code == 201

    gen = client.post(f"/api/ai/campaigns/{r.json()['id']}/generate", json={})
    assert gen.status_code == 400
    assert gen.json()["error"]["code"] == "AI_NO_PROVIDER"


def test_cipher_refuses_plaintext_and_bad_keys() -> None:
    # A malformed key is rejected loudly at construction, never silently ignored.
    try:
        SecretCipher("bogus")
        raise AssertionError("an invalid key should raise")
    except Exception as exc:
        assert "Fernet key" in str(exc) or "not a valid" in str(exc)

    # A valid cipher refuses ciphertext it did not produce.
    cipher = SecretCipher(Fernet.generate_key().decode())
    try:
        cipher.decrypt("plaintext-value")
        raise AssertionError("should refuse")
    except Exception as exc:
        assert "encrypted format" in str(exc)

    # And without a key configured, encryption is refused rather than degraded.
    assert SecretCipher(None).available is False