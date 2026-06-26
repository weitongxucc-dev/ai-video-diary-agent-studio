from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from pathlib import Path

import httpx

from app.config import Settings
from app.media_commands import build_burn_subtitle_command, run_media_command
from app.same_style_bgm_job import read_same_style_bgm_job
from app.same_style_cover_job import read_same_style_cover_job
from app.same_style_publish_copy_job import read_same_style_publish_copy_job
from app.same_style_provider_tasks import normalize_provider_status, provider_value
from app.same_style_subtitle_job import read_same_style_subtitle_job
from app.schemas import SameStylePublishJobRequest, SameStylePublishJobResponse


PUBLISH_MODULE = "social_auto_upload_adapter"


def create_same_style_publish_job(
    settings: Settings,
    request: SameStylePublishJobRequest,
    *,
    runner: Callable[[list[str]], None] = run_media_command,
    publisher: Callable[[dict[str, object]], dict[str, object]] | None = None,
    job_id_factory: Callable[[], str] | None = None,
) -> SameStylePublishJobResponse:
    job_id = (job_id_factory or _new_publish_job_id)()
    workspace = _publish_job_workspace(settings, job_id)
    manifest_path = workspace / "publish_job_manifest.json"

    bgm_job_id = request.bgm_job_id.strip() or None
    subtitle_job_id = request.subtitle_job_id.strip() or None
    cover_job_id = request.cover_job_id.strip() or None
    publish_copy_job_id = request.publish_copy_job_id.strip() or None
    source_video_path = _bgm_video_path(settings, bgm_job_id)
    if source_video_path is None or not Path(source_video_path).is_file():
        return _write_publish_job_manifest(
            manifest_path,
            job_id=job_id,
            status="failed",
            message="缺少最终视频，请先完成视频生成和 BGM 步骤。",
            request=request,
            final_video_path=source_video_path,
            subtitle_job_id=subtitle_job_id,
            cover_path=None,
            title="",
            publish_copy="",
            tags=[],
        )

    final_video_path = _compose_final_video(
        settings,
        workspace,
        source_video_path,
        subtitle_job_id,
        runner,
    )
    if final_video_path is None or not Path(final_video_path).is_file():
        return _write_publish_job_manifest(
            manifest_path,
            job_id=job_id,
            status="failed",
            message="最终视频合成失败，请检查字幕任务和 FFmpeg。",
            request=request,
            final_video_path=None,
            subtitle_job_id=subtitle_job_id,
            cover_path=None,
            title="",
            publish_copy="",
            tags=[],
        )

    cover_path = _cover_path(settings, cover_job_id)
    title = ""
    publish_copy = ""
    tags: list[str] = []
    if publish_copy_job_id:
        try:
            publish_copy_job = read_same_style_publish_copy_job(
                settings,
                publish_copy_job_id,
            )
            title = publish_copy_job.title_options[0] if publish_copy_job.title_options else ""
            publish_copy = publish_copy_job.publish_copy
            tags = publish_copy_job.tags
        except FileNotFoundError:
            pass

    if not title:
        title = "同款视频待发布"

    platforms = [platform for platform in request.platforms if platform.strip()]
    if not platforms:
        platforms = ["douyin"]

    status = "ready_for_review"
    message = "发布包已生成，等待人工确认后发布。"
    provider_job_id: object | None = None
    if request.auto_submit:
        publish_result = _submit_publish_package(
            settings,
            publisher,
            {
                "final_video_path": final_video_path,
                "cover_path": cover_path,
                "title": title,
                "publish_copy": publish_copy,
                "tags": tags,
                "platforms": platforms,
            },
        )
        if publish_result is None:
            message = "发布包已生成；自动上传服务未配置，已保留人工确认。"
        else:
            status = normalize_provider_status(provider_value(publish_result, ["status", "state"]))
            if status == "completed":
                status = "published"
            message = str(
                provider_value(publish_result, ["message", "msg"])
                or "发布包已提交到自动上传服务。"
            )
            provider_job_id = provider_value(
                publish_result,
                ["provider_job_id", "id", "task_id", "job_id", "data.id"],
            )

    return _write_publish_job_manifest(
        manifest_path,
        job_id=job_id,
        status=status,
        message=message,
        request=request,
        final_video_path=final_video_path,
        subtitle_job_id=subtitle_job_id,
        cover_path=cover_path,
        title=title,
        publish_copy=publish_copy,
        tags=tags,
        platforms=platforms,
        provider_job_id=provider_job_id,
    )


def read_same_style_publish_job(
    settings: Settings,
    job_id: str,
) -> SameStylePublishJobResponse:
    manifest_path = _publish_job_workspace(settings, job_id) / "publish_job_manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError("same style publish job manifest not found")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    return SameStylePublishJobResponse(
        id=str(manifest["id"]),
        module_name=str(manifest["module_name"]),
        status=manifest["status"],
        message=str(manifest["message"]),
        bgm_job_id=manifest.get("bgm_job_id"),
        subtitle_job_id=manifest.get("subtitle_job_id"),
        cover_job_id=manifest.get("cover_job_id"),
        publish_copy_job_id=manifest.get("publish_copy_job_id"),
        final_video_path=manifest.get("final_video_path"),
        provider_job_id=manifest.get("provider_job_id"),
        cover_path=manifest.get("cover_path"),
        title=str(manifest["title"]),
        publish_copy=str(manifest["publish_copy"]),
        tags=[str(tag) for tag in manifest["tags"]],
        platforms=[str(platform) for platform in manifest["platforms"]],
        manifest_path=str(manifest_path),
    )


def _write_publish_job_manifest(
    manifest_path: Path,
    *,
    job_id: str,
    status: str,
    message: str,
    request: SameStylePublishJobRequest,
    final_video_path: str | None,
    subtitle_job_id: str | None,
    cover_path: str | None,
    title: str,
    publish_copy: str,
    tags: list[str],
    platforms: list[str] | None = None,
    provider_job_id: object | None = None,
) -> SameStylePublishJobResponse:
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest = {
        "id": job_id,
        "module_name": PUBLISH_MODULE,
        "status": status,
        "message": message,
        "bgm_job_id": request.bgm_job_id.strip() or None,
        "subtitle_job_id": subtitle_job_id,
        "cover_job_id": request.cover_job_id.strip() or None,
        "publish_copy_job_id": request.publish_copy_job_id.strip() or None,
        "final_video_path": final_video_path,
        "provider_job_id": str(provider_job_id) if provider_job_id else None,
        "cover_path": cover_path,
        "title": title,
        "publish_copy": publish_copy,
        "tags": tags,
        "platforms": platforms if platforms is not None else request.platforms,
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return SameStylePublishJobResponse(**manifest, manifest_path=str(manifest_path))


def _bgm_video_path(settings: Settings, bgm_job_id: str | None) -> str | None:
    if not bgm_job_id:
        return None
    try:
        bgm_job = read_same_style_bgm_job(settings, bgm_job_id)
    except FileNotFoundError:
        return None
    return bgm_job.mixed_video_path


def _compose_final_video(
    settings: Settings,
    workspace: Path,
    source_video_path: str,
    subtitle_job_id: str | None,
    runner: Callable[[list[str]], None],
) -> str | None:
    if not subtitle_job_id:
        return source_video_path
    try:
        subtitle_job = read_same_style_subtitle_job(settings, subtitle_job_id)
    except FileNotFoundError:
        return None
    if not subtitle_job.subtitle_path or not Path(subtitle_job.subtitle_path).is_file():
        return None
    workspace.mkdir(parents=True, exist_ok=True)
    output_path = workspace / "final_video.mp4"
    try:
        runner(
            build_burn_subtitle_command(
                Path(source_video_path).expanduser(),
                Path(subtitle_job.subtitle_path).expanduser(),
                output_path,
                font_name=subtitle_job.font_name,
                font_color=subtitle_job.font_color,
                outline_color=subtitle_job.outline_color,
            )
        )
    except Exception:
        return None
    return str(output_path)


def _submit_publish_package(
    settings: Settings,
    publisher: Callable[[dict[str, object]], dict[str, object]] | None,
    payload: dict[str, object],
) -> dict[str, object] | None:
    if publisher is not None:
        return publisher(payload)
    if not settings.social_auto_upload_base_url:
        return None
    headers = {}
    if settings.social_auto_upload_api_key:
        headers["Authorization"] = f"Bearer {settings.social_auto_upload_api_key}"
    with httpx.Client(timeout=60.0) as client:
        response = client.post(
            settings.social_auto_upload_base_url,
            json=payload,
            headers=headers,
        )
    response.raise_for_status()
    return response.json()


def _cover_path(settings: Settings, cover_job_id: str | None) -> str | None:
    if not cover_job_id:
        return None
    try:
        cover_job = read_same_style_cover_job(settings, cover_job_id)
    except FileNotFoundError:
        return None
    return cover_job.cover_path


def _publish_job_workspace(settings: Settings, job_id: str) -> Path:
    return Path(settings.draft_output_path).expanduser() / "_same_style_publish_jobs" / job_id


def _new_publish_job_id() -> str:
    return f"same_style_publish_{uuid.uuid4().hex[:12]}"
