from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

# ── Dotenv path resolution ──────────────────────────────────
# When set, AI_VIDEO_DIARY_DOTENV overrides the default
# repo-root .env lookup.  Electron passes a path under
# app.getPath('userData') so the packaged API and Worker
# share one writable config file outside the signed Resources
# bundle.
_ENV_FILE_OVERRIDE_VAR = "AI_VIDEO_DIARY_DOTENV"


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


def configure_process_temporary_directory(settings: Settings) -> Path:
    """Route Python and child-process scratch files to the configured data disk."""
    root = settings.effective_temporary_root_path.resolve()
    if root.parent.is_dir():
        root.mkdir(parents=False, exist_ok=True)
        os.environ["TMPDIR"] = str(root)
        tempfile.tempdir = str(root)
        return root

    # An explicitly configured data disk must fail closed when it disappears.
    # Unconfigured development and CI environments may keep their OS temp root.
    strict_external_path = (
        settings.temporary_root_path is not None or settings.model_root_path_configured
    )
    if strict_external_path:
        os.environ["TMPDIR"] = str(root)
        tempfile.tempdir = str(root)
        return root
    return Path(tempfile.gettempdir())


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
            if key and key not in os.environ:
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


def _default_obsidian_vault_path() -> str | None:
    return None


def _default_video_inbox_path() -> str:
    return str(Path.home() / "Movies" / "AI视频日记" / "01_待处理视频")


def _default_video_output_path() -> str:
    return str(Path.home() / "Movies" / "AI视频日记" / "06_待发布成片")


def _default_draft_output_path() -> str:
    return str(Path.home() / "Movies" / "AI视频日记" / "05_剪映草稿")


def _default_jianying_draft_root_path() -> str:
    return str(
        Path.home()
        / "Movies"
        / "JianyingPro"
        / "User Data"
        / "Projects"
        / "com.lveditor.draft"
    )


def _default_database_path() -> str:
    return str(Path.home() / "Movies" / "AI视频日记" / "agent.sqlite3")


def _default_local_whisper_python_path() -> str | None:
    return None


def _default_local_whisper_model_path() -> str | None:
    return None


def _default_model_root_path() -> str:
    dotenv_override = os.getenv(_ENV_FILE_OVERRIDE_VAR)
    if dotenv_override:
        return str(Path(dotenv_override).expanduser().parent / "models")
    return str(Path.home() / "Movies" / "AI视频日记" / "models")


def _default_model_runtime_python_path(model_root_path: str | None = None) -> str:
    root = Path(model_root_path or _default_model_root_path()).expanduser()
    return str(root.parent / "model-runtime" / "bin" / "python")


def _default_visual_model_worker_path() -> str:
    return str(Path(__file__).resolve().parents[1] / "model_workers" / "chinese_clip_worker.py")


def _default_voice_model_worker_path() -> str:
    return str(Path(__file__).resolve().parents[1] / "model_workers" / "qwen3_tts_worker.py")


def _default_hermes_radar_path() -> str:
    return str(Path.home() / "Desktop" / "product-hunt-raw" / "daily-reports")


def _topic_source_urls_from_env() -> tuple[str, ...]:
    raw = os.getenv("TOPIC_SOURCE_URLS")
    if raw is None:
        return ("https://watcha.cn/",)
    return tuple(url.strip() for url in raw.split(",") if url.strip())


def _markdown_count(path: str | None) -> int:
    if not path:
        return 0
    vault = Path(path).expanduser()
    if not vault.exists() or not vault.is_dir():
        return 0
    return len(
        [
            note
            for note in vault.rglob("*.md")
            if not any(part.startswith(".") for part in note.relative_to(vault).parts)
        ]
    )


def _pending_video_count(path: str | None) -> int:
    if not path:
        return 0
    inbox = Path(path).expanduser()
    if not inbox.exists() or not inbox.is_dir():
        return 0
    supported_extensions = {".mp4", ".mov", ".m4v"}
    return len(
        [
            item
            for item in inbox.iterdir()
            if item.is_file() and item.suffix.lower() in supported_extensions
        ]
    )


def _hermes_radar_status(path: str | None) -> dict[str, Any]:
    from app.hermes_radar import HermesRadarSourceClient

    status = HermesRadarSourceClient(path).status()
    return {
        "configured": status.configured,
        "exists": status.exists,
        "path": status.path,
        "latest_report": status.latest_report,
        "total_count": status.total_count,
        "product_hunt_count": status.product_hunt_count,
        "watcha_count": status.watcha_count,
    }


@dataclass(frozen=True)
class Settings:
    llm_provider: str | None = None
    llm_api_key: str | None = None
    llm_api_key_provider: str | None = None
    llm_base_url: str | None = None
    llm_model: str | None = None
    deepseek_api_key: str | None = None
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-v4-flash"
    obsidian_vault_path: str | None = None
    product_hunt_token: str | None = None
    product_hunt_api_url: str = "https://api.producthunt.com/v2/api/graphql"
    topic_source_urls: tuple[str, ...] = ("https://watcha.cn/",)
    hermes_radar_path: str | None = _default_hermes_radar_path()
    video_inbox_path: str = _default_video_inbox_path()
    video_output_path: str = _default_video_output_path()
    draft_output_path: str = _default_draft_output_path()
    jianying_draft_root_path: str = _default_jianying_draft_root_path()
    database_path: str = _default_database_path()
    tencentcloud_secret_id: str | None = None
    tencentcloud_secret_key: str | None = None
    tencentcloud_asr_region: str = "ap-shanghai"
    tencentcloud_asr_engine_model_type: str = "16k_zh_en"
    tencentcloud_asr_max_wait_seconds: int = 120
    tencentcloud_asr_poll_interval_seconds: float = 2.0
    tencentcloud_asr_max_local_audio_bytes: int = 5_000_000
    local_whisper_python_path: str | None = None
    local_whisper_model_path: str | None = None
    local_whisper_language: str = "zh"
    model_root_path: str = _default_model_root_path()
    model_root_path_configured: bool = False
    temporary_root_path: str | None = None
    model_runtime_python_path: str = _default_model_runtime_python_path()
    visual_model_worker_path: str = _default_visual_model_worker_path()
    voice_model_worker_path: str = _default_voice_model_worker_path()
    draft_background_music_path: str | None = None
    supabase_url: str | None = None
    supabase_publishable_key: str | None = None
    supabase_secret_key: str | None = None
    supabase_jwt_audience: str = "authenticated"
    supabase_jwt_issuer: str | None = None

    @property
    def effective_llm_provider(self) -> str:
        return (self.llm_provider or "deepseek").strip().lower()

    @property
    def effective_llm_api_key(self) -> str | None:
        if self.llm_api_key:
            owner = (self.llm_api_key_provider or "").strip().lower()
            if not owner or owner == self.effective_llm_provider:
                return self.llm_api_key
        if self.effective_llm_provider == "deepseek":
            return self.deepseek_api_key
        return None

    @property
    def effective_llm_base_url(self) -> str:
        return (self.llm_base_url or self.deepseek_base_url).rstrip("/")

    @property
    def effective_llm_model(self) -> str:
        return self.llm_model or self.deepseek_model

    @property
    def llm_configured(self) -> bool:
        provider = self.effective_llm_provider
        has_connection = bool(self.effective_llm_base_url and self.effective_llm_model)
        return has_connection and (
            provider in {"ollama", "custom"} or bool(self.effective_llm_api_key)
        )

    @property
    def effective_temporary_root_path(self) -> Path:
        configured = self.temporary_root_path
        if configured:
            return Path(configured).expanduser()
        return Path(self.model_root_path).expanduser().parent / "temporary"

    @property
    def auth_enabled(self) -> bool:
        """True when Supabase is configured and product-data auth is enforced."""
        return bool(self.supabase_url and self.supabase_secret_key)

    @property
    def supabase_jwt_issuer_resolved(self) -> str | None:
        if self.supabase_jwt_issuer:
            return self.supabase_jwt_issuer
        if self.supabase_url:
            return f"{self.supabase_url.rstrip('/')}/auth/v1"
        return None

    @classmethod
    def from_env(cls) -> Settings:
        load_dotenv_files()
        model_root_path = os.getenv("MODEL_ROOT_PATH") or _default_model_root_path()
        return cls(
            llm_provider=os.getenv("LLM_PROVIDER"),
            llm_api_key=os.getenv("LLM_API_KEY"),
            llm_api_key_provider=os.getenv("LLM_API_KEY_PROVIDER"),
            llm_base_url=os.getenv("LLM_BASE_URL"),
            llm_model=os.getenv("LLM_MODEL"),
            deepseek_api_key=os.getenv("DEEPSEEK_API_KEY"),
            deepseek_base_url=os.getenv("DEEPSEEK_BASE_URL", cls.deepseek_base_url),
            deepseek_model=os.getenv("DEEPSEEK_MODEL", cls.deepseek_model),
            obsidian_vault_path=os.getenv("OBSIDIAN_VAULT_PATH")
            or _default_obsidian_vault_path(),
            product_hunt_token=os.getenv("PRODUCT_HUNT_TOKEN"),
            product_hunt_api_url=os.getenv(
                "PRODUCT_HUNT_API_URL",
                cls.product_hunt_api_url,
            ),
            topic_source_urls=_topic_source_urls_from_env(),
            hermes_radar_path=os.getenv("HERMES_RADAR_PATH")
            or _default_hermes_radar_path(),
            video_inbox_path=os.getenv("VIDEO_INBOX_PATH")
            or _default_video_inbox_path(),
            video_output_path=os.getenv("VIDEO_OUTPUT_PATH")
            or _default_video_output_path(),
            draft_output_path=os.getenv("DRAFT_OUTPUT_PATH")
            or _default_draft_output_path(),
            jianying_draft_root_path=os.getenv("JIANYING_DRAFT_ROOT_PATH")
            or _default_jianying_draft_root_path(),
            database_path=os.getenv("DATABASE_PATH") or _default_database_path(),
            tencentcloud_secret_id=os.getenv("TENCENTCLOUD_SECRET_ID"),
            tencentcloud_secret_key=os.getenv("TENCENTCLOUD_SECRET_KEY"),
            tencentcloud_asr_region=os.getenv(
                "TENCENTCLOUD_ASR_REGION",
                cls.tencentcloud_asr_region,
            ),
            tencentcloud_asr_engine_model_type=os.getenv(
                "TENCENTCLOUD_ASR_ENGINE_MODEL_TYPE",
                cls.tencentcloud_asr_engine_model_type,
            ),
            tencentcloud_asr_max_wait_seconds=int(
                os.getenv(
                    "TENCENTCLOUD_ASR_MAX_WAIT_SECONDS",
                    str(cls.tencentcloud_asr_max_wait_seconds),
                )
            ),
            tencentcloud_asr_poll_interval_seconds=float(
                os.getenv(
                    "TENCENTCLOUD_ASR_POLL_INTERVAL_SECONDS",
                    str(cls.tencentcloud_asr_poll_interval_seconds),
                )
            ),
            tencentcloud_asr_max_local_audio_bytes=int(
                os.getenv(
                    "TENCENTCLOUD_ASR_MAX_LOCAL_AUDIO_BYTES",
                    str(cls.tencentcloud_asr_max_local_audio_bytes),
                )
            ),
            local_whisper_python_path=os.getenv("LOCAL_WHISPER_PYTHON_PATH")
            or _default_local_whisper_python_path(),
            local_whisper_model_path=os.getenv("LOCAL_WHISPER_MODEL_PATH")
            or _default_local_whisper_model_path(),
            local_whisper_language=os.getenv(
                "LOCAL_WHISPER_LANGUAGE",
                cls.local_whisper_language,
            ),
            model_root_path=model_root_path,
            model_root_path_configured=bool(os.getenv("MODEL_ROOT_PATH")),
            temporary_root_path=os.getenv("TEMPORARY_ROOT_PATH"),
            model_runtime_python_path=os.getenv("MODEL_RUNTIME_PYTHON_PATH")
            or _default_model_runtime_python_path(model_root_path),
            visual_model_worker_path=os.getenv("VISUAL_MODEL_WORKER_PATH")
            or _default_visual_model_worker_path(),
            voice_model_worker_path=os.getenv("VOICE_MODEL_WORKER_PATH")
            or _default_voice_model_worker_path(),
            draft_background_music_path=os.getenv("DRAFT_BACKGROUND_MUSIC_PATH"),
            supabase_url=os.getenv("SUPABASE_URL"),
            supabase_publishable_key=os.getenv("SUPABASE_PUBLISHABLE_KEY"),
            supabase_secret_key=os.getenv("SUPABASE_SECRET_KEY"),
            supabase_jwt_audience=os.getenv(
                "SUPABASE_JWT_AUDIENCE",
                cls.supabase_jwt_audience,
            ),
            supabase_jwt_issuer=os.getenv("SUPABASE_JWT_ISSUER"),
        )

    def public_status(self) -> dict[str, Any]:
        cloud_configured = self.llm_configured
        obsidian_path = self.obsidian_vault_path
        obsidian_exists = bool(
            obsidian_path
            and Path(obsidian_path).expanduser().exists()
            and Path(obsidian_path).expanduser().is_dir()
        )
        video_inbox = Path(self.video_inbox_path).expanduser()
        video_output = Path(self.video_output_path).expanduser()
        database_path = Path(self.database_path).expanduser()
        return {
            "llm": {
                "provider": self.effective_llm_provider if cloud_configured else "local-extractive",
                "configured": cloud_configured,
                "model": self.effective_llm_model if cloud_configured else "source-grounded-v1",
                "base_url": self.effective_llm_base_url if cloud_configured else "local",
            },
            "obsidian": {
                "configured": bool(obsidian_path),
                "exists": obsidian_exists,
                "path": obsidian_path,
                "markdown_count": _markdown_count(obsidian_path),
            },
            "product_hunt": {
                "configured": bool(self.product_hunt_token),
                "api_url": self.product_hunt_api_url,
            },
            "external_sources": {
                "configured": len(self.topic_source_urls) > 0,
                "urls": list(self.topic_source_urls),
            },
            "hermes_radar": _hermes_radar_status(self.hermes_radar_path),
            "video": {
                "inbox_path": str(video_inbox),
                "output_path": str(video_output),
                "inbox_exists": video_inbox.exists() and video_inbox.is_dir(),
                "output_exists": video_output.exists() and video_output.is_dir(),
                "pending_count": _pending_video_count(str(video_inbox)),
                "ffmpeg_available": shutil.which("ffmpeg") is not None,
            },
            "storage": {
                "database_path": str(database_path),
                "database_exists": database_path.exists(),
            },
            "asr": {
                "provider": "tencentcloud",
                "configured": bool(
                    self.tencentcloud_secret_id and self.tencentcloud_secret_key
                ),
                "region": self.tencentcloud_asr_region,
                "engine_model_type": self.tencentcloud_asr_engine_model_type,
                "max_local_audio_mb": round(
                    self.tencentcloud_asr_max_local_audio_bytes / 1_000_000,
                    2,
                ),
                "local_whisper_configured": bool(
                    self.local_whisper_python_path
                    and Path(self.local_whisper_python_path).expanduser().is_file()
                    and self.local_whisper_model_path
                    and Path(self.local_whisper_model_path).expanduser().is_file()
                ),
                "local_whisper_python_path": self.local_whisper_python_path,
                "local_whisper_model_path": self.local_whisper_model_path,
            },
            "auth": {
                "enabled": self.auth_enabled,
                "supabase_configured": bool(self.supabase_url),
            },
        }

    def safe_runtime_settings(self) -> dict[str, Any]:
        return {
            "settings": [
                {
                    "name": "LLM_PROVIDER",
                    "configured": True,
                    "value": self.effective_llm_provider,
                    "secret": False,
                },
                {
                    "name": "LLM_API_KEY",
                    "configured": bool(self.effective_llm_api_key),
                    "value": "已配置" if self.effective_llm_api_key else "未配置",
                    "secret": True,
                },
                {
                    "name": "LLM_BASE_URL",
                    "configured": bool(self.effective_llm_base_url),
                    "value": self.effective_llm_base_url,
                    "secret": False,
                },
                {
                    "name": "LLM_MODEL",
                    "configured": bool(self.effective_llm_model),
                    "value": self.effective_llm_model,
                    "secret": False,
                },
                {
                    "name": "DEEPSEEK_API_KEY",
                    "configured": bool(self.deepseek_api_key),
                    "value": "已配置" if self.deepseek_api_key else "未配置",
                    "secret": True,
                },
                {
                    "name": "DEEPSEEK_MODEL",
                    "configured": bool(self.deepseek_model),
                    "value": self.deepseek_model,
                    "secret": False,
                },
                {
                    "name": "OBSIDIAN_VAULT_PATH",
                    "configured": bool(self.obsidian_vault_path),
                    "value": self.obsidian_vault_path,
                    "secret": False,
                },
                {
                    "name": "HERMES_RADAR_PATH",
                    "configured": bool(self.hermes_radar_path),
                    "value": self.hermes_radar_path,
                    "secret": False,
                },
                {
                    "name": "VIDEO_INBOX_PATH",
                    "configured": bool(self.video_inbox_path),
                    "value": self.video_inbox_path,
                    "secret": False,
                },
                {
                    "name": "VIDEO_OUTPUT_PATH",
                    "configured": bool(self.video_output_path),
                    "value": self.video_output_path,
                    "secret": False,
                },
                {
                    "name": "DRAFT_OUTPUT_PATH",
                    "configured": bool(self.draft_output_path),
                    "value": self.draft_output_path,
                    "secret": False,
                },
                {
                    "name": "JIANYING_DRAFT_ROOT_PATH",
                    "configured": bool(self.jianying_draft_root_path),
                    "value": self.jianying_draft_root_path,
                    "secret": False,
                },
                {
                    "name": "DATABASE_PATH",
                    "configured": bool(self.database_path),
                    "value": self.database_path,
                    "secret": False,
                },
                {
                    "name": "MODEL_ROOT_PATH",
                    "configured": bool(self.model_root_path),
                    "value": self.model_root_path,
                    "secret": False,
                },
                {
                    "name": "TEMPORARY_ROOT_PATH",
                    "configured": bool(self.effective_temporary_root_path),
                    "value": str(self.effective_temporary_root_path),
                    "secret": False,
                },
                {
                    "name": "PRODUCT_HUNT_TOKEN",
                    "configured": bool(self.product_hunt_token),
                    "value": "已配置" if self.product_hunt_token else "未配置",
                    "secret": True,
                },
                {
                    "name": "TENCENTCLOUD_SECRET_ID",
                    "configured": bool(self.tencentcloud_secret_id),
                    "value": "已配置" if self.tencentcloud_secret_id else "未配置",
                    "secret": True,
                },
                {
                    "name": "TENCENTCLOUD_SECRET_KEY",
                    "configured": bool(self.tencentcloud_secret_key),
                    "value": "已配置" if self.tencentcloud_secret_key else "未配置",
                    "secret": True,
                },
                {
                    "name": "TENCENTCLOUD_ASR_REGION",
                    "configured": bool(self.tencentcloud_asr_region),
                    "value": self.tencentcloud_asr_region,
                    "secret": False,
                },
                {
                    "name": "TENCENTCLOUD_ASR_ENGINE_MODEL_TYPE",
                    "configured": bool(self.tencentcloud_asr_engine_model_type),
                    "value": self.tencentcloud_asr_engine_model_type,
                    "secret": False,
                },
                {
                    "name": "LOCAL_WHISPER_PYTHON_PATH",
                    "configured": bool(self.local_whisper_python_path),
                    "value": self.local_whisper_python_path,
                    "secret": False,
                },
                {
                    "name": "LOCAL_WHISPER_MODEL_PATH",
                    "configured": bool(self.local_whisper_model_path),
                    "value": self.local_whisper_model_path,
                    "secret": False,
                },
                {
                    "name": "LOCAL_WHISPER_LANGUAGE",
                    "configured": bool(self.local_whisper_language),
                    "value": self.local_whisper_language,
                    "secret": False,
                },
                {
                    "name": "DRAFT_BACKGROUND_MUSIC_PATH",
                    "configured": bool(self.draft_background_music_path),
                    "value": self.draft_background_music_path,
                    "secret": False,
                },
                {
                    "name": "SUPABASE_URL",
                    "configured": bool(self.supabase_url),
                    "value": self.supabase_url,
                    "secret": False,
                },
                {
                    "name": "SUPABASE_PUBLISHABLE_KEY",
                    "configured": bool(self.supabase_publishable_key),
                    "value": "已配置" if self.supabase_publishable_key else "未配置",
                    "secret": True,
                },
                {
                    "name": "SUPABASE_SECRET_KEY",
                    "configured": bool(self.supabase_secret_key),
                    "value": "已配置" if self.supabase_secret_key else "未配置",
                    "secret": True,
                },
                {
                    "name": "SUPABASE_JWT_AUDIENCE",
                    "configured": bool(self.supabase_jwt_audience),
                    "value": self.supabase_jwt_audience,
                    "secret": False,
                },
            ],
            "status": self.public_status(),
        }
