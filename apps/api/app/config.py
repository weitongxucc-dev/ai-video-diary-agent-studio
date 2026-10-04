from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import threading
from dataclasses import dataclass
from datetime import datetime
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


def structured_completion_options(model_id: str) -> dict[str, object]:
    """Keep MiniMax reasoning outside the JSON content parsed by production."""
    return {"reasoning_split": True} if model_id.startswith("MiniMax-") else {}


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
    # On macOS a leftover /Volumes/<name> directory can exist on the internal
    # disk even though the external volume is absent. Existence is not enough.
    if len(root.parts) >= 3 and root.parts[1] == "Volumes":
        volume = Path(*root.parts[:3])
        if not volume.is_mount():
            raise FileNotFoundError(f"外接存储未挂载：{volume}。请连接原数据盘后重新启动应用。")
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


def _default_avatar_model_worker_path() -> str:
    return str(Path(__file__).resolve().parents[1] / "model_workers" / "musetalk_worker.py")


def _default_hermes_radar_path() -> str:
    return str(Path.home() / "Desktop" / "product-hunt-raw" / "daily-reports")


def _topic_source_urls_from_env() -> tuple[str, ...]:
    raw = os.getenv("TOPIC_SOURCE_URLS")
    if raw is None:
        return ("https://watcha.cn/",)
    return tuple(url.strip() for url in raw.split(",") if url.strip())


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


# ── 外部文字模型服务状态（0.11 B03）──────────────────────────────────────
# 四档状态，语义严格按 backlog：已填写 / 未验证 / 可用 / 失败。
# 「可用」只能由一次**真实发生过**的外部调用产生，绝不由字段是否填好推断。
LLM_SERVICE_MISSING = "missing"        # 未填写
LLM_SERVICE_UNVERIFIED = "unverified"  # 已填写，未验证
LLM_SERVICE_AVAILABLE = "available"    # 可用（真的连通过）
LLM_SERVICE_FAILED = "failed"          # 失败（真的试过且没成功）

_LLM_SERVICE_LABELS = {
    LLM_SERVICE_MISSING: "未填写",
    LLM_SERVICE_UNVERIFIED: "已填写，未验证",
    LLM_SERVICE_AVAILABLE: "可用",
    LLM_SERVICE_FAILED: "失败",
}

# 每种失败原因对应一句日常中文。cause 来自 assistant_pi 的真实观测结果，
# 未知原因不许编，写「拿不到更具体的原因」。
_LLM_SERVICE_FAILURE_REASONS = {
    "key_rejected": "服务拒绝了这把密钥。这一步是真的连到了服务，是密钥没通过。",
    "address_unreachable": "连不上设置里的服务地址。可能是地址填错，或当前网络到不了这个服务。",
    "timeout": "等模型回复超时，这一轮没有拿到回复。",
}

_LLM_SERVICE_DEFAULT_FAILURE_REASON = (
    "上一次真实尝试没有成功，但日志里没有留下能说明原因的信息，拿不到更具体的原因。"
)

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
        runtime = self.local_whisper_runtime_path
        return bool(runtime and Path(runtime).expanduser().is_file()
                    and self.local_whisper_model_path and Path(self.local_whisper_model_path).expanduser().is_file())

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
            "https://api.minimax.io/v1"
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
        if not self.llm_configured:
            return LLM_SERVICE_MISSING
        record = llm_service_verification(self)
        if record is None:
            return LLM_SERVICE_UNVERIFIED
        return LLM_SERVICE_AVAILABLE if record["ok"] else LLM_SERVICE_FAILED

    def llm_service_status(self, workspace_id: str = "") -> dict[str, Any]:
        """服务状态。带 workspace_id 时只看该工作区自己记录的观测。

        一个工作区跑通不会给另一个工作区点亮「可用」：记录按工作区分开存。
        """
        if workspace_id:
            record = llm_service_verification(self, workspace_id)
            if not self.llm_configured:
                state = LLM_SERVICE_MISSING
            elif record is None:
                state = LLM_SERVICE_UNVERIFIED
            else:
                state = LLM_SERVICE_AVAILABLE if record["ok"] else LLM_SERVICE_FAILED
        else:
            state = self.llm_service_state
            record = llm_service_verification(self)
        if state == LLM_SERVICE_MISSING:
            detail = "服务地址、模型或 API Key 没有填全，现在走不了外部文字模型。"
        elif state == LLM_SERVICE_UNVERIFIED:
            detail = (
                "地址、模型和密钥都填了，但还没有真的连过一次服务；"
                "能不能用要等下一次真实对话或写稿才知道。"
            )
        elif state == LLM_SERVICE_AVAILABLE:
            detail = "上一次真实调用成功返回了回复，当前这套地址、模型和密钥连得通。"
        else:
            detail = record.get("detail") or _LLM_SERVICE_FAILURE_REASONS.get(
                str(record.get("cause")), _LLM_SERVICE_DEFAULT_FAILURE_REASON
            )
        return {
            "state": state,
            "label": _LLM_SERVICE_LABELS[state],
            "detail": detail,
            "fields_filled": self.llm_configured,
            "verified": state == LLM_SERVICE_AVAILABLE,
            "cause": str(record.get("cause")) if record else "",
            "repair_action": str(record.get("repair_action", "")) if record else "",
            "observed_at": str(record.get("observed_at")) if record else "",
        }

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
            asr_provider=os.getenv("ASR_PROVIDER", cls.asr_provider),
            local_whisper_backend=os.getenv("LOCAL_WHISPER_BACKEND", cls.local_whisper_backend),
            local_whisper_cli_path=os.getenv("LOCAL_WHISPER_CLI_PATH"),
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
            understanding_runtime_python_path=os.getenv("UNDERSTANDING_RUNTIME_PYTHON_PATH") or str(Path(model_root_path).parent / "model-runtimes" / "qwen-vl" / ".venv" / "bin" / "python"),
            understanding_worker_path=os.getenv("UNDERSTANDING_WORKER_PATH")
            or cls.understanding_worker_path,
            native_ocr_worker_path=os.getenv("NATIVE_OCR_WORKER_PATH"),
            native_portrait_worker_path=os.getenv("NATIVE_PORTRAIT_WORKER_PATH"),
            avatar_model_runtime_python_path=os.getenv(
                "AVATAR_MODEL_RUNTIME_PYTHON_PATH"
            ),
            avatar_model_worker_path=os.getenv("AVATAR_MODEL_WORKER_PATH")
            or _default_avatar_model_worker_path(),
            avatar_model_root_path=os.getenv("AVATAR_MODEL_ROOT_PATH"),
            avatar_model_id=os.getenv("AVATAR_MODEL_ID", cls.avatar_model_id),
            latentsync_worker_path=os.getenv("LATENTSYNC_WORKER_PATH") or cls.latentsync_worker_path,
            latentsync_runtime_python_path=os.getenv("LATENTSYNC_RUNTIME_PYTHON_PATH")
            or str(Path(model_root_path).parent / "model-runtimes" / "musetalk-mac" / ".venv" / "bin" / "python"),
            latentsync_root_path=os.getenv("LATENTSYNC_ROOT_PATH")
            or str(Path(model_root_path).parent / "model-runtimes" / "latentsync-mlx-candidate"),
            latentsync_pose_root_path=os.getenv("LATENTSYNC_POSE_ROOT_PATH")
            or str(Path(model_root_path) / "LatentSync-1.5" / "pose"),
            draft_background_music_path=os.getenv("DRAFT_BACKGROUND_MUSIC_PATH"),
            supabase_url=os.getenv("SUPABASE_URL"),
            supabase_publishable_key=os.getenv("SUPABASE_PUBLISHABLE_KEY"),
            supabase_jwt_audience=os.getenv(
                "SUPABASE_JWT_AUDIENCE",
                cls.supabase_jwt_audience,
            ),
            supabase_jwt_issuer=os.getenv("SUPABASE_JWT_ISSUER"),
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
                "markdown_count": None,
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
