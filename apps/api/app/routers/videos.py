from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.edit_plan import safe_processed_video_path
from app.routers.context import AppContext
from app.schemas import (
    PendingVideoResponse,
    ProcessedVideoResponse,
    ProcessVideoRequest,
)


def create_router(get_context: Callable[[], AppContext]) -> APIRouter:
    router = APIRouter()

    @router.get("/api/videos/pending", response_model=list[PendingVideoResponse])
    def get_pending_videos() -> list[PendingVideoResponse]:
        context = get_context()
        return [
            PendingVideoResponse.model_validate(video, from_attributes=True)
            for video in context.video_inbox.pending_videos()
        ]

    @router.post("/api/videos/process", response_model=ProcessedVideoResponse)
    def process_video(request: ProcessVideoRequest) -> ProcessedVideoResponse:
        context = get_context()
        try:
            if not request.file_name:
                raise ValueError("file_name is required")
            processed = context.video_pipeline.process(request.file_name)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return ProcessedVideoResponse.model_validate(processed, from_attributes=True)

    @router.get("/api/videos/processed/{file_name}")
    def get_processed_video(file_name: str) -> FileResponse:
        context = get_context()
        try:
            path = safe_processed_video_path(
                file_name,
                context.settings.video_output_path,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            raise HTTPException(
                status_code=404,
                detail="processed video not found",
            ) from exc
        return FileResponse(path)

    return router
