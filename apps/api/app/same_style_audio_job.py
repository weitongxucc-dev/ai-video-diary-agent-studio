from __future__ import annotations

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
    raise_for_status_with_body,
)
from app.schemas import SameStyleAudioJobRequest, SameStyleAudioJobResponse


COSYVOICE_MODULE = "cosyvoice_adapter"
SUPPORTED_REFERENCE_AUDIO_EXTENSIONS = {".wav", ".mp3", ".m4a", ".aac", ".flac"}


class CosyVoiceGenerator(Protocol):
    def submit(
        self,
        *,
        text: str,
        voice_id: str,
        reference_audio_path: Path | None,
        speed: float,
        workspace: Path,
    ) -> dict[str, object]:
        ...


class CosyVoiceHttpGenerator:
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
        text: str,
        voice_id: str,
        reference_audio_path: Path | None,
        speed: float,
        workspace: Path,
    ) -> dict[str, object]:
        if not self._settings.cosyvoice_base_url:
            raise RuntimeError("COSYVOICE_BASE_URL is required")

        payload = _build_cosyvoice_payload(
            settings=self._settings,
            text=text,
            voice_id=voice_id,
            reference_audio_path=reference_audio_path,
            speed=speed,
            workspace=workspace,
        )
        headers = {}
        if self._settings.cosyvoice_api_key:
            headers["Authorization"] = f"Bearer {self._settings.cosyvoice_api_key}"

        with httpx.Client(transport=self._transport, timeout=self._timeout) as client:
            response = client.post(
                self._settings.cosyvoice_base_url,
                json=payload,
                headers=headers,
            )
        raise_for_status_with_body(response)
        data = response.json()
        audio_url = provider_value(
            data,
            [
                "audio_url",
                "voice_url",
                "url",
                "output.audio.url",
                "data.output.audio.url",
                "data.audio.url",
            ],
        )
        audio_path = provider_value(
            data,
            [
                "audio_path",
                "voice_path",
                "output.audio.path",
                "data.output.audio.path",
                "data.audio.path",
            ],
        )
        return {
            "provider_job_id": provider_value(
                data,
                [
                    "id",
                    "task_id",
                    "job_id",
                    "request_id",
                    "output.audio.id",
                    "data.output.audio.id",
                ],
            ),
            "status": data.get("status")
            or ("completed" if audio_path or audio_url else "submitted"),
            "message": data.get("message") or "CosyVoice task submitted",
            "audio_path": audio_path,
            "audio_url": audio_url,
            "duration_seconds": data.get("duration_seconds"),
        }


def create_same_style_audio_job(
    settings: Settings,
    request: SameStyleAudioJobRequest,
    *,
    generator: CosyVoiceGenerator | None = None,
    job_id_factory: Callable[[], str] | None = None,
) -> SameStyleAudioJobResponse:
    job_id = (job_id_factory or _new_audio_job_id)()
    workspace = _audio_job_workspace(settings, job_id)
    manifest_path = workspace / "audio_job_manifest.json"

    try:
        reference_job = read_reference_job(settings, request.reference_job_id)
    except FileNotFoundError:
        return _write_audio_job_manifest(
            manifest_path,
            job_id=job_id,
            status="failed",
            message="对标参考任务不存在，无法创建音频任务。",
            request=request,
            text=request.text,
            voice_id=_voice_id(settings, request),
        )

    text = request.text.strip() or _script_text_from_reference_job(reference_job)
    voice_id = _voice_id(settings, request)
    try:
        reference_audio_path = _resolve_reference_audio_path(
            settings,
            request.reference_audio_path,
        )
    except ValueError as exc:
        return _write_audio_job_manifest(
            manifest_path,
            job_id=job_id,
            status="failed",
            message=f"参考音频不可用：{exc}",
            request=request,
            text=text,
            voice_id=voice_id,
            reference_audio_path=None,
        )
    if not text:
        return _write_audio_job_manifest(
            manifest_path,
            job_id=job_id,
            status="failed",
            message="对标任务缺少仿写文案，请先完成文案生成。",
            request=request,
            text=text,
            voice_id=voice_id,
            reference_audio_path=reference_audio_path,
        )

    if generator is None and not settings.cosyvoice_base_url:
        return _write_audio_job_manifest(
            manifest_path,
            job_id=job_id,
            status="configuration_required",
            message="CosyVoice 未配置；请设置 COSYVOICE_BASE_URL 后再生成口播音频。",
            request=request,
            text=text,
            voice_id=voice_id,
            reference_audio_path=reference_audio_path,
        )

    active_generator = generator or CosyVoiceHttpGenerator(settings)
    workspace.mkdir(parents=True, exist_ok=True)
    try:
        provider_result = active_generator.submit(
            text=text,
            voice_id=voice_id,
            reference_audio_path=reference_audio_path,
            speed=request.speed,
            workspace=workspace,
        )
    except Exception as exc:
        return _write_audio_job_manifest(
            manifest_path,
            job_id=job_id,
            status="failed",
            message=f"CosyVoice 音频生成失败：{exc}",
            request=request,
            text=text,
            voice_id=voice_id,
            reference_audio_path=reference_audio_path,
        )

    status = str(provider_result.get("status") or "submitted")
    if status not in {"submitted", "completed", "failed"}:
        status = normalize_provider_status(status)
    try:
        audio_path = _audio_path_from_provider_result(provider_result, workspace)
    except Exception:
        audio_path = None
    if status == "completed" and audio_path is None:
        status = "submitted"
    return _write_audio_job_manifest(
        manifest_path,
        job_id=job_id,
        status=status,
        message=str(provider_result.get("message") or "CosyVoice task submitted"),
        request=request,
        text=text,
        voice_id=voice_id,
        reference_audio_path=reference_audio_path,
        provider_job_id=provider_result.get("provider_job_id"),
        audio_path=audio_path,
        duration_seconds=provider_result.get("duration_seconds"),
    )


def read_same_style_audio_job(
    settings: Settings,
    job_id: str,
    *,
    status_fetcher: Callable[[str, Path], dict[str, object] | None] | None = None,
) -> SameStyleAudioJobResponse:
    manifest_path = _audio_job_workspace(settings, job_id) / "audio_job_manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError("same style audio job manifest not found")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest = _refresh_audio_manifest_if_needed(
        settings,
        manifest,
        manifest_path,
        status_fetcher=status_fetcher,
    )
    return SameStyleAudioJobResponse(
        id=str(manifest["id"]),
        module_name=str(manifest["module_name"]),
        status=manifest["status"],
        message=str(manifest["message"]),
        reference_job_id=str(manifest["reference_job_id"]),
        text=str(manifest["text"]),
        voice_id=str(manifest["voice_id"]),
        reference_audio_path=manifest.get("reference_audio_path"),
        speed=float(manifest["speed"]),
        provider_job_id=manifest.get("provider_job_id"),
        audio_path=manifest.get("audio_path"),
        duration_seconds=manifest.get("duration_seconds"),
        manifest_path=str(manifest_path),
    )


def _refresh_audio_manifest_if_needed(
    settings: Settings,
    manifest: dict[str, object],
    manifest_path: Path,
    *,
    status_fetcher: Callable[[str, Path], dict[str, object] | None] | None,
) -> dict[str, object]:
    if manifest.get("status") != "submitted" or manifest.get("audio_path"):
        return manifest
    provider_job_id = manifest.get("provider_job_id")
    if not provider_job_id:
        return manifest
    workspace = manifest_path.parent
    if status_fetcher is None:
        def status_fetcher(provider_job_id: str, workspace: Path) -> dict[str, object] | None:
            return fetch_provider_status(
                status_url_template=settings.cosyvoice_status_url_template,
                provider_job_id=provider_job_id,
                api_key=settings.cosyvoice_api_key,
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
        audio_path = _audio_path_from_provider_result(provider_result, workspace)
    except Exception:
        audio_path = None
    if status == "completed" and audio_path is None:
        status = "submitted"
    manifest["status"] = status
    manifest["message"] = str(
        provider_value(provider_result, ["message", "msg"]) or manifest.get("message") or ""
    )
    if audio_path:
        manifest["audio_path"] = str(audio_path)
    duration_seconds = provider_value(provider_result, ["duration_seconds", "duration"])
    if duration_seconds is not None:
        manifest["duration_seconds"] = float(duration_seconds)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def _write_audio_job_manifest(
    manifest_path: Path,
    *,
    job_id: str,
    status: str,
    message: str,
    request: SameStyleAudioJobRequest,
    text: str,
    voice_id: str,
    reference_audio_path: Path | str | None = None,
    provider_job_id: object | None = None,
    audio_path: object | None = None,
    duration_seconds: object | None = None,
) -> SameStyleAudioJobResponse:
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest = {
        "id": job_id,
        "module_name": COSYVOICE_MODULE,
        "status": status,
        "message": message,
        "reference_job_id": request.reference_job_id,
        "text": text,
        "voice_id": voice_id,
        "reference_audio_path": str(reference_audio_path) if reference_audio_path else None,
        "speed": request.speed,
        "provider_job_id": str(provider_job_id) if provider_job_id else None,
        "audio_path": str(audio_path) if audio_path else None,
        "duration_seconds": float(duration_seconds)
        if duration_seconds is not None
        else None,
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return SameStyleAudioJobResponse(**manifest, manifest_path=str(manifest_path))


def _audio_path_from_provider_result(
    provider_result: dict[str, object],
    workspace: Path,
) -> Path | None:
    media_value = provider_value(
        provider_result,
        [
            "audio_path",
            "voice_path",
            "output.audio_path",
            "data.audio_path",
            "audio_url",
            "voice_url",
            "url",
            "output.audio_url",
            "output.audio.url",
            "data.audio_url",
            "data.audio.url",
            "data.output.audio.url",
        ],
    )
    return download_provider_media(media_value, workspace, "voice.wav")


def _build_cosyvoice_payload(
    *,
    settings: Settings,
    text: str,
    voice_id: str,
    reference_audio_path: Path | None,
    speed: float,
    workspace: Path,
) -> dict[str, object]:
    if _is_dashscope_cosyvoice_url(settings.cosyvoice_base_url):
        return {
            "model": settings.cosyvoice_model,
            "input": {
                "text": text,
                "voice": voice_id,
                "format": "wav",
                "sample_rate": 24000,
            },
        }
    return {
        "text": text,
        "voice_id": voice_id,
        "reference_audio_path": str(reference_audio_path) if reference_audio_path else None,
        "speed": speed,
        "workspace_path": str(workspace),
    }


def _is_dashscope_cosyvoice_url(value: str | None) -> bool:
    if not value:
        return False
    parsed = urlparse(value)
    return parsed.netloc.endswith("aliyuncs.com") or parsed.path.endswith(
        "/services/audio/tts/SpeechSynthesizer"
    )


def _script_text_from_reference_job(reference_job: object) -> str:
    script_draft = getattr(reference_job, "script_draft", None)
    if script_draft is None:
        return ""
    lines = [script_draft.hook]
    lines.extend(section.content for section in getattr(script_draft, "sections", []))
    return "\n".join(line.strip() for line in lines if line.strip())


def _voice_id(settings: Settings, request: SameStyleAudioJobRequest) -> str:
    return request.voice_id.strip() or settings.cosyvoice_voice_id or "default"


def _resolve_reference_audio_path(settings: Settings, value: str) -> Path | None:
    audio_value = value.strip()
    if not audio_value:
        return None
    raw_path = Path(audio_value).expanduser()
    roots = [
        Path(settings.video_inbox_path).expanduser().resolve(),
        Path(settings.video_output_path).expanduser().resolve(),
        Path(settings.draft_output_path).expanduser().resolve(),
    ]
    candidates = [raw_path] if raw_path.is_absolute() else []
    if not raw_path.is_absolute():
        candidates.extend(root / raw_path for root in roots)
    for candidate in candidates:
        resolved = candidate.resolve()
        if (
            resolved.is_file()
            and resolved.suffix.lower() in SUPPORTED_REFERENCE_AUDIO_EXTENSIONS
            and any(resolved.is_relative_to(root) for root in roots)
        ):
            return resolved
    raise ValueError("reference audio must be a WAV, MP3, M4A, AAC, or FLAC under the media directories")


def _audio_job_workspace(settings: Settings, job_id: str) -> Path:
    return Path(settings.draft_output_path).expanduser() / "_same_style_audio_jobs" / job_id


def _new_audio_job_id() -> str:
    return f"same_style_audio_{uuid.uuid4().hex[:12]}"
