from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path

from app.config import write_dotenv_values


LOCAL_CLIENT_HOSTS = {"127.0.0.1", "::1", "localhost", "testclient"}


FIELD_TO_ENV = {
    "deepseek_api_key": "DEEPSEEK_API_KEY",
    "deepseek_model": "DEEPSEEK_MODEL",
    "seedance_api_key": "SEEDANCE_API_KEY",
    "seedance_base_url": "SEEDANCE_BASE_URL",
    "seedance_status_url_template": "SEEDANCE_STATUS_URL_TEMPLATE",
    "seedance_model": "SEEDANCE_MODEL",
    "cosyvoice_api_key": "COSYVOICE_API_KEY",
    "cosyvoice_base_url": "COSYVOICE_BASE_URL",
    "cosyvoice_status_url_template": "COSYVOICE_STATUS_URL_TEMPLATE",
    "cosyvoice_model": "COSYVOICE_MODEL",
    "cosyvoice_voice_id": "COSYVOICE_VOICE_ID",
    "heygem_api_key": "HEYGEM_API_KEY",
    "heygem_base_url": "HEYGEM_BASE_URL",
    "heygem_status_url_template": "HEYGEM_STATUS_URL_TEMPLATE",
    "heygem_avatar_id": "HEYGEM_AVATAR_ID",
    "social_auto_upload_api_key": "SOCIAL_AUTO_UPLOAD_API_KEY",
    "social_auto_upload_base_url": "SOCIAL_AUTO_UPLOAD_BASE_URL",
    "social_auto_upload_status_url_template": "SOCIAL_AUTO_UPLOAD_STATUS_URL_TEMPLATE",
    "yt_dlp_cookies_path": "YT_DLP_COOKIES_PATH",
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
    "bgm_library_path": "BGM_LIBRARY_PATH",
    "jianying_draft_root_path": "JIANYING_DRAFT_ROOT_PATH",
    "database_path": "DATABASE_PATH",
}


def is_local_client_host(host: str | None) -> bool:
    return bool(host and host.lower() in LOCAL_CLIENT_HOSTS)


def runtime_settings_updates(payload: Mapping[str, object]) -> dict[str, str]:
    updates: dict[str, str] = {}
    for field, env_key in FIELD_TO_ENV.items():
        value = payload.get(field)
        if isinstance(value, str) and value.strip():
            updates[env_key] = value.strip()

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
