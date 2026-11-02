from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from pathlib import Path

from app.env_files import (
    write_dotenv_values as write_dotenv_values,
    prepare_dotenv_values,
    commit_prepared_dotenv_values,
    restore_prepared_dotenv_values,
    discard_prepared_dotenv_values,
)
from app.workspace_settings import (
    WORKSPACE_SECRET_FIELDS,
    delete_workspace_secret,
    load_workspace_config,
    read_workspace_secret_fresh,
    prepare_workspace_config,
    commit_prepared_workspace_config,
    restore_prepared_workspace_config,
    discard_prepared_workspace_config,
    prepare_workspace_secret_changes,
    commit_prepared_workspace_secrets,
    restore_prepared_workspace_secrets,
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
    """Save the device-only public path through the same owned persistence rules."""
    if not _save_runtime_settings(None, Path(env_path), payload):
        return False
    reload_dependencies()
    return True


LLM_KEY_DELETE_ACTION = "delete"


def _secret_fields_for_provider(workspace_id: str) -> list[str]:
    """当前工作区实际在用的那把钥匙串键，以及同供应商的旧备用键。

    DeepSeek 安装还可能留着旧的 `deepseek_api_key`；只删 `llm_api_key` 的话，
    删完那把旧键会继续生效。还有一种真实状态是：快照里没写 provider，只存了
    `deepseek_api_key` —— 此时生效的供应商就是 DeepSeek，删除也得把它清掉。
    删除按当前供应商来，别的供应商和其他平台的凭据不碰。
    """
    snapshot = load_workspace_config(workspace_id)
    provider = (snapshot.get("llm_provider") or "").strip().lower()
    owner = (snapshot.get("llm_api_key_provider") or "").strip().lower()
    if not provider and read_workspace_secret_fresh(workspace_id, "deepseek_api_key"):
        provider = "deepseek"
    if provider == "deepseek":
        fields = ["deepseek_api_key"]
        if not owner or owner == "deepseek":
            fields.append("llm_api_key")
        return fields
    return ["llm_api_key"]

# 本单只接管文字服务这一组字段。模型盘、媒体目录、ASR、腾讯云、Supabase 这些
# 设备级设置仍走原来的全局 dotenv 路径，不当成工作区文案配置处理。
WORKSPACE_TEXT_FIELDS = ("llm_provider", "llm_base_url", "llm_model", "deepseek_model")


def split_runtime_settings_updates(
    payload: Mapping[str, object],
) -> tuple[dict[str, str], dict[str, str]]:
    """把文字服务更新拆成「进工作区配置快照」和「进系统钥匙串」两份。

    密钥只进钥匙串：快照和 dotenv 都不许出现明文。非文字服务的设置不在这份
    拆分里，它们仍旧走原来的 dotenv 写入路径。
    """
    updates = runtime_settings_updates(payload)
    config_updates: dict[str, str] = {}
    secret_updates: dict[str, str] = {}
    for field in WORKSPACE_TEXT_FIELDS:
        env_key = FIELD_TO_ENV[field]
        if env_key in updates:
            config_updates[field] = updates[env_key]
    for field in WORKSPACE_SECRET_FIELDS:
        env_key = FIELD_TO_ENV[field]
        if env_key in updates:
            secret_updates[field] = updates[env_key]
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

    删除走同一个保存路由的显式动作字段 `llm_key_action=delete`。没传动作字段
    时，密钥输入框留空表示「不改」，不能因为空输入把已存的密钥删掉。
    """
    if not _save_runtime_settings(workspace_id, None, payload):
        return False
    reload_dependencies()
    return True


class RuntimeSettingsSaveError(RuntimeError):
    """A rejected persistence operation whose recovery could not be verified."""
    def __init__(self, original: Exception, failures: list[tuple[str, Exception]]) -> None:
        self.original = original
        self.failures = tuple(failures)
        super().__init__("配置保存失败，恢复未完成；请检查设置。涉及：" + "、".join(name for name, _ in failures))


def device_runtime_settings_values(payload: Mapping[str, object]) -> dict[str, object]:
    return {name: value for name, value in payload.items()
        if name not in WORKSPACE_TEXT_FIELDS and name not in WORKSPACE_SECRET_FIELDS and name != "llm_key_action"}


def apply_runtime_settings_save(workspace_id: str, env_path: str | Path, payload: Mapping[str, object]) -> bool:
    """Coordinate this local combined save; runtime activation stays with main."""
    return _save_runtime_settings(workspace_id, Path(env_path), payload)


def _save_runtime_settings(workspace_id: str | None, env_path: Path | None, payload: Mapping[str, object]) -> bool:
    secret_plan = workspace_plan = device_plan = None
    device_cleanup_attempted = workspace_cleanup_attempted = False
    device_cleanup_complete = workspace_cleanup_complete = False
    try:
        deleting = workspace_id is not None and str(payload.get("llm_key_action", "")) == LLM_KEY_DELETE_ACTION
        config_updates, secret_updates = split_runtime_settings_updates(payload) if workspace_id is not None else ({}, {})
        if deleting:
            secret_updates = {name: None for name in _secret_fields_for_provider(workspace_id)}
        if secret_updates:
            secret_plan = prepare_workspace_secret_changes(workspace_id, secret_updates)
            if deleting and any(value is not None for value in secret_plan.originals.values()):
                config_updates["llm_api_key_provider"] = ""
        if config_updates:
            workspace_plan = prepare_workspace_config(workspace_id, config_updates)
        device_values = device_runtime_settings_values(payload) if workspace_id is not None else payload
        env_updates = runtime_settings_updates(device_values) if env_path is not None else {}
        if env_updates:
            device_plan = prepare_dotenv_values(env_path, env_updates)
        if secret_plan is not None:
            commit_prepared_workspace_secrets(secret_plan)
        if workspace_plan is not None:
            commit_prepared_workspace_config(workspace_plan)
        if device_plan is not None:
            commit_prepared_dotenv_values(device_plan)
        if device_plan is not None:
            device_cleanup_attempted = True
            discard_prepared_dotenv_values(device_plan)
            device_cleanup_complete = True
        if workspace_plan is not None:
            workspace_cleanup_attempted = True
            discard_prepared_workspace_config(workspace_plan)
            workspace_cleanup_complete = True
    except Exception as original:
        failures = []
        first_error = original
        if isinstance(original, ExceptionGroup):
            first_error = original.exceptions[0]
            failures.append(("预备临时文件", original))
        if device_cleanup_attempted and not device_cleanup_complete:
            failures.append(("设备临时文件", original))
        if workspace_cleanup_attempted and not workspace_cleanup_complete:
            failures.append(("工作区临时文件", original))
        if device_plan is not None:
            try:
                restore_prepared_dotenv_values(device_plan)
            except Exception as exc:
                failures.append(("设备配置", exc))
        if workspace_plan is not None:
            try:
                restore_prepared_workspace_config(workspace_plan)
            except Exception as exc:
                failures.append(("工作区配置", exc))
        if secret_plan is not None:
            try:
                restore_prepared_workspace_secrets(secret_plan)
            except Exception as exc:
                failures.append(("工作区密钥", exc))
        if device_plan is not None and not device_cleanup_attempted:
            try:
                discard_prepared_dotenv_values(device_plan)
            except Exception as exc:
                failures.append(("设备临时文件", exc))
        if workspace_plan is not None and not workspace_cleanup_attempted:
            try:
                discard_prepared_workspace_config(workspace_plan)
            except Exception as exc:
                failures.append(("工作区临时文件", exc))
        if failures:
            raise RuntimeSettingsSaveError(first_error, failures) from ExceptionGroup(
                "settings persistence and recovery failed", [original, *(exc for _, exc in failures)])
        raise
    return bool(deleting or secret_plan is not None or workspace_plan is not None or device_plan is not None)


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
