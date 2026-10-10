"""Provider registry.

Campaign generation depends on this module, never on a concrete adapter. Adding
a provider means adding one file and one entry here - no changes to the
campaign service or the API layer.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.services.ai.anthropic_adapter import AnthropicAdapter
from app.services.ai.base import AIProviderAdapter, ProviderNotConfiguredError
from app.services.ai.gemini_adapter import GeminiAdapter
from app.services.ai.openai_adapter import OpenAIAdapter


@dataclass(frozen=True)
class ProviderSpec:
    slug: str
    display_name: str
    adapter: type[AIProviderAdapter]
    supports_model_discovery: bool
    default_base_url: str | None
    default_model: str | None
    key_label: str = "API key"
    help_url: str | None = None
    # Providers that need extra fields beyond key/base_url/model.
    supports_organization: bool = False
    supports_project: bool = False
    requires_base_url: bool = False


_SPECS: tuple[ProviderSpec, ...] = (
    ProviderSpec(
        slug="openai",
        display_name="OpenAI",
        adapter=OpenAIAdapter,
        supports_model_discovery=True,
        default_base_url=OpenAIAdapter.default_base_url,
        default_model=OpenAIAdapter.default_model,
        key_label="OpenAI API key",
        help_url="https://platform.openai.com/api-keys",
        supports_organization=True,
        supports_project=True,
    ),
    ProviderSpec(
        slug="anthropic",
        display_name="Anthropic",
        adapter=AnthropicAdapter,
        supports_model_discovery=True,
        default_base_url=AnthropicAdapter.default_base_url,
        default_model=AnthropicAdapter.default_model,
        key_label="Anthropic API key",
        help_url="https://console.anthropic.com/settings/keys",
    ),
    ProviderSpec(
        slug="gemini",
        display_name="Google Gemini",
        adapter=GeminiAdapter,
        supports_model_discovery=True,
        default_base_url=GeminiAdapter.default_base_url,
        default_model=GeminiAdapter.default_model,
        key_label="Google AI API key",
        help_url="https://aistudio.google.com/app/apikey",
    ),
    ProviderSpec(
        slug="openai_compatible",
        display_name="OpenAI-compatible",
        adapter=OpenAIAdapter,
        supports_model_discovery=False,
        default_base_url=None,
        default_model=None,
        key_label="API key",
        requires_base_url=True,
    ),
)

BY_SLUG: dict[str, ProviderSpec] = {s.slug: s for s in _SPECS}


def get_spec(slug: str) -> ProviderSpec:
    spec = BY_SLUG.get((slug or "").strip().lower())
    if spec is None:
        raise ProviderNotConfiguredError(f"Unknown AI provider '{slug}'.")
    return spec


def list_specs() -> list[ProviderSpec]:
    return list(_SPECS)


def build_adapter(
    slug: str,
    *,
    api_key: str,
    base_url: str | None,
    model: str | None,
    timeout: float,
    organization: str | None = None,
    project: str | None = None,
) -> AIProviderAdapter:
    """Instantiate the right adapter for a configured provider row."""
    spec = get_spec(slug)

    # openai_compatible reuses the OpenAI wire format against a custom host.
    adapter_cls = spec.adapter
    return adapter_cls(
        api_key=api_key,
        base_url=base_url or spec.default_base_url,
        model=model or spec.default_model,
        timeout=timeout,
        organization=organization,
        project=project,
    )