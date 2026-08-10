from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def runtime_env_file_path() -> Path:
    return _repo_root() / ".env"


def load_dotenv_files() -> None:
    for path in (_repo_root() / ".env", _repo_root() / "apps/api/.env"):
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

    @classmethod
    def from_env(cls) -> Settings:
        load_dotenv_files()
        return cls(
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
        )

    def public_status(self) -> dict[str, Any]:
        configured = bool(self.deepseek_api_key)
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
                "provider": "deepseek",
                "configured": configured,
                "model": self.deepseek_model if configured else None,
                "base_url": self.deepseek_base_url,
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
        }

    def safe_runtime_settings(self) -> dict[str, Any]:
        return {
            "settings": [
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
            ],
            "status": self.public_status(),
        }
