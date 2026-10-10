"""Live end-to-end check of the dynamic onboarding flow.

Runs against REAL HTTP servers (no TestClient, no in-process fakes):
  * a fake Buffer GraphQL API on its own port
  * the real FastAPI app pointed at it, on a throwaway database

Exercises the journey the UI drives, and asserts nothing real is published.
Run:  python backend/tests/live_onboarding_check.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

API_PORT = 8123
BUFFER_PORT = 8124
BASE = f"http://127.0.0.1:{API_PORT}"

passed = 0
failed = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global passed, failed
    if condition:
        passed += 1
        print(f"  PASS  {label}")
    else:
        failed += 1
        print(f"  FAIL  {label}  ->  {detail}")


# --------------------------------------------------------------------------- #
# Fake Buffer GraphQL API
# --------------------------------------------------------------------------- #
ORG_ID = "org_live_1"
ORG_NAME = "Live workspace"

CHANNELS = {
    "channels": [
        {
            "id": "chan_live_instagram",
            "name": "glaamora",
            "displayName": "Glaamora",
            "descriptor": "Instagram Profile",
            "service": "instagram",
            "type": "PROFILE",
            "avatar": None,
            "externalLink": "https://instagram.com/glaamora",
            "isDisconnected": False,
            "isLocked": False,
            "isQueuePaused": False,
            "timezone": "Asia/Dhaka",
        },
        {
            "id": "chan_live_linkedin",
            "name": "sortpi",
            "displayName": "SortPi",
            "descriptor": "LinkedIn Page",
            "service": "linkedin",
            "type": "PAGE",
            "avatar": None,
            "externalLink": None,
            "isDisconnected": False,
            "isLocked": False,
            "isQueuePaused": False,
            "timezone": "Asia/Dhaka",
        },
        {
            "id": "chan_live_locked",
            "name": "locked-one",
            "displayName": "Locked One",
            "descriptor": "Facebook Page",
            "service": "facebook",
            "type": "PAGE",
            "avatar": None,
            "externalLink": None,
            "isDisconnected": False,
            # Buffer refuses posts to locked channels.
            "isLocked": True,
            "isQueuePaused": False,
            "timezone": "UTC",
        },
    ]
}

#: Records every createPost so we can prove the right channel id was used.
create_post_calls: list[dict] = []


class BufferHandler(BaseHTTPRequestHandler):
    def log_message(self, *args: object) -> None:  # silence
        pass

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            body = {}

        query = str(body.get("query", ""))
        auth = self.headers.get("Authorization", "")

        if not auth.startswith("Bearer "):
            return self._json(401, {"errors": [{"message": "Unauthorized"}]})

        if "organizations" in query:
            return self._json(200, {"data": {"account": {"id": "a1", "organizations": [{"id": ORG_ID, "name": ORG_NAME}]}}})
        if "channels(input" in query:
            return self._json(200, {"data": CHANNELS})
        if "createPost" in query:
            create_post_calls.append(body)
            channel_id = body["variables"]["input"]["channelId"]
            return self._json(
                200,
                {
                    "data": {
                        "createPost": {
                            "__typename": "PostActionSuccess",
                            "post": {
                                "id": "post_live_1",
                                "text": body["variables"]["input"]["text"],
                                "status": "sent",
                                "dueAt": None,
                                "channelId": channel_id,
                                "channelService": "linkedin",
                                "externalLink": "https://linkedin.com/feed/update/1",
                                "isCustomScheduled": False,
                                "via": "app",
                                "error": None,
                            },
                        }
                    }
                },
            )
        self._json(400, {"errors": [{"message": "unknown operation"}]})

    def _json(self, status: int, payload: dict) -> None:
        raw = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def call(method: str, path: str, body: dict | None = None, headers: dict | None = None):
    req = urllib.request.Request(
        f"{BASE}{path}",
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        return exc.code, (json.loads(raw) if raw else None)


def wait_for(url: str, tries: int = 60) -> bool:
    for _ in range(tries):
        try:
            urllib.request.urlopen(url, timeout=2)
            return True
        except urllib.error.HTTPError:
            return True
        except Exception:
            time.sleep(0.5)
    return False


def main() -> int:
    global passed, failed

    tmpdir = tempfile.mkdtemp(prefix="sma-live-")
    db_path = os.path.join(tmpdir, "live.db")

    print("\n=== starting fake Buffer API ===")
    buffer_server = HTTPServer(("127.0.0.1", BUFFER_PORT), BufferHandler)
    threading.Thread(target=buffer_server.serve_forever, daemon=True).start()

    os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
    os.environ["BUFFER_API_BASE_URL"] = f"http://127.0.0.1:{BUFFER_PORT}"
    os.environ["PUBLISH_ENGINE"] = "direct"
    os.environ["INTERNAL_SERVICE_SECRET"] = "live-secret"
    os.environ["N8N_PUBLISH_WEBHOOK_URL"] = "http://127.0.0.1:1/webhook/social/publish"
    os.environ["APP_ENV"] = "test"
    os.environ["DEBUG"] = "false"
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    from cryptography.fernet import Fernet

    os.environ["CREDENTIAL_ENCRYPTION_KEY"] = Fernet.generate_key().decode()

    from app.main import app
    from app.services import storage as storage_module

    # Buffer "downloads" media itself; do not make real network calls.
    storage_module.verify_publicly_reachable = lambda urls, timeout=12.0: (True, None)

    import uvicorn

    config = uvicorn.Config(app, host="127.0.0.1", port=API_PORT, log_level="warning")
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, daemon=True).start()

    print(f"=== starting FastAPI on {BASE} ===")
    if not wait_for(f"{BASE}/api/health"):
        print("  backend never became ready")
        return 1

    try:
        # ---------------------------------------------------------------- 1
        print("\n[1] Seeded brand exists and is untouched")
        status, accounts = call("GET", "/api/accounts")
        check("GET /api/accounts", status == 200, str(accounts))
        check("seeded company account present", any(a["id"] == "company" for a in accounts), str(accounts))

        status, channels = call("GET", "/api/accounts/company/channels")
        legacy = [c for c in channels if c["id"].startswith("company_")]
        check("legacy channels still listed", len(legacy) == 3, f"{len(legacy)}")
        check("legacy channels not alarmed", all(c["publishable"] for c in legacy), str(legacy))

        # ---------------------------------------------------------------- 2
        print("\n[2] Add a Buffer integration from the UI")
        status, integration = call(
            "POST", "/api/integrations",
            {"display_name": "Live workspace", "api_key": "live_token_abcdef123456"},
        )
        check("POST /api/integrations -> 201", status == 201, str(integration))
        check("status is connected (real probe)", integration["status"] == "connected", str(integration["status"]))
        check("workspace bound", integration["external_organization_name"] == ORG_NAME, str(integration))
        check("token never returned", "live_token_abcdef123456" not in json.dumps(integration), "LEAK")
        check("only a masked hint returned", integration["credential_hint"].endswith("3456"), str(integration["credential_hint"]))
        integration_id = integration["id"]

        # ---------------------------------------------------------------- 3
        print("\n[3] Test connection")
        status, result = call("POST", f"/api/integrations/{integration_id}/test")
        check("POST /test -> 200", status == 200, str(result))
        check("reports ok", result["ok"] is True, str(result))

        # ---------------------------------------------------------------- 4
        print("\n[4] Discover channels (live Buffer call)")
        status, discovery = call("POST", f"/api/integrations/{integration_id}/discover")
        check("POST /discover -> 200", status == 200, str(discovery))
        check("found the 3 channels Buffer reports", discovery["total"] == 3, str(discovery["total"]))
        check("locked channel excluded from importable", discovery["importable"] == 2, str(discovery["importable"]))
        found = {c["external_channel_id"] for c in discovery["channels"]}
        check("ids match Buffer's", found == {"chan_live_instagram", "chan_live_linkedin", "chan_live_locked"}, str(found))

        # ---------------------------------------------------------------- 5
        print("\n[5] Create a new brand, then import channels into it")
        status, created = call("POST", "/api/accounts", {
            "id": "glaamora", "name": "Glaamora", "type": "brand", "sort_order": 5,
        })
        check("brand created via API (no seed edit)", status in (200, 201), str(created))

        status, sync = call("POST", f"/api/integrations/{integration_id}/sync", {
            "account_id": "glaamora",
            "external_channel_ids": ["chan_live_instagram", "chan_live_linkedin"],
        })
        check("POST /sync -> 200", status == 200, str(sync))
        check("created 2 channels", sync["created"] == 2, str(sync))

        # ---------------------------------------------------------------- 6
        print("\n[6] New channels appear in the selectors with no code change")
        status, chans = call("GET", "/api/accounts/glaamora/channels")
        check("GET /api/accounts/glaamora/channels -> 200", status == 200, str(chans))
        check("2 channels served", len(chans) == 2, f"{len(chans)}")
        platforms = {c["platform"] for c in chans}
        check("correct platforms", platforms == {"instagram", "linkedin"}, str(platforms))
        check("all publishable", all(c["publishable"] for c in chans), str(chans))
        check("integration name surfaced", all(c["integration_name"] == "Live workspace" for c in chans), str(chans))

        status, everything = call("GET", "/api/channels")
        check("new brand is in the global channel list", any(c["account_id"] == "glaamora" for c in everything), "")

        # ---------------------------------------------------------------- 7
        print("\n[7] Re-sync is idempotent")
        status, again = call("POST", f"/api/integrations/{integration_id}/sync", {
            "account_id": "glaamora",
            "external_channel_ids": ["chan_live_instagram", "chan_live_linkedin"],
        })
        check("nothing created on the second run", again["created"] == 0, str(again))
        check("2 updated instead", again["updated"] == 2, str(again))
        status, chans2 = call("GET", "/api/accounts/glaamora/channels")
        check("still exactly 2 channels (no duplicates)", len(chans2) == 2, f"{len(chans2)}")

        # ---------------------------------------------------------------- 8
        print("\n[8] Ownership is enforced across brands")
        status, conflict = call("POST", f"/api/integrations/{integration_id}/sync", {
            "account_id": "company",
            "external_channel_ids": ["chan_live_instagram"],
        })
        check("cannot steal a channel into another brand", status == 409, str(status))
        check("clear error code", conflict["error"]["code"] == "CHANNEL_ALREADY_ASSIGNED", str(conflict))

        # ---------------------------------------------------------------- 9
        print("\n[9] Direct publishing resolves the right Buffer channel id")
        instagram = next(c for c in chans2 if c["platform"] == "instagram")
        linkedin = next(c for c in chans2 if c["platform"] == "linkedin")
        status, published = call("POST", "/api/publish", {
            "account_id": "glaamora",
            "channel_id": linkedin["id"],
            "content": "Live end-to-end check",
            "publish_mode": "NOW",
            "timezone": "Asia/Dhaka",
        })
        check("publish -> 200", status == 200, str(published))
        check("reported as published", published["success"] and published["status"] == "published", str(published))
        check("Buffer received our internal id's mapping", create_post_calls
              and create_post_calls[-1]["variables"]["input"]["channelId"] == "chan_live_linkedin",
              str(create_post_calls[-1]["variables"]["input"] if create_post_calls else "no call"))
        check("no token in the Buffer payload",
              "live_token_abcdef123456" not in json.dumps(create_post_calls[-1] if create_post_calls else {}), "")

        # ---------------------------------------------------------------- 10
        print("\n[10] A disabled integration cannot publish silently")
        status, _ = call("PATCH", f"/api/integrations/{integration_id}", {"enabled": False})
        check("integration disabled", status == 200, str(status))
        before = len(create_post_calls)
        status, blocked = call("POST", "/api/publish", {
            "account_id": "glaamora", "channel_id": linkedin["id"],
            "content": "must not go out", "publish_mode": "NOW", "timezone": "Asia/Dhaka",
        })
        check("publish blocked with 409", status == 409, str(status))
        check("explicit code", blocked["error"]["code"] == "INTEGRATION_DISABLED", str(blocked))
        check("Buffer was NOT called", len(create_post_calls) == before, f"{len(create_post_calls)} vs {before}")

        # Re-enabling must NOT silently trust the old verdict.
        status, re_enabled = call("PATCH", f"/api/integrations/{integration_id}", {"enabled": True})
        check("re-enabled", status == 200, str(status))
        check("status reset until re-tested", re_enabled["status"] == "not_configured", str(re_enabled["status"]))
        status, retested = call("POST", f"/api/integrations/{integration_id}/test")
        check("re-test restores connected", retested["ok"] and retested["status"] == "connected", str(retested))

        # ---------------------------------------------------------------- 11
        print("\n[11] n8n callback endpoint is secured")
        status, _ = call("POST", "/internal/publish", {
            "request_id": "live-1", "account_id": "glaamora",
            "channel_id": linkedin["id"], "content": "x", "publish_mode": "NOW",
        }, headers={"X-Internal-Secret": "wrong"})
        check("wrong secret rejected with 401", status == 401, str(status))
        before = len(create_post_calls)
        status, internal = call("POST", "/internal/publish", {
            "request_id": "live-2", "account_id": "glaamora",
            "channel_id": linkedin["id"], "content": "via generic workflow", "publish_mode": "NOW",
        }, headers={"X-Internal-Secret": "live-secret"})
        check("correct secret accepted", status == 200, str(internal))
        check("request id preserved end to end", internal["request_id"] == "live-2", str(internal))
        check("Buffer actually called", len(create_post_calls) == before + 1, "")

        # ---------------------------------------------------------------- 12
        print("\n[12] History reflects reality")
        status, history = call("GET", "/api/publish/history")
        check("GET /api/publish/history -> 200", status == 200, str(status))
        top = history["items"][0]
        check("latest row is the n8n publish", top["request_id"] == "live-2", str(top))
        check("recorded as published", top["status"] == "published", str(top))

        # ---------------------------------------------------------------- 13
        print("\n[13] System status is truthful")
        status, sys_status = call("GET", "/api/system/status")
        check("GET /api/system/status -> 200", status == 200, "")
        check("engine reported", sys_status["publish_engine"] == "direct", str(sys_status["publish_engine"]))
        check("1 integration", sys_status["integrations"]["total"] == 1, str(sys_status["integrations"]))
        check("2 channels bound to it", sys_status["integrations"]["channels_bound"] == 2, str(sys_status["integrations"]))
        check("3 legacy channels still unbound (unchanged)", sys_status["integrations"]["channels_unbound"] == 3, str(sys_status["integrations"]))
        check("integration connected", sys_status["integrations"]["connected"] == 1, str(sys_status["integrations"]))
        check("encryption on", sys_status["encryption"]["configured"] is True, "")
        check("no secret in the payload",
              "live_token_abcdef123456" not in json.dumps(sys_status), "LEAK")
        check("internal endpoint reported enabled", sys_status["backend"]["internal_publish_enabled"] is True, "")

        # ---------------------------------------------------------------- 14
        print("\n[14] Brand management from the UI")
        status, brand = call("POST", "/api/accounts", {"name": "Sortorium", "type": "brand"})
        check("POST /api/accounts -> 201", status == 201, str(brand))
        check("id derived from the name", brand["id"] == "sortorium", str(brand["id"]))
        check("starts with no channels", brand["channel_count"] == 0, str(brand))

        status, again = call("POST", "/api/accounts", {"name": "Sortorium", "type": "brand"})
        check("duplicate name still creates a usable brand", status == 201, str(again))
        check("second one got a distinct id", again["id"] != brand["id"], str(again["id"]))

        status, renamed = call("PATCH", f"/api/accounts/{brand['id']}", {"name": "Sortorium Ltd", "active": False})
        check("rename works", renamed["name"] == "Sortorium Ltd", str(renamed["name"]))
        check("can be deactivated", renamed["active"] is False, str(renamed["active"]))

        status, listed = call("GET", "/api/accounts/manage")
        check("manage view includes empty/inactive brands", any(a["id"] == brand["id"] for a in listed), "")

        status, refused = call("DELETE", f"/api/accounts/{brand['id']}")
        check("deleting an empty brand works", status == 204, str(status))

        status, _ = call("DELETE", "/api/accounts/glaamora")
        check("deleting a brand WITH channels is refused", status == 409, str(status))

    finally:
        server.should_exit = True
        buffer_server.shutdown()

    print(f"\n{'=' * 60}")
    print(f"  {passed} passed, {failed} failed")
    print(f"{'=' * 60}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
