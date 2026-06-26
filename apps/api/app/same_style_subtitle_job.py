from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from pathlib import Path

from app.config import Settings
from app.draft_artifacts import format_srt_time, script_to_srt, subtitle_chunks
from app.models import ScriptPackage, ScriptSection
from app.same_style_audio_job import read_same_style_audio_job
from app.same_style_reference import read_reference_job
from app.schemas import SameStyleSubtitleJobRequest, SameStyleSubtitleJobResponse


SUBTITLE_MODULE = "subtitle_artifact_writer"


def create_same_style_subtitle_job(
    settings: Settings,
    request: SameStyleSubtitleJobRequest,
    *,
    job_id_factory: Callable[[], str] | None = None,
) -> SameStyleSubtitleJobResponse:
    job_id = (job_id_factory or _new_subtitle_job_id)()
    workspace = _subtitle_job_workspace(settings, job_id)
    manifest_path = workspace / "subtitle_job_manifest.json"
    subtitle_path = workspace / "subtitle.srt"

    audio_text = ""
    audio_job_id = request.audio_job_id.strip() or None
    if audio_job_id:
        try:
            audio_job = read_same_style_audio_job(settings, audio_job_id)
        except FileNotFoundError:
            return _write_subtitle_job_manifest(
                manifest_path,
                job_id=job_id,
                status="failed",
                message="音频任务不存在，无法生成字幕。",
                request=request,
                audio_job_id=audio_job_id,
                subtitle_path=None,
                subtitle_source="none",
            )
        audio_text = audio_job.text.strip()

    if audio_text:
        srt = _text_to_srt(audio_text, request.max_duration_seconds)
        subtitle_source = "audio_job_text"
    else:
        try:
            reference_job = read_reference_job(settings, request.reference_job_id)
        except FileNotFoundError:
            return _write_subtitle_job_manifest(
                manifest_path,
                job_id=job_id,
                status="failed",
                message="对标参考任务不存在，无法生成字幕。",
                request=request,
                audio_job_id=audio_job_id,
                subtitle_path=None,
                subtitle_source="none",
            )
        script = _script_package_from_reference_job(reference_job)
        if script is None:
            return _write_subtitle_job_manifest(
                manifest_path,
                job_id=job_id,
                status="failed",
                message="缺少可生成字幕文本的音频任务或仿写文案。",
                request=request,
                audio_job_id=audio_job_id,
                subtitle_path=None,
                subtitle_source="none",
            )
        srt = script_to_srt(
            script,
            max_duration_ms=request.max_duration_seconds * 1_000,
        )
        subtitle_source = "reference_script_draft"

    workspace.mkdir(parents=True, exist_ok=True)
    subtitle_path.write_text(srt, encoding="utf-8")
    return _write_subtitle_job_manifest(
        manifest_path,
        job_id=job_id,
        status="completed",
        message="同款字幕 SRT 已生成。",
        request=request,
        audio_job_id=audio_job_id,
        subtitle_path=str(subtitle_path),
        subtitle_source=subtitle_source,
    )


def read_same_style_subtitle_job(
    settings: Settings,
    job_id: str,
) -> SameStyleSubtitleJobResponse:
    manifest_path = _subtitle_job_workspace(settings, job_id) / "subtitle_job_manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError("same style subtitle job manifest not found")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    return SameStyleSubtitleJobResponse(
        id=str(manifest["id"]),
        module_name=str(manifest["module_name"]),
        status=manifest["status"],
        message=str(manifest["message"]),
        reference_job_id=str(manifest["reference_job_id"]),
        audio_job_id=manifest.get("audio_job_id"),
        subtitle_path=manifest.get("subtitle_path"),
        subtitle_source=str(manifest["subtitle_source"]),
        style=str(manifest["style"]),
        font_name=str(manifest.get("font_name") or "PingFang SC"),
        font_color=str(manifest.get("font_color") or "#fff4d8"),
        outline_color=str(manifest.get("outline_color") or "#111111"),
        max_duration_seconds=int(manifest["max_duration_seconds"]),
        manifest_path=str(manifest_path),
    )


def _write_subtitle_job_manifest(
    manifest_path: Path,
    *,
    job_id: str,
    status: str,
    message: str,
    request: SameStyleSubtitleJobRequest,
    audio_job_id: str | None,
    subtitle_path: str | None,
    subtitle_source: str,
) -> SameStyleSubtitleJobResponse:
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest = {
        "id": job_id,
        "module_name": SUBTITLE_MODULE,
        "status": status,
        "message": message,
        "reference_job_id": request.reference_job_id,
        "audio_job_id": audio_job_id,
        "subtitle_path": subtitle_path,
        "subtitle_source": subtitle_source,
        "style": request.style,
        "font_name": request.font_name,
        "font_color": request.font_color,
        "outline_color": request.outline_color,
        "max_duration_seconds": request.max_duration_seconds,
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return SameStyleSubtitleJobResponse(
        **manifest,
        manifest_path=str(manifest_path),
    )


def _text_to_srt(text: str, max_duration_seconds: int) -> str:
    chunks = subtitle_chunks(text)
    if not chunks:
        return ""
    max_duration_ms = max_duration_seconds * 1_000
    chunk_duration_ms = max(1_800, min(3_000, max_duration_ms // max(1, len(chunks))))
    entries: list[str] = []
    cursor_ms = 0
    for index, chunk in enumerate(chunks, start=1):
        end_ms = min(max_duration_ms, cursor_ms + chunk_duration_ms)
        entries.append(
            "\n".join(
                [
                    str(index),
                    f"{format_srt_time(cursor_ms)} --> {format_srt_time(end_ms)}",
                    chunk,
                ]
            )
        )
        cursor_ms = end_ms
        if cursor_ms >= max_duration_ms:
            break
    return "\n\n".join(entries) + "\n"


def _script_package_from_reference_job(reference_job: object) -> ScriptPackage | None:
    script_draft = getattr(reference_job, "script_draft", None)
    if script_draft is None:
        return None
    sections = [
        ScriptSection(
            id=f"same_style_section_{index}",
            title=section.title,
            estimated_seconds=section.estimated_seconds,
            content=section.content,
        )
        for index, section in enumerate(getattr(script_draft, "sections", []), start=1)
    ]
    return ScriptPackage(
        id=f"{reference_job.id}_same_style_script",
        hook=script_draft.hook,
        sections=sections,
        title_options=list(script_draft.title_options),
        cover_copy=script_draft.cover_copy,
        publish_copy=script_draft.publish_copy,
        comment_prompt=script_draft.comment_prompt,
    )


def _subtitle_job_workspace(settings: Settings, job_id: str) -> Path:
    return Path(settings.draft_output_path).expanduser() / "_same_style_subtitle_jobs" / job_id


def _new_subtitle_job_id() -> str:
    return f"same_style_subtitle_{uuid.uuid4().hex[:12]}"
