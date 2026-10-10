"""Tests for GET /api/accounts and GET /api/accounts/{id}/channels."""
from __future__ import annotations

from fastapi.testclient import TestClient


def test_list_accounts_returns_active_accounts(client: TestClient) -> None:
    r = client.get("/api/accounts")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, list)
    assert len(body) == 1

    account = body[0]
    assert account["id"] == "company"
    assert account["name"] == "Company"
    assert account["active"] is True
    assert account["channel_count"] == 3


def test_account_list_never_exposes_buffer_fields(client: TestClient) -> None:
    r = client.get("/api/accounts")
    raw = r.text
    for leaked in ("buffer_profile_id", "buffer", "profile_id"):
        assert leaked not in raw.lower()


def test_account_detail_includes_channels(client: TestClient) -> None:
    r = client.get("/api/accounts/company")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == "company"
    assert len(body["channels"]) == 3
    platforms = {c["platform"] for c in body["channels"]}
    assert platforms == {"instagram", "facebook", "linkedin"}


def test_account_detail_unknown_returns_normalized_error(client: TestClient) -> None:
    r = client.get("/api/accounts/does-not-exist")
    assert r.status_code == 404
    body = r.json()
    assert body["success"] is False
    assert body["error"]["code"] == "ACCOUNT_NOT_FOUND"
    assert body["error"]["message"] == "That account no longer exists."
    assert body["request_id"]


def test_list_channels_for_account(client: TestClient) -> None:
    r = client.get("/api/accounts/company/channels")
    assert r.status_code == 200
    body = r.json()
    assert [c["id"] for c in body] == [
        "company_instagram",
        "company_facebook",
        "company_linkedin",
    ]
    assert all(c["account_id"] == "company" for c in body)


def test_channels_are_data_driven_not_hardcoded(client: TestClient) -> None:
    """Selecting a different account returns that account's own channels."""
    from app.database.seed import seed  # noqa: F401  (documents intent)
    from app.database.session import session_scope
    from app.models.entities import Account, Channel

    with session_scope() as db:
        db.add(Account(id="ceo", name="CEO", type="person", active=True, sort_order=1))
        db.flush()
        for i, platform in enumerate(["instagram", "facebook", "twitter", "linkedin", "threads"]):
            db.add(
                Channel(
                    id=f"ceo_{platform}",
                    account_id="ceo",
                    platform=platform,
                    display_name=platform.title(),
                    username=f"@ceo_{platform}",
                    active=True,
                    sort_order=i,
                )
            )

    ceo = client.get("/api/accounts/ceo/channels")
    assert ceo.status_code == 200
    assert len(ceo.json()) == 5

    # ...and the original account is unaffected.
    company = client.get("/api/accounts/company/channels")
    assert len(company.json()) == 3


def test_list_channels_unknown_account_is_404(client: TestClient) -> None:
    r = client.get("/api/accounts/nope/channels")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "ACCOUNT_NOT_FOUND"


def test_health_endpoint(client: TestClient) -> None:
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["accounts_seeded"] is True
    assert "n8n_publish_webhook_url" not in r.text