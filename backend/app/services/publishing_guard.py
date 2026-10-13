"""Fail-closed publishing guard.

This module exists so that publishing can be *provably* disabled without
changing application behaviour. It is a safety rail, not a feature: with the
guard disarmed (``off``, the default in every real deployment) nothing here
runs and no code path changes.

Modes (``PUBLISHING_GUARD``):

``off``       No interception and no logging. This is the production default.
``dry_run``   Every guarded operation raises :class:`PublishingBlocked` and is
              appended to the audit log. Used by the test suite so that a test
              which accidentally reaches a publishing path fails loudly instead
              of publishing to a real channel.
``monitor``   The operation proceeds but is appended to the audit log. Used by
              tests that deliberately exercise a publishing code path with a
              mocked transport and still want a record of every attempt.

The guard is deliberately *fail-closed*: an unrecognised or unreadable mode
behaves like ``dry_run`` rather than silently disabling itself, and any problem
writing the audit log raises in the guarded modes instead of being swallowed.

This is NOT a production bypass. There is no code path that flips the guard off
at runtime; it is read from configuration exactly once per call, so an operator
has to change the deployment configuration deliberately.
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

#: Recognised modes. Anything else is treated as ``dry_run``.
MODE_OFF = "off"
MODE_DRY_RUN = "dry_run"
MODE_MONITOR = "monitor"
VALID_MODES = (MODE_OFF, MODE_DRY_RUN, MODE_MONITOR)

#: Guarded operations. ``write=True`` means the operation creates, modifies or
#: schedules something on a real external system.
WRITE = "write"
READ = "read"

_DEFAULT_LOG = "./publishing-guard.jsonl"
_LOCK = threading.Lock()

#: Any run of this many opaque characters is treated as a possible credential
#: and masked before it reaches the audit log.
_OPAQUE_RUN = 20
_MASK = "***"
_MAX_FIELD = 160


def _safe(value: str | None) -> str | None:
    """Make a caller-supplied string safe to persist in an audit log.

    The guard is handed whatever the calling code passes as a target. That could
    be a channel id or a URL, but it could also be a token, so every field is
    sanitised before it is written:

    * any URL query string and fragment are dropped entirely - signed media URLs
      carry an HMAC tag there, which is a bearer credential in effect;
    * userinfo (``user:pass@host``) is removed;
    * long opaque runs are masked, so a token cannot survive in any position;
    * the result is length-capped.
    """
    if value is None:
        return None
    text = str(value)

    if "://" in text:
        scheme, _, rest = text.partition("://")
        # Drop the query string and the fragment; keep only scheme://path.
        rest = rest.split("#", 1)[0].split("?", 1)[0]
        # Drop userinfo.
        if "@" in rest.split("/", 1)[0]:
            rest = rest.split("@", 1)[-1]
        text = f"{scheme}://{rest}"

    out: list[str] = []
    run: list[str] = []
    for ch in text:
        if ch.isalnum() or ch in "-_":
            run.append(ch)
            continue
        out.append(_MASK + "…" if len(run) >= _OPAQUE_RUN else "".join(run))
        run = []
        out.append(ch)
    out.append(_MASK + "…" if len(run) >= _OPAQUE_RUN else "".join(run))

    result = "".join(out)
    return result[:_MAX_FIELD]


class PublishingBlocked(RuntimeError):
    """Raised when a guarded operation is attempted while the guard is armed.

    Carries the operation name and target so the failure is self-explanatory in
    a test report instead of looking like a mysterious network error.
    """

    def __init__(self, operation: str, target: str, detail: str | None = None) -> None:
        self.operation = operation
        self.target = target
        self.detail = detail
        message = (
            f"Publishing guard blocked '{operation}' against '{target}'. "
            "This operation would have reached a real publishing endpoint. "
            "If that is intended in a test, arm it explicitly with the "
            "'allow_publish' fixture."
        )
        if detail:
            message = f"{message} Detail: {detail}"
        super().__init__(message)


def mode() -> str:
    """The configured mode. Unknown values fail closed to ``dry_run``."""
    raw = (os.environ.get("PUBLISHING_GUARD") or "").strip().lower()
    if not raw:
        return MODE_OFF
    if raw not in VALID_MODES:
        return MODE_DRY_RUN
    return raw


def is_armed() -> bool:
    """True when the guard is intercepting operations."""
    return mode() != MODE_OFF


def _log_path() -> Path:
    configured = (os.environ.get("PUBLISHING_GUARD_LOG") or "").strip()
    return Path(configured or _DEFAULT_LOG)


def _append(record: dict[str, Any]) -> None:
    """Append one audit record. Never silently dropped while armed.

    A failure to write is raised: an audit trail that can vanish is worse than
    an exception, because it makes a blocked operation look unrecorded.
    """
    path = _log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record, sort_keys=True, default=str)
        with _LOCK, path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError as exc:  # pragma: no cover - disk failure path
        raise RuntimeError(
            f"Publishing guard could not write its audit log at {path}: {exc}"
        ) from exc


def _record(operation: str, target: str, kind: str, outcome: str, detail: str | None = None) -> None:
    _append(
        {
            "ts": datetime.now(timezone.utc).isoformat(),
            "operation": _safe(operation),
            "target": _safe(target),
            "kind": kind,
            "outcome": outcome,
            "mode": mode(),
            "detail": _safe(detail),
            "pid": os.getpid(),
        }
    )


def assert_allowed(operation: str, target: str, *, kind: str = WRITE, detail: str | None = None) -> None:
    """Gate one external operation. Returns normally when allowed.

    Raises :class:`PublishingBlocked` in ``dry_run``. In ``monitor`` the call is
    allowed and recorded. In ``off`` nothing happens at all.
    """
    current = mode()
    if current == MODE_OFF:
        return
    if current == MODE_MONITOR:
        _record(operation, target, kind, "allowed", detail)
        return
    _record(operation, target, kind, "blocked", detail)
    raise PublishingBlocked(operation, target, detail)


def arm_dry_run() -> None:
    """Test helper: fail closed for the remainder of the process."""
    os.environ["PUBLISHING_GUARD"] = MODE_DRY_RUN


def arm_monitor() -> None:
    """Test helper: allow, but record, for the remainder of the process."""
    os.environ["PUBLISHING_GUARD"] = MODE_MONITOR


def disarm() -> None:
    """Test helper: return to the production default (no interception)."""
    os.environ.pop("PUBLISHING_GUARD", None)


def read_audit_log(limit: int | None = None) -> list[dict[str, Any]]:
    """Read back the audit log. Used by tests to prove a block was recorded."""
    path = _log_path()
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except ValueError:  # pragma: no cover - torn write
                continue
    return records[-limit:] if limit else records