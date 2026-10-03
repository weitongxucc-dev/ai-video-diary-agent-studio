"""按工作区隔离的文字服务配置与密钥存储。

两块职责，都只服务当前这一个消费者——设置页写配置、对话助手和写稿读配置：

1. 工作区配置快照：非密钥字段（provider / base_url / model 等）存到每个工作区
   自己的 JSON 文件，不再共用一份全局 .env。
2. 文字服务密钥：LLM 与旧 DeepSeek 两把密钥放进系统钥匙串（macOS `security`），
   条目名按工作区分开。没有明文兜底：钥匙串写失败就是失败，绝不退回 dotenv。

旧全局 .env 里已有的明文密钥不在这单范围内：它们没有工作区归属，不自动分配、
不复制、不清除。真实钥匙串与登录钥匙串只在测试里用本单自建的临时钥匙串。
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
from dataclasses import dataclass, replace
from pathlib import Path

from app.config import Settings, runtime_env_file_path

# 唯一进钥匙串的两个字段。其余凭据（腾讯云、ProductHunt、Supabase）本单不动。
WORKSPACE_SECRET_FIELDS = ("llm_api_key", "deepseek_api_key")

# 快照里允许出现的非密钥字段。写入前按这张名单过滤，密钥字段永远写不进去。
WORKSPACE_CONFIG_FIELDS = (
    "llm_provider", "llm_base_url", "llm_model", "deepseek_model", "llm_api_key_provider",
)

_CONFIG_ROOT_ENV = "AI_VIDEO_DIARY_WORKSPACE_CONFIG_ROOT"
_KEYCHAIN_ENV = "AI_VIDEO_DIARY_KEYCHAIN_PATH"
_SERVICE_PREFIX = "ai-video-diary.workspace-secret"

# `security` 用 44 报「钥匙串里没这条」，这和「钥匙串锁了/拒绝访问」是两件事：
# 前者是正常的「没存过」，后者必须报出来，不能装作没有。
_ITEM_NOT_FOUND_RETURNCODE = 44
_ITEM_NOT_FOUND_TEXT = "could not be found"

_lock = threading.Lock()
# 键值读取缓存。删除时必须连缓存一起清掉，否则旧密钥会被缓存复活。
_secret_cache: dict[tuple[str, str], str] = {}


class WorkspaceSecretStoreError(RuntimeError):
    """系统安全存储读写失败。调用方只能报错，不许改用明文兜底。"""


def workspace_config_root() -> Path:
    """工作区配置根目录。没显式配置时贴在 dotenv 旁边，不进仓库。"""
    override = os.getenv(_CONFIG_ROOT_ENV)
    if override:
        return Path(override).expanduser()
    return runtime_env_file_path().parent / "workspaces"


def workspace_config_path(workspace_id: str) -> Path:
    return workspace_config_root() / _safe_segment(workspace_id) / "config.json"


def _safe_segment(workspace_id: str) -> str:
    """工作区 id 进文件路径前必须只剩安全字符。"""
    cleaned = "".join(char if char.isalnum() or char in "-_" else "-" for char in workspace_id)
    return cleaned or "unknown"


def _service_name(workspace_id: str, field: str) -> str:
    return f"{_SERVICE_PREFIX}.{_safe_segment(workspace_id)}.{field}"


def _keychain_args() -> list[str]:
    """指定临时钥匙串时用它，测试因此完全不碰登录钥匙串。

    `security` 的钥匙串是位置参数，不是 `-k`。
    """
    keychain = os.getenv(_KEYCHAIN_ENV)
    return [keychain] if keychain else []


def _run_security(arguments: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["security", *arguments],
            text=True, capture_output=True, check=False,
        )
    except OSError as exc:  # security 不存在说明不是 macOS 桌面环境
        raise WorkspaceSecretStoreError("系统钥匙串不可用，密钥没有保存。") from exc


def _is_item_not_found(result: subprocess.CompletedProcess[str]) -> bool:
    return _ITEM_NOT_FOUND_TEXT in (result.stderr or "")


def _require_store(result: subprocess.CompletedProcess[str], action: str) -> None:
    """除「没有这条」以外的失败都是真实的安全边界问题，必须让调用方看见。"""
    if result.returncode == 0 or _is_item_not_found(result):
        return
    raise WorkspaceSecretStoreError(f"密钥{action}失败：系统钥匙串拒绝了这次操作。")


def _require_write(result: subprocess.CompletedProcess[str], action: str) -> None:
    """写入没有「本来就不存在」这回事：任何非零结果都是写入失败，必须失败。"""
    if result.returncode == 0:
        return
    raise WorkspaceSecretStoreError(f"密钥{action}失败：系统钥匙串拒绝了这次写入。")


def save_workspace_secret(workspace_id: str, field: str, value: str) -> None:
    """把一把密钥写进系统钥匙串。失败即抛错，调用方不得退回明文。"""
    if field not in WORKSPACE_SECRET_FIELDS:
        raise ValueError(f"不是本单范围内的文字服务密钥：{field}")
    if not value.strip():
        raise WorkspaceSecretStoreError("密钥内容为空，没有保存。")
    result = _run_security([
        "add-generic-password",
        "-a", workspace_id,
        "-s", _service_name(workspace_id, field),
        "-w", value,
        "-U",
        *_keychain_args(),
    ])
    if result.returncode != 0:
        _require_write(result, "保存")
    with _lock:
        _secret_cache.pop((workspace_id, field), None)


def read_workspace_secret(workspace_id: str, field: str) -> str | None:
    if field not in WORKSPACE_SECRET_FIELDS:
        raise ValueError(f"不是本单范围内的文字服务密钥：{field}")
    with _lock:
        cached = _secret_cache.get((workspace_id, field))
    if cached is not None:
        return cached
    result = _run_security([
        "find-generic-password",
        "-a", workspace_id,
        "-s", _service_name(workspace_id, field),
        "-w",
        *_keychain_args(),
    ])
    if _is_item_not_found(result):
        return None
    _require_store(result, "读取")
    value = result.stdout.strip("\n")
    if not value:
        return None
    with _lock:
        _secret_cache[(workspace_id, field)] = value
    return value


def delete_workspace_secret(workspace_id: str, field: str) -> bool:
    """删除一把密钥。返回是否真的删掉了一条；缓存同时清掉。

    钥匙串拒绝或锁定时抛错，不当成「没删掉」——那会让界面显示已删、实际还在。
    """
    if field not in WORKSPACE_SECRET_FIELDS:
        raise ValueError(f"不是本单范围内的文字服务密钥：{field}")
    result = _run_security([
        "delete-generic-password",
        "-a", workspace_id,
        "-s", _service_name(workspace_id, field),
        *_keychain_args(),
    ])
    with _lock:
        _secret_cache.pop((workspace_id, field), None)
    if _is_item_not_found(result):
        return False
    _require_store(result, "删除")
    return True


def load_workspace_config(workspace_id: str) -> dict[str, str]:
    """读工作区配置快照。文件不存在就是空配置，不是错误。"""
    path = workspace_config_path(workspace_id)
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return {}
    return {
        key: value
        for key, value in payload.items()
        if key in WORKSPACE_CONFIG_FIELDS and isinstance(value, str) and value
    }


def save_workspace_config(workspace_id: str, fields: dict[str, str]) -> dict[str, str]:
    """写工作区配置快照，按已有快照合并更新。

    只改模型名不能把上次的 provider 和地址一起丢掉，所以这里与既有快照合并。
    密钥字段直接拒绝，不落任何明文。
    """
    rejected = sorted(key for key in fields if key in WORKSPACE_SECRET_FIELDS)
    if rejected:
        raise WorkspaceSecretStoreError(
            f"密钥不能写进配置文件：{', '.join(rejected)}"
        )
    merged = load_workspace_config(workspace_id)
    for key, value in fields.items():
        if key in WORKSPACE_CONFIG_FIELDS and isinstance(value, str):
            cleaned = value.strip()
            if cleaned:
                merged[key] = cleaned
            else:
                merged.pop(key, None)
    path = workspace_config_path(workspace_id)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.chmod(0o600)
    os.replace(temporary, path)
    return merged


@dataclass(frozen=True)
class WorkspaceSettings:
    """一个工作区当前生效的文字服务配置。"""

    workspace_id: str
    settings: Settings

    @property
    def has_workspace_secret(self) -> bool:
        return bool(self.settings.effective_llm_api_key)


def workspace_settings(
    workspace_id: str,
    base: Settings | None = None,
) -> WorkspaceSettings:
    """产品默认打底，叠本工作区快照与本工作区钥匙串里的密钥。

    另一工作区存了什么，这里看不到；也不读别的 id 的配置。全局 dotenv 里的旧文字
    配置和明文密钥不叠进来——它们没有工作区归属，叠进来就等于把一把没有归属的
    旧密钥分给了这个工作区。
    """
    settings = base or Settings.from_env()
    settings = replace(
        settings,
        llm_provider=None,
        llm_api_key=None,
        llm_api_key_provider=None,
        llm_base_url=None,
        llm_model=None,
        deepseek_api_key=None,
    )
    overrides = load_workspace_config(workspace_id)
    if overrides:
        settings = replace(settings, **overrides)
    secrets = {
        field: value
        for field in WORKSPACE_SECRET_FIELDS
        if (value := read_workspace_secret(workspace_id, field))
    }
    if secrets:
        settings = replace(settings, **secrets)
    return WorkspaceSettings(workspace_id=workspace_id, settings=settings)


def workspace_pi_config_dir(workspace_id: str, parent: Path) -> Path:
    """Pi 运行配置与会话按工作区分开，不共用一个 agent 根目录。"""
    root = parent / "workspaces" / _safe_segment(workspace_id)
    (root / "sessions").mkdir(mode=0o700, parents=True, exist_ok=True)
    root.chmod(0o700)
    return root