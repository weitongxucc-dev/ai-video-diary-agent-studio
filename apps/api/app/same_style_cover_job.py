from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from datetime import date
from pathlib import Path

from PIL import Image

from app.config import Settings
from app.cover_artifacts import write_cover
from app.models import Episode, EpisodeStatus, ScriptPackage, ScriptSection
from app.same_style_publish_copy_job import read_same_style_publish_copy_job
from app.same_style_reference import read_reference_job
from app.schemas import SameStyleCoverJobRequest, SameStyleCoverJobResponse


COVER_MODULE = "cover_artifact_writer"


def create_same_style_cover_job(
    settings: Settings,
    request: SameStyleCoverJobRequest,
    *,
    job_id_factory: Callable[[], str] | None = None,
) -> SameStyleCoverJobResponse:
    job_id = (job_id_factory or _new_cover_job_id)()
    workspace = _cover_job_workspace(settings, job_id)
    manifest_path = workspace / "cover_job_manifest.json"
    cover_path = workspace / "cover.png"

    try:
        reference_job = read_reference_job(settings, request.reference_job_id)
    except FileNotFoundError:
        return _write_cover_job_manifest(
            manifest_path,
            job_id=job_id,
            status="failed",
            message="对标参考任务不存在，无法生成封面。",
            request=request,
            publish_copy_job_id=request.publish_copy_job_id.strip() or None,
            cover_path=None,
            cover_copy="",
            width=None,
            height=None,
        )

    publish_copy_job_id = request.publish_copy_job_id.strip() or None
    cover_copy = ""
    title_options: list[str] = []
    publish_copy = ""
    comment_prompt = ""
    if publish_copy_job_id:
        try:
            publish_copy_job = read_same_style_publish_copy_job(
                settings,
                publish_copy_job_id,
            )
        except FileNotFoundError:
            return _write_cover_job_manifest(
                manifest_path,
                job_id=job_id,
                status="failed",
                message="发布文案任务不存在，无法生成封面。",
                request=request,
                publish_copy_job_id=publish_copy_job_id,
                cover_path=None,
                cover_copy="",
                width=None,
                height=None,
            )
        cover_copy = publish_copy_job.cover_copy
        title_options = publish_copy_job.title_options
        publish_copy = publish_copy_job.publish_copy
        comment_prompt = publish_copy_job.comment_prompt

    script_draft = reference_job.script_draft
    if script_draft is None and not cover_copy:
        return _write_cover_job_manifest(
            manifest_path,
            job_id=job_id,
            status="failed",
            message="缺少封面文案，请先生成同款脚本或发布文案。",
            request=request,
            publish_copy_job_id=publish_copy_job_id,
            cover_path=None,
            cover_copy="",
            width=None,
            height=None,
        )

    if script_draft is not None:
        cover_copy = cover_copy or script_draft.cover_copy
        title_options = title_options or list(script_draft.title_options)
        publish_copy = publish_copy or script_draft.publish_copy
        comment_prompt = comment_prompt or script_draft.comment_prompt
        sections = [
            ScriptSection(
                id=f"same_style_section_{index}",
                title=section.title,
                estimated_seconds=section.estimated_seconds,
                content=section.content,
            )
            for index, section in enumerate(script_draft.sections, start=1)
        ]
        hook = script_draft.hook
    else:
        sections = []
        hook = title_options[0] if title_options else cover_copy.splitlines()[0]

    script = ScriptPackage(
        id=f"{request.reference_job_id}_same_style_cover_script",
        hook=hook,
        sections=sections,
        title_options=title_options or [hook],
        cover_copy=cover_copy,
        publish_copy=publish_copy,
        comment_prompt=comment_prompt,
    )
    episode = Episode(
        id=job_id,
        day_number=1,
        episode_date=date.today(),
        status=EpisodeStatus.SCRIPT_READY,
        cover_template_id=request.template_id,
        script=script,
    )
    write_cover(cover_path, episode)
    with Image.open(cover_path) as image:
        width, height = image.size

    return _write_cover_job_manifest(
        manifest_path,
        job_id=job_id,
        status="completed",
        message="同款封面已生成。",
        request=request,
        publish_copy_job_id=publish_copy_job_id,
        cover_path=str(cover_path),
        cover_copy=cover_copy,
        width=width,
        height=height,
    )


def read_same_style_cover_job(
    settings: Settings,
    job_id: str,
) -> SameStyleCoverJobResponse:
    manifest_path = _cover_job_workspace(settings, job_id) / "cover_job_manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError("same style cover job manifest not found")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    return SameStyleCoverJobResponse(
        id=str(manifest["id"]),
        module_name=str(manifest["module_name"]),
        status=manifest["status"],
        message=str(manifest["message"]),
        reference_job_id=str(manifest["reference_job_id"]),
        publish_copy_job_id=manifest.get("publish_copy_job_id"),
        cover_path=manifest.get("cover_path"),
        cover_source=str(manifest["cover_source"]),
        cover_copy=str(manifest["cover_copy"]),
        template_id=str(manifest["template_id"]),
        width=manifest.get("width"),
        height=manifest.get("height"),
        manifest_path=str(manifest_path),
    )


def _write_cover_job_manifest(
    manifest_path: Path,
    *,
    job_id: str,
    status: str,
    message: str,
    request: SameStyleCoverJobRequest,
    publish_copy_job_id: str | None,
    cover_path: str | None,
    cover_copy: str,
    width: int | None,
    height: int | None,
) -> SameStyleCoverJobResponse:
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest = {
        "id": job_id,
        "module_name": COVER_MODULE,
        "status": status,
        "message": message,
        "reference_job_id": request.reference_job_id,
        "publish_copy_job_id": publish_copy_job_id,
        "cover_path": cover_path,
        "cover_source": "pixel_template" if cover_path else "none",
        "cover_copy": cover_copy,
        "template_id": request.template_id,
        "width": width,
        "height": height,
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return SameStyleCoverJobResponse(**manifest, manifest_path=str(manifest_path))


def _cover_job_workspace(settings: Settings, job_id: str) -> Path:
    return Path(settings.draft_output_path).expanduser() / "_same_style_cover_jobs" / job_id


def _new_cover_job_id() -> str:
    return f"same_style_cover_{uuid.uuid4().hex[:12]}"
