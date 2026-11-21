from app.assistant.provider_catalog import PROVIDER_CATALOG, provider_catalog_for_settings, provider_spec
from app.assistant.runtime_configuration import _configured_model
from app.config import Settings


def test_catalog_covers_openai_compatible_provider_presets_without_prices():
    expected = {"minimax", "deepseek", "openai", "openrouter", "gemini", "anthropic", "moonshot", "qwen", "zhipu", "hunyuan", "siliconflow", "mistral", "groq", "together", "fireworks", "xai", "cohere", "perplexity", "ollama", "custom"}
    assert set(PROVIDER_CATALOG) == expected
    rows = provider_catalog_for_settings()
    assert all(row["usage"] == "provider_or_unknown" for row in rows)
    assert {row["protocol"] for row in rows} == {"openai_compatible", "anthropic_messages"}
    assert all("price" not in row and "amount" not in row for row in provider_catalog_for_settings())
    assert provider_spec("unknown-provider").id == "custom"


def test_settings_defaults_follow_the_same_catalog_shape():
    assert Settings(llm_provider="openai").effective_llm_base_url == "https://api.openai.com/v1"
    assert Settings(llm_provider="openai").effective_llm_model == "gpt-5.4"
    assert Settings(llm_provider="gemini").effective_llm_base_url.endswith("/v1beta/openai")
    assert Settings(llm_provider="ollama").effective_llm_model == "qwen3:8b"


def test_configured_model_requires_keys_only_for_keyed_providers():
    try:
        _configured_model(Settings(llm_provider="openai", llm_model="gpt-5.4", llm_base_url="https://api.openai.com/v1"))
    except Exception as exc:
        assert "API Key" in str(exc)
    else:
        raise AssertionError("OpenAI without a key must remain recoverably unavailable")
    assert _configured_model(Settings(llm_provider="ollama", llm_model="qwen3:8b", llm_base_url="http://127.0.0.1:11434/v1"))[1] == "qwen3:8b"
    assert _configured_model(Settings(llm_provider="custom", llm_model="fixture", llm_base_url="https://provider.example/v1"))[1] == "fixture"


def test_native_protocol_is_catalogued_but_not_sent_through_openai_bridge():
    assert provider_spec("anthropic").protocol == "anthropic_messages"
    try:
        _configured_model(Settings(llm_provider="anthropic", llm_model="claude-sonnet-4-5",
                                   llm_base_url="https://api.anthropic.com/v1", llm_api_key="fixture"))
    except Exception as exc:
        assert "原生协议" in str(exc)
    else:
        raise AssertionError("Anthropic must wait for a native runtime bridge")
