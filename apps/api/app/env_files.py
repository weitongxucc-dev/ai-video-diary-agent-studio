from __future__ import annotations

import os
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


def write_dotenv_values(path: Path, updates: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing_lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
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
    temporary_path: Path | None = None
    try:
        with NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f"{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_file.write(payload)
            temporary_path = Path(temporary_file.name)
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()
    for key, value in updates.items():
        os.environ[key] = value
