"""OpenAI adapter.

Endpoint and auth verified against current OpenAI documentation:
  POST {base}/v1/chat/completions   Authorization: Bearer <key>
  GET  {base}/v1/models             model discovery
Structured output uses `response_format: {type: "json_schema", ...}`.
"""
from __future__ import annotations

from typing import Any

import httpx

from app.services.ai.base import (
    AIProviderAdapter,
    GeneratedPost,
    ProviderAuthError,
    ProviderBadResponseError,
    ProviderCreditsError,
    ProviderError,
    ProviderInfo,
    ProviderModelUnavailableError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    extract_json_object,
)

# Schema every adapter asks for. Kept in one place so all providers return
# the same shape.
POST_JSON_SCHEMA: dict[str, Any] = {
    "name": "social_post",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "post_text": {"type": "string"},
            "hashtags": {"type": "array", "items": {"type": "string"}},
            "suggested_cta": {"type": "string"},
            "media_idea": {"type": "string"},
            "notes": {"type": "string"},
            "validation_warnings": {"type": "array", "items": {"type": "string"}},
        },
        "required": [
            "post_text",
            "hashtags",
            "suggested_cta",
            "media_idea",
            "notes",
            "validation_warnings",
        ],
    },
}


def build_payload(parsed: dict, *, model: str | None, in_tokens: int | None, out_tokens: int | None) -> GeneratedPost:
    hashtags = [str(h).lstrip("#").strip() for h in (parsed.get("hashtags") or []) if str(h).strip()]
    warnings = [str(w) for w in (parsed.get("validation_warnings") or []) if str(w).strip()]
    return GeneratedPost(
        post_text=str(parsed.get("post_text") or "").strip(),
        hashtags=hashtags,
        suggested_cta=(str(parsed.get("suggested_cta")).strip() or None) if parsed.get("suggested_cta") else None,
        media_idea=(str(parsed.get("media_idea")).strip() or None) if parsed.get("media_idea") else None,
        notes=(str(parsed.get("notes")).strip() or None) if parsed.get("notes") else None,
        warnings=warnings,
        model=model,
        prompt_tokens=in_tokens,
        completion_tokens=out_tokens,
    )


def classify_status(status: int, body_text: str) -> ProviderError:
    """Map an HTTP failure onto a typed, user-safe provider error."""
    lowered = (body_text or "").lower()

    # Check the body BEFORE the status code. Several providers (Gemini in
    # particular) return 429 for "you are out of quota", which is a billing
    # problem the user must fix - not something that resolves by retrying.
    if any(signal in lowered for signal in ("exceeded your current quota", "billing", "insufficient_quota", "quota exceeded")):
        return ProviderCreditsError(
            "This provider account has no quota left. Check its billing or plan, "
            "or use a cheaper model."
        )

    if status in (401, 403):
        return ProviderAuthError("The provider rejected this API key. Check the key in Settings.")

    # A model that has been retired for this account. The fix is to pick a
    # different model, so say that instead of showing a bare 404.
    if status == 404 and ("no longer available" in lowered or "not found" in lowered or "is not supported" in lowered):
        return ProviderModelUnavailableError(
            "That model is not available for this account. "
            "Open the model list and choose one that is available."
        )

    if status == 429:
        return ProviderRateLimitError("The provider is rate limiting this key. Wait a moment and retry.")

    if status >= 500:
        return ProviderUnavailableError("The provider is having trouble right now. Please retry.")

    return ProviderError(f"The provider returned HTTP {status}.", status_code=status)


def request_json(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    headers: dict,
    json_body: dict | None = None,
    provider: str,
) -> dict:
    """One request with consistent error handling across every provider."""
    try:
        response = client.request(method, url, headers=headers, json=json_body)
    except httpx.TimeoutException as exc:
        raise ProviderTimeoutError(f"{provider} did not respond in time.") from exc
    except httpx.HTTPError as exc:
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


class OpenAIAdapter(AIProviderAdapter):
    slug = "openai"
    display_name = "OpenAI"
    supports_model_discovery = True
    default_base_url = "https://api.openai.com/v1"
    default_model = "gpt-4o"

    def _headers(self) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        if self.organization:
            headers["OpenAI-Organization"] = self.organization
        if self.project:
            headers["OpenAI-Project"] = self.project
        return headers

    def validate_credentials(self) -> ProviderInfo:
        self._require_key_only()
        try:
            with httpx.Client(timeout=min(self.timeout, 20.0)) as client:
                request_json(
                    client,
                    "GET",
                    f"{self.base_url}/models",
                    headers=self._headers(),
                    provider="OpenAI",
                )
        except ProviderError as exc:
            return ProviderInfo(ok=False, detail=exc.message, error_code=exc.code)
        return ProviderInfo(ok=True, detail="Connected to OpenAI.")

    def list_models(self) -> ProviderInfo:
        try:
            with httpx.Client(timeout=min(self.timeout, 20.0)) as client:
                data = request_json(
                    client, "GET", f"{self.base_url}/models", headers=self._headers(), provider="OpenAI"
                )
        except ProviderError as exc:
            return ProviderInfo(ok=False, detail=exc.message, error_code=exc.code)
        models = sorted({str(m.get("id")) for m in data.get("data", []) if m.get("id")})
        return ProviderInfo(ok=True, models=models, detail=f"{len(models)} models available.")

    def generate_structured(self, system_prompt: str, user_prompt: str) -> GeneratedPost:
        self._require()
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": POST_JSON_SCHEMA,
            },
            "temperature": 0.8,
        }
        with httpx.Client(timeout=self.timeout) as client:
            data = request_json(
                client,
                "POST",
                f"{self.base_url}/chat/completions",
                headers=self._headers(),
                json_body=body,
                provider="OpenAI",
            )

        choices = data.get("choices") or []
        if not choices:
            raise ProviderBadResponseError("OpenAI returned no choices.")
        content = (choices[0].get("message") or {}).get("content") or ""
        usage = data.get("usage") or {}
        parsed = extract_json_object(content)
        return build_payload(
            parsed,
            model=data.get("model"),
            in_tokens=usage.get("prompt_tokens"),
            out_tokens=usage.get("completion_tokens"),
        )

    def _require_key_only(self) -> None:
        if not self.api_key:
            raise ProviderAuthError("No API key is configured for this provider.")