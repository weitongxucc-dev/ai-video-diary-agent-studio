from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

import httpx

from app.config import Settings
from app.same_style_audio_job import read_same_style_audio_job
from app.same_style_provider_tasks import (
    download_provider_media,
    fetch_provider_status,
    normalize_provider_status,
    provider_value,
)
from app.schemas import (
    SameStyleDigitalHumanJobRequest,
    SameStyleDigitalHumanJobResponse,
)


DIGITAL_HUMAN_MODULE = "heygem_digital_human_job"


class DigitalHumanGenerator(Protocol):
    def submit(
        self,
        *,
        audio_path: Path,
        avatar_id: str,
        aspect_ratio: str,
        workspace: Path,
    ) -> dict[str, object]:
        ...


class HeyGemHttpDigitalHumanGenerator:
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
        audio_path: Path,
        avatar_id: str,
        aspect_ratio: str,
        workspace: Path,
    ) -> dict[str, object]:
        if not self._settings.heygem_base_url:
            raise RuntimeError("HEYGEM_BASE_URL is required")

        payload = {
            "audio_path": str(audio_path),
            "avatar_id": avatar_id,
            "aspect_ratio": aspect_ratio,
            "workspace_path": str(workspace),
        }
        headers = {}
        if self._settings.heygem_api_key:
            headers["Authorization"] = f"Bearer {self._settings.heygem_api_key}"

        with httpx.Client(transport=self._transport, timeout=self._timeout) as client:
            response = client.post(
                self._settings.heygem_base_url,
                json=payload,
                headers=headers,
            )
        response.raise_for_status()
        data = response.json()
        video_path = data.get("video_path") or data.get("output_video_path")
        return {
            "provider_job_id": data.get("id") or data.get("task_id") or data.get("job_id"),
            "status": data.get("status") or ("completed" if video_path else "submitted"),
            "message": data.get("message") or "HeyGem digital human task submitted",
            "video_path": video_path,
            "video_url": data.get("video_url") or data.get("output_video_url") or data.get("url"),
        }


def create_same_style_digital_human_job(
    settings: Settings,
    request: SameStyleDigitalHumanJobRequest,
    *,
    generator: DigitalHumanGenerator | None = None,
    job_id_factory: Callable[[], str] | None = None,
) -> SameStyleDigitalHumanJobResponse:
    job_id = (job_id_factory or _new_digital_human_job_id)()
    workspace = _digital_human_job_workspace(settings, job_id)
    manifest_path = workspace / "digital_human_job_manifest.json"
    avatar_id = _avatar_id(settings, request)

    try:
        audio_job = read_same_style_audio_job(settings, request.audio_job_id)
    except FileNotFoundError:
        return _write_digital_human_job_manifest(
            manifest_path,
            job_id=job_id,
            status="failed",
            message="音频任务不存在，无法创建数字人口播视频任务。",
            request=request,
            audio_path=None,
            avatar_id=avatar_id,
        )

    audio_path = (
        Path(audio_job.audio_path).expanduser() if audio_job.audio_path else None
    )
    if audio_path is None or not audio_path.is_file():
        return _write_digital_human_job_manifest(
            manifest_path,
            job_id=job_id,
            status="failed",
            message="缺少口播音频，请先完成音频生成。",
            request=request,
            audio_path=str(audio_path) if audio_path else None,
            avatar_id=avatar_id,
        )

    if generator is None and not settings.heygem_base_url:
        return _write_digital_human_job_manifest(
            manifest_path,
            job_id=job_id,
            status="configuration_required",
            message="HeyGem/Duix 数字人服务未配置；请设置 HEYGEM_BASE_URL 后再生成口播视频。",
            request=request,
            audio_path=str(audio_path),
            avatar_id=avatar_id,
        )

    active_generator = generator or HeyGemHttpDigitalHumanGenerator(settings)
    workspace.mkdir(parents=True, exist_ok=True)
    try:
        provider_result = active_generator.submit(
            audio_path=audio_path,
            avatar_id=avatar_id,
            aspect_ratio=request.aspect_ratio,
            workspace=workspace,
        )
    except Exception as exc:
        return _write_digital_human_job_manifest(
            manifest_path,
            job_id=job_id,
            status="failed",
            message=f"HeyGem/Duix 数字人口播视频任务提交失败：{exc}",
            request=request,
            audio_path=str(audio_path),
            avatar_id=avatar_id,
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
    return _write_digital_human_job_manifest(
        manifest_path,
        job_id=job_id,
        status=status,
        message=str(provider_result.get("message") or "HeyGem digital human task submitted"),
        request=request,
        audio_path=str(audio_path),
        avatar_id=avatar_id,
        provider_job_id=provider_result.get("provider_job_id"),
        video_path=video_path,
    )


def read_same_style_digital_human_job(
    settings: Settings,
    job_id: str,
    *,
    status_fetcher: Callable[[str, Path], dict[str, object] | None] | None = None,
) -> SameStyleDigitalHumanJobResponse:
    manifest_path = (
        _digital_human_job_workspace(settings, job_id)
        / "digital_human_job_manifest.json"
    )
    if not manifest_path.is_file():
        raise FileNotFoundError("same style digital human job manifest not found")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest = _refresh_digital_human_manifest_if_needed(
        settings,
        manifest,
        manifest_path,
        status_fetcher=status_fetcher,
    )
    return SameStyleDigitalHumanJobResponse(
        id=str(manifest["id"]),
        module_name=str(manifest["module_name"]),
        status=manifest["status"],
        message=str(manifest["message"]),
        audio_job_id=str(manifest["audio_job_id"]),
        audio_path=manifest.get("audio_path"),
        avatar_id=str(manifest["avatar_id"]),
        aspect_ratio=manifest["aspect_ratio"],
        provider_job_id=manifest.get("provider_job_id"),
        video_path=manifest.get("video_path"),
        manifest_path=str(manifest_path),
    )


def _refresh_digital_human_manifest_if_needed(
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
                status_url_template=settings.heygem_status_url_template,
                provider_job_id=provider_job_id,
                api_key=settings.heygem_api_key,
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


def _write_digital_human_job_manifest(
    manifest_path: Path,
    *,
    job_id: str,
    status: str,
    message: str,
    request: SameStyleDigitalHumanJobRequest,
    audio_path: str | None,
    avatar_id: str,
    provider_job_id: object | None = None,
    video_path: object | None = None,
) -> SameStyleDigitalHumanJobResponse:
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest = {
        "id": job_id,
        "module_name": DIGITAL_HUMAN_MODULE,
        "status": status,
        "message": message,
        "audio_job_id": request.audio_job_id,
        "audio_path": audio_path,
        "avatar_id": avatar_id,
        "aspect_ratio": request.aspect_ratio,
        "provider_job_id": str(provider_job_id) if provider_job_id else None,
        "video_path": str(video_path) if video_path else None,
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return SameStyleDigitalHumanJobResponse(
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
            "data.video_url",
        ],
    )
    return download_provider_media(media_value, workspace, "talking_head.mp4")


def _avatar_id(settings: Settings, request: SameStyleDigitalHumanJobRequest) -> str:
    return request.avatar_id.strip() or settings.heygem_avatar_id or "default"


def _digital_human_job_workspace(settings: Settings, job_id: str) -> Path:
    return (
        Path(settings.draft_output_path).expanduser()
        / "_same_style_digital_human_jobs"
        / job_id
    )


def _new_digital_human_job_id() -> str:
    return f"same_style_digital_{uuid.uuid4().hex[:12]}"
