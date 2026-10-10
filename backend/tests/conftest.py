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