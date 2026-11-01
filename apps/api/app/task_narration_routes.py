"""Task narration HTTP; voice selection, authorized audio and scheduling keep their original owners."""
from uuid import UUID
from fastapi import APIRouter, Request
from fastapi.responses import FileResponse
from app.schemas import CreationTaskNarrationVoiceSelectionRequest, CreationTaskNarrationVoiceSelectionResponse, CreationTaskNarrationResponse

def build_task_narration_router(*, access_for, adoption_for, production_for, execution_for, actor_for, control_for, runtime_for, http_error_for, authority_error_for, failure_for, blocked_for, selection_response_for, workspace_for, readable_for, path_for, file_response_for, ingress_for):
    router = APIRouter()

    @router.post('/api/creation-tasks/{task_id}/narration/voice', response_model=CreationTaskNarrationVoiceSelectionResponse)
    def select_creation_task_narration_voice(task_id: str, request: Request, payload: CreationTaskNarrationVoiceSelectionRequest) -> CreationTaskNarrationVoiceSelectionResponse:
        task, member_id = access_for()(task_id, request, write=True)
        if payload.assistant_turn_id is not None:
            adoption_for()(task_id, request, str(payload.assistant_turn_id))
        try:
            actor = None
            try:
                production_for().narration_voice_selection.get_creation_task_narration_voice_selection(task.workspace_id, task.id, str(payload.request_id), member_id=member_id)
            except KeyError:
                job = execution_for().latest(task.workspace_id, task.id)
                if job is not None and job.status == 'queued':
                    actor = actor_for()(request, task.workspace_id)
            receipt = control_for()().select_narration_voice(runtime=runtime_for(), actor=actor, workspace_id=task.workspace_id, task_id=task.id, member_id=member_id, request_id=str(payload.request_id), voice_profile_id=payload.voice_profile_id, voice_version=payload.voice_version, expected_plan_updated_at=payload.expected_plan_updated_at, expected_task_updated_at=payload.expected_task_updated_at)
        except KeyError as exc:
            raise http_error_for()(status_code=404, detail='没有找到当前任务或配音声音。') from exc
        except PermissionError as exc:
            raise http_error_for()(status_code=403, detail=str(exc)) from exc
        except authority_error_for() as exc:
            raise failure_for()(status_code=409, error=exc.reason, reason=exc.reason, impact=exc.impact, repair_action=exc.repair_action, retryable=True) from exc
        except (ValueError, blocked_for()) as exc:
            raise failure_for()(status_code=409, error='creation_task_voice_selection_conflict', reason='当前任务不能选择这个声音版本。', impact='没有改变任务阶段，也没有调用声音模型。', repair_action=str(exc), retryable=False) from exc
        return selection_response_for().model_validate(receipt)

    @router.get('/api/creation-tasks/{task_id}/narration/voice/requests/{request_id}', response_model=CreationTaskNarrationVoiceSelectionResponse)
    def get_creation_task_narration_voice_selection(task_id: str, request_id: UUID, request: Request):
        task, member_id = access_for()(task_id, request)
        try:
            receipt = production_for().narration_voice_selection.get_creation_task_narration_voice_selection(workspace_id=task.workspace_id, task_id=task.id, request_id=str(request_id), member_id=member_id)
        except KeyError as exc:
            raise http_error_for()(status_code=404, detail='没有找到当前成员的原声音采用请求。') from exc
        except PermissionError as exc:
            raise http_error_for()(status_code=403, detail=str(exc)) from exc
        return selection_response_for().model_validate(receipt)

    @router.post('/api/creation-tasks/{task_id}/narration/generate', response_model=CreationTaskNarrationResponse)
    def generate_creation_task_narration(task_id: str, request: Request) -> CreationTaskNarrationResponse:
        raise http_error_for()(status_code=409, detail='请确认文案和制作要求后使用开始/继续制作，配音必须进入设备队列')

    @router.get('/api/creation-tasks/{task_id}/narration/audio')
    def get_creation_task_narration_audio(task_id: str, request: Request) -> FileResponse:
        workspace_id = workspace_for()(request)
        task = readable_for()(request, task_id)
        artifact = production_for().narration_artifacts.get_narration_artifact(workspace_id, task.narration_cache_key) if task.narration_cache_key else None
        if artifact is None or artifact.task_id != task.id:
            raise http_error_for()(status_code=404, detail='creation task narration not found')
        media_type = {'.wav': 'audio/wav', '.webm': 'audio/webm', '.ogg': 'audio/ogg', '.m4a': 'audio/mp4', '.mp4': 'audio/mp4'}.get(path_for()(artifact.audio_path).suffix.lower(), 'application/octet-stream')
        return file_response_for()(artifact.audio_path, media_type=media_type, filename=f'narration{path_for()(artifact.audio_path).suffix}')

    @router.get('/api/creation-tasks/{task_id}/narration/waveform')
    def get_creation_task_narration_waveform(task_id: str, request: Request) -> dict:
        return ingress_for()(task_id, request, 'narration_waveform', 'waveform')

    @router.get('/api/creation-tasks/{task_id}/narration/segments/{segment_id}/audio')
    def get_creation_task_narration_segment_audio(task_id: str, segment_id: str, request: Request) -> FileResponse:
        workspace_id = workspace_for()(request)
        readable_for()(request, task_id)
        try:
            segment = production_for().narration_records.get_narration_segment(workspace_id, task_id, segment_id)
        except KeyError as exc:
            raise http_error_for()(status_code=404, detail='creation task narration segment not found') from exc
        if segment.status != 'ready' or not segment.audio_reference:
            raise http_error_for()(status_code=409, detail='creation task narration segment is not ready')
        path = production_for().narration_artifacts.resolve_authorized_narration_audio_reference(workspace_id, segment.audio_reference)
        media_type = {'.wav': 'audio/wav', '.webm': 'audio/webm', '.ogg': 'audio/ogg', '.m4a': 'audio/mp4', '.mp4': 'audio/mp4'}.get(path.suffix.lower(), 'application/octet-stream')
        return file_response_for()(path, media_type=media_type, filename=f'{segment.id}{path.suffix}')
    return router
