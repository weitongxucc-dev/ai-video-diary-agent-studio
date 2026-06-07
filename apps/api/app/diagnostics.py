from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import date, datetime, timezone
import io
import json
from pathlib import Path
import zipfile
from typing import Any

from app.config import Settings
from app.models import Episode, VideoJobStatus, utc_now
from app.repository import EpisodeRepository
from app.runtime_health import runtime_health

REPORT_FILE_NAMES = [
    "runtime_status.json",
    "settings_snapshot_redacted.json",
    "last_errors.log",
    "episode_manifest.json",
    "draft_manifest.json",
    "edit_plan.json",
]

EXCLUDED_ITEMS = [
    "API Key 明文",
    "用户完整 Obsidian 内容",
    "未经用户选择的视频文件",
    "剪映私有配置原文",
]


def diagnostics_status(settings: Settings, repo: EpisodeRepository) -> dict[str, Any]:
    generated_at = utc_now()
    errors = recent_errors(repo)
    health = runtime_health(settings)
    return {
        "generated_at": generated_at,
        "summary": {
            "health_total": health["summary"]["total"],
            "health_ready": health["summary"]["ready"],
            "health_missing": health["summary"]["missing"],
            "health_degraded": health["summary"]["degraded"],
            "health_invalid": health["summary"]["invalid"],
            "recent_error_count": len(errors),
            "report_file_count": len(REPORT_FILE_NAMES),
        },
        "log_categories": log_categories(repo, settings),
        "recent_errors": errors,
        "settings_snapshot": redacted_settings_snapshot(settings),
        "report_files": REPORT_FILE_NAMES,
        "excluded_items": EXCLUDED_ITEMS,
    }


def diagnostics_report_bytes(settings: Settings, repo: EpisodeRepository) -> bytes:
    payloads = {
        "runtime_status.json": _json_bytes(
            {
                "generated_at": utc_now(),
                "system_status": settings.public_status(),
                "runtime_health": runtime_health(settings),
            }
        ),
        "settings_snapshot_redacted.json": _json_bytes(redacted_settings_snapshot(settings)),
        "last_errors.log": _last_errors_log(repo).encode("utf-8"),
        "episode_manifest.json": _json_bytes(episode_manifest(repo)),
        "draft_manifest.json": _json_bytes(draft_manifest(repo, settings)),
        "edit_plan.json": _json_bytes(edit_plan_manifest(repo, settings)),
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for file_name in REPORT_FILE_NAMES:
            archive.writestr(file_name, payloads[file_name])
    return buffer.getvalue()


def redacted_settings_snapshot(settings: Settings) -> dict[str, dict[str, Any]]:
    snapshot: dict[str, dict[str, Any]] = {}
    for item in settings.safe_runtime_settings()["settings"]:
        name = str(item["name"])
        snapshot[name] = {
            "configured": bool(item["configured"]),
            "value": item["value"],
            "redacted": bool(item["secret"]),
        }
    return snapshot


def log_categories(
    repo: EpisodeRepository,
    settings: Settings,
) -> list[dict[str, Any]]:
    episodes = repo.all()
    active_jobs = [
        episode
        for episode in episodes
        if str(episode.video_job_status)
        in {VideoJobStatus.PROCESSING.value, VideoJobStatus.DRAFTING.value}
    ]
    video_events = [
        episode
        for episode in episodes
        if episode.video_job_message or episode.video_job_error or episode.video_processed_path
    ]
    asr_events = [
        episode
        for episode in episodes
        if episode.video_edit_plan_path or episode.video_dedup_report_path
    ]
    draft_events = [
        episode
        for episode in episodes
        if episode.draft_package_name or episode.draft_package_path
    ]
    errors = recent_errors(repo)
    return [
        {
            "id": "api",
            "label": "API 启动日志",
            "status": "available",
            "entry_count": 1,
            "latest_message": "FastAPI 本地 API 正在响应诊断接口。",
        },
        {
            "id": "worker",
            "label": "Worker 启动日志",
            "status": "available" if active_jobs else "empty",
            "entry_count": len(active_jobs),
            "latest_message": (
                f"当前有 {len(active_jobs)} 个后台视频任务运行中。"
                if active_jobs
                else "当前没有后台任务；桌面 Worker 进程日志会在 macOS 客户端阶段接入。"
            ),
        },
        {
            "id": "video",
            "label": "视频处理日志",
            "status": "available" if video_events else "empty",
            "entry_count": len(video_events),
            "latest_message": _latest_episode_message(video_events)
            or "尚未记录视频处理任务。",
        },
        {
            "id": "asr",
            "label": "ASR 日志",
            "status": "available" if asr_events else "empty",
            "entry_count": len(asr_events),
            "latest_message": (
                "本地 Whisper 已配置。"
                if settings.public_status()["asr"]["local_whisper_configured"]
                else "尚未配置本地 Whisper；可在设置页补齐 Python 和模型路径。"
            ),
        },
        {
            "id": "jianying",
            "label": "剪映导入日志",
            "status": "available" if draft_events else "empty",
            "entry_count": len(draft_events),
            "latest_message": _latest_draft_message(draft_events)
            or "尚未记录剪映草稿导入。",
        },
        {
            "id": "errors",
            "label": "最近错误",
            "status": "degraded" if errors else "empty",
            "entry_count": len(errors),
            "latest_message": errors[0]["message"] if errors else "最近没有记录错误。",
        },
        {
            "id": "settings",
            "label": "配置快照",
            "status": "available",
            "entry_count": len(settings.safe_runtime_settings()["settings"]),
            "latest_message": "配置快照已脱敏，密钥只显示配置状态。",
        },
    ]


def recent_errors(repo: EpisodeRepository, limit: int = 20) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    for episode in repo.all():
        if not episode.video_job_error:
            continue
        errors.append(
            {
                "source": "video_job",
                "episode_id": episode.id,
                "message": episode.video_job_error,
                "created_at": episode.video_job_finished_at or episode.updated_at,
            }
        )
    return sorted(
        errors,
        key=lambda item: item["created_at"] or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )[:limit]


def episode_manifest(repo: EpisodeRepository) -> dict[str, Any]:
    episodes = repo.all()
    latest = episodes[0] if episodes else None
    return {
        "latest_episode": _episode_summary(latest) if latest else None,
        "recent_episodes": [_episode_summary(episode) for episode in episodes[:10]],
    }


def draft_manifest(repo: EpisodeRepository, settings: Settings) -> dict[str, Any]:
    episode = _latest_episode(repo)
    candidate = _draft_manifest_path(episode, settings)
    if candidate is None:
        return {"available": False, "reason": "draft manifest not found"}
    return {
        "available": True,
        "path": str(candidate),
        "manifest": _read_json_file(candidate),
    }


def edit_plan_manifest(repo: EpisodeRepository, settings: Settings) -> dict[str, Any]:
    episode = _latest_episode(repo)
    if episode is None or not episode.video_edit_plan_path:
        return {"available": False, "reason": "edit plan not found"}
    path = Path(episode.video_edit_plan_path).expanduser()
    output_root = Path(settings.video_output_path).expanduser()
    if not _is_within(path, output_root) or not path.is_file():
        return {"available": False, "reason": "edit plan is outside video output directory"}
    return _read_json_file(path)


def _latest_episode(repo: EpisodeRepository) -> Episode | None:
    episodes = repo.all()
    return episodes[0] if episodes else None


def _episode_summary(episode: Episode | None) -> dict[str, Any] | None:
    if episode is None:
        return None
    return {
        "id": episode.id,
        "day_number": episode.day_number,
        "episode_date": episode.episode_date.isoformat(),
        "status": str(episode.status),
        "intake_mode": str(episode.intake_mode),
        "content_template_id": episode.content_template_id,
        "selected_topic_id": episode.selected_topic_id,
        "script_id": episode.script.id if episode.script else None,
        "script_version_count": len(episode.script_versions),
        "video_source_file_name": episode.video_source_file_name,
        "video_source_file_names": episode.video_source_file_names,
        "video_processed_file_name": episode.video_processed_file_name,
        "video_edit_plan_path": episode.video_edit_plan_path,
        "video_dedup_report_path": episode.video_dedup_report_path,
        "video_job_status": str(episode.video_job_status),
        "video_job_message": episode.video_job_message,
        "video_job_error": episode.video_job_error,
        "draft_package_name": episode.draft_package_name,
        "draft_package_path": episode.draft_package_path,
        "publish_url": episode.publish_url,
        "play_count": episode.play_count,
        "like_count": episode.like_count,
        "comment_count": episode.comment_count,
        "published_at": _iso_or_none(episode.published_at),
        "feedback_event_count": len(episode.feedback_events),
        "created_at": episode.created_at.isoformat(),
        "updated_at": episode.updated_at.isoformat(),
    }


def _draft_manifest_path(episode: Episode | None, settings: Settings) -> Path | None:
    draft_root = Path(settings.draft_output_path).expanduser()
    if episode and episode.draft_package_path:
        path = Path(episode.draft_package_path).expanduser() / "manifest.json"
        if _is_within(path, draft_root) and path.is_file():
            return path
    if not draft_root.exists() or not draft_root.is_dir():
        return None
    manifests = [
        path
        for path in draft_root.glob("*/manifest.json")
        if path.is_file() and _is_within(path, draft_root)
    ]
    if not manifests:
        return None
    return sorted(manifests, key=lambda path: path.stat().st_mtime, reverse=True)[0]


def _read_json_file(path: Path) -> dict[str, Any] | list[Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"available": False, "reason": str(exc)}


def _last_errors_log(repo: EpisodeRepository) -> str:
    errors = recent_errors(repo)
    if not errors:
        return "No recent errors.\n"
    lines = []
    for error in errors:
        timestamp = _iso_or_none(error["created_at"]) or "unknown-time"
        lines.append(
            f"[{timestamp}] {error['source']} {error['episode_id']}: {error['message']}"
        )
    return "\n".join(lines) + "\n"


def _latest_episode_message(episodes: list[Episode]) -> str | None:
    if not episodes:
        return None
    latest = sorted(episodes, key=lambda episode: episode.updated_at, reverse=True)[0]
    return latest.video_job_error or latest.video_job_message or latest.video_processed_path


def _latest_draft_message(episodes: list[Episode]) -> str | None:
    if not episodes:
        return None
    latest = sorted(episodes, key=lambda episode: episode.updated_at, reverse=True)[0]
    return latest.draft_package_name or latest.draft_package_path


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.expanduser().resolve().relative_to(root.expanduser().resolve())
        return True
    except (OSError, ValueError):
        return False


def _json_bytes(payload: Any) -> bytes:
    return json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default).encode(
        "utf-8"
    )


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, Path):
        return str(value)
    return str(value)


def _iso_or_none(value: Any) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None
