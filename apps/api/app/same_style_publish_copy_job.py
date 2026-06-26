from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from pathlib import Path

from app.config import Settings
from app.same_style_reference import read_reference_job
from app.schemas import (
    SameStylePublishCopyJobRequest,
    SameStylePublishCopyJobResponse,
)


PUBLISH_COPY_MODULE = "publish_copy_generator"


def create_same_style_publish_copy_job(
    settings: Settings,
    request: SameStylePublishCopyJobRequest,
    *,
    job_id_factory: Callable[[], str] | None = None,
) -> SameStylePublishCopyJobResponse:
    job_id = (job_id_factory or _new_publish_copy_job_id)()
    manifest_path = _publish_copy_job_workspace(settings, job_id) / "publish_copy_job_manifest.json"

    try:
        reference_job = read_reference_job(settings, request.reference_job_id)
    except FileNotFoundError:
        return _write_publish_copy_job_manifest(
            manifest_path,
            job_id=job_id,
            status="failed",
            message="对标参考任务不存在，无法生成发布文案。",
            request=request,
            title_options=[],
            tags=[],
            publish_copy="",
            cover_copy="",
            comment_prompt="",
        )

    script_draft = reference_job.script_draft
    if script_draft is None:
        return _write_publish_copy_job_manifest(
            manifest_path,
            job_id=job_id,
            status="failed",
            message="缺少同款脚本草案，无法生成发布文案。",
            request=request,
            title_options=[],
            tags=[],
            publish_copy="",
            cover_copy="",
            comment_prompt="",
        )

    return _write_publish_copy_job_manifest(
        manifest_path,
        job_id=job_id,
        status="completed",
        message="标题、标签和发布文案已生成。",
        request=request,
        title_options=[title for title in script_draft.title_options if title.strip()],
        tags=_tags_for_platform(request.platform),
        publish_copy=_publish_copy_for_platform(script_draft.publish_copy, request.platform),
        cover_copy=script_draft.cover_copy,
        comment_prompt=script_draft.comment_prompt,
    )


def read_same_style_publish_copy_job(
    settings: Settings,
    job_id: str,
) -> SameStylePublishCopyJobResponse:
    manifest_path = (
        _publish_copy_job_workspace(settings, job_id)
        / "publish_copy_job_manifest.json"
    )
    if not manifest_path.is_file():
        raise FileNotFoundError("same style publish copy job manifest not found")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    return SameStylePublishCopyJobResponse(
        id=str(manifest["id"]),
        module_name=str(manifest["module_name"]),
        status=manifest["status"],
        message=str(manifest["message"]),
        reference_job_id=str(manifest["reference_job_id"]),
        platform=str(manifest["platform"]),
        audience=str(manifest["audience"]),
        title_options=[str(item) for item in manifest["title_options"]],
        tags=[str(item) for item in manifest["tags"]],
        publish_copy=str(manifest["publish_copy"]),
        cover_copy=str(manifest["cover_copy"]),
        comment_prompt=str(manifest["comment_prompt"]),
        manifest_path=str(manifest_path),
    )


def _write_publish_copy_job_manifest(
    manifest_path: Path,
    *,
    job_id: str,
    status: str,
    message: str,
    request: SameStylePublishCopyJobRequest,
    title_options: list[str],
    tags: list[str],
    publish_copy: str,
    cover_copy: str,
    comment_prompt: str,
) -> SameStylePublishCopyJobResponse:
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest = {
        "id": job_id,
        "module_name": PUBLISH_COPY_MODULE,
        "status": status,
        "message": message,
        "reference_job_id": request.reference_job_id,
        "platform": request.platform,
        "audience": request.audience,
        "title_options": title_options,
        "tags": tags,
        "publish_copy": publish_copy,
        "cover_copy": cover_copy,
        "comment_prompt": comment_prompt,
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return SameStylePublishCopyJobResponse(
        **manifest,
        manifest_path=str(manifest_path),
    )


def _tags_for_platform(platform: str) -> list[str]:
    base_tags = ["#AI视频日记", "#同款复刻", "#爆款拆解"]
    if platform.lower() in {"xiaohongshu", "red"}:
        return [*base_tags, "#小红书运营"]
    if platform.lower() in {"bilibili", "b站", "bili"}:
        return [*base_tags, "#AI创作"]
    return [*base_tags, "#抖音同款"]


def _publish_copy_for_platform(publish_copy: str, platform: str) -> str:
    normalized = publish_copy.strip()
    if not normalized:
        return ""
    if platform.lower() in {"douyin", "抖音"}:
        return f"{normalized}\n\n#AI视频日记 #同款复刻 #爆款拆解"
    return normalized


def _publish_copy_job_workspace(settings: Settings, job_id: str) -> Path:
    return (
        Path(settings.draft_output_path).expanduser()
        / "_same_style_publish_copy_jobs"
        / job_id
    )


def _new_publish_copy_job_id() -> str:
    return f"same_style_publish_copy_{uuid.uuid4().hex[:12]}"
