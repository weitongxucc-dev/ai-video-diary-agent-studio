from __future__ import annotations

import os
import stat
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import NamedTemporaryFile

# ── Dotenv path resolution ──────────────────────────────────
# When set, AI_VIDEO_DIARY_DOTENV overrides the default
# repo-root .env lookup.  Electron passes a path under
# app.getPath('userData') so the packaged API and Worker
# share one writable config file outside the signed Resources
# bundle.
_ENV_FILE_OVERRIDE_VAR = "AI_VIDEO_DIARY_DOTENV"

# 设置页写盘的那批键。对它们来说 .env 是唯一真相来源：桌面壳把 Electron 自己的
# process.env 原样透传给 API 子进程（apps/desktop/main.js:283），用户 shell 里
# 恰好有过同名变量时，first-wins 会让那份继承值把 .env 永久遮住——同一进程里更正
# 密钥静默不生效，llm_service_fingerprint 也跟着停在旧值上。列在这里的键改为
# 「.env 覆盖环境变量」；不在列里的键保持 first-wins，12-factor 的
# 「真实环境变量优先于 .env」原样保留。
#
# 与 app.runtime_settings.FIELD_TO_ENV 一一对应（外加它单独派生的两个键）；
# tests/test_env_shadow.py 有一条断言守着这份名单，漏登记会当场失败。
SETTINGS_OWNED_ENV_KEYS = frozenset(
    {
        "ASR_PROVIDER",
        "DATABASE_PATH",
        "DEEPSEEK_API_KEY",
        "DEEPSEEK_MODEL",
        "DRAFT_BACKGROUND_MUSIC_PATH",
        "DRAFT_OUTPUT_PATH",
        "HERMES_RADAR_PATH",
        "JIANYING_DRAFT_ROOT_PATH",
        "LLM_API_KEY",
        "LLM_API_KEY_PROVIDER",
        "LLM_BASE_URL",
        "LLM_MODEL",
        "LLM_PROVIDER",
        "LOCAL_WHISPER_BACKEND",
        "LOCAL_WHISPER_CLI_PATH",
        "LOCAL_WHISPER_LANGUAGE",
        "LOCAL_WHISPER_MODEL_PATH",
        "LOCAL_WHISPER_PYTHON_PATH",
        "MODEL_ROOT_PATH",
        "NATIVE_OCR_WORKER_PATH",
        "OBSIDIAN_VAULT_PATH",
        "PRODUCT_HUNT_TOKEN",
        "SUPABASE_JWT_AUDIENCE",
        "SUPABASE_PUBLISHABLE_KEY",
        "SUPABASE_URL",
        "TEMPORARY_ROOT_PATH",
        "TENCENTCLOUD_ASR_ENGINE_MODEL_TYPE",
        "TENCENTCLOUD_ASR_REGION",
        "TENCENTCLOUD_SECRET_ID",
        "TENCENTCLOUD_SECRET_KEY",
        "TOPIC_SOURCE_URLS",
        "UNDERSTANDING_RUNTIME_PYTHON_PATH",
        "VIDEO_INBOX_PATH",
        "VIDEO_OUTPUT_PATH",
    }
)


def _resolve_dotenv_path() -> Path:
    """Return the primary .env path, honouring the override env var."""
    override = os.getenv(_ENV_FILE_OVERRIDE_VAR)
    if override:
        return Path(override)
    return _repo_root() / ".env"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def runtime_env_file_path() -> Path:
    return _resolve_dotenv_path()


def load_dotenv_files() -> None:
    for path in (_resolve_dotenv_path(), _repo_root() / "apps/api/.env"):
        if not path.exists():
            continue
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if not key:
                continue
            file_wins = key in SETTINGS_OWNED_ENV_KEYS and bool(value)
            if key in os.environ and not file_wins:
                continue
            os.environ[key] = value


@dataclass
class PreparedDotenvWrite:
    path: Path
    original: tuple | None = field(repr=False)
    temporary: Path
    staged: tuple = field(repr=False)
    updates: dict[str, str] = field(repr=False)
    environment_before: dict[str, str | None] = field(repr=False)
    attempted: bool = False


def _dotenv_file_state(path: Path) -> tuple | None:
    try:
        before = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(before.st_mode):
        raise RuntimeError("dotenv target is not a regular file")
    payload = path.read_bytes()
    after = path.lstat()
    def identity(value):
        return (value.st_dev, value.st_ino, stat.S_IMODE(value.st_mode), value.st_mtime_ns, value.st_size)
    if identity(before) != identity(after):
        raise RuntimeError("dotenv target changed while reading")
    return (*identity(after), payload)


def _stage_dotenv_bytes(path: Path, payload: bytes, mode: int = 0o600) -> tuple[Path, tuple]:
    temporary_path = None
    identity = None
    try:
        with NamedTemporaryFile("wb", dir=path.parent, prefix=f"{path.name}.", suffix=".tmp", delete=False) as stream:
            temporary_path = Path(stream.name)
            created = os.fstat(stream.fileno())
            identity = (created.st_dev, created.st_ino)
            stream.write(payload)
        temporary_path.chmod(mode)
        return temporary_path, _dotenv_file_state(temporary_path)
    except Exception as original:
        if temporary_path is not None:
            try:
                current = temporary_path.lstat()
                if (current.st_dev, current.st_ino) != identity:
                    raise RuntimeError("dotenv temporary ownership changed; cleanup refused")
                temporary_path.unlink()
            except FileNotFoundError:
                pass
            except Exception as cleanup:
                raise ExceptionGroup("dotenv staging and cleanup failed", [original, cleanup]) from original
        raise


def prepare_dotenv_values(path: Path, updates: dict[str, str]) -> PreparedDotenvWrite:
    original = _dotenv_file_state(path)
    existing_lines = original[-1].decode("utf-8").splitlines() if original is not None else []
    consumed: set[str] = set()
    lines: list[str] = []
    for raw_line in existing_lines:
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#") or "=" not in raw_line:
            lines.append(raw_line)
            continue
        key = raw_line.split("=", 1)[0].strip()
        if key in updates:
            lines.append(f"{key}={updates[key]}")
            consumed.add(key)
        else:
            lines.append(raw_line)
    for key, value in updates.items():
        if key not in consumed:
            lines.append(f"{key}={value}")
    payload = "\n".join(lines).rstrip() + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary, staged = _stage_dotenv_bytes(path, payload.encode("utf-8"))
    return PreparedDotenvWrite(path, original, temporary, staged, dict(updates),
        {key: os.environ.get(key) for key in updates})


def commit_prepared_dotenv_values(prepared: PreparedDotenvWrite) -> None:
    if _dotenv_file_state(prepared.path) != prepared.original:
        raise RuntimeError("dotenv target changed before save")
    if any(os.environ.get(key) != value for key, value in prepared.environment_before.items()):
        raise RuntimeError("device environment changed before save")
    prepared.attempted = True
    os.replace(prepared.temporary, prepared.path)
    if _dotenv_file_state(prepared.path) != prepared.staged:
        raise RuntimeError("dotenv replacement could not be confirmed")
    for key, value in prepared.updates.items():
        os.environ[key] = value


def restore_prepared_dotenv_values(prepared: PreparedDotenvWrite) -> None:
    if not prepared.attempted:
        return
    failures = []
    try:
        current = _dotenv_file_state(prepared.path)
        if current != prepared.original:
            if current != prepared.staged:
                raise RuntimeError("dotenv changed outside this save; restoration refused")
            if prepared.original is None:
                prepared.path.unlink()
                if _dotenv_file_state(prepared.path) is not None:
                    raise RuntimeError("dotenv removal could not be confirmed")
            else:
                temporary, staged = _stage_dotenv_bytes(prepared.path, prepared.original[-1], prepared.original[2])
                try:
                    if _dotenv_file_state(prepared.path) != current:
                        raise RuntimeError("dotenv changed before restoration")
                    os.replace(temporary, prepared.path)
                    if _dotenv_file_state(prepared.path) != staged:
                        raise RuntimeError("dotenv restoration could not be confirmed")
                except Exception as original:
                    try:
                        if _dotenv_file_state(temporary) == staged:
                            temporary.unlink()
                    except Exception as cleanup:
                        raise ExceptionGroup("dotenv restoration and cleanup failed", [original, cleanup]) from original
                    raise
    except Exception as exc:
        failures.append(exc)
    for key, previous in prepared.environment_before.items():
        try:
            current = os.environ.get(key)
            if current == previous:
                continue
            if current != prepared.updates[key]:
                raise RuntimeError("device environment changed outside this save; restoration refused")
            if previous is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = previous
        except Exception as exc:
            failures.append(exc)
    if failures:
        raise ExceptionGroup("device settings restoration incomplete", failures)


def discard_prepared_dotenv_values(prepared: PreparedDotenvWrite) -> None:
    current = _dotenv_file_state(prepared.temporary)
    if current is not None:
        if current != prepared.staged:
            raise RuntimeError("dotenv temporary file changed; cleanup refused")
        prepared.temporary.unlink()


def write_dotenv_values(path: Path, updates: dict[str, str]) -> None:
    prepared = prepare_dotenv_values(path, updates)
    try:
        commit_prepared_dotenv_values(prepared)
    except Exception as original:
        try:
            discard_prepared_dotenv_values(prepared)
        except Exception as cleanup:
            raise ExceptionGroup("dotenv write and cleanup failed", [original, cleanup]) from original
        raise
    discard_prepared_dotenv_values(prepared)
