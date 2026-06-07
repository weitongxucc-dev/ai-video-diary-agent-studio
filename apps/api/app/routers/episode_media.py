from __future__ import annotations

import logging
from collections.abc import Callable

from fastapi import APIRouter, HTTPException

from app import episode_media_flow
from app.routers.context import AppContext
from app.schemas import (
    EpisodeResponse,
    ProcessVideoBatchRequest,
    ProcessVideoRequest,
    UploadVideoRequest,
)

logger = logging.getLogger(__name__)


def create_router(get_context: Callable[[], AppContext]) -> APIRouter:
    router = APIRouter()

    @router.post("/api/episodes/{episode_id}/upload-video", response_model=EpisodeResponse)
    def upload_video(episode_id: str, request: UploadVideoRequest) -> EpisodeResponse:
        context = get_context()
        try:
            video = episode_media_flow.pending_video_by_name(context, request.file_name)
            episode = context.workflow.run(
                "bind_video",
                _episode_or_404(context, episode_id),
                video=video,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return episode_media_flow.episode_response(context, context.repo.save(episode))

    @router.post("/api/episodes/{episode_id}/process-video", response_model=EpisodeResponse)
    def process_episode_video(
        episode_id: str,
        request: ProcessVideoRequest,
    ) -> EpisodeResponse:
        context = get_context()
        episode = _episode_or_404(context, episode_id)
        file_name = request.file_name or episode.video_source_file_name
        if not file_name:
            raise HTTPException(status_code=400, detail="bind or provide a video first")
        try:
            processed = context.video_pipeline.process(file_name)
            episode = context.workflow.run("bind_processed_video", episode, video=processed)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return episode_media_flow.episode_response(context, context.repo.save(episode))

    @router.post(
        "/api/episodes/{episode_id}/process-ad-hoc-video",
        response_model=EpisodeResponse,
    )
    def process_ad_hoc_episode_video(
        episode_id: str,
        request: ProcessVideoRequest,
    ) -> EpisodeResponse:
        context = get_context()
        if not request.file_name:
            raise HTTPException(status_code=400, detail="file_name is required")
        if context.video_job_runner.is_active(episode_id):
            return episode_media_flow.episode_response(
                context,
                _episode_or_404(context, episode_id),
            )
        try:
            episode = context.workflow.run(
                "bind_ad_hoc_video",
                _episode_or_404(context, episode_id),
                video=episode_media_flow.pending_video_by_name(context, request.file_name),
            )
            episode_media_flow.start_video_job(episode)
            episode = context.repo.save(episode)
            context.video_job_runner.submit(
                episode_id,
                lambda: episode_media_flow.complete_ad_hoc_episode_video(
                    get_context,
                    episode_id,
                    request.file_name,
                    logger=logger,
                ),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return episode_media_flow.episode_response(context, context.repo.get(episode.id))

    @router.post(
        "/api/episodes/{episode_id}/process-ad-hoc-video-batch",
        response_model=EpisodeResponse,
    )
    def process_ad_hoc_episode_video_batch(
        episode_id: str,
        request: ProcessVideoBatchRequest,
    ) -> EpisodeResponse:
        context = get_context()
        if context.video_job_runner.is_active(episode_id):
            return episode_media_flow.episode_response(
                context,
                _episode_or_404(context, episode_id),
            )
        try:
            videos = episode_media_flow.pending_videos_by_name(
                context,
                request.file_names,
            )
            episode = context.workflow.run(
                "bind_ad_hoc_video_batch",
                _episode_or_404(context, episode_id),
                videos=videos,
            )
            episode_media_flow.start_video_job(episode)
            episode = context.repo.save(episode)
            context.video_job_runner.submit(
                episode_id,
                lambda: episode_media_flow.complete_ad_hoc_episode_video_batch(
                    get_context,
                    episode_id,
                    request.file_names,
                    logger=logger,
                ),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return episode_media_flow.episode_response(context, context.repo.get(episode.id))

    @router.post("/api/episodes/{episode_id}/create-draft", response_model=EpisodeResponse)
    def create_episode_draft(episode_id: str) -> EpisodeResponse:
        context = get_context()
        episode = _episode_or_404(context, episode_id)
        if not episode.video_processed_file_name:
            raise HTTPException(status_code=400, detail="process a video first")
        try:
            episode = episode_media_flow.create_draft_for_episode(context, episode)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return episode_media_flow.episode_response(context, context.repo.save(episode))

    return router


def _episode_or_404(context: AppContext, episode_id: str):
    try:
        return episode_media_flow.get_episode(context, episode_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="episode not found") from exc
