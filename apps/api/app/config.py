from __future__ import annotations

import hashlib
import os
import tempfile
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from app.env_files import (
    SETTINGS_OWNED_ENV_KEYS as SETTINGS_OWNED_ENV_KEYS,
    _ENV_FILE_OVERRIDE_VAR as _ENV_FILE_OVERRIDE_VAR,
    _repo_root as _repo_root,
    _resolve_dotenv_path as _resolve_dotenv_path,
    load_dotenv_files as load_dotenv_files,
    runtime_env_file_path as runtime_env_file_path,
    write_dotenv_values as write_dotenv_values,
)

from app.local_runtime_status import observe_local_runtime_status, whisper_file_present
from app.service_status_projection import (
    LLM_SERVICE_AVAILABLE as LLM_SERVICE_AVAILABLE,
    LLM_SERVICE_FAILED as LLM_SERVICE_FAILED,
    LLM_SERVICE_MISSING as LLM_SERVICE_MISSING,
    LLM_SERVICE_UNVERIFIED as LLM_SERVICE_UNVERIFIED,
    project_service_status,
    service_state,
)


def structured_completion_options(model_id: str, *, forced_tools: bool = False) -> dict[str, object]:
    """Separate MiniMax reasoning; disable DeepSeek thinking for forced tools."""
    if forced_tools and model_id in {"deepseek-flash", "deepseek-v4-flash", "deepseek-v4-pro"}:
        return {"thinking": {"type": "disabled"}}
    return {"reasoning_split": True} if model_id.startswith("MiniMax-") else {}


def prepare_process_temporary_directory(settings: Settings) -> Path | None:
    """Prepare the selected scratch path without changing process temp state.

    None preserves the unconfigured development fallback to the OS temp root.
    """
    root = settings.effective_temporary_root_path.resolve()
    # On macOS a leftover /Volumes/<name> directory can exist on the internal
    # disk even though the external volume is absent. Existence is not enough.
    if len(root.parts) >= 3 and root.parts[1] == "Volumes":
        volume = Path(*root.parts[:3])
        if not volume.is_mount():
            raise FileNotFoundError(f"外接存储未挂载：{volume}。请连接原数据盘后重新启动应用。")
    if root.parent.is_dir():
        root.mkdir(parents=False, exist_ok=True)
        return root

    # An explicitly configured data disk must fail closed when it disappears.
    # Unconfigured development and CI environments may keep their OS temp root.
    strict_external_path = (
        settings.temporary_root_path is not None or settings.model_root_path_configured
    )
    if strict_external_path:
        return root
    return None


def publish_process_temporary_directory(root: Path | None) -> Path:
    """Publish an already prepared scratch path at the runtime switch boundary."""
    if root is None:
        return Path(tempfile.gettempdir())
    os.environ["TMPDIR"] = str(root)
    if os.name == "nt":
        # Windows tools commonly consult TEMP/TMP instead of TMPDIR.
        os.environ["TEMP"] = str(root)
        os.environ["TMP"] = str(root)
    tempfile.tempdir = str(root)
    return root


def configure_process_temporary_directory(settings: Settings) -> Path:
    """Route Python and child-process scratch files to the configured data disk."""
    return publish_process_temporary_directory(prepare_process_temporary_directory(settings))


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


def _default_model_root_path(environ: Mapping[str, str] | None = None) -> str:
    values = os.environ if environ is None else environ
    dotenv_override = values.get(_ENV_FILE_OVERRIDE_VAR)
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


def _default_avatar_model_worker_path() -> str:
    return str(Path(__file__).resolve().parents[1] / "model_workers" / "musetalk_worker.py")


def _default_hermes_radar_path() -> str:
    return str(Path.home() / "Desktop" / "product-hunt-raw" / "daily-reports")


def _topic_source_urls_from_env(environ: Mapping[str, str] | None = None) -> tuple[str, ...]:
    values = os.environ if environ is None else environ
    raw = values.get("TOPIC_SOURCE_URLS")
    if raw is None:
        return ("https://watcha.cn/",)
    return tuple(url.strip() for url in raw.split(",") if url.strip())





# 进程内记录「本次运行真的连通过/没通过」。不落盘：重启后我们确实不知道，
# 就回到「未验证」，而不是沿用一次陈旧的结论。
_llm_service_verifications: dict[str, dict[str, Any]] = {}
_llm_service_lock = threading.Lock()


def llm_service_fingerprint(settings: Settings, workspace_id: str = "") -> str:
    """Identify the exact credential set an observation belongs to.

    Changing provider / address / model / key must invalidate an earlier
    「可用」; otherwise a fixed key would keep an old green badge. The workspace
    is part of the identity too: one workspace's verification never lights up
    another's service state.
    """
    material = "\n".join(
        [
            workspace_id,
            settings.effective_llm_provider,
            settings.effective_llm_base_url,
            settings.effective_llm_model,
            settings.effective_llm_api_key or "",
        ]
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def record_llm_service_verification(
    settings: Settings, *, ok: bool, cause: str, detail: str = "", repair_action: str = "",
    workspace_id: str = "",
) -> dict[str, Any]:
    """Record one real attempt against the external model service.

    Only call this from a code path that has actually reached the provider.
    A Pi/工具/本机盘故障 must not be recorded as a service failure — the
    service was never asked, and saying otherwise would be the same lie B03
    is fixing.
    """
    record = {
        "ok": bool(ok),
        "cause": cause,
        "detail": detail,
        "repair_action": repair_action,
        "observed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    with _llm_service_lock:
        _llm_service_verifications[llm_service_fingerprint(settings, workspace_id)] = record
    return record


def llm_service_verification(settings: Settings, workspace_id: str = "") -> dict[str, Any] | None:
    with _llm_service_lock:
        record = _llm_service_verifications.get(
            llm_service_fingerprint(settings, workspace_id)
        )
    return dict(record) if record else None


@dataclass(frozen=True)
class Settings:
    device_resource_profile_path: str | None = None
    device_resource_mode: str = "normal"
    device_resource_experiment_id: str | None = None
    llm_provider: str | None = None
    llm_api_key: str | None = None
    llm_api_key_provider: str | None = None
    llm_base_url: str | None = None
    llm_model: str | None = None
    deepseek_api_key: str | None = None
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-flash"
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
    asr_provider: str = "local_whisper"
    tencentcloud_secret_id: str | None = None
    tencentcloud_secret_key: str | None = None
    tencentcloud_asr_region: str = "ap-shanghai"
    tencentcloud_asr_engine_model_type: str = "16k_zh_en"
    tencentcloud_asr_max_wait_seconds: int = 120
    tencentcloud_asr_poll_interval_seconds: float = 2.0
    tencentcloud_asr_max_local_audio_bytes: int = 5_000_000
    local_whisper_backend: str = "openai_whisper"
    local_whisper_cli_path: str | None = None
    local_whisper_python_path: str | None = None
    local_whisper_model_path: str | None = None
    local_whisper_language: str = "auto"
    model_root_path: str = _default_model_root_path()
    model_root_path_configured: bool = False
    temporary_root_path: str | None = None
    model_runtime_python_path: str = _default_model_runtime_python_path()
    visual_model_worker_path: str = _default_visual_model_worker_path()
    voice_model_worker_path: str = _default_voice_model_worker_path()
    understanding_runtime_python_path: str | None = None
    understanding_worker_path: str = str(Path(__file__).resolve().parents[1] / "model_workers" / "qwen3_vl_worker.py")
    native_ocr_worker_path: str | None = None
    native_portrait_worker_path: str | None = None
    avatar_model_runtime_python_path: str | None = None
    avatar_model_worker_path: str = _default_avatar_model_worker_path()
    avatar_model_root_path: str | None = None
    avatar_model_id: str = "musetalk-v1.5-mps"
    latentsync_worker_path: str = str(Path(__file__).resolve().parents[1] / "model_workers" / "latentsync_worker.py")
    latentsync_runtime_python_path: str | None = None
    latentsync_root_path: str | None = None
    latentsync_pose_root_path: str | None = None
    draft_background_music_path: str | None = None
    supabase_url: str | None = None
    supabase_publishable_key: str | None = None
    supabase_jwt_audience: str = "authenticated"
    supabase_jwt_issuer: str | None = None

    def __post_init__(self) -> None:
        if self.asr_provider not in {"local_whisper", "tencentcloud"}:
            raise ValueError("ASR_PROVIDER 必须是 local_whisper 或 tencentcloud。")

        if self.local_whisper_backend not in {"openai_whisper", "whisper_cpp"}:
            raise ValueError("LOCAL_WHISPER_BACKEND 必须是 openai_whisper 或 whisper_cpp。")

    @property
    def local_whisper_runtime_path(self) -> str | None:
        return self.local_whisper_cli_path if self.local_whisper_backend == "whisper_cpp" else self.local_whisper_python_path

    @property
    def local_whisper_configured(self) -> bool:
        return (
            whisper_file_present(self.local_whisper_runtime_path)
            and whisper_file_present(self.local_whisper_model_path)
        )

    @property
    def asr_configured(self) -> bool:
        if self.asr_provider == "tencentcloud":
            return bool(self.tencentcloud_secret_id and self.tencentcloud_secret_key)
        return self.local_whisper_configured

    @property
    def effective_llm_provider(self) -> str:
        # An existing DeepSeek-only installation keeps its previous provider.
        return (self.llm_provider or ("deepseek" if self.deepseek_api_key else "minimax")).strip().lower()

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
        default_url = (
            "https://api.minimax.cn/v1"
            if self.effective_llm_provider == "minimax"
            else self.deepseek_base_url
        )
        return (self.llm_base_url or default_url).rstrip("/")

    @property
    def effective_llm_model(self) -> str:
        return self.llm_model or (
            "MiniMax-M3" if self.effective_llm_provider == "minimax" else self.deepseek_model
        )

    @property
    def llm_configured(self) -> bool:
        """True when the fields needed to *attempt* a cloud call are present.

        This is a field-presence gate, never a health claim: it never contacts
        the provider. Callers use it to decide "try the external model, else
        fall back locally". For what we have actually verified against the
        service, read `llm_service_status` instead. `llm_configured == True`
        must never be shown to a user as 「可用」.
        """
        provider = self.effective_llm_provider
        has_connection = bool(self.effective_llm_base_url and self.effective_llm_model)
        return has_connection and (
            provider in {"ollama", "custom"} or bool(self.effective_llm_api_key)
        )

    @property
    def llm_service_state(self) -> str:
        """One of missing / unverified / available / failed.

        「可用」is only ever reached from a recorded real call — a filled-in
        form can produce 「已填写，未验证」and nothing more.
        """
        fields_filled = self.llm_configured
        return service_state(
            fields_filled=fields_filled,
            verification=llm_service_verification(self) if fields_filled else None,
        )

    def llm_service_status(self, workspace_id: str = "") -> dict[str, Any]:
        """服务状态。带 workspace_id 时只看该工作区自己记录的观测。

        一个工作区跑通不会给另一个工作区点亮「可用」：记录按工作区分开存。
        """
        return project_service_status(
            fields_filled=self.llm_configured,
            verification=llm_service_verification(self, workspace_id),
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
        return bool(self.supabase_url and self.supabase_publishable_key)

    @property
    def supabase_jwt_issuer_resolved(self) -> str | None:
        if self.supabase_jwt_issuer:
            return self.supabase_jwt_issuer
        if self.supabase_url:
            return f"{self.supabase_url.rstrip('/')}/auth/v1"
        return None

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Settings:
        if environ is None:
            load_dotenv_files()
        values = os.environ if environ is None else environ
        catalog_configuration = model_catalog_configuration(values)
        model_root_path = catalog_configuration["model_root_path"]
        return cls(
            device_resource_profile_path=values.get("DEVICE_RESOURCE_PROFILE_PATH"),
            device_resource_mode=values.get("DEVICE_RESOURCE_MODE", "normal"),
            device_resource_experiment_id=values.get("DEVICE_RESOURCE_EXPERIMENT_ID"),
            llm_provider=values.get("LLM_PROVIDER"),
            llm_api_key=values.get("LLM_API_KEY"),
            llm_api_key_provider=values.get("LLM_API_KEY_PROVIDER"),
            llm_base_url=values.get("LLM_BASE_URL"),
            llm_model=values.get("LLM_MODEL"),
            deepseek_api_key=values.get("DEEPSEEK_API_KEY"),
            deepseek_base_url=values.get("DEEPSEEK_BASE_URL", cls.deepseek_base_url),
            deepseek_model=values.get("DEEPSEEK_MODEL", cls.deepseek_model),
            obsidian_vault_path=values.get("OBSIDIAN_VAULT_PATH")
            or _default_obsidian_vault_path(),
            product_hunt_token=values.get("PRODUCT_HUNT_TOKEN"),
            product_hunt_api_url=values.get(
                "PRODUCT_HUNT_API_URL",
                cls.product_hunt_api_url,
            ),
            topic_source_urls=_topic_source_urls_from_env(values),
            hermes_radar_path=values.get("HERMES_RADAR_PATH")
            or _default_hermes_radar_path(),
            video_inbox_path=values.get("VIDEO_INBOX_PATH")
            or _default_video_inbox_path(),
            video_output_path=values.get("VIDEO_OUTPUT_PATH")
            or _default_video_output_path(),
            draft_output_path=values.get("DRAFT_OUTPUT_PATH")
            or _default_draft_output_path(),
            jianying_draft_root_path=values.get("JIANYING_DRAFT_ROOT_PATH")
            or _default_jianying_draft_root_path(),
            database_path=values.get("DATABASE_PATH") or _default_database_path(),
            tencentcloud_secret_id=values.get("TENCENTCLOUD_SECRET_ID"),
            tencentcloud_secret_key=values.get("TENCENTCLOUD_SECRET_KEY"),
            tencentcloud_asr_region=values.get(
                "TENCENTCLOUD_ASR_REGION",
                cls.tencentcloud_asr_region,
            ),
            tencentcloud_asr_engine_model_type=values.get(
                "TENCENTCLOUD_ASR_ENGINE_MODEL_TYPE",
                cls.tencentcloud_asr_engine_model_type,
            ),
            tencentcloud_asr_max_wait_seconds=int(
                values.get(
                    "TENCENTCLOUD_ASR_MAX_WAIT_SECONDS",
                    str(cls.tencentcloud_asr_max_wait_seconds),
                )
            ),
            tencentcloud_asr_poll_interval_seconds=float(
                values.get(
                    "TENCENTCLOUD_ASR_POLL_INTERVAL_SECONDS",
                    str(cls.tencentcloud_asr_poll_interval_seconds),
                )
            ),
            tencentcloud_asr_max_local_audio_bytes=int(
                values.get(
                    "TENCENTCLOUD_ASR_MAX_LOCAL_AUDIO_BYTES",
                    str(cls.tencentcloud_asr_max_local_audio_bytes),
                )
            ),
            asr_provider=values.get("ASR_PROVIDER", cls.asr_provider),
            local_whisper_backend=values.get("LOCAL_WHISPER_BACKEND", cls.local_whisper_backend),
            local_whisper_cli_path=values.get("LOCAL_WHISPER_CLI_PATH"),
            local_whisper_python_path=values.get("LOCAL_WHISPER_PYTHON_PATH")
            or _default_local_whisper_python_path(),
            local_whisper_model_path=values.get("LOCAL_WHISPER_MODEL_PATH")
            or _default_local_whisper_model_path(),
            local_whisper_language=values.get(
                "LOCAL_WHISPER_LANGUAGE",
                cls.local_whisper_language,
            ),
            **catalog_configuration,
            model_root_path_configured=bool(values.get("MODEL_ROOT_PATH")),
            temporary_root_path=values.get("TEMPORARY_ROOT_PATH"),
            native_ocr_worker_path=values.get("NATIVE_OCR_WORKER_PATH"),
            native_portrait_worker_path=values.get("NATIVE_PORTRAIT_WORKER_PATH"),
            avatar_model_runtime_python_path=values.get(
                "AVATAR_MODEL_RUNTIME_PYTHON_PATH"
            ),
            avatar_model_worker_path=values.get("AVATAR_MODEL_WORKER_PATH")
            or _default_avatar_model_worker_path(),
            avatar_model_root_path=values.get("AVATAR_MODEL_ROOT_PATH"),
            avatar_model_id=values.get("AVATAR_MODEL_ID", cls.avatar_model_id),
            latentsync_worker_path=values.get("LATENTSYNC_WORKER_PATH") or cls.latentsync_worker_path,
            latentsync_runtime_python_path=values.get("LATENTSYNC_RUNTIME_PYTHON_PATH")
            or str(Path(model_root_path).parent / "model-runtimes" / "musetalk-mac" / ".venv" / "bin" / "python"),
            latentsync_root_path=values.get("LATENTSYNC_ROOT_PATH")
            or str(Path(model_root_path).parent / "model-runtimes" / "latentsync-mlx-candidate"),
            latentsync_pose_root_path=values.get("LATENTSYNC_POSE_ROOT_PATH")
            or str(Path(model_root_path) / "LatentSync-1.5" / "pose"),
            draft_background_music_path=values.get("DRAFT_BACKGROUND_MUSIC_PATH"),
            supabase_url=values.get("SUPABASE_URL"),
            supabase_publishable_key=values.get("SUPABASE_PUBLISHABLE_KEY"),
            supabase_jwt_audience=values.get(
                "SUPABASE_JWT_AUDIENCE",
                cls.supabase_jwt_audience,
            ),
            supabase_jwt_issuer=values.get("SUPABASE_JWT_ISSUER"),
        )

    def public_status(self) -> dict[str, Any]:
        """Status shown to the client.

        The four-level service state deliberately does **not** go here: this dict
        is validated by `LLMStatusResponse`, which has no field for it, so any
        extra key is silently dropped before it ever reaches the browser. Put it
        in `safe_runtime_settings()` (row `LLM_SERVICE_STATE`) instead, and only
        mirror it here once `LLMStatusResponse` grows a real field.
        """
        cloud_configured = self.llm_configured
        local_status = observe_local_runtime_status(
            obsidian_vault_path=self.obsidian_vault_path,
            hermes_radar_path=self.hermes_radar_path,
            video_inbox_path=self.video_inbox_path,
            video_output_path=self.video_output_path,
            database_path=self.database_path,
        )
        return {
            "llm": {
                "provider": self.effective_llm_provider if cloud_configured else "local-extractive",
                "configured": cloud_configured,
                "model": self.effective_llm_model if cloud_configured else "source-grounded-v1",
                "base_url": self.effective_llm_base_url if cloud_configured else "local",
            },
            "obsidian": local_status["obsidian"],
            "product_hunt": {
                "configured": bool(self.product_hunt_token),
                "api_url": self.product_hunt_api_url,
            },
            "external_sources": {
                "configured": len(self.topic_source_urls) > 0,
                "urls": list(self.topic_source_urls),
            },
            "hermes_radar": local_status["hermes_radar"],
            "video": local_status["video"],
            "storage": local_status["storage"],
            "asr": {
                "provider": self.asr_provider,
                "configured": self.asr_configured,
                "region": self.tencentcloud_asr_region,
                "engine_model_type": self.tencentcloud_asr_engine_model_type,
                "max_local_audio_mb": round(
                    self.tencentcloud_asr_max_local_audio_bytes / 1_000_000,
                    2,
                ),
                "local_whisper_configured": self.local_whisper_configured,
                "local_whisper_python_path": self.local_whisper_python_path,
                "local_whisper_model_path": self.local_whisper_model_path,
            },
            "auth": {
                "enabled": self.auth_enabled,
                "supabase_configured": bool(self.supabase_url),
            },
        }

    def safe_runtime_settings(self, workspace_id: str = "") -> dict[str, Any]:
        llm_service = self.llm_service_status(workspace_id)
        return {
            "settings": [
                {
                    "name": "LLM_PROVIDER",
                    "configured": True,
                    "value": self.effective_llm_provider,
                    "secret": False,
                },
                {
                    # 「已配置」只说明钥匙填了，不说明服务认它。B03 之前这里写
                    # 「已配置」，会被直接读成「可用」。
                    "name": "LLM_API_KEY",
                    "configured": bool(self.effective_llm_api_key),
                    "value": "已填写" if self.effective_llm_api_key else "未填写",
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
                    # 四档状态里前端唯一能拿到的形状：settings 是自由数组，
                    # RuntimeSettingItemResponse 原样放行，不需要动 schemas.py。
                    # 只读，RuntimeSettingsUpdateRequest 里没有它，PATCH 不会回写。
                    "name": "LLM_SERVICE_STATE",
                    "configured": True,
                    "value": llm_service["label"],
                    "secret": False,
                },
                *[
                    {"name": name, "configured": bool(llm_service[field]),
                     "value": llm_service[field], "secret": False}
                    for name, field in (
                        ("LLM_SERVICE_CAUSE", "cause"),
                        ("LLM_SERVICE_DETAIL", "detail"),
                        ("LLM_SERVICE_REPAIR_ACTION", "repair_action"),
                    )
                ],
                {
                    "name": "DEEPSEEK_API_KEY",
                    "configured": bool(self.deepseek_api_key),
                    "value": "已填写" if self.deepseek_api_key else "未填写",
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
                    "name": "ASR_PROVIDER",
                    "configured": True,
                    "value": self.asr_provider,
                    "secret": False,
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
                {"name": "LOCAL_WHISPER_BACKEND", "configured": True, "value": self.local_whisper_backend, "secret": False},
                {"name": "LOCAL_WHISPER_CLI_PATH", "configured": bool(self.local_whisper_cli_path), "value": self.local_whisper_cli_path, "secret": False},
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
                {"name": "UNDERSTANDING_RUNTIME_PYTHON_PATH", "configured": bool(self.understanding_runtime_python_path), "value": self.understanding_runtime_python_path, "secret": False},
                {"name": "NATIVE_OCR_WORKER_PATH", "configured": bool(self.native_ocr_worker_path), "value": self.native_ocr_worker_path, "secret": False},
                {
                    "name": "AVATAR_MODEL_RUNTIME_PYTHON_PATH",
                    "configured": bool(self.avatar_model_runtime_python_path),
                    "value": self.avatar_model_runtime_python_path,
                    "secret": False,
                },
                {
                    "name": "AVATAR_MODEL_ROOT_PATH",
                    "configured": bool(self.avatar_model_root_path),
                    "value": self.avatar_model_root_path,
                    "secret": False,
                },
                {
                    "name": "AVATAR_MODEL_ID",
                    "configured": bool(self.avatar_model_id),
                    "value": self.avatar_model_id,
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
                    "name": "SUPABASE_JWT_AUDIENCE",
                    "configured": bool(self.supabase_jwt_audience),
                    "value": self.supabase_jwt_audience,
                    "secret": False,
                },
            ],
            "status": self.public_status(),
        }


def model_catalog_configuration(environ: Mapping[str, str]) -> dict[str, str]:
    """Resolve the six catalog paths for startup and prospective guarded updates."""
    root = environ.get("MODEL_ROOT_PATH") or _default_model_root_path(environ)
    return {
        "model_root_path": root,
        "model_runtime_python_path": environ.get("MODEL_RUNTIME_PYTHON_PATH")
        or _default_model_runtime_python_path(root),
        "visual_model_worker_path": environ.get("VISUAL_MODEL_WORKER_PATH")
        or _default_visual_model_worker_path(),
        "voice_model_worker_path": environ.get("VOICE_MODEL_WORKER_PATH")
        or _default_voice_model_worker_path(),
        "understanding_runtime_python_path": environ.get("UNDERSTANDING_RUNTIME_PYTHON_PATH")
        or str(Path(root).parent / "model-runtimes" / "qwen-vl" / ".venv" / "bin" / "python"),
        "understanding_worker_path": environ.get("UNDERSTANDING_WORKER_PATH")
        or Settings.understanding_worker_path,
    }
