from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path

from app.config import write_dotenv_values

LOCAL_CLIENT_HOSTS = {"127.0.0.1", "::1", "localhost", "testclient"}


FIELD_TO_ENV = {
    "llm_provider": "LLM_PROVIDER",
    "llm_api_key": "LLM_API_KEY",
    "llm_base_url": "LLM_BASE_URL",
    "llm_model": "LLM_MODEL",
    "deepseek_api_key": "DEEPSEEK_API_KEY",
    "deepseek_model": "DEEPSEEK_MODEL",
    "product_hunt_token": "PRODUCT_HUNT_TOKEN",
    "tencentcloud_secret_id": "TENCENTCLOUD_SECRET_ID",
    "tencentcloud_secret_key": "TENCENTCLOUD_SECRET_KEY",
    "tencentcloud_asr_region": "TENCENTCLOUD_ASR_REGION",
    "tencentcloud_asr_engine_model_type": "TENCENTCLOUD_ASR_ENGINE_MODEL_TYPE",
    "local_whisper_python_path": "LOCAL_WHISPER_PYTHON_PATH",
    "local_whisper_model_path": "LOCAL_WHISPER_MODEL_PATH",
    "local_whisper_language": "LOCAL_WHISPER_LANGUAGE",
    "obsidian_vault_path": "OBSIDIAN_VAULT_PATH",
    "hermes_radar_path": "HERMES_RADAR_PATH",
    "video_inbox_path": "VIDEO_INBOX_PATH",
    "video_output_path": "VIDEO_OUTPUT_PATH",
    "draft_output_path": "DRAFT_OUTPUT_PATH",
    "jianying_draft_root_path": "JIANYING_DRAFT_ROOT_PATH",
    "database_path": "DATABASE_PATH",
    "model_root_path": "MODEL_ROOT_PATH",
    "temporary_root_path": "TEMPORARY_ROOT_PATH",
    "draft_background_music_path": "DRAFT_BACKGROUND_MUSIC_PATH",
    "supabase_url": "SUPABASE_URL",
    "supabase_publishable_key": "SUPABASE_PUBLISHABLE_KEY",
    "supabase_secret_key": "SUPABASE_SECRET_KEY",
    "supabase_jwt_audience": "SUPABASE_JWT_AUDIENCE",
}


def is_local_client_host(host: str | None) -> bool:
    return bool(host and host.lower() in LOCAL_CLIENT_HOSTS)


def runtime_settings_updates(payload: Mapping[str, object]) -> dict[str, str]:
    updates: dict[str, str] = {}
    for field, env_key in FIELD_TO_ENV.items():
        value = payload.get(field)
        if isinstance(value, str) and value.strip():
            updates[env_key] = value.strip()

    if "LLM_API_KEY" in updates:
        provider = payload.get("llm_provider")
        if isinstance(provider, str) and provider.strip():
            updates["LLM_API_KEY_PROVIDER"] = provider.strip().lower()

    topic_source_urls = payload.get("topic_source_urls")
    if isinstance(topic_source_urls, list):
        urls = [
            url.strip()
            for url in topic_source_urls
            if isinstance(url, str) and url.strip()
        ]
        if urls:
            updates["TOPIC_SOURCE_URLS"] = ",".join(urls)
    return updates


def apply_runtime_settings_update(
    env_path: str | Path,
    payload: Mapping[str, object],
    reload_dependencies: Callable[[], None],
) -> bool:
    updates = runtime_settings_updates(payload)
    if not updates:
        return False
    write_dotenv_values(Path(env_path), updates)
    reload_dependencies()
    return True
