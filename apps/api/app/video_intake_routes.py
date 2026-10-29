"""Video intake and draft-status HTTP; processing and filesystem rules stay with their original owners."""
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from app.schemas import PendingVideoResponse, ProcessedVideoResponse, ProcessVideoRequest, DraftStatusResponse

def build_video_intake_router(*, inbox_for, pipeline_for, drafts_for, feature_for, path_for, settings_for, file_response_for, pending_type_for, processed_type_for, status_type_for, environment_type_for, asset_type_for) -> APIRouter:
    router = APIRouter()

    @router.get('/api/videos/pending', response_model=list[PendingVideoResponse])
    def get_pending_videos() -> list[PendingVideoResponse]:
        return [pending_type_for().model_validate(video, from_attributes=True) for video in inbox_for().pending_videos()]

    @router.post('/api/videos/process', response_model=ProcessedVideoResponse)
    def process_video(request: ProcessVideoRequest) -> ProcessedVideoResponse:
        feature_for()('process-video')
        try:
            if not request.file_name:
                raise ValueError('file_name is required')
            processed = pipeline_for().process(request.file_name)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return processed_type_for().model_validate(processed, from_attributes=True)

    @router.get('/api/videos/processed/{file_name}')
    def get_processed_video(file_name: str) -> FileResponse:
        try:
            path = path_for()(file_name, settings_for().video_output_path)
        except (OSError, RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=404, detail='processed video not found') from exc
        return file_response_for()(path)

    @router.get('/api/drafts/status', response_model=DraftStatusResponse)
    def get_draft_status() -> DraftStatusResponse:
        return status_type_for()(environment=environment_type_for().model_validate(drafts_for().environment(), from_attributes=True), processed_videos=[asset_type_for().model_validate(video, from_attributes=True) for video in drafts_for().processed_videos()], packages=[asset_type_for().model_validate(package, from_attributes=True) for package in drafts_for().packages()])
    return router
