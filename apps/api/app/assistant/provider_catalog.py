"""Provider configuration facts shared by settings validation and Pi runtime."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderSpec:
    id: str
    base_url: str
    default_model: str
    auth: str
    protocol: str
    usage: str
    cost: str
    recoverable_error: str = "missing_key_or_endpoint"
    usage_fields: tuple[str, ...] = ("provider", "model", "input", "output", "cacheRead", "cacheWrite", "totalTokens", "latency_ms", "estimated_cost")
    secret_redaction: bool = True


PROVIDER_CATALOG = {
    "minimax": ProviderSpec("minimax", "https://api.minimax.cn/v1", "MiniMax-M3", "api_key", "openai_compatible", "provider_or_unknown", "sdk_estimate_or_unknown", "missing_key_or_endpoint", ("provider", "model", "input", "output", "cacheRead", "cacheWrite", "totalTokens", "latency_ms", "estimated_cost"), True),
    "deepseek": ProviderSpec("deepseek", "https://api.deepseek.com", "deepseek-flash", "api_key", "openai_compatible", "provider_or_unknown", "sdk_estimate_or_unknown", "missing_key_or_endpoint", ("provider", "model", "input", "output", "cacheRead", "cacheWrite", "totalTokens", "latency_ms", "estimated_cost"), True),
    "openai": ProviderSpec("openai", "https://api.openai.com/v1", "gpt-5.4", "api_key", "openai_compatible", "provider_or_unknown", "sdk_estimate_or_unknown"),
    "openrouter": ProviderSpec("openrouter", "https://openrouter.ai/api/v1", "openai/gpt-5.4", "api_key", "openai_compatible", "provider_or_unknown", "sdk_estimate_or_unknown"),
    "gemini": ProviderSpec("gemini", "https://generativelanguage.googleapis.com/v1beta/openai", "gemini-3-flash-preview", "api_key", "openai_compatible", "provider_or_unknown", "sdk_estimate_or_unknown"),
    "anthropic": ProviderSpec("anthropic", "https://api.anthropic.com/v1", "claude-sonnet-4-5", "api_key", "anthropic_messages", "provider_or_unknown", "sdk_estimate_or_unknown", "missing_key_or_endpoint"),
    "moonshot": ProviderSpec("moonshot", "https://api.moonshot.cn/v1", "kimi-k2-0905-preview", "api_key", "openai_compatible", "provider_or_unknown", "sdk_estimate_or_unknown"),
    "qwen": ProviderSpec("qwen", "https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen-plus", "api_key", "openai_compatible", "provider_or_unknown", "sdk_estimate_or_unknown"),
    "zhipu": ProviderSpec("zhipu", "https://open.bigmodel.cn/api/paas/v4", "glm-4.5", "api_key", "openai_compatible", "provider_or_unknown", "sdk_estimate_or_unknown"),
    "baichuan": ProviderSpec("baichuan", "https://api.baichuan-ai.com/v1", "Baichuan4-Turbo", "api_key", "openai_compatible", "provider_or_unknown", "sdk_estimate_or_unknown"),
    "hunyuan": ProviderSpec("hunyuan", "https://api.hunyuan.cloud.tencent.com/v1", "hunyuan-turbos", "api_key", "openai_compatible", "provider_or_unknown", "sdk_estimate_or_unknown"),
    "siliconflow": ProviderSpec("siliconflow", "https://api.siliconflow.cn/v1", "deepseek-ai/DeepSeek-V3.2", "api_key", "openai_compatible", "provider_or_unknown", "sdk_estimate_or_unknown"),
    "mistral": ProviderSpec("mistral", "https://api.mistral.ai/v1", "mistral-large-latest", "api_key", "openai_compatible", "provider_or_unknown", "sdk_estimate_or_unknown"),
    "groq": ProviderSpec("groq", "https://api.groq.com/openai/v1", "llama-3.3-70b-versatile", "api_key", "openai_compatible", "provider_or_unknown", "sdk_estimate_or_unknown"),
    "together": ProviderSpec("together", "https://api.together.xyz/v1", "meta-llama/Llama-3.3-70B-Instruct-Turbo", "api_key", "openai_compatible", "provider_or_unknown", "sdk_estimate_or_unknown"),
    "fireworks": ProviderSpec("fireworks", "https://api.fireworks.ai/inference/v1", "accounts/fireworks/models/llama-v3p1-70b-instruct", "api_key", "openai_compatible", "provider_or_unknown", "sdk_estimate_or_unknown"),
    "xai": ProviderSpec("xai", "https://api.x.ai/v1", "grok-4", "api_key", "openai_compatible", "provider_or_unknown", "sdk_estimate_or_unknown"),
    "cohere": ProviderSpec("cohere", "https://api.cohere.com/compatibility/v1", "command-a-03-2025", "api_key", "openai_compatible", "provider_or_unknown", "sdk_estimate_or_unknown"),
    "perplexity": ProviderSpec("perplexity", "https://api.perplexity.ai", "sonar-pro", "api_key", "openai_compatible", "provider_or_unknown", "sdk_estimate_or_unknown"),
    "ollama": ProviderSpec("ollama", "http://127.0.0.1:11434/v1", "qwen3:8b", "optional", "openai_compatible", "provider_or_unknown", "unknown", "local_endpoint_unavailable"),
    "lmstudio": ProviderSpec("lmstudio", "http://127.0.0.1:1234/v1", "", "optional", "openai_compatible", "provider_or_unknown", "unknown", "local_endpoint_unavailable"),
    "custom": ProviderSpec("custom", "", "", "optional", "openai_compatible", "provider_or_unknown", "sdk_estimate_or_unknown"),
}


def provider_spec(provider: str) -> ProviderSpec:
    return PROVIDER_CATALOG.get((provider or "").strip().lower(), PROVIDER_CATALOG["custom"])


def provider_contract(provider: str) -> dict[str, object]:
    spec = provider_spec(provider)
    return {"provider": spec.id, "protocol": spec.protocol, "base_url": spec.base_url,
            "default_model": spec.default_model, "auth": spec.auth,
            "recoverable_error": spec.recoverable_error, "usage_fields": list(spec.usage_fields),
            "secret_redaction": spec.secret_redaction}


def provider_catalog_for_settings() -> tuple[dict[str, str], ...]:
    return tuple({"id": spec.id, "base_url": spec.base_url, "default_model": spec.default_model,
                  "auth": spec.auth, "protocol": spec.protocol, "usage": spec.usage, "cost": spec.cost,
                  "recoverable_error": spec.recoverable_error, "usage_fields": list(spec.usage_fields),
                  "secret_redaction": spec.secret_redaction}
                 for spec in PROVIDER_CATALOG.values())
