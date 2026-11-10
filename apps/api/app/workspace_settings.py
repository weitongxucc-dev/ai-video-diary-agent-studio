"""按工作区隔离的文字服务配置与密钥存储。

两块职责，都只服务当前这一个消费者——设置页写配置、对话助手和写稿读配置：

1. 工作区配置快照：非密钥字段（provider / base_url / model 等）存到每个工作区
   自己的 JSON 文件，不再共用一份全局 .env。
2. 文字服务密钥：LLM 与旧 DeepSeek 两把密钥放进系统安全存储（macOS 钥匙串、Windows 凭据管理器），
   条目名按工作区分开。没有明文兜底：钥匙串写失败就是失败，绝不退回 dotenv。

旧全局 .env 里已有的明文密钥不在这单范围内：它们没有工作区归属，不自动分配、
不复制、不清除。真实钥匙串与登录钥匙串只在测试里用本单自建的临时钥匙串。
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import threading
from dataclasses import dataclass, field, replace
from pathlib import Path
from tempfile import NamedTemporaryFile

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
_WINDOWS_CREDENTIALS = sys.platform == "win32"

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
    if _WINDOWS_CREDENTIALS:
        import pywintypes
        import win32cred

        try:
            win32cred.CredWrite({
                "Type": win32cred.CRED_TYPE_GENERIC,
                "TargetName": _service_name(workspace_id, field),
                "UserName": workspace_id,
                "CredentialBlob": value,
                "Persist": win32cred.CRED_PERSIST_LOCAL_MACHINE,
            }, 0)
        except pywintypes.error as exc:
            raise WorkspaceSecretStoreError("密钥保存失败：Windows 凭据管理器拒绝了这次操作。") from exc
    else:
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
    value = read_workspace_secret_fresh(workspace_id, field)
    if not value:
        return None
    with _lock:
        _secret_cache[(workspace_id, field)] = value
    return value


def read_workspace_secret_fresh(workspace_id: str, field: str, *, recoverable: bool = False) -> str | None:
    """Read the owned backend without using or populating the cache."""
    if field not in WORKSPACE_SECRET_FIELDS:
        raise ValueError(f"不是本单范围内的文字服务密钥：{field}")
    if _WINDOWS_CREDENTIALS:
        import pywintypes
        import win32cred

        try:
            credential = win32cred.CredRead(_service_name(workspace_id, field), win32cred.CRED_TYPE_GENERIC)
        except pywintypes.error as exc:
            if exc.winerror == 1168:  # ERROR_NOT_FOUND, not a store-access failure.
                return None
            raise WorkspaceSecretStoreError("密钥读取失败：Windows 凭据管理器拒绝了这次操作。") from exc
        try:
            # pywin32 writes Unicode blobs as UTF-16LE and returns raw bytes.
            value = credential["CredentialBlob"].decode("utf-16-le")
        except UnicodeError as exc:
            raise WorkspaceSecretStoreError("系统凭据编码无效，无法读取原密钥。") from exc
        if recoverable and not value.strip():
            raise WorkspaceSecretStoreError("无法确认可恢复的原密钥，配置未保存。")
        return value
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
    if recoverable and (not value.strip() or result.stdout not in (value, value + "\n")):
        raise WorkspaceSecretStoreError("无法确认可恢复的原密钥，配置未保存。")
    return value


def delete_workspace_secret(workspace_id: str, field: str) -> bool:
    """删除一把密钥。返回是否真的删掉了一条；缓存同时清掉。

    钥匙串拒绝或锁定时抛错，不当成「没删掉」——那会让界面显示已删、实际还在。
    """
    if field not in WORKSPACE_SECRET_FIELDS:
        raise ValueError(f"不是本单范围内的文字服务密钥：{field}")
    if _WINDOWS_CREDENTIALS:
        import pywintypes
        import win32cred

        try:
            win32cred.CredDelete(_service_name(workspace_id, field), win32cred.CRED_TYPE_GENERIC)
        except pywintypes.error as exc:
            if exc.winerror != 1168:
                raise WorkspaceSecretStoreError("密钥删除失败：Windows 凭据管理器拒绝了这次操作。") from exc
            deleted = False
        else:
            deleted = True
        with _lock:
            _secret_cache.pop((workspace_id, field), None)
        return deleted
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


def _workspace_config_fields(payload: object) -> dict[str, str]:
    if not isinstance(payload, dict):
        return {}
    return {
        key: value
        for key, value in payload.items()
        if key in WORKSPACE_CONFIG_FIELDS and isinstance(value, str) and value
    }


def load_workspace_config(workspace_id: str) -> dict[str, str]:
    """读工作区配置快照。文件不存在就是空配置，不是错误。"""
    path = workspace_config_path(workspace_id)
    if not path.exists():
        return {}
    return _workspace_config_fields(json.loads(path.read_text(encoding="utf-8")))


@dataclass
class PreparedWorkspaceConfig:
    path: Path
    original: tuple | None = field(repr=False)
    temporary: Path
    staged: tuple = field(repr=False)
    merged: dict[str, str]
    attempted: bool = False


def _workspace_config_file_state(path: Path) -> tuple | None:
    try:
        before = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(before.st_mode):
        raise RuntimeError("workspace config target is not a regular file")
    payload = path.read_bytes()
    after = path.lstat()
    def identity(value):
        return (value.st_dev, value.st_ino, stat.S_IMODE(value.st_mode), value.st_mtime_ns, value.st_size)
    if identity(before) != identity(after):
        raise RuntimeError("workspace config changed while reading")
    return (*identity(after), payload)


def _stage_workspace_config(path: Path, payload: bytes, mode: int = 0o600) -> tuple[Path, tuple]:
    temporary_path = None
    identity = None
    try:
        with NamedTemporaryFile("wb", dir=path.parent, prefix=f"{path.name}.", suffix=".tmp", delete=False) as stream:
            temporary_path = Path(stream.name)
            created = os.fstat(stream.fileno())
            identity = (created.st_dev, created.st_ino)
            stream.write(payload)
        temporary_path.chmod(mode)
        return temporary_path, _workspace_config_file_state(temporary_path)
    except Exception as original:
        if temporary_path is not None:
            try:
                current = temporary_path.lstat()
                if (current.st_dev, current.st_ino) != identity:
                    raise RuntimeError("workspace temporary ownership changed; cleanup refused")
                temporary_path.unlink()
            except FileNotFoundError:
                pass
            except Exception as cleanup:
                raise ExceptionGroup("workspace staging and cleanup failed", [original, cleanup]) from original
        raise


def prepare_workspace_config(workspace_id: str, fields: dict[str, str]) -> PreparedWorkspaceConfig:
    """Prepare the existing filtered merge, retaining the exact owned original."""

    rejected = sorted(key for key in fields if key in WORKSPACE_SECRET_FIELDS)
    if rejected:
        raise WorkspaceSecretStoreError(
            f"密钥不能写进配置文件：{', '.join(rejected)}"
        )
    path = workspace_config_path(workspace_id)
    original = _workspace_config_file_state(path)
    merged = _workspace_config_fields(json.loads(original[-1].decode("utf-8"))) if original is not None else {}
    for key, value in fields.items():
        if key in WORKSPACE_CONFIG_FIELDS and isinstance(value, str):
            cleaned = value.strip()
            if cleaned:
                merged[key] = cleaned
            else:
                merged.pop(key, None)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary, staged = _stage_workspace_config(path, json.dumps(merged, ensure_ascii=False, indent=2).encode("utf-8"))
    return PreparedWorkspaceConfig(path, original, temporary, staged, merged)


def commit_prepared_workspace_config(prepared: PreparedWorkspaceConfig) -> None:
    if _workspace_config_file_state(prepared.path) != prepared.original:
        raise RuntimeError("workspace config changed before save")
    prepared.attempted = True
    os.replace(prepared.temporary, prepared.path)
    if _workspace_config_file_state(prepared.path) != prepared.staged:
        raise RuntimeError("workspace config replacement could not be confirmed")


def restore_prepared_workspace_config(prepared: PreparedWorkspaceConfig) -> None:
    if not prepared.attempted:
        return
    current = _workspace_config_file_state(prepared.path)
    if current == prepared.original:
        return
    if current != prepared.staged:
        raise RuntimeError("workspace config changed outside this save; restoration refused")
    if prepared.original is None:
        prepared.path.unlink()
        if _workspace_config_file_state(prepared.path) is not None:
            raise RuntimeError("workspace config removal could not be confirmed")
        return
    temporary, staged = _stage_workspace_config(prepared.path, prepared.original[-1], prepared.original[2])
    try:
        if _workspace_config_file_state(prepared.path) != current:
            raise RuntimeError("workspace config changed before restoration")
        os.replace(temporary, prepared.path)
        if _workspace_config_file_state(prepared.path) != staged:
            raise RuntimeError("workspace config restoration could not be confirmed")
    except Exception as original:
        try:
            if _workspace_config_file_state(temporary) == staged:
                temporary.unlink()
        except Exception as cleanup:
            raise ExceptionGroup("workspace restoration and cleanup failed", [original, cleanup]) from original
        raise


def discard_prepared_workspace_config(prepared: PreparedWorkspaceConfig) -> None:
    current = _workspace_config_file_state(prepared.temporary)
    if current is not None:
        if current != prepared.staged:
            raise RuntimeError("workspace temporary file changed; cleanup refused")
        prepared.temporary.unlink()


def save_workspace_config(workspace_id: str, fields: dict[str, str]) -> dict[str, str]:
    """Save the single owned config merge; secrets never enter this plan."""
    prepared = prepare_workspace_config(workspace_id, fields)
    try:
        commit_prepared_workspace_config(prepared)
    except Exception as original:
        try:
            discard_prepared_workspace_config(prepared)
        except Exception as cleanup:
            raise ExceptionGroup("workspace write and cleanup failed", [original, cleanup]) from original
        raise
    discard_prepared_workspace_config(prepared)
    return prepared.merged


@dataclass
class PreparedWorkspaceSecrets:
    workspace_id: str
    originals: dict[str, str | None] = field(repr=False)
    cache_before: dict[str, str | None] = field(repr=False)
    updates: dict[str, str | None] = field(repr=False)
    attempted: list[str] = field(default_factory=list)
    confirmed: set[str] = field(default_factory=set)


def prepare_workspace_secret_changes(workspace_id: str, updates: dict[str, str | None]) -> PreparedWorkspaceSecrets:
    for name, value in updates.items():
        if name not in WORKSPACE_SECRET_FIELDS:
            raise ValueError(f"不是本单范围内的文字服务密钥：{name}")
        if value is not None and not value.strip():
            raise WorkspaceSecretStoreError("密钥内容为空，没有保存。")
    with _lock:
        cached = {name: _secret_cache.get((workspace_id, name)) for name in updates}
    originals = {name: read_workspace_secret_fresh(workspace_id, name, recoverable=True) for name in updates}
    return PreparedWorkspaceSecrets(workspace_id, originals, cached, dict(updates))


def commit_prepared_workspace_secrets(prepared: PreparedWorkspaceSecrets) -> None:
    for name, value in prepared.updates.items():
        if read_workspace_secret_fresh(prepared.workspace_id, name) != prepared.originals[name]:
            raise WorkspaceSecretStoreError("密钥在保存前已变化，配置未保存。")
        prepared.attempted.append(name)
        if value is None:
            if delete_workspace_secret(prepared.workspace_id, name):
                prepared.confirmed.add(name)
        else:
            save_workspace_secret(prepared.workspace_id, name, value)
            prepared.confirmed.add(name)
        if read_workspace_secret_fresh(prepared.workspace_id, name) != value:
            raise WorkspaceSecretStoreError("无法确认密钥保存结果。")


def restore_prepared_workspace_secrets(prepared: PreparedWorkspaceSecrets) -> None:
    failures = []
    for name in reversed(prepared.attempted):
        previous = prepared.originals[name]
        try:
            current = read_workspace_secret_fresh(prepared.workspace_id, name)
            if current != previous:
                if name not in prepared.confirmed or current != prepared.updates[name]:
                    raise WorkspaceSecretStoreError("密钥改变不属于已确认的本次写入，恢复被拒绝。")
                if previous is None:
                    delete_workspace_secret(prepared.workspace_id, name)
                else:
                    save_workspace_secret(prepared.workspace_id, name, previous)
                if read_workspace_secret_fresh(prepared.workspace_id, name) != previous:
                    raise WorkspaceSecretStoreError("无法确认密钥恢复结果。")
            with _lock:
                cached = prepared.cache_before[name]
                if cached is None:
                    _secret_cache.pop((prepared.workspace_id, name), None)
                else:
                    _secret_cache[(prepared.workspace_id, name)] = cached
        except Exception as exc:
            with _lock:
                _secret_cache.pop((prepared.workspace_id, name), None)
            failures.append(exc)
    if failures:
        raise ExceptionGroup("workspace secret restoration incomplete", failures)


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
        deepseek_model=Settings.deepseek_model,
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
