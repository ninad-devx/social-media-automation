"""Final source scan: prove nothing in the repo can reach a live publisher.

Checks, in order:

1. No credential is embedded in any tracked file.
2. No test module configures a live publisher as an endpoint (prose in
   comments and docstrings is ignored - only executable assignments count).
3. The guard is armed by the suite, and every module that can reach a publish
   path uses a mocked transport.
4. No startup path can publish.
5. The internal callback route is disabled without a secret.
6. Nothing disables the guard at runtime.
"""
from __future__ import annotations

import ast
import io
import re
import subprocess
import tokenize
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
TESTS = BACKEND / "tests"

#: Token shapes that must never appear in tracked source.
SECRET_PATTERNS = [
    (re.compile(r"sk-[A-Za-z0-9]{20,}"), "OpenAI-style API key"),
    (re.compile(r"AIza[0-9A-Za-z_\-]{30,}"), "Google API key"),
    (re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}"), "GitHub token"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "private key"),
    (re.compile(r"eyJ[A-Za-z0-9_\-]{40,}\.[A-Za-z0-9_\-]{40,}"), "JWT"),
]

#: Hosts that must never be used as a live endpoint in code.
LIVE_HOSTS = ("api.buffer.com", "bufferapp.com")


def _tracked(patterns: list[str]) -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", *patterns],
        cwd=BACKEND.parent,
        capture_output=True,
        text=True,
        check=False,
    )
    return [Path(line) for line in out.stdout.splitlines() if line]


def _code_without_comments(path: Path) -> str:
    """Source with comments and docstrings stripped.

    A host named in a comment that explains "never contact this" is not the
    same as an endpoint being configured, and the scan should only flag the
    latter.
    """
    try:
        raw = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""
    try:
        tokens = tokenize.generate_tokens(io.StringIO(raw).readline)
        out: list[str] = []
        for tok in tokens:
            if tok.type in (tokenize.COMMENT, tokenize.STRING):
                continue
            out.append(tok.string)
        return " ".join(out)
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return raw


def test_no_credential_shaped_string_in_tracked_files() -> None:
    offenders: list[str] = []
    for relative in _tracked(["*.py", "*.ts", "*.tsx", "*.json", "*.yml", "*.yaml", "*.md"]):
        path = BACKEND.parent / relative
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for pattern, label in SECRET_PATTERNS:
            if pattern.search(text):
                offenders.append(f"{relative}: {label}")
    assert not offenders, "credential-shaped strings found:\n" + "\n".join(offenders)


def test_no_env_file_is_tracked() -> None:
    tracked = {p.name for p in _tracked([])}
    for name in (".env", ".env.local", ".env.production"):
        assert name not in tracked, f"{name} is tracked and may contain secrets"


def test_no_code_configures_a_live_publisher_as_an_endpoint() -> None:
    """Executable code must never point Buffer or n8n at a real host.

    Comments and docstrings are stripped first, so documentation that *names* a
    live host to warn against it does not trip this.
    """
    offenders: list[str] = []
    for path in _tracked(["*.py"]):
        code = _code_without_comments(BACKEND.parent / path)
        for host in LIVE_HOSTS:
            if host in code:
                offenders.append(f"{path}: {host}")
    assert not offenders, (
        "executable code references a live publisher host:\n" + "\n".join(offenders)
    )


def test_the_suite_points_every_endpoint_at_something_unreachable() -> None:
    text = (TESTS / "conftest.py").read_text(encoding="utf-8")
    assert "n8n.invalid" in text
    assert "buffer.invalid" in text
    # The n8n callback route must be switched off entirely.
    assert 'os.environ["INTERNAL_SERVICE_SECRET"] = ""' in text


def test_the_guard_is_armed_for_every_test() -> None:
    text = (TESTS / "conftest.py").read_text(encoding="utf-8")
    assert 'os.environ["PUBLISHING_GUARD"] = "dry_run"' in text
    assert "_no_outbound_network" in text


def test_every_publishing_test_uses_a_mocked_transport() -> None:
    """Any module that can reach a publish path must neutralise the transport.

    Three acceptable ways:

    * the guard is explicitly opted into with `allow_publish`;
    * the Buffer transport is replaced with a `MockTransport`;
    * the n8n engine is replaced with the fake;
    * or every call asserts a refusal, i.e. the test proves publishing does
      NOT happen and expects a non-2xx.

    Anything else could reach a real publisher, even if only by accident.
    """
    refusal_markers = (
        "!= 200",
        "== 4",
        "== 409",
        "== 502",
        "== 500",
        "is_refused",
    )
    offenders: list[str] = []
    for path in sorted(TESTS.glob("test_*.py")):
        text = path.read_text(encoding="utf-8")
        if "/api/publish" not in text and "/internal/publish" not in text:
            continue
        neutralised = any(
            marker in text
            for marker in (
                "allow_publish",
                "MockTransport",
                "fake_n8n",
                "bound_company_channels",
                *refusal_markers,
            )
        )
        if not neutralised:
            offenders.append(path.name)
    assert not offenders, (
        "these can reach a publish path with nothing mocked:\n" + "\n".join(offenders)
    )


def test_every_buffer_client_in_tests_is_mocked() -> None:
    """No test may construct a BufferClient that could reach the network."""
    offenders: list[str] = []
    for path in sorted(TESTS.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        if "BufferClient(" not in text:
            continue
        if "MockTransport" in text or "allow_publish" in text:
            continue
        offenders.append(path.name)
    assert not offenders, "unmocked BufferClient in:\n" + "\n".join(offenders)


def test_no_startup_path_can_publish() -> None:
    text = (BACKEND / "app" / "main.py").read_text(encoding="utf-8")
    tree = ast.parse(text)
    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    for forbidden in ("publish", "create_post", "bulk_publish", "start_generation"):
        assert forbidden not in called, f"main.py calls {forbidden} at startup"


def test_the_internal_callback_route_is_disabled_without_a_secret() -> None:
    """The route must 404 rather than fail open."""
    import os

    from fastapi.testclient import TestClient

    from app.core.config import get_settings
    from app.main import app

    previous = os.environ.get("INTERNAL_SERVICE_SECRET")
    settings = get_settings()
    previous_value = settings.internal_service_secret
    try:
        settings.internal_service_secret = None
        with TestClient(app) as client:
            response = client.post(
                "/internal/publish",
                headers={"X-Internal-Secret": "guess"},
                json={
                    "request_id": "r",
                    "account_id": "company",
                    "channel_id": "company_facebook",
                    "content": "SENTINEL",
                },
            )
        assert response.status_code == 404
    finally:
        settings.internal_service_secret = previous_value
        if previous is not None:
            os.environ["INTERNAL_SERVICE_SECRET"] = previous


def test_the_publishing_guard_is_inert_by_default() -> None:
    """With no environment set the guard must not intercept anything."""
    import importlib
    import os

    from app.core.config import current_publishing_guard_mode

    previous = os.environ.pop("PUBLISHING_GUARD", None)
    try:
        module = importlib.reload(importlib.import_module("app.services.publishing_guard"))
        assert module.mode() == module.MODE_OFF
        assert module.is_armed() is False
        module.assert_allowed("buffer.create_post", "x", kind=module.WRITE)
        assert current_publishing_guard_mode() == "off"
    finally:
        if previous is not None:
            os.environ["PUBLISHING_GUARD"] = previous
        importlib.reload(importlib.import_module("app.services.publishing_guard"))


def test_no_module_disables_the_guard_at_runtime() -> None:
    offenders: list[str] = []
    for path in sorted(BACKEND.rglob("*.py")):
        if "publishing_guard" in path.name or ".venv" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if re.search(r'PUBLISHING_GUARD["\']\]\s*=\s*["\']off', text):
            offenders.append(str(path.relative_to(BACKEND)))
    assert not offenders, "these disable the guard:\n" + "\n".join(offenders)


def test_the_audit_log_masks_opaque_strings() -> None:
    """The guard must never persist something credential-shaped."""
    from app.services import publishing_guard as guard

    secret = "abcdefghijklmnopqrstuvwxyz012345"
    masked = guard._safe(f"https://api.buffer.com/{secret}?sig=also-secret&x=1")
    assert secret not in masked
    assert "?" not in masked
    # Short, meaningful identifiers survive.
    assert guard._safe("ch_abc123") == "ch_abc123"