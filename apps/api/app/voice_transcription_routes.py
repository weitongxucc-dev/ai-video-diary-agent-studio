"""Uploaded voice transcription HTTP; ASR selection, execution and temporary cleanup remain with the voice owner."""
from fastapi import APIRouter, UploadFile, File, Form
from app.schemas import VoiceTranscriptionResponse

def build_voice_transcription_router(*, http_error_type_for, transcribe_for, settings_for, response_type_for) -> APIRouter:
    router = APIRouter()

    @router.post('/api/voice/transcribe', response_model=VoiceTranscriptionResponse)
    async def voice_transcribe(audio: UploadFile=File(...), local_only: bool=Form(False)) -> VoiceTranscriptionResponse:
        """Transcribe uploaded audio; local_only selects LocalWhisper for this request."""
        try:
            content = await audio.read()
        except Exception as exc:
            raise http_error_type_for()(status_code=400, detail=f'Failed to read uploaded file: {exc}') from exc
        if not content:
            raise http_error_type_for()(status_code=400, detail='Empty audio file')
        try:
            if local_only:
                result = transcribe_for()(content, settings_for(), local_only=True)
            else:
                result = transcribe_for()(content, settings_for())
        except RuntimeError as exc:
            raise http_error_type_for()(status_code=503, detail=str(exc)) from exc
        return response_type_for()(transcript=result['transcript'], provider=result['provider'], segments=result['segments'])
    return router
