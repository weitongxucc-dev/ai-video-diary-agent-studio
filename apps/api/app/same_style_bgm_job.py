from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from pathlib import Path

from app.config import Settings
from app.media_commands import build_mix_bgm_command, run_media_command
from app.same_style_digital_human_job import read_same_style_digital_human_job
from app.same_style_video_job import read_same_style_video_job
from app.schemas import SameStyleBgmJobRequest, SameStyleBgmJobResponse


BGM_MODULE = "bgm_mix_job"
BGM_AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".aac", ".flac"}


def create_same_style_bgm_job(
    settings: Settings,
    request: SameStyleBgmJobRequest,
    *,
    runner: Callable[[list[str]], None] = run_media_command,
    job_id_factory: Callable[[], str] | None = None,
) -> SameStyleBgmJobResponse:
    job_id = (job_id_factory or _new_bgm_job_id)()
    workspace = _bgm_job_workspace(settings, job_id)
    manifest_path = workspace / "bgm_job_manifest.json"

    source_video_path = _source_video_path(settings, request)
    if source_video_path is None or not source_video_path.is_file():
        return _write_bgm_job_manifest(
            manifest_path,
            job_id=job_id,
            status="failed",
            message="缺少可混音的成片视频，请先完成数字人或图生视频生成。",
            request=request,
            source_video_path=str(source_video_path) if source_video_path else None,
            bgm_source="none",
            bgm_path=None,
            mixed_video_path=None,
        )

    if request.strategy == "none":
        return _write_bgm_job_manifest(
            manifest_path,
            job_id=job_id,
            status="completed",
            message="已跳过 BGM，原视频进入下一步。",
            request=request,
            source_video_path=str(source_video_path),
            bgm_source="none",
            bgm_path=None,
            mixed_video_path=str(source_video_path),
        )

    bgm_path = _resolve_bgm_path(settings, request)
    if bgm_path is None or not bgm_path.is_file():
        missing_message = (
            "素材库没有可用 BGM，请配置 BGM_LIBRARY_PATH 或填写素材库内的音频文件名。"
            if request.strategy == "library"
            else "缺少可用 BGM 文件，请填写本地 BGM 文件路径或选择不使用 BGM。"
        )
        return _write_bgm_job_manifest(
            manifest_path,
            job_id=job_id,
            status="failed",
            message=missing_message,
            request=request,
            source_video_path=str(source_video_path),
            bgm_source="none",
            bgm_path=str(bgm_path) if bgm_path else None,
            mixed_video_path=None,
        )

    workspace.mkdir(parents=True, exist_ok=True)
    mixed_video_path = workspace / "mixed_video.mp4"
    try:
        runner(
            build_mix_bgm_command(
                source_video_path,
                bgm_path,
                mixed_video_path,
                bgm_volume=request.volume,
            )
        )
    except Exception as exc:
        return _write_bgm_job_manifest(
            manifest_path,
            job_id=job_id,
            status="failed",
            message=f"BGM 混音失败：{exc}",
            request=request,
            source_video_path=str(source_video_path),
            bgm_source=str(bgm_path),
            bgm_path=str(bgm_path),
            mixed_video_path=None,
        )

    return _write_bgm_job_manifest(
        manifest_path,
        job_id=job_id,
        status="completed",
        message="BGM 已混入视频。",
        request=request,
        source_video_path=str(source_video_path),
        bgm_source="library" if request.strategy == "library" else str(bgm_path),
        bgm_path=str(bgm_path),
        mixed_video_path=str(mixed_video_path),
    )


def read_same_style_bgm_job(
    settings: Settings,
    job_id: str,
) -> SameStyleBgmJobResponse:
    manifest_path = _bgm_job_workspace(settings, job_id) / "bgm_job_manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError("same style bgm job manifest not found")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    return SameStyleBgmJobResponse(
        id=str(manifest["id"]),
        module_name=str(manifest["module_name"]),
        status=manifest["status"],
        message=str(manifest["message"]),
        digital_human_job_id=manifest.get("digital_human_job_id"),
        video_job_id=manifest.get("video_job_id"),
        source_video_path=manifest.get("source_video_path"),
        bgm_source=str(manifest["bgm_source"]),
        bgm_path=manifest.get("bgm_path"),
        mixed_video_path=manifest.get("mixed_video_path"),
        strategy=manifest["strategy"],
        volume=float(manifest["volume"]),
        manifest_path=str(manifest_path),
    )


def _write_bgm_job_manifest(
    manifest_path: Path,
    *,
    job_id: str,
    status: str,
    message: str,
    request: SameStyleBgmJobRequest,
    source_video_path: str | None,
    bgm_source: str,
    bgm_path: str | None,
    mixed_video_path: str | None,
) -> SameStyleBgmJobResponse:
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    digital_human_job_id = request.digital_human_job_id.strip() or None
    video_job_id = request.video_job_id.strip() or None
    manifest = {
        "id": job_id,
        "module_name": BGM_MODULE,
        "status": status,
        "message": message,
        "digital_human_job_id": digital_human_job_id,
        "video_job_id": video_job_id,
        "source_video_path": source_video_path,
        "bgm_source": bgm_source,
        "bgm_path": bgm_path,
        "mixed_video_path": mixed_video_path,
        "strategy": request.strategy,
        "volume": request.volume,
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return SameStyleBgmJobResponse(**manifest, manifest_path=str(manifest_path))


def _source_video_path(settings: Settings, request: SameStyleBgmJobRequest) -> Path | None:
    digital_human_job_id = request.digital_human_job_id.strip()
    if digital_human_job_id:
        try:
            digital_human_job = read_same_style_digital_human_job(
                settings,
                digital_human_job_id,
            )
        except FileNotFoundError:
            return None
        return (
            Path(digital_human_job.video_path).expanduser()
            if digital_human_job.video_path
            else None
        )

    video_job_id = request.video_job_id.strip()
    if video_job_id:
        try:
            video_job = read_same_style_video_job(settings, video_job_id)
        except FileNotFoundError:
            return None
        return Path(video_job.video_path).expanduser() if video_job.video_path else None

    return None


def _resolve_bgm_path(settings: Settings, request: SameStyleBgmJobRequest) -> Path | None:
    raw_value = request.bgm_path.strip()
    if request.strategy == "library":
        library_path = Path(settings.bgm_library_path).expanduser()
        if raw_value:
            candidate = Path(raw_value).expanduser()
            if not candidate.is_absolute():
                candidate = library_path / raw_value
            return candidate if candidate.is_file() else None
        if not library_path.is_dir():
            return None
        for candidate in sorted(library_path.iterdir()):
            if candidate.is_file() and candidate.suffix.lower() in BGM_AUDIO_EXTENSIONS:
                return candidate
        return None
    return Path(raw_value).expanduser() if raw_value else None


def _bgm_job_workspace(settings: Settings, job_id: str) -> Path:
    return Path(settings.draft_output_path).expanduser() / "_same_style_bgm_jobs" / job_id


def _new_bgm_job_id() -> str:
    return f"same_style_bgm_{uuid.uuid4().hex[:12]}"
