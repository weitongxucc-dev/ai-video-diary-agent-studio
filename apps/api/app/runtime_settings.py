from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from pathlib import Path

from app.config import write_dotenv_values
from app.workspace_settings import (
    WORKSPACE_SECRET_FIELDS,
    WorkspaceSecretStoreError,
    delete_workspace_secret,
    save_workspace_config,
    save_workspace_secret,
)

LOCAL_CLIENT_HOSTS = {"127.0.0.1", "::1", "localhost", "testclient"}


FIELD_TO_ENV = {
    "understanding_runtime_python_path": "UNDERSTANDING_RUNTIME_PYTHON_PATH",
    "native_ocr_worker_path": "NATIVE_OCR_WORKER_PATH",
    "asr_provider": "ASR_PROVIDER",
    "local_whisper_backend": "LOCAL_WHISPER_BACKEND",
    "local_whisper_cli_path": "LOCAL_WHISPER_CLI_PATH",
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
    """旧的全局 dotenv 写入路径，main.py 目前仍调用；本单新接线走工作区函数。"""
    updates = runtime_settings_updates(payload)
    if not updates:
        return False
    write_dotenv_values(Path(env_path), updates)
    reload_dependencies()
    return True


def split_runtime_settings_updates(
    payload: Mapping[str, object],
) -> tuple[dict[str, str], dict[str, str]]:
    """把设置更新拆成「进工作区配置快照」和「进系统钥匙串」两份。

    密钥只进钥匙串：快照和 dotenv 都不许出现明文。删除用空字符串表示。
    """
    updates = runtime_settings_updates(payload)
    config_updates: dict[str, str] = {}
    secret_updates: dict[str, str] = {}
    for field, env_key in FIELD_TO_ENV.items():
        if env_key not in updates:
            continue
        if field in WORKSPACE_SECRET_FIELDS:
            secret_updates[field] = updates[env_key]
        else:
            config_updates[field] = updates[env_key]
    if "LLM_API_KEY" in updates:
        provider = updates.get("LLM_API_KEY_PROVIDER")
        if provider:
            config_updates["llm_api_key_provider"] = provider
    return config_updates, secret_updates


def apply_workspace_runtime_settings_update(
    workspace_id: str,
    payload: Mapping[str, object],
    reload_dependencies: Callable[[], None],
) -> bool:
    """按工作区保存文字服务配置。

    密钥先落系统钥匙串再写配置：钥匙串失败就整单失败，绝不退回明文文件，
    也不会出现「配置显示已保存、密钥其实没存」的状态。
    """
    config_updates, secret_updates = split_runtime_settings_updates(payload)
    for field, value in secret_updates.items():
        save_workspace_secret(workspace_id, field, value)
    if config_updates:
        save_workspace_config(workspace_id, config_updates)
    if not config_updates and not secret_updates:
        return False
    reload_dependencies()
    return True


def delete_workspace_runtime_secrets(
    workspace_id: str,
    fields: Iterable[str],
) -> list[str]:
    """删掉指定工作区的文字服务密钥，返回真的删掉的字段。"""
    deleted = []
    for field in fields:
        if delete_workspace_secret(workspace_id, field):
            deleted.append(field)
    return deleted
