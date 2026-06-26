from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, HTTPException

from app.media_commands import run_media_command
from app.routers.context import AppContext
from app.same_style_audio_job import create_same_style_audio_job
from app.same_style_audio_job import read_same_style_audio_job
from app.same_style_bgm_job import create_same_style_bgm_job
from app.same_style_bgm_job import read_same_style_bgm_job
from app.same_style_cover_job import create_same_style_cover_job
from app.same_style_cover_job import read_same_style_cover_job
from app.same_style_digital_human_job import create_same_style_digital_human_job
from app.same_style_digital_human_job import read_same_style_digital_human_job
from app.same_style_publish_copy_job import create_same_style_publish_copy_job
from app.same_style_publish_copy_job import read_same_style_publish_copy_job
from app.same_style_publish_job import create_same_style_publish_job
from app.same_style_publish_job import read_same_style_publish_job
from app.same_style_reference import create_reference_job, read_reference_job
from app.same_style_subtitle_job import create_same_style_subtitle_job
from app.same_style_subtitle_job import read_same_style_subtitle_job
from app.same_style_video_job import create_same_style_video_job
from app.same_style_video_job import read_same_style_video_job
from app.same_style_workflow import same_style_workflow_status
from app.schemas import (
    SameStyleAudioJobRequest,
    SameStyleAudioJobResponse,
    SameStyleBgmJobRequest,
    SameStyleBgmJobResponse,
    SameStyleCoverJobRequest,
    SameStyleCoverJobResponse,
    SameStyleDigitalHumanJobRequest,
    SameStyleDigitalHumanJobResponse,
    SameStylePublishCopyJobRequest,
    SameStylePublishCopyJobResponse,
    SameStylePublishJobRequest,
    SameStylePublishJobResponse,
    SameStyleReferenceJobRequest,
    SameStyleReferenceJobResponse,
    SameStyleSubtitleJobRequest,
    SameStyleSubtitleJobResponse,
    SameStyleVideoJobRequest,
    SameStyleVideoJobResponse,
    SameStyleWorkflowResponse,
)


def create_router(get_context: Callable[[], AppContext]) -> APIRouter:
    router = APIRouter()

    @router.get("/api/same-style/workflow", response_model=SameStyleWorkflowResponse)
    def get_same_style_workflow() -> SameStyleWorkflowResponse:
        context = get_context()
        return SameStyleWorkflowResponse.model_validate(
            same_style_workflow_status(context.settings)
        )

    @router.post(
        "/api/same-style/reference-jobs",
        response_model=SameStyleReferenceJobResponse,
    )
    def create_same_style_reference_job(
        request: SameStyleReferenceJobRequest,
    ) -> SameStyleReferenceJobResponse:
        context = get_context()
        return create_reference_job(
            context.settings,
            request,
            asr_transcriber=context.voice_transcriber,
            runner=run_media_command,
        )

    @router.get(
        "/api/same-style/reference-jobs/{job_id}",
        response_model=SameStyleReferenceJobResponse,
    )
    def get_same_style_reference_job(job_id: str) -> SameStyleReferenceJobResponse:
        context = get_context()
        try:
            return read_reference_job(context.settings, job_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="reference job not found") from exc

    @router.post(
        "/api/same-style/audio-jobs",
        response_model=SameStyleAudioJobResponse,
    )
    def create_same_style_audio_generation_job(
        request: SameStyleAudioJobRequest,
    ) -> SameStyleAudioJobResponse:
        context = get_context()
        return create_same_style_audio_job(context.settings, request)

    @router.get(
        "/api/same-style/audio-jobs/{job_id}",
        response_model=SameStyleAudioJobResponse,
    )
    def get_same_style_audio_generation_job(job_id: str) -> SameStyleAudioJobResponse:
        context = get_context()
        try:
            return read_same_style_audio_job(context.settings, job_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="audio job not found") from exc

    @router.post(
        "/api/same-style/digital-human-jobs",
        response_model=SameStyleDigitalHumanJobResponse,
    )
    def create_same_style_digital_human_generation_job(
        request: SameStyleDigitalHumanJobRequest,
    ) -> SameStyleDigitalHumanJobResponse:
        context = get_context()
        return create_same_style_digital_human_job(context.settings, request)

    @router.get(
        "/api/same-style/digital-human-jobs/{job_id}",
        response_model=SameStyleDigitalHumanJobResponse,
    )
    def get_same_style_digital_human_generation_job(
        job_id: str,
    ) -> SameStyleDigitalHumanJobResponse:
        context = get_context()
        try:
            return read_same_style_digital_human_job(context.settings, job_id)
        except FileNotFoundError as exc:
            raise HTTPException(
                status_code=404,
                detail="digital human job not found",
            ) from exc

    @router.post(
        "/api/same-style/subtitle-jobs",
        response_model=SameStyleSubtitleJobResponse,
    )
    def create_same_style_subtitle_generation_job(
        request: SameStyleSubtitleJobRequest,
    ) -> SameStyleSubtitleJobResponse:
        context = get_context()
        return create_same_style_subtitle_job(context.settings, request)

    @router.get(
        "/api/same-style/subtitle-jobs/{job_id}",
        response_model=SameStyleSubtitleJobResponse,
    )
    def get_same_style_subtitle_generation_job(
        job_id: str,
    ) -> SameStyleSubtitleJobResponse:
        context = get_context()
        try:
            return read_same_style_subtitle_job(context.settings, job_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="subtitle job not found") from exc

    @router.post(
        "/api/same-style/bgm-jobs",
        response_model=SameStyleBgmJobResponse,
    )
    def create_same_style_bgm_generation_job(
        request: SameStyleBgmJobRequest,
    ) -> SameStyleBgmJobResponse:
        context = get_context()
        return create_same_style_bgm_job(context.settings, request)

    @router.get(
        "/api/same-style/bgm-jobs/{job_id}",
        response_model=SameStyleBgmJobResponse,
    )
    def get_same_style_bgm_generation_job(job_id: str) -> SameStyleBgmJobResponse:
        context = get_context()
        try:
            return read_same_style_bgm_job(context.settings, job_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="bgm job not found") from exc

    @router.post(
        "/api/same-style/publish-copy-jobs",
        response_model=SameStylePublishCopyJobResponse,
    )
    def create_same_style_publish_copy_generation_job(
        request: SameStylePublishCopyJobRequest,
    ) -> SameStylePublishCopyJobResponse:
        context = get_context()
        return create_same_style_publish_copy_job(context.settings, request)

    @router.get(
        "/api/same-style/publish-copy-jobs/{job_id}",
        response_model=SameStylePublishCopyJobResponse,
    )
    def get_same_style_publish_copy_generation_job(
        job_id: str,
    ) -> SameStylePublishCopyJobResponse:
        context = get_context()
        try:
            return read_same_style_publish_copy_job(context.settings, job_id)
        except FileNotFoundError as exc:
            raise HTTPException(
                status_code=404,
                detail="publish copy job not found",
            ) from exc

    @router.post(
        "/api/same-style/cover-jobs",
        response_model=SameStyleCoverJobResponse,
    )
    def create_same_style_cover_generation_job(
        request: SameStyleCoverJobRequest,
    ) -> SameStyleCoverJobResponse:
        context = get_context()
        return create_same_style_cover_job(context.settings, request)

    @router.get(
        "/api/same-style/cover-jobs/{job_id}",
        response_model=SameStyleCoverJobResponse,
    )
    def get_same_style_cover_generation_job(
        job_id: str,
    ) -> SameStyleCoverJobResponse:
        context = get_context()
        try:
            return read_same_style_cover_job(context.settings, job_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="cover job not found") from exc

    @router.post(
        "/api/same-style/publish-jobs",
        response_model=SameStylePublishJobResponse,
    )
    def create_same_style_publish_generation_job(
        request: SameStylePublishJobRequest,
    ) -> SameStylePublishJobResponse:
        context = get_context()
        return create_same_style_publish_job(context.settings, request)

    @router.get(
        "/api/same-style/publish-jobs/{job_id}",
        response_model=SameStylePublishJobResponse,
    )
    def get_same_style_publish_generation_job(
        job_id: str,
    ) -> SameStylePublishJobResponse:
        context = get_context()
        try:
            return read_same_style_publish_job(context.settings, job_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="publish job not found") from exc

    @router.post(
        "/api/same-style/video-jobs",
        response_model=SameStyleVideoJobResponse,
    )
    def create_same_style_video_generation_job(
        request: SameStyleVideoJobRequest,
    ) -> SameStyleVideoJobResponse:
        context = get_context()
        return create_same_style_video_job(context.settings, request)

    @router.get(
        "/api/same-style/video-jobs/{job_id}",
        response_model=SameStyleVideoJobResponse,
    )
    def get_same_style_video_generation_job(job_id: str) -> SameStyleVideoJobResponse:
        context = get_context()
        try:
            return read_same_style_video_job(context.settings, job_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="video job not found") from exc

    return router
