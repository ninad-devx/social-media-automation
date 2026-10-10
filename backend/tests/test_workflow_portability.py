"""Portability guarantees for the exported n8n workflow.

These tests are the contract for `deploy/n8n/social-publishing-workflow.json`:
if someone re-introduces a token, a channel id or a hardcoded host into the
export, the suite fails.

They are static checks - no n8n instance is required.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
WORKFLOW = REPO / "deploy" / "n8n" / "social-publishing-workflow.json"
BUILDER = REPO / "deploy" / "n8n" / "build_workflow.py"
LEGACY = REPO / "social-media-automation.workflow.json"


@pytest.fixture(scope="module")
def workflow() -> dict:
    assert WORKFLOW.exists(), f"missing export: {WORKFLOW}"
    return json.loads(WORKFLOW.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def raw() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


class TestExportShape:
    def test_is_valid_n8n_workflow_json(self, workflow):
        assert isinstance(workflow.get("nodes"), list)
        assert workflow["nodes"], "workflow has no nodes"
        assert "connections" in workflow

    def test_every_node_type_is_standard_n8n(self, workflow):
        for node in workflow["nodes"]:
            assert node["type"].startswith("n8n-nodes-base."), node["type"]
            assert "position" in node and len(node["position"]) == 2

    def test_every_connection_target_exists(self, workflow):
        names = {n["name"] for n in workflow["nodes"]}
        for source, outputs in workflow["connections"].items():
            assert source in names, f"connection from unknown node {source}"
            for branch in outputs["main"]:
                for target in branch:
                    assert target["node"] in names, target["node"]

    def test_build_script_reproduces_the_export(self):
        """The file is generated, so a stale hand edit is impossible."""
        import subprocess
        import sys

        before = WORKFLOW.read_bytes()
        subprocess.run(
            [sys.executable, str(BUILDER)], check=True, capture_output=True, cwd=str(REPO)
        )
        assert WORKFLOW.read_bytes() == before, (
            "export is out of date - re-run deploy/n8n/build_workflow.py"
        )


class TestNoSecrets:
    def test_no_credential_references(self, workflow):
        """The workflow must not point at any n8n credential store entry."""
        for node in workflow["nodes"]:
            assert "credentials" not in node, (
                f"node {node['name']} references credentials: {node.get('credentials')}"
            )
            assert node.get("authentication", "none") == "none"

    def test_no_bearer_token_anywhere_in_the_file(self, raw):
        assert "Bearer " not in raw
        # Buffer's API host is not needed: the backend owns that call now.
        assert "api.buffer.com" not in raw
        assert "bufferapp" not in raw

    def test_no_secret_values_are_hardcoded(self, raw):
        """The internal secret must only ever come from the environment."""
        # It may be *referenced* via $env, never assigned a literal.
        assert re.search(r"SMA_INTERNAL_SECRET\s*[:=]\s*['\"][^'\"$]", raw) is None
        assert "process.env.SMA_INTERNAL_SECRET" not in raw

    def test_declares_no_environment_variable_default_that_could_leak(self, raw):
        """The URL fallback must be localhost only, never a real host."""
        fallback = re.search(r"SMA_BACKEND_URL\s*\|\|\s*'([^']+)'", raw)
        assert fallback, "expected a localhost fallback for SMA_BACKEND_URL"
        assert fallback.group(1).startswith("http://localhost")


class TestNoAccountRegistry:
    def test_no_hardcoded_account_or_channel_ids(self, raw):
        """The legacy export embedded Buffer channel ids like this."""
        assert not re.search(r"buffer_profile_id", raw)
        assert not re.search(r"organization_id\s*:\s*'[^']+'", raw)
        # No 24-hex-char Buffer identifiers anywhere.
        assert not re.search(r"\b[0-9a-f]{24}\b", raw)

    def test_no_account_or_channel_enumeration(self, raw):
        for marker in ("accounts:", "channels:", "supported_platforms", "platform_metadata"):
            assert marker not in raw, f"workflow still contains a registry: {marker}"

    def test_no_brand_specific_branching(self, raw):
        for brand in ("company", "sortpi", "glaamora", "fitssort", "sortorium", "ceo"):
            assert brand not in raw.lower(), f"workflow branches on brand: {brand}"

    def test_single_workflow_covers_every_channel(self, workflow):
        """No duplicated publishing branches to keep in sync."""
        http_nodes = [
            n for n in workflow["nodes"] if n["type"] == "n8n-nodes-base.httpRequest"
        ]
        assert len(http_nodes) == 1, "expected exactly one outbound call"


class TestRuntimeConfiguration:
    def test_backend_url_comes_from_environment(self, raw):
        assert "$env.SMA_BACKEND_URL" in raw

    def test_internal_secret_comes_from_environment(self, raw):
        assert "$env.SMA_INTERNAL_SECRET" in raw

    def test_backend_url_is_not_a_localhost_literal(self, raw):
        """No hardcoded host outside the $env fallback."""
        literals = re.findall(r"https?://[a-zA-Z0-9.\-]+", raw)
        for url in literals:
            assert url.startswith("http://localhost"), f"hardcoded host: {url}"

    def test_webhook_path_is_stable(self, workflow):
        webhook = next(
            n for n in workflow["nodes"] if n["type"] == "n8n-nodes-base.webhook"
        )
        assert webhook["parameters"]["path"] == "social/publish"
        assert webhook["parameters"]["httpMethod"] == "POST"

    def test_retries_are_disabled_on_a_non_idempotent_call(self, workflow):
        """A blind retry can publish the same post twice."""
        http_node = next(
            n for n in workflow["nodes"] if n["type"] == "n8n-nodes-base.httpRequest"
        )
        assert http_node.get("retryOnFail") is False
        assert http_node.get("onError") == "continueRegularOutput"


class TestRequestContract:
    def test_validates_without_consulting_a_registry(self, workflow):
        """Validation must not need account data to run."""
        code = next(
            n for n in workflow["nodes"] if n["name"] == "02 - Validate Request Contract"
        )
        js = code["parameters"]["jsCode"]
        assert "MISSING_ACCOUNT_ID" in js
        assert "MISSING_CHANNEL_ID" in js
        assert "ACCOUNT_NOT_FOUND" not in js

    def test_preserves_a_caller_supplied_request_id(self, workflow):
        code = next(
            n for n in workflow["nodes"] if n["name"] == "02 - Validate Request Contract"
        )
        assert "request_id" in code["parameters"]["jsCode"]
        assert "x-request-id" in code["parameters"]["jsCode"]

    def test_forwards_only_internal_ids_to_the_backend(self, workflow):
        """The payload sent downstream must carry no provider identifiers."""
        http_node = next(
            n for n in workflow["nodes"] if n["type"] == "n8n-nodes-base.httpRequest"
        )
        body_expr = http_node["parameters"]["body"]
        assert "$json.payload" in body_expr
        # The payload is built purely from the incoming request.
        code = next(
            n for n in workflow["nodes"] if n["name"] == "02 - Validate Request Contract"
        )
        js = code["parameters"]["jsCode"]
        assert "external_channel_id" not in js
        assert "api_key" not in js


class TestLegacyExportPreserved:
    def test_original_workflow_file_still_exists(self):
        """The working export must not be deleted by this work."""
        assert LEGACY.exists(), "original workflow JSON was removed"

    def test_original_workflow_is_still_untouched(self):
        """Its hardcoded account registry is what we moved out of n8n."""
        legacy = json.loads(LEGACY.read_text(encoding="utf-8"))
        assert len(legacy["nodes"]) == 22
