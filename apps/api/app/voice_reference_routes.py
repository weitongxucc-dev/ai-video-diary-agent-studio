"""Voice-reference byte import HTTP; WAV validation, ownership and publication remain repository-owned."""
from fastapi import APIRouter, Request, UploadFile, File
from app.schemas import VoiceReferenceImportResponse

def build_voice_reference_router(*, path_type_for, http_error_type_for, records_for, workspace_for, reader_for, response_type_for) -> APIRouter:
    router = APIRouter()

    @router.post('/api/media/voice-references/import', response_model=VoiceReferenceImportResponse, status_code=201)
    async def import_media_voice_reference(request: Request, audio: UploadFile=File(...)) -> VoiceReferenceImportResponse:
        if not audio.filename or path_type_for()(audio.filename).suffix.lower() != '.wav':
            raise http_error_type_for()(status_code=422, detail='请选择 WAV 声音参考文件')
        try:
            content = await audio.read(50 * 1024 * 1024 + 1)
            reference, size_bytes, duration_seconds = records_for().import_voice_reference_wav(workspace_for()(request), content, verified_owner_user_id=reader_for()(request, workspace_for()(request))[0])
        except (OSError, ValueError) as exc:
            raise http_error_type_for()(status_code=422, detail=str(exc)) from exc
        return response_type_for()(reference=reference, file_name='已导入声音参考.wav', size_bytes=size_bytes, duration_seconds=duration_seconds)
    return router
