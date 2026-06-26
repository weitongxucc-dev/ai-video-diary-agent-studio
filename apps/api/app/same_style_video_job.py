from __future__ import annotations

import base64
import json
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Protocol
from urllib.parse import urlparse

import httpx

from app.config import Settings
from app.same_style_reference import read_reference_job
from app.same_style_provider_tasks import (
    download_provider_media,
    fetch_provider_status,
    normalize_provider_status,
    provider_value,
)
from app.schemas import SameStyleVideoJobRequest, SameStyleVideoJobResponse


SEEDANCE_VIDEO_MODULE = "seedance_video_job"


class SeedanceVideoGenerator(Protocol):
    def submit(
        self,
        *,
        reference_grid_path: Path,
        prompt: str,
        aspect_ratio: str,
        duration_seconds: int,
        workspace: Path,
    ) -> dict[str, object]:
        ...


class SeedanceHttpVideoGenerator:
    def __init__(
        self,
        settings: Settings,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 60.0,
    ) -> None:
        self._settings = settings
        self._transport = transport
        self._timeout = timeout

    def submit(
        self,
        *,
        reference_grid_path: Path,
        prompt: str,
        aspect_ratio: str,
        duration_seconds: int,
        workspace: Path,
    ) -> dict[str, object]:
        if not self._settings.seedance_api_key or not self._settings.seedance_base_url:
            raise RuntimeError("SEEDANCE_API_KEY and SEEDANCE_BASE_URL are required")

        payload = _build_seedance_payload(
            settings=self._settings,
            reference_grid_path=reference_grid_path,
            prompt=prompt,
            aspect_ratio=aspect_ratio,
            duration_seconds=duration_seconds,
            workspace=workspace,
        )
        headers = {"Authorization": f"Bearer {self._settings.seedance_api_key}"}
        with httpx.Client(transport=self._transport, timeout=self._timeout) as client:
            response = client.post(
                self._settings.seedance_base_url,
                json=payload,
                headers=headers,
            )
        response.raise_for_status()
        data = response.json()
        return {
            "provider_job_id": str(
                provider_value(
                    data,
                    ["id", "task_id", "job_id", "data.id", "data.task_id"],
                )
                or ""
            )
            or None,
            "status": normalize_provider_status(data.get("status") or "submitted"),
            "message": str(data.get("message") or "Seedance task submitted"),
            "video_path": data.get("video_path") or data.get("output_video_path"),
            "video_url": data.get("video_url")
            or data.get("output_video_url")
            or data.get("url"),
        }


def create_same_style_video_job(
    settings: Settings,
    request: SameStyleVideoJobRequest,
    *,
    generator: SeedanceVideoGenerator | None = None,
    job_id_factory: Callable[[], str] | None = None,
) -> SameStyleVideoJobResponse:
    job_id = (job_id_factory or _new_video_job_id)()
    workspace = _video_job_workspace(settings, job_id)
    manifest_path = workspace / "video_job_manifest.json"

    try:
        reference_job = read_reference_job(settings, request.reference_job_id)
    except FileNotFoundError:
        return _write_video_job_manifest(
            manifest_path,
            job_id=job_id,
            status="failed",
            message="对标参考任务不存在，无法创建图生视频任务。",
            request=request,
            reference_grid_path=None,
            prompt=request.prompt,
        )

    reference_grid_value = (
        reference_job.product_reference_grid_path
        or reference_job.reference_grid_path
    )
    reference_grid_path = Path(reference_grid_value).expanduser() if reference_grid_value else None
    if reference_grid_path is None or not reference_grid_path.is_file():
        return _write_video_job_manifest(
            manifest_path,
            job_id=job_id,
            status="failed",
            message="对标任务缺少宫格参考图，请先完成视频抽帧和 reference_grid.jpg 生成。",
            request=request,
            reference_grid_path=str(reference_grid_path) if reference_grid_path else None,
            prompt=request.prompt,
        )

    prompt = request.prompt.strip() or _prompt_from_reference_job(reference_job)
    if generator is None and not (
        settings.seedance_api_key and settings.seedance_base_url
    ):
        return _write_video_job_manifest(
            manifest_path,
            job_id=job_id,
            status="configuration_required",
            message="Seedance 图生视频未配置；请设置 SEEDANCE_API_KEY 和 SEEDANCE_BASE_URL 后再提交。",
            request=request,
            reference_grid_path=str(reference_grid_path),
            prompt=prompt,
        )

    active_generator = generator or SeedanceHttpVideoGenerator(settings)
    try:
        provider_result = active_generator.submit(
            reference_grid_path=reference_grid_path,
            prompt=prompt,
            aspect_ratio=request.aspect_ratio,
            duration_seconds=request.duration_seconds,
            workspace=workspace,
        )
    except Exception as exc:
        return _write_video_job_manifest(
            manifest_path,
            job_id=job_id,
            status="failed",
            message=f"Seedance 图生视频任务提交失败：{exc}",
            request=request,
            reference_grid_path=str(reference_grid_path),
            prompt=prompt,
        )

    status = str(provider_result.get("status") or "submitted")
    if status not in {"submitted", "completed", "failed"}:
        status = normalize_provider_status(status)
    try:
        video_path = _video_path_from_provider_result(provider_result, workspace)
    except Exception:
        video_path = None
    if status == "completed" and video_path is None:
        status = "submitted"
    return _write_video_job_manifest(
        manifest_path,
        job_id=job_id,
        status=status,
        message=str(provider_result.get("message") or "Seedance task submitted"),
        request=request,
        reference_grid_path=str(reference_grid_path),
        prompt=prompt,
        provider_job_id=provider_result.get("provider_job_id"),
        video_path=video_path,
    )


def read_same_style_video_job(
    settings: Settings,
    job_id: str,
    *,
    status_fetcher: Callable[[str, Path], dict[str, object] | None] | None = None,
) -> SameStyleVideoJobResponse:
    manifest_path = _video_job_workspace(settings, job_id) / "video_job_manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError("same style video job manifest not found")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest = _refresh_video_manifest_if_needed(
        settings,
        manifest,
        manifest_path,
        status_fetcher=status_fetcher,
    )
    return SameStyleVideoJobResponse(
        id=str(manifest["id"]),
        module_name=str(manifest["module_name"]),
        status=manifest["status"],
        message=str(manifest["message"]),
        reference_job_id=str(manifest["reference_job_id"]),
        reference_grid_path=manifest.get("reference_grid_path"),
        prompt=str(manifest["prompt"]),
        aspect_ratio=manifest["aspect_ratio"],
        duration_seconds=int(manifest["duration_seconds"]),
        provider_job_id=manifest.get("provider_job_id"),
        video_path=manifest.get("video_path"),
        manifest_path=str(manifest_path),
    )


def _refresh_video_manifest_if_needed(
    settings: Settings,
    manifest: dict[str, object],
    manifest_path: Path,
    *,
    status_fetcher: Callable[[str, Path], dict[str, object] | None] | None,
) -> dict[str, object]:
    if manifest.get("status") != "submitted" or manifest.get("video_path"):
        return manifest
    provider_job_id = manifest.get("provider_job_id")
    if not provider_job_id:
        return manifest
    workspace = manifest_path.parent
    if status_fetcher is None:
        def status_fetcher(provider_job_id: str, workspace: Path) -> dict[str, object] | None:
            return fetch_provider_status(
                status_url_template=settings.seedance_status_url_template,
                provider_job_id=provider_job_id,
                api_key=settings.seedance_api_key,
            )

    try:
        provider_result = status_fetcher(str(provider_job_id), workspace)
    except Exception:
        return manifest
    if provider_result is None:
        return manifest
    status = normalize_provider_status(
        provider_value(provider_result, ["status", "state", "data.status", "data.state"])
    )
    try:
        video_path = _video_path_from_provider_result(provider_result, workspace)
    except Exception:
        video_path = None
    if status == "completed" and video_path is None:
        status = "submitted"
    manifest["status"] = status
    manifest["message"] = str(
        provider_value(provider_result, ["message", "msg"]) or manifest.get("message") or ""
    )
    if video_path:
        manifest["video_path"] = str(video_path)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def _write_video_job_manifest(
    manifest_path: Path,
    *,
    job_id: str,
    status: str,
    message: str,
    request: SameStyleVideoJobRequest,
    reference_grid_path: str | None,
    prompt: str,
    provider_job_id: object | None = None,
    video_path: object | None = None,
) -> SameStyleVideoJobResponse:
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest = {
        "id": job_id,
        "module_name": SEEDANCE_VIDEO_MODULE,
        "status": status,
        "message": message,
        "reference_job_id": request.reference_job_id,
        "reference_grid_path": reference_grid_path,
        "prompt": prompt,
        "aspect_ratio": request.aspect_ratio,
        "duration_seconds": request.duration_seconds,
        "provider_job_id": str(provider_job_id) if provider_job_id else None,
        "video_path": str(video_path) if video_path else None,
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return SameStyleVideoJobResponse(
        **manifest,
        manifest_path=str(manifest_path),
    )


def _video_path_from_provider_result(
    provider_result: dict[str, object],
    workspace: Path,
) -> Path | None:
    media_value = provider_value(
        provider_result,
        [
            "video_path",
            "output_video_path",
            "output.video_path",
            "data.video_path",
            "video_url",
            "output_video_url",
            "url",
            "output.video_url",
            "content.video_url",
            "data.video_url",
            "data.output.video_url",
            "data.content.video_url",
            "data.result.video_url",
        ],
    )
    return download_provider_media(media_value, workspace, "seedance_video.mp4")


def _build_seedance_payload(
    *,
    settings: Settings,
    reference_grid_path: Path,
    prompt: str,
    aspect_ratio: str,
    duration_seconds: int,
    workspace: Path,
) -> dict[str, object]:
    if _is_ark_seedance_url(settings.seedance_base_url):
        return {
            "model": settings.seedance_model,
            "content": [
                {"type": "text", "text": prompt},
                {
                    "type": "image_url",
                    "role": "first_frame",
                    "image_url": {"url": _image_data_url(reference_grid_path)},
                },
            ],
            "ratio": aspect_ratio,
            "duration": duration_seconds,
            "generate_audio": False,
        }
    return {
        "model": settings.seedance_model,
        "prompt": prompt,
        "aspect_ratio": aspect_ratio,
        "duration_seconds": duration_seconds,
        "reference_image_path": str(reference_grid_path),
        "workspace_path": str(workspace),
    }


def _is_ark_seedance_url(value: str | None) -> bool:
    if not value:
        return False
    parsed = urlparse(value)
    return parsed.netloc.endswith("volces.com") or parsed.netloc.endswith("byteplus.com")


def _image_data_url(path: Path) -> str:
    mime_type = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
    }.get(path.suffix.lower(), "image/jpeg")
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def _prompt_from_reference_job(reference_job: object) -> str:
    script_draft = getattr(reference_job, "script_draft", None)
    if script_draft is None:
        return "保留对标视频的镜头节奏和视觉结构，生成自有产品版本。"
    section_text = "；".join(
        section.content for section in getattr(script_draft, "sections", [])[:4]
    )
    return (
        f"{script_draft.hook}。{section_text}"
        " 保留对标视频的镜头节奏和视觉结构，不复制原视频素材。"
    )


def _video_job_workspace(settings: Settings, job_id: str) -> Path:
    return Path(settings.draft_output_path).expanduser() / "_same_style_video_jobs" / job_id


def _new_video_job_id() -> str:
    return f"same_style_video_{uuid.uuid4().hex[:12]}"
