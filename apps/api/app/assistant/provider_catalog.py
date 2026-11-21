"""Provider configuration facts shared by settings validation and Pi runtime."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderSpec:
    id: str
    base_url: str
    default_model: str
    auth: str
    usage: str
    cost: str


PROVIDER_CATALOG = {
    "minimax": ProviderSpec("minimax", "https://api.minimax.cn/v1", "MiniMax-M3", "api_key", "provider_or_unknown", "sdk_estimate_or_unknown"),
    "deepseek": ProviderSpec("deepseek", "https://api.deepseek.com", "deepseek-flash", "api_key", "provider_or_unknown", "sdk_estimate_or_unknown"),
    "openai": ProviderSpec("openai", "https://api.openai.com/v1", "gpt-5.4", "api_key", "provider_or_unknown", "sdk_estimate_or_unknown"),
    "openrouter": ProviderSpec("openrouter", "https://openrouter.ai/api/v1", "openai/gpt-5.4", "api_key", "provider_or_unknown", "sdk_estimate_or_unknown"),
    "gemini": ProviderSpec("gemini", "https://generativelanguage.googleapis.com/v1beta/openai", "gemini-3-flash-preview", "api_key", "provider_or_unknown", "sdk_estimate_or_unknown"),
    "ollama": ProviderSpec("ollama", "http://127.0.0.1:11434/v1", "qwen3:8b", "optional", "provider_or_unknown", "unknown"),
    "custom": ProviderSpec("custom", "", "", "optional", "provider_or_unknown", "sdk_estimate_or_unknown"),
}


def provider_spec(provider: str) -> ProviderSpec:
    return PROVIDER_CATALOG.get((provider or "").strip().lower(), PROVIDER_CATALOG["custom"])


def provider_catalog_for_settings() -> tuple[dict[str, str], ...]:
    return tuple({"id": spec.id, "base_url": spec.base_url, "default_model": spec.default_model,
                  "auth": spec.auth, "usage": spec.usage, "cost": spec.cost}
                 for spec in PROVIDER_CATALOG.values())
