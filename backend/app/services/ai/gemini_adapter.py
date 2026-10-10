"""Google Gemini adapter.

Endpoint verified against current Google AI documentation:
  POST {base}/v1beta/models/{model}:generateContent?key=<API_KEY>
  system prompt goes in `system_instruction`; content in `contents`.
  Structured output via `generationConfig.responseMimeType = "application/json"`.
"""
from __future__ import annotations

import json

import httpx

from app.services.ai.base import (
    AIProviderAdapter,
    GeneratedPost,
    ProviderBadResponseError,
    ProviderError,
    ProviderInfo,
    extract_json_object,
)
from app.services.ai.openai_adapter import POST_JSON_SCHEMA, build_payload, classify_status


class GeminiAdapter(AIProviderAdapter):
    slug = "gemini"
    display_name = "Google Gemini"
    supports_model_discovery = True
    default_base_url = "https://generativelanguage.googleapis.com/v1beta"
    # A dated model (gemini-2.0-flash) is dead for new accounts, and Google
    # 404s it. The "-latest" alias always resolves to the current release, so
    # this default keeps working instead of failing on first generate.
    default_model = "gemini-flash-latest"

    def _key_param(self) -> str:
        # Gemini authenticates with the key as a query parameter.
        return f"?key={self.api_key}"

    def validate_credentials(self) -> ProviderInfo:
        if not self.api_key:
            return ProviderInfo(ok=False, detail="No API key is configured.", error_code="AI_AUTH_ERROR")
        try:
            with httpx.Client(timeout=min(self.timeout, 20.0)) as client:
                _gemini_request(client, "GET", f"{self.base_url}/models{self._key_param()}", "Gemini")
        except ProviderError as exc:
            return ProviderInfo(ok=False, detail=exc.message, error_code=exc.code)
        return ProviderInfo(ok=True, detail="Connected to Google Gemini.")

    def list_models(self) -> ProviderInfo:
        try:
            with httpx.Client(timeout=min(self.timeout, 20.0)) as client:
                data = _gemini_request(client, "GET", f"{self.base_url}/models{self._key_param()}", "Gemini")
        except ProviderError as exc:
            return ProviderInfo(ok=False, detail=exc.message, error_code=exc.code)
        models = sorted(
            {
                str(m.get("name")).split("/")[-1]
                for m in data.get("models", [])
                if m.get("name") and "generateContent" in str(m.get("supportedGenerationMethods", []))
            }
        )
        return ProviderInfo(ok=True, models=models, detail=f"{len(models)} models available.")

    def generate_structured(self, system_prompt: str, user_prompt: str) -> GeneratedPost:
        self._require()
        body = {
            "system_instruction": {
                "parts": [{"text": system_prompt + "\n\n" + _schema_hint()}]
            },
            "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
            "generationConfig": {
                "temperature": 0.8,
                "responseMimeType": "application/json",
                "responseSchema": _gemini_schema(),
            },
        }
        url = f"{self.base_url}/models/{self.model}:generateContent{self._key_param()}"
        with httpx.Client(timeout=self.timeout) as client:
            data = _gemini_request(client, "POST", url, "Gemini", json_body=body)

        candidates = data.get("candidates") or []
        if not candidates:
            raise ProviderBadResponseError("Gemini returned no candidates.")
        parts = (candidates[0].get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts if isinstance(p, dict))
        if not text.strip():
            raise ProviderBadResponseError("Gemini returned no text content.")

        usage = data.get("usageMetadata") or {}
        parsed = extract_json_object(text)
        return build_payload(
            parsed,
            model=self.model,
            in_tokens=usage.get("promptTokenCount"),
            out_tokens=usage.get("candidatesTokenCount"),
        )


def _gemini_request(
    client: httpx.Client,
    method: str,
    url: str,
    provider: str,
    json_body: dict | None = None,
) -> dict:
    try:
        response = client.request(
            method,
            url,
            headers={"Content-Type": "application/json"},
            json=json_body,
        )
    except httpx.TimeoutException as exc:
        from app.services.ai.base import ProviderTimeoutError

        raise ProviderTimeoutError(f"{provider} did not respond in time.") from exc
    except httpx.HTTPError as exc:
        from app.services.ai.base import ProviderUnavailableError

        raise ProviderUnavailableError(f"Could not reach {provider}.") from exc

    if response.status_code >= 400:
        raise classify_status(response.status_code, response.text)

    try:
        data = response.json()
    except ValueError as exc:
        raise ProviderBadResponseError(f"{provider} returned a non-JSON response.") from exc
    if not isinstance(data, dict):
        raise ProviderBadResponseError(f"{provider} returned an unexpected response shape.")
    return data


def _gemini_schema() -> dict:
    """Gemini uses OpenAPI-ish subset; upper-case types."""
    schema = POST_JSON_SCHEMA["schema"]
    return {
        "type": "OBJECT",
        "properties": {
            "post_text": {"type": "STRING"},
            "hashtags": {"type": "ARRAY", "items": {"type": "STRING"}},
            "suggested_cta": {"type": "STRING"},
            "media_idea": {"type": "STRING"},
            "notes": {"type": "STRING"},
            "validation_warnings": {"type": "ARRAY", "items": {"type": "STRING"}},
        },
        "required": [
            "post_text",
            "hashtags",
            "suggested_cta",
            "media_idea",
            "notes",
            "validation_warnings",
        ],
    }


def _schema_hint() -> str:
    return (
        "Respond with a single JSON object matching this schema exactly:\n"
        + json.dumps(POST_JSON_SCHEMA["schema"], indent=2)
    )