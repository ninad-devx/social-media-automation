"""Anthropic adapter.

Endpoint and auth verified against current Anthropic documentation:
  POST {base}/v1/messages
  headers: x-api-key, anthropic-version: 2023-06-01
The system prompt is a TOP-LEVEL `system` parameter - the Messages API has no
"system" role. `max_tokens` is required.
"""
from __future__ import annotations

from typing import Any

import httpx

from app.services.ai.base import (
    AIProviderAdapter,
    GeneratedPost,
    ProviderAuthError,
    ProviderBadResponseError,
    ProviderError,
    ProviderInfo,
    extract_json_object,
)
from app.services.ai.openai_adapter import POST_JSON_SCHEMA, classify_status, request_json

ANTHROPIC_VERSION = "2023-06-01"


class AnthropicAdapter(AIProviderAdapter):
    slug = "anthropic"
    display_name = "Anthropic"
    supports_model_discovery = True
    default_base_url = "https://api.anthropic.com/v1"
    default_model = "claude-sonnet-4-5"

    def _headers(self) -> dict[str, str]:
        return {
            "x-api-key": self.api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "Content-Type": "application/json",
        }

    def validate_credentials(self) -> ProviderInfo:
        if not self.api_key:
            return ProviderInfo(ok=False, detail="No API key is configured.", error_code="AI_AUTH_ERROR")
        try:
            with httpx.Client(timeout=min(self.timeout, 20.0)) as client:
                request_json(
                    client, "GET", f"{self.base_url}/models", headers=self._headers(), provider="Anthropic"
                )
        except ProviderError as exc:
            return ProviderInfo(ok=False, detail=exc.message, error_code=exc.code)
        return ProviderInfo(ok=True, detail="Connected to Anthropic.")

    def list_models(self) -> ProviderInfo:
        try:
            with httpx.Client(timeout=min(self.timeout, 20.0)) as client:
                data = request_json(
                    client, "GET", f"{self.base_url}/models", headers=self._headers(), provider="Anthropic"
                )
        except ProviderError as exc:
            return ProviderInfo(ok=False, detail=exc.message, error_code=exc.code)
        models = sorted({str(m.get("id")) for m in data.get("data", []) if m.get("id")})
        return ProviderInfo(ok=True, models=models, detail=f"{len(models)} models available.")

    def generate_structured(self, system_prompt: str, user_prompt: str) -> GeneratedPost:
        self._require()
        body: dict[str, Any] = {
            "model": self.model,
            "max_tokens": 1400,
            # Top-level system prompt, not a message role.
            "system": system_prompt + _schema_hint(),
            "messages": [{"role": "user", "content": user_prompt}],
            "temperature": 0.8,
        }
        with httpx.Client(timeout=self.timeout) as client:
            data = request_json(
                client,
                "POST",
                f"{self.base_url}/messages",
                headers=self._headers(),
                json_body=body,
                provider="Anthropic",
            )

        blocks = data.get("content") or []
        text = "".join(b.get("text", "") for b in blocks if isinstance(b, dict) and b.get("type") == "text")
        if not text.strip():
            raise ProviderBadResponseError("Anthropic returned no text content.")

        usage = data.get("usage") or {}
        parsed = extract_json_object(text)

        from app.services.ai.openai_adapter import build_payload

        return build_payload(
            parsed,
            model=data.get("model"),
            in_tokens=usage.get("input_tokens"),
            out_tokens=usage.get("output_tokens"),
        )


def _schema_hint() -> str:
    """State the required JSON shape explicitly.

    Anthropic's structured-output support is still beta, so the schema is
    described in the prompt and the response is validated on our side.
    """
    import json

    return (
        "\n\nRespond with a single JSON object and nothing else. "
        "It must match this schema exactly:\n"
        + json.dumps(POST_JSON_SCHEMA["schema"], indent=2)
    )