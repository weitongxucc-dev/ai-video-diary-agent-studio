from __future__ import annotations

import json
import shutil
import subprocess
import sys
import uuid
from collections.abc import Callable
from importlib import util as importlib_util
from pathlib import Path

from app.asr import (
    ASRConfigurationError,
    ASRTimeoutError,
    ASRTranscriber,
    ASRTranscriptionError,
    transcription_to_srt,
)
from app.config import Settings
from app.media_commands import (
    build_extract_audio_command,
    build_extract_reference_frames_command,
)
from app.schemas import (
    SameStyleReferenceJobRequest,
    SameStyleReferenceJobResponse,
)
from app.same_style_script_generator import (
    SameStyleScriptGenerator,
    TemplateSameStyleScriptGenerator,
    same_style_script_generator_from_settings,
)
from app.same_style_reference_images import (
    build_product_reference_grid_image,
    build_reference_grid_image,
)
from app.video import SUPPORTED_VIDEO_EXTENSIONS


REFERENCE_IMPORTER_MODULE = "reference_video_importer"
ReferenceVideoDownloader = Callable[[str, Path], Path | None]
SUPPORTED_PRODUCT_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


def create_reference_job(
    settings: Settings,
    request: SameStyleReferenceJobRequest,
    *,
    asr_transcriber: ASRTranscriber,
    runner: Callable[[list[str]], None],
    downloader: ReferenceVideoDownloader | None = None,
    script_generator: SameStyleScriptGenerator | None = None,
    job_id_factory: Callable[[], str] | None = None,
) -> SameStyleReferenceJobResponse:
    job_id = (job_id_factory or _new_job_id)()
    reference_url = request.reference_url.strip()
    workspace = _job_workspace(settings, job_id)
    manifest_path = workspace / "reference_manifest.json"
    if _is_external_url(reference_url):
        workspace.mkdir(parents=True, exist_ok=True)
        active_downloader = downloader or _default_reference_downloader(settings)
        try:
            source_video = active_downloader(reference_url, workspace)
        except Exception as exc:
            return _failed_reference_job(
                job_id=job_id,
                request=request,
                reference_url=reference_url,
                workspace=workspace,
                manifest_path=manifest_path,
                source_type="external_url",
                source_video=None,
                message=f"参考视频下载失败：{exc}",
            )
        if source_video is None:
            message = "外部视频链接下载器待接入；安装 yt-dlp 后可自动下载，或先把视频放入视频待处理目录。"
            _write_reference_manifest(
                manifest_path,
                {
                    "id": job_id,
                    "module_name": REFERENCE_IMPORTER_MODULE,
                    "status": "download_required",
                    "message": message,
                    "request": request.model_dump(),
                    "source": {
                        "type": "external_url",
                        "reference_url": reference_url,
                        "local_video_path": None,
                        "downloaded_video_path": None,
                    },
                    "artifacts": {
                        "workspace_path": str(workspace),
                        "audio_path": None,
                        "subtitle_path": None,
                        "reference_grid_path": None,
                        "product_image_path": None,
                        "product_reference_grid_path": None,
                    },
                    "transcription": None,
                    "shot_manifest": [],
                    "reference_frames": [],
                    "script_draft": None,
                    "rewrite_brief": _rewrite_brief(request, None),
                },
            )
            return SameStyleReferenceJobResponse(
                id=job_id,
                module_name=REFERENCE_IMPORTER_MODULE,
                status="download_required",
                message=message,
                source_type="external_url",
                reference_url=reference_url,
                workspace_path=str(workspace),
                manifest_path=str(manifest_path),
            )
        source_type = "external_url"
    else:
        try:
            source_video = _resolve_local_reference_video(settings, reference_url)
        except ValueError as exc:
            return _failed_reference_job(
                job_id=job_id,
                request=request,
                reference_url=reference_url,
                workspace=workspace,
                manifest_path=manifest_path,
                source_type="local_video",
                source_video=None,
                message=f"参考视频不可用：{exc}",
            )
        source_type = "local_video"

    workspace.mkdir(parents=True, exist_ok=True)
    try:
        product_image_path = _resolve_product_image(settings, request.product_image_path)
    except ValueError as exc:
        return _failed_reference_job(
            job_id=job_id,
            request=request,
            reference_url=reference_url,
            workspace=workspace,
            manifest_path=manifest_path,
            source_type=source_type,
            source_video=source_video,
            message=f"产品参考图不可用：{exc}",
        )
    audio_path = workspace / "audio_16k.wav"
    subtitle_path = workspace / "subtitle.srt"
    frames_dir = workspace / "reference_frames"
    frame_pattern = frames_dir / "frame_%04d.jpg"
    frames_dir.mkdir(parents=True, exist_ok=True)

    try:
        runner(build_extract_audio_command(source_video, audio_path))
    except Exception as exc:
        return _failed_reference_job(
            job_id=job_id,
            request=request,
            reference_url=reference_url,
            workspace=workspace,
            manifest_path=manifest_path,
            source_type=source_type,
            source_video=source_video,
            message=f"参考视频抽音频失败：{exc}",
        )
    try:
        runner(build_extract_reference_frames_command(source_video, frame_pattern))
    except Exception as exc:
        return _failed_reference_job(
            job_id=job_id,
            request=request,
            reference_url=reference_url,
            workspace=workspace,
            manifest_path=manifest_path,
            source_type=source_type,
            source_video=source_video,
            message=f"参考视频抽帧失败：{exc}",
        )
    reference_frames = _reference_frames_manifest(frames_dir)
    reference_grid_path = _build_reference_grid_if_possible(
        reference_frames,
        workspace / "reference_grid.jpg",
    )
    product_reference_grid_path = _build_product_reference_grid_if_possible(
        product_image_path,
        reference_frames,
        workspace / "product_reference_grid.jpg",
    )
    transcription_payload = None
    transcript = None
    shot_manifest: list[dict[str, object]] = []
    script_draft = None
    status = "audio_ready"
    message = "已抽取参考视频音频，等待 ASR 配置后可生成对标文案。"

    if asr_transcriber.configured:
        try:
            transcription = asr_transcriber.transcribe(audio_path)
        except (ASRConfigurationError, ASRTimeoutError, ASRTranscriptionError) as exc:
            message = f"已抽取音频，但 ASR 未完成：{exc}"
        else:
            subtitle_path.write_text(transcription_to_srt(transcription), encoding="utf-8")
            transcript = transcription.text
            transcription_payload = {
                "provider": transcription.provider,
                "text": transcription.text,
                "segments": [
                    {
                        "start_ms": segment.start_ms,
                        "end_ms": segment.end_ms,
                        "text": segment.text,
                    }
                    for segment in transcription.segments
                ],
            }
            shot_manifest = [
                {
                    "start_ms": segment.start_ms,
                    "end_ms": segment.end_ms,
                    "text": segment.text,
                    "purpose": "口播节奏参考",
                }
                for segment in transcription.segments
            ]
            active_script_generator = (
                script_generator or same_style_script_generator_from_settings(settings)
            )
            try:
                script_draft = active_script_generator.generate(
                    request,
                    transcript=transcription.text,
                    shot_manifest=shot_manifest,
                )
            except Exception as exc:
                script_draft = TemplateSameStyleScriptGenerator().generate(
                    request,
                    transcript=transcription.text,
                    shot_manifest=shot_manifest,
                )
                message = (
                    f"已抽取音频并完成对标口播转写，但大模型仿写未完成：{exc}。"
                    "已生成可编辑兜底草案。"
                )
            else:
                message = "已抽取音频并完成对标口播转写。"
            status = "transcribed"

    manifest = {
        "id": job_id,
        "module_name": REFERENCE_IMPORTER_MODULE,
        "status": status,
        "message": message,
        "request": request.model_dump(),
        "source": {
            "type": source_type,
            "reference_url": reference_url,
            "local_video_path": str(source_video) if source_type == "local_video" else None,
            "downloaded_video_path": str(source_video) if source_type == "external_url" else None,
        },
        "artifacts": {
            "workspace_path": str(workspace),
            "audio_path": str(audio_path),
            "subtitle_path": str(subtitle_path) if subtitle_path.exists() else None,
            "reference_grid_path": reference_grid_path,
            "product_image_path": str(product_image_path) if product_image_path else None,
            "product_reference_grid_path": product_reference_grid_path,
        },
        "transcription": transcription_payload,
        "shot_manifest": shot_manifest,
        "reference_frames": reference_frames,
        "script_draft": script_draft,
        "rewrite_brief": _rewrite_brief(request, transcript),
    }
    _write_reference_manifest(manifest_path, manifest)

    return SameStyleReferenceJobResponse(
        id=job_id,
        module_name=REFERENCE_IMPORTER_MODULE,
        status=status,
        message=message,
        source_type=source_type,
        reference_url=reference_url,
        local_video_path=str(source_video),
        workspace_path=str(workspace),
        audio_path=str(audio_path),
        subtitle_path=str(subtitle_path) if subtitle_path.exists() else None,
        reference_grid_path=reference_grid_path,
        product_image_path=str(product_image_path) if product_image_path else None,
        product_reference_grid_path=product_reference_grid_path,
        manifest_path=str(manifest_path),
        transcript=transcript,
        shot_manifest=shot_manifest,
        reference_frames=reference_frames,
        script_draft=script_draft,
    )


def build_same_style_script_draft(
    request: SameStyleReferenceJobRequest,
    *,
    transcript: str,
    shot_manifest: list[dict[str, object]],
) -> dict[str, object]:
    return TemplateSameStyleScriptGenerator().generate(
        request,
        transcript=transcript,
        shot_manifest=shot_manifest,
    )


def _failed_reference_job(
    *,
    job_id: str,
    request: SameStyleReferenceJobRequest,
    reference_url: str,
    workspace: Path,
    manifest_path: Path,
    source_type: str,
    source_video: Path | None,
    message: str,
) -> SameStyleReferenceJobResponse:
    workspace.mkdir(parents=True, exist_ok=True)
    source_video_path = str(source_video) if source_video is not None else None
    _write_reference_manifest(
        manifest_path,
        {
            "id": job_id,
            "module_name": REFERENCE_IMPORTER_MODULE,
            "status": "failed",
            "message": message,
            "request": request.model_dump(),
            "source": {
                "type": source_type,
                "reference_url": reference_url,
                "local_video_path": source_video_path
                if source_type == "local_video"
                else None,
                "downloaded_video_path": source_video_path
                if source_type == "external_url"
                else None,
            },
            "artifacts": {
                "workspace_path": str(workspace),
                "audio_path": None,
                "subtitle_path": None,
                "reference_grid_path": None,
                "product_image_path": None,
                "product_reference_grid_path": None,
            },
            "transcription": None,
            "shot_manifest": [],
            "reference_frames": [],
            "script_draft": None,
            "rewrite_brief": _rewrite_brief(request, None),
        },
    )
    return SameStyleReferenceJobResponse(
        id=job_id,
        module_name=REFERENCE_IMPORTER_MODULE,
        status="failed",
        message=message,
        source_type=source_type,
        reference_url=reference_url,
        local_video_path=source_video_path,
        workspace_path=str(workspace),
        manifest_path=str(manifest_path),
    )


def read_reference_job(
    settings: Settings,
    job_id: str,
) -> SameStyleReferenceJobResponse:
    manifest_path = _job_workspace(settings, job_id) / "reference_manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError("same style reference job manifest not found")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    transcription = manifest.get("transcription")
    source = manifest.get("source") if isinstance(manifest.get("source"), dict) else {}
    artifacts = manifest.get("artifacts") if isinstance(manifest.get("artifacts"), dict) else {}
    return SameStyleReferenceJobResponse(
        id=str(manifest["id"]),
        module_name=str(manifest["module_name"]),
        status=manifest["status"],
        message=str(manifest["message"]),
        source_type=source.get("type") or "local_video",
        reference_url=str(source.get("reference_url") or ""),
        local_video_path=source.get("local_video_path") or source.get("downloaded_video_path"),
        workspace_path=artifacts.get("workspace_path"),
        audio_path=artifacts.get("audio_path"),
        subtitle_path=artifacts.get("subtitle_path"),
        reference_grid_path=artifacts.get("reference_grid_path"),
        product_image_path=artifacts.get("product_image_path"),
        product_reference_grid_path=artifacts.get("product_reference_grid_path"),
        manifest_path=str(manifest_path),
        transcript=transcription.get("text") if isinstance(transcription, dict) else None,
        shot_manifest=manifest.get("shot_manifest") or [],
        reference_frames=manifest.get("reference_frames") or [],
        script_draft=manifest.get("script_draft"),
    )


def _write_reference_manifest(manifest_path: Path, manifest: dict[str, object]) -> None:
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _default_reference_downloader(settings: Settings) -> ReferenceVideoDownloader:
    try:
        return YtDlpReferenceDownloader(cookies_path=settings.yt_dlp_cookies_path)
    except TypeError:
        return YtDlpReferenceDownloader()


class YtDlpReferenceDownloader:
    def __init__(
        self,
        *,
        executable_lookup: Callable[[str], str | None] = shutil.which,
        module_lookup: Callable[[str], bool] | None = None,
        python_executable: str = sys.executable,
        cookies_path: str | None = None,
        runner: Callable[[list[str]], None] | None = None,
    ) -> None:
        self._executable_lookup = executable_lookup
        self._module_lookup = module_lookup or _python_module_available
        self._python_executable = python_executable
        self._cookies_path = cookies_path
        self._runner = runner or self._run_command

    def __call__(self, reference_url: str, workspace: Path) -> Path | None:
        executable = self._executable_lookup("yt-dlp")
        if executable is None and not self._module_lookup("yt_dlp"):
            return None
        workspace.mkdir(parents=True, exist_ok=True)
        output_template = workspace / "reference.%(ext)s"
        executable_command = [executable] if executable else [self._python_executable, "-m", "yt_dlp"]
        cookie_args: list[str] = []
        if self._cookies_path:
            cookie_path = Path(self._cookies_path).expanduser()
            if cookie_path.is_file():
                cookie_args = ["--cookies", str(cookie_path)]
        self._runner(
            [
                *executable_command,
                "--no-playlist",
                "--restrict-filenames",
                *cookie_args,
                "-f",
                "bv*+ba/b",
                "--merge-output-format",
                "mp4",
                "-o",
                str(output_template),
                reference_url,
            ]
        )
        return _downloaded_video_from_workspace(workspace)

    def _run_command(self, command: list[str]) -> None:
        subprocess.run(command, check=True)


def _new_job_id() -> str:
    return f"same_style_{uuid.uuid4().hex[:12]}"


def _is_external_url(value: str) -> bool:
    return value.startswith(("http://", "https://"))


def _python_module_available(module_name: str) -> bool:
    return importlib_util.find_spec(module_name) is not None


def _job_workspace(settings: Settings, job_id: str) -> Path:
    return Path(settings.draft_output_path).expanduser() / "_same_style_jobs" / job_id


def _resolve_local_reference_video(settings: Settings, value: str) -> Path:
    raw_path = Path(value).expanduser()
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
            and resolved.suffix.lower() in SUPPORTED_VIDEO_EXTENSIONS
            and any(resolved.is_relative_to(root) for root in roots)
        ):
            return resolved
    raise ValueError("reference video must be a supported local file under the video inbox, video output, or draft output directory")


def _resolve_product_image(settings: Settings, value: str) -> Path | None:
    image_value = value.strip()
    if not image_value:
        return None
    raw_path = Path(image_value).expanduser()
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
            and resolved.suffix.lower() in SUPPORTED_PRODUCT_IMAGE_EXTENSIONS
            and any(resolved.is_relative_to(root) for root in roots)
        ):
            return resolved
    raise ValueError("product image must be a JPG, PNG, or WEBP under the video inbox, video output, or draft output directory")


def _downloaded_video_from_workspace(workspace: Path) -> Path | None:
    for candidate in sorted(workspace.iterdir()):
        if candidate.is_file() and candidate.suffix.lower() in SUPPORTED_VIDEO_EXTENSIONS:
            return candidate.resolve()
    return None


def _reference_frames_manifest(frames_dir: Path) -> list[dict[str, object]]:
    frames = sorted(frames_dir.glob("frame_*.jpg"))
    return [
        {
            "index": index,
            "path": str(frame.resolve()),
            "purpose": "视觉风格参考",
        }
        for index, frame in enumerate(frames, start=1)
    ]


def _build_reference_grid_if_possible(
    reference_frames: list[dict[str, object]],
    output_path: Path,
) -> str | None:
    if not reference_frames:
        return None
    frame_paths = [Path(str(frame["path"])) for frame in reference_frames]
    try:
        return str(build_reference_grid_image(frame_paths, output_path, columns=4))
    except Exception:
        return None


def _build_product_reference_grid_if_possible(
    product_image_path: Path | None,
    reference_frames: list[dict[str, object]],
    output_path: Path,
) -> str | None:
    if product_image_path is None:
        return None
    frame_paths = [Path(str(frame["path"])) for frame in reference_frames]
    try:
        return str(
            build_product_reference_grid_image(
                product_image_path,
                frame_paths,
                output_path,
                columns=4,
            )
        )
    except Exception:
        return None


def _rewrite_brief(
    request: SameStyleReferenceJobRequest,
    transcript: str | None,
) -> dict[str, object]:
    return {
        "product_name": request.product_name,
        "product_brief": request.product_brief,
        "aspect_ratio": request.aspect_ratio,
        "rewrite_mode": request.rewrite_mode,
        "benchmark_transcript": transcript or "",
        "instruction": "保留对标视频的节奏、镜头结构和转化逻辑，替换为自有产品表达。",
    }
