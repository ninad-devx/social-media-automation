"""Shared test fixtures.

Every test runs against an isolated in-memory database with a mocked n8n
engine, so the suite never publishes a real post.
"""
from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator

import pytest

# Point the app at a throwaway SQLite file before anything imports settings.
_tmpdir = tempfile.mkdtemp(prefix="sma-test-")
os.environ["DATABASE_URL"] = f"sqlite:///{os.path.join(_tmpdir, 'test.db')}"
os.environ["N8N_PUBLISH_WEBHOOK_URL"] = "http://n8n.invalid/webhook/social/publish"
os.environ["API_KEY"] = ""
os.environ["APP_ENV"] = "test"
os.environ["DEBUG"] = "false"

# A real Fernet key so the AI suite can actually encrypt provider secrets.
from cryptography.fernet import Fernet  # noqa: E402

os.environ["AI_ENCRYPTION_KEY"] = Fernet.generate_key().decode()
os.environ["AI_MAX_CONCURRENCY"] = "4"

# ---------------------------------------------------------------------------
# PUBLISHING SAFETY GUARD - FAIL CLOSED
# ---------------------------------------------------------------------------
# The company channels connected to this project are real. The suite must never
# be able to publish, schedule or queue anything to them, even by accident.
#
# Two independent layers:
#   1. PUBLISHING_GUARD=dry_run makes every guarded Buffer/n8n operation raise
#      and append to an audit log. Installed in app.services.buffer_client and
#      app.services.n8n_service, at the real-transport seam.
#   2. The `_no_outbound_network` fixture below refuses to open a socket to any
#      non-loopback address, so anything that bypasses layer 1 still cannot
#      reach api.buffer.com, an n8n instance, an AI provider or a public media
#      URL.
#
# A test that genuinely needs to exercise a publishing code path must say so
# with the `allow_publish` fixture. Nothing bypasses this implicitly.
os.environ["PUBLISHING_GUARD"] = "dry_run"
os.environ["PUBLISHING_GUARD_LOG"] = os.path.join(_tmpdir, "publishing-guard.jsonl")
# The n8n and internal-secret gates stay shut for the whole suite, so even a
# direct call to the internal publish route is inert.
os.environ["INTERNAL_SERVICE_SECRET"] = ""
os.environ["BUFFER_API_BASE_URL"] = "https://buffer.invalid"

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.api.deps import get_n8n  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.database.base import Base  # noqa: E402
from app.database.seed import seed  # noqa: E402
from app.database.session import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.services.ai.base import GeneratedPost, ProviderInfo  # noqa: E402
from app.services.ai_provider_service import ProviderService  # noqa: E402
from app.services.n8n_service import N8nResult  # noqa: E402


@pytest.fixture(autouse=True)
def clean_db() -> Iterator[None]:
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        seed(db)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def db() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


class FakeN8n:
    """Records calls and returns a programmable result."""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.result: N8nResult = N8nResult(
            success=True,
            status="published",
            buffer_publication_id="buf_test_123",
            external_link="https://facebook.com/example/1",
            n8n_request_id="pub_N8NTEST",
            http_status=200,
        )

    def publish(self, payload: dict) -> N8nResult:
        self.calls.append(payload)
        return self.result

    def is_configured(self) -> bool:
        return True

    def health(self) -> dict:
        return {"configured": True, "reachable": True}

    # helpers for tests -------------------------------------------------
    def succeed_now(self, **kw) -> None:
        self.result = N8nResult(success=True, status="published", buffer_publication_id="buf_test_123",
                                external_link="https://facebook.com/example/1",
                                n8n_request_id="pub_N8NTEST", http_status=200, **kw)

    def succeed_scheduled(self) -> None:
        self.result = N8nResult(success=True, status="scheduled", buffer_publication_id="buf_sched_1",
                                scheduled_at=None, timezone="Asia/Dhaka",
                                n8n_request_id="pub_N8NSCHED", http_status=200)

    def fail(self, code: str = "BUFFER_SERVER_ERROR", message: str = "boom") -> None:
        self.result = N8nResult(success=False, upstream_code=code, upstream_message=message,
                                n8n_request_id="pub_N8NFAIL", http_status=500)


@pytest.fixture
def fake_n8n() -> Iterator[FakeN8n]:
    """Replaces the n8n service for every route that depends on it."""
    fake = FakeN8n()
    app.dependency_overrides[get_n8n] = lambda: fake
    try:
        yield fake
    finally:
        app.dependency_overrides.pop(get_n8n, None)


@pytest.fixture
def settings():
    return get_settings()

# --------------------------------------------------------------------------- #
# AI fixtures (providers, fake adapter, fake n8n)
# --------------------------------------------------------------------------- #

class FakeAdapter:
    """Scripted adapter so generation is deterministic and offline."""

    slug = "fake"
    display_name = "Fake"
    supports_model_discovery = True
    default_base_url = "https://fake.local/v1"
    default_model = "fake-model-1"

#: Set by tests to make the next call fail.
    fail_with: Exception | None = None
    #: Fail only for these platforms, to simulate a partial failure.
    fail_platforms: set[str] = set()
    #: Raw text returned instead of proper JSON.
    raw_override: str | None = None
    #: Records every (system_prompt, user_prompt) pair.
    calls: list[tuple[str, str]] = []

    #: Providers whose credentials the fake should reject.
    reject_slugs: set[str] = set()

    def __init__(self, *args, **kwargs) -> None:
        self.api_key = kwargs.get("api_key", "k")
        self.model = kwargs.get("model", "fake-model-1")
        self.slug_name = kwargs.get("slug_name", "fake")

    @classmethod
    def reset(cls) -> None:
        cls.fail_with = None
        cls.fail_platforms = set()
        cls.reject_slugs = set()
        cls.raw_override = None
        cls.calls = []

    def validate_credentials(self) -> ProviderInfo:
        if self.slug_name in type(self).reject_slugs:
            return ProviderInfo(
                ok=False,
                detail="The provider rejected this API key. Check the key in Settings.",
                error_code="AI_AUTH_ERROR",
            )
        return ProviderInfo(ok=True, models=["fake-model-1", "fake-model-2"], detail="ok")

    def list_models(self) -> ProviderInfo:
        return ProviderInfo(ok=True, models=["fake-model-1", "fake-model-2"])

    def generate_structured(self, system_prompt: str, user_prompt: str) -> GeneratedPost:
        type(self).calls.append((system_prompt, user_prompt))
        if type(self).fail_with is not None:
            raise type(self).fail_with
        if type(self).raw_override is not None:
            from app.services.ai.base import extract_json_object

            return _as_post(extract_json_object(type(self).raw_override))

        # Echo a recognisable marker so tests can assert which channel's
        # instructions were used.
        platform = _platform_from_prompt(system_prompt)
        if platform in type(self).fail_platforms:
            from app.services.ai.base import ProviderAuthError

            raise ProviderAuthError("invalid key")
        return GeneratedPost(
            post_text=f"Generated draft for {platform}.",
            hashtags=[f"#{platform}tag"],
            suggested_cta="Learn more",
            media_idea="A simple graphic",
            notes=None,
            warnings=[],
            model=self.model,
        )


def _platform_from_prompt(prompt: str) -> str:
    # The bullet is "- **Platform:** LinkedIn". Split on the bold marker, not
    # the first colon, which sits inside "**Platform:**".
    for line in prompt.splitlines():
        if line.strip().startswith("- **Platform:**"):
            return line.split(":**", 1)[1].strip().lower()
    return ""
    return "unknown"


def _as_post(parsed: dict) -> GeneratedPost:
    return GeneratedPost(
        post_text=str(parsed.get("post_text") or ""),
        hashtags=[str(h) for h in (parsed.get("hashtags") or [])],
        suggested_cta=parsed.get("suggested_cta"),
        media_idea=parsed.get("media_idea"),
        notes=parsed.get("notes"),
        warnings=[str(w) for w in (parsed.get("validation_warnings") or [])],
        model="fake-model-1",
    )


@pytest.fixture(autouse=True)
def _no_outbound_network(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> None:
    """Hard network kill switch for the whole suite.

    Layer 2 of the publishing guard. Any attempt to open a socket to a
    non-loopback address raises immediately. `httpx.MockTransport` never
    touches a socket, so the mocked publishing tests keep working while a real
    request to api.buffer.com, an n8n webhook, an AI provider or a public media
    URL is impossible.

    Loopback stays allowed because a few tests exercise local filesystem and
    sqlite behaviour; nothing there can reach a social channel.
    """
    import socket

    from app.services import publishing_guard as guard

    loopback = {"127.0.0.1", "::1", "localhost", "", "0.0.0.0", "::"}

    def _host_of(address) -> str:
        if isinstance(address, tuple) and address:
            return str(address[0])
        return ""

    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def guarded_connect(self, address, *args, **kwargs):
        host = _host_of(address)
        if host not in loopback:
            guard._record(
                "network.connect",
                host or str(address),
                guard.READ,
                "blocked",
                "blocked by the test-suite network kill switch",
            )
            raise RuntimeError(
                f"Test suite blocked an outbound connection to {host or address!r}. "
                "No test may reach a real external service. Mock the transport "
                "instead (httpx.MockTransport)."
            )
        return real_connect(self, address, *args, **kwargs)

    def guarded_connect_ex(self, address, *args, **kwargs):
        host = _host_of(address)
        if host not in loopback:
            guard._record(
                "network.connect_ex",
                host or str(address),
                guard.READ,
                "blocked",
                "blocked by the test-suite network kill switch",
            )
            return 111  # ECONNREFUSED
        return real_connect_ex(self, address, *args, **kwargs)

    def blocked_getaddrinfo(*args, **kwargs):
        # DNS resolution itself is harmless, but keeping it means a loopback
        # URL still works end to end.
        return real_getaddrinfo(*args, **kwargs)

    real_getaddrinfo = socket.getaddrinfo

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", guarded_connect_ex)
    monkeypatch.setattr(socket, "getaddrinfo", blocked_getaddrinfo)
    yield


@pytest.fixture
def allow_publish(request: pytest.FixtureRequest) -> object:
    """Opt a single test in to exercising a guarded publishing code path.

    Switches the guard from `dry_run` to `monitor`: the operation proceeds but
    every attempt is written to the audit log, and the network kill switch above
    still prevents a real connection. This makes an intentional publishing
    test explicit and auditable rather than hidden.
    """
    from app.services import publishing_guard as guard

    previous = os.environ.get("PUBLISHING_GUARD")
    guard.arm_monitor()
    try:
        yield guard
    finally:
        if previous is None:
            guard.disarm()
            os.environ["PUBLISHING_GUARD"] = "dry_run"
        else:
            os.environ["PUBLISHING_GUARD"] = previous


@pytest.fixture(autouse=True)
def _no_media_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stop the publish path making real HTTP calls during tests.

    PublishService proves every attachment URL is reachable before sending.
    That is correct in production and wrong in a test suite, so it is stubbed
    here and tested directly in test_media_publishing.py.
    """
    from app.services import storage as storage_module

    monkeypatch.setattr(
        storage_module, "verify_publicly_reachable", lambda urls, timeout=12.0: (True, None)
    )


# --------------------------------------------------------------------------- #
# Seeded-channel binding
# --------------------------------------------------------------------------- #
_SEEDED_BINDINGS = {
    "company_facebook": ("buf_sma_fb_1", "facebook"),
    "company_instagram": ("buf_sma_ig_1", "instagram"),
    "company_linkedin": ("buf_sma_li_1", "linkedin"),
}


@pytest.fixture
def bound_company_channels(client, db):
    """Give the seeded demo channels a genuine Buffer binding.

    Publishing now refuses a channel with no Buffer profile, because that is
    the state the audit found silently failing. Any test that publishes through
    a seeded channel asks for this fixture so it exercises the real, bound path
    rather than relying on a permissive legacy shortcut.

    The integration is created against the module's Buffer mock, so no socket is
    opened and no credential is involved.
    """
    from app.models.entities import Channel, Integration
    from app.services.credentials import build_cipher

    settings = get_settings()
    cipher = build_cipher(settings)
    integration = Integration(
        id="int_test_sma",
        provider="buffer",
        display_name="Test Buffer",
        credential_encrypted=cipher.encrypt("tok_bound_company_channels_1"),
        credential_hint="test_1",
        external_organization_id="org_sma",
        external_organization_name="Test workspace",
        enabled=True,
        status="connected",
    )
    db.add(integration)
    db.commit()

    for channel_id, (external_id, service) in _SEEDED_BINDINGS.items():
        row = db.get(Channel, channel_id)
        if row is None:
            continue
        row.external_channel_id = external_id
        row.integration_id = integration.id
        row.connection_status = "connected"
        row.is_locked = False
        row.queue_paused = False
        row.platform = service
    db.commit()
    return integration.id


@pytest.fixture
def fake_adapter(monkeypatch: pytest.MonkeyPatch) -> type[FakeAdapter]:
    """Swap every real adapter for the fake one.

    `build_adapter` is patched in the registry AND in the service module that
    imports it, because the service binds it at module level while the detect
    endpoint imports it per call. ProviderSpec is a frozen dataclass, so the
    adapter cannot simply be swapped on the spec.
    """
    FakeAdapter.reset()

    import app.services.ai.registry as registry
    import app.services.ai_provider_service as svc

    def fake_build(slug, **kwargs):
        return FakeAdapter(
            api_key=kwargs.get("api_key", "k"),
            model=kwargs.get("model", "fake-model-1"),
            slug_name=slug,
        )

    monkeypatch.setattr(registry, "build_adapter", fake_build)
    monkeypatch.setattr(svc, "build_adapter", fake_build)
    return FakeAdapter


@pytest.fixture
def provider_row(db: Session) -> AIProvider:
    """An enabled, default provider with a real encrypted key."""
    from app.services.ai_provider_service import ProviderService

    service = ProviderService(db, get_settings())
    return service.create(
        provider_slug="openai",
        display_name="Test provider",
        api_key="sk-test-secret-value-123456",
        base_url=None,
        model="fake-model-1",
make_default=True,
    )


def wait_for_generation(timeout: float = 15.0) -> None:
    """Block until background generation threads settle."""
    import time

    from app.services.campaign_service import _ACTIVE, _ACTIVE_LOCK

    deadline = time.time() + timeout
    while time.time() < deadline:
        with _ACTIVE_LOCK:
            busy = [cid for cid, lock in _ACTIVE.items() if lock.locked()]
        if not busy:
            return
        time.sleep(0.05)


def make_campaign(client: TestClient, targets: list[dict], topic: str = "Test campaign topic") -> dict:
    return client.post(
        "/api/ai/campaigns",
        json={"topic": topic, "targets": targets},
    ).json()
