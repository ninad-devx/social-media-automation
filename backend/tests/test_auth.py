"""Tests for the operator login: /api/auth/* and the session/API-key gate.

Covers the security properties that matter, not just the happy path:
wrong passwords, revoked sessions, expiry, username enumeration, rate
limiting, and the guarantee that neither the password nor the hash ever
appears in a response.
"""
from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.auth_entities import Operator, OperatorSession
from app.services import auth_service

GOOD_PASSWORD = "correct horse battery staple 7"


def _make_operator(db: Session, password: str = GOOD_PASSWORD) -> Operator:
    """Create the operator and commit.

    The `db` fixture yields a session that does not commit on teardown, while
    each request opens its own. Without an explicit commit here the handler
    would never see the row.
    """
    operator = auth_service.ensure_operator(db, username="admin", password=password)
    db.commit()
    return operator


def test_status_reports_login_unavailable_when_no_operator_exists(
    client: TestClient, db: Session
) -> None:
    assert db.query(Operator).count() == 0
    r = client.get("/api/auth/status")
    assert r.status_code == 200
    assert r.json()["enabled"] is False
    assert r.json()["authenticated"] is False


def test_status_reports_login_available_once_operator_exists(
    client: TestClient, db: Session
) -> None:
    _make_operator(db)
    r = client.get("/api/auth/status")
    assert r.status_code == 200
    assert r.json()["enabled"] is True


def test_login_succeeds_and_sets_httponly_session_cookie(
    client: TestClient, db: Session
) -> None:
    _make_operator(db)
    r = client.post(
        "/api/auth/login",
        data={"username": "admin", "password": GOOD_PASSWORD},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["authenticated"] is True
    assert body["username"] == "admin"

    cookie_header = r.headers.get("set-cookie", "")
    assert auth_service.SESSION_COOKIE_NAME in cookie_header
    # The token must be unreadable from JavaScript.
    assert "HttpOnly" in cookie_header
    # And must not be echoed in the body.
    assert auth_service.SESSION_COOKIE_NAME not in r.text


def test_login_never_echoes_the_password_or_the_hash(
    client: TestClient, db: Session
) -> None:
    _make_operator(db)
    r = client.post(
        "/api/auth/login",
        data={"username": "admin", "password": GOOD_PASSWORD},
    )
    assert r.status_code == 200
    raw = r.text
    assert GOOD_PASSWORD not in raw
    assert "$argon2id$" not in raw


def test_login_with_wrong_password_is_rejected(
    client: TestClient, db: Session
) -> None:
    _make_operator(db)
    r = client.post(
        "/api/auth/login",
        data={"username": "admin", "password": "definitely not the password"},
    )
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "INVALID_CREDENTIALS"
    assert auth_service.SESSION_COOKIE_NAME not in r.headers.get("set-cookie", "")


def test_unknown_user_is_indistinguishable_from_a_wrong_password(
    client: TestClient, db: Session
) -> None:
    """Login must not be usable to discover which usernames exist."""
    _make_operator(db)
    unknown = client.post(
        "/api/auth/login",
        data={"username": "nobody", "password": "whatever value here"},
    )
    wrong = client.post(
        "/api/auth/login",
        data={"username": "admin", "password": "definitely not the password"},
    )
    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json()["error"]["code"] == wrong.json()["error"]["code"]
    assert unknown.json()["error"]["message"] == wrong.json()["error"]["message"]


def test_session_cookie_unlocks_a_protected_route(
    client: TestClient, db: Session
) -> None:
    _make_operator(db)
    client.post("/api/auth/login", data={"username": "admin", "password": GOOD_PASSWORD})
    r = client.get("/api/accounts")
    assert r.status_code == 200
    assert len(r.json()) == 1


def test_protected_route_rejects_anonymous_when_api_key_is_set(
    client: TestClient, db: Session, monkeypatch
) -> None:
    """The gate must actually close in production, not just add a login page."""
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "api_key", "a-real-secret-key")
    r = client.get("/api/accounts")
    assert r.status_code == 401


def test_static_api_key_still_works_for_service_callers(
    client: TestClient, db: Session, monkeypatch
) -> None:
    """Backward compatibility: anything using X-API-Key keeps working."""
    from app.core.config import get_settings

    _make_operator(db)
    monkeypatch.setattr(get_settings(), "api_key", "a-real-secret-key")

    with_wrong_key = client.get(
        "/api/accounts", headers={"X-API-Key": "not-the-key"}
    )
    assert with_wrong_key.status_code == 401

    with_right_key = client.get(
        "/api/accounts", headers={"X-API-Key": "a-real-secret-key"}
    )
    assert with_right_key.status_code == 200


def test_health_stays_open_without_credentials(
    client: TestClient, db: Session, monkeypatch
) -> None:
    """The Docker HEALTHCHECK and Render's health check must never 401."""
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "api_key", "a-real-secret-key")
    r = client.get("/api/health")
    assert r.status_code == 200


def test_forged_cookie_is_rejected(client: TestClient, db: Session, monkeypatch) -> None:
    from app.core.config import get_settings

    _make_operator(db)
    monkeypatch.setattr(get_settings(), "api_key", "a-real-secret-key")
    client.cookies.set(auth_service.SESSION_COOKIE_NAME, "made-up-token-value")
    r = client.get("/api/accounts")
    assert r.status_code == 401


def test_logout_revokes_the_session_immediately(
    client: TestClient, db: Session, monkeypatch
) -> None:
    from app.core.config import get_settings

    _make_operator(db)
    monkeypatch.setattr(get_settings(), "api_key", "a-real-secret-key")

    client.post("/api/auth/login", data={"username": "admin", "password": GOOD_PASSWORD})
    assert client.get("/api/accounts").status_code == 200

    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/api/accounts").status_code == 401


def test_expired_session_is_rejected(client: TestClient, db: Session) -> None:
    from datetime import datetime, timedelta, timezone

    _make_operator(db)
    client.post("/api/auth/login", data={"username": "admin", "password": GOOD_PASSWORD})
    db.expire_all()
    row = db.query(OperatorSession).one()
    row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    db.commit()
    db.expire_all()

    r = client.get("/api/auth/session")
    assert r.status_code == 200
    assert r.json()["authenticated"] is False


def test_rotating_the_password_invalidates_existing_sessions(
    client: TestClient, db: Session
) -> None:
    """session_epoch must beat an already-issued cookie."""
    _make_operator(db)
    client.post("/api/auth/login", data={"username": "admin", "password": GOOD_PASSWORD})
    assert client.get("/api/auth/session").json()["authenticated"] is True

    # Simulate the deployment restarting with a new APP_ADMIN_PASSWORD.
    auth_service.ensure_operator(db, username="admin", password="a brand new strong password 9")
    db.commit()

    r = client.get("/api/auth/session")
    assert r.json()["authenticated"] is False


def test_sessions_survive_a_new_database_session(client: TestClient, db: Session) -> None:
    """Sessions live in the database, not process memory, so a redeploy keeps them."""
    _make_operator(db)
    client.post("/api/auth/login", data={"username": "admin", "password": GOOD_PASSWORD})
    db.expire_all()
    db.commit()
    r = client.get("/api/auth/session")
    assert r.json()["authenticated"] is True


def test_repeated_failures_lock_the_account_and_survive(
    client: TestClient, db: Session
) -> None:
    """Account lockout. Stored on the row, so a restart does not clear it."""
    auth_service.reset_throttling()
    _make_operator(db)

    for _ in range(auth_service.LOCKOUT_THRESHOLD):
        client.post(
            "/api/auth/login",
            data={"username": "admin", "password": "wrong guess here"},
        )

    # The correct password is now refused too, because the account is locked.
    r = client.post(
        "/api/auth/login",
        data={"username": "admin", "password": GOOD_PASSWORD},
    )
    assert r.status_code == 429
    assert r.json()["error"]["code"] == "ACCOUNT_LOCKED"

    operator = db.query(Operator).one()
    assert auth_service.is_locked(operator) is True
    auth_service.reset_throttling()


def test_repeated_failures_from_one_ip_are_throttled(
    client: TestClient, db: Session
) -> None:
    """IP throttling, exercised via a username that does not exist.

    A missing operator never locks an account, so this isolates the in-process
    per-IP window from the persistent account lockout.
    """
    auth_service.reset_throttling()

    for _ in range(auth_service.MAX_ATTEMPTS_PER_IP):
        client.post(
            "/api/auth/login",
            data={"username": "ghost", "password": "wrong guess here"},
        )

    _make_operator(db)
    r = client.post(
        "/api/auth/login",
        data={"username": "admin", "password": GOOD_PASSWORD},
    )
    assert r.status_code == 429
    assert r.json()["error"]["code"] == "TOO_MANY_ATTEMPTS"
    auth_service.reset_throttling()


def test_password_policy_rejects_weak_choices() -> None:
    assert auth_service.password_problem("short1A") is not None
    assert auth_service.password_problem("changeme") is not None
    assert auth_service.password_problem("123456789012") is not None
    assert auth_service.password_problem("alllettersnodigits") is not None
    assert auth_service.password_problem("a-reasonably-long-1") is None


def test_hash_is_argon2id_and_salted(client: TestClient, db: Session) -> None:
    operator = _make_operator(db)
    assert operator.password_hash.startswith("$argon2id$")
    # Two hashes of the same password must differ: the salt is random.
    again = auth_service.hash_password(GOOD_PASSWORD)
    assert again != operator.password_hash
    assert auth_service.verify_password(GOOD_PASSWORD, again) is True
    assert auth_service.verify_password("wrong", again) is False


def test_change_password_requires_the_current_one(client: TestClient, db: Session) -> None:
    _make_operator(db)
    client.post("/api/auth/login", data={"username": "admin", "password": GOOD_PASSWORD})

    wrong = client.post(
        "/api/auth/change-password",
        json={"current_password": "nope", "new_password": "a fresh strong password 3"},
    )
    assert wrong.status_code == 401

    ok = client.post(
        "/api/auth/change-password",
        json={"current_password": GOOD_PASSWORD, "new_password": "a fresh strong password 3"},
    )
    assert ok.status_code == 200
    # Changing it signs this browser out too.
    assert client.get("/api/auth/session").json()["authenticated"] is False


def test_change_password_requires_a_sign_in(client: TestClient, db: Session) -> None:
    _make_operator(db)
    r = client.post(
        "/api/auth/change-password",
        json={"current_password": GOOD_PASSWORD, "new_password": "a fresh strong password 3"},
    )
    assert r.status_code == 401


def test_change_password_refuses_a_weak_password(client: TestClient, db: Session) -> None:
    _make_operator(db)
    client.post("/api/auth/login", data={"username": "admin", "password": GOOD_PASSWORD})
    r = client.post(
        "/api/auth/change-password",
        json={"current_password": GOOD_PASSWORD, "new_password": "alllettersonly"},
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "WEAK_PASSWORD"