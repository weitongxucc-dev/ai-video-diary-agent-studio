"""Task narration HTTP; voice selection, authorized audio and scheduling keep their original owners."""
from uuid import UUID
from datetime import datetime
from fastapi import APIRouter, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from app.assistant.workspace_save_notifications import notify_workspace_save
from app.creation.narration_duration import NarrationDurationReview
from app.schemas import CreationTaskNarrationVoiceSelectionRequest, CreationTaskNarrationVoiceSelectionResponse, CreationTaskNarrationResponse

class NarrationDurationAdoptionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    review_id: str = Field(min_length=1, max_length=160)

class SectionAuditionSelectionRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: UUID
    preview_run_id: str | None = Field(max_length=200)
    expected_script_id: str = Field(min_length=1, max_length=160)
    expected_script_updated_at: datetime
    expected_preview_run_id: str | None = Field(max_length=200)
    expected_output_sha256: str | None = Field(default=None, pattern=r'^[0-9a-f]{64}$')


def build_task_narration_router(*, access_for, adoption_for, production_for, execution_for, actor_for, control_for, runtime_for, http_error_for, authority_error_for, failure_for, blocked_for, selection_response_for, workspace_for, readable_for, path_for, file_response_for, ingress_for,
                               auditions_for=None, audition_actor_for=None):
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
        result = selection_response_for().model_validate(receipt)
        if payload.assistant_turn_id is not None and (
            result.status, result.workspace_id, result.member_id, result.task_id, str(result.request_id)
        ) == ('selected', task.workspace_id, member_id, task.id, str(payload.request_id)):
            notify_workspace_save(
                request, task.workspace_id, member_id, task.id, payload.assistant_turn_id, f"voice:{result.request_id}")
        return result

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

    @router.get("/api/creation-tasks/{task_id}/narration/duration")
    def read_narration_duration(task_id: str, request: Request):
        task, _ = access_for()(task_id, request)
        actor = actor_for()(request, task.workspace_id)
        if actor is None:
            raise http_error_for()(status_code=403, detail="请以当前成员身份读取配音进度。")
        return NarrationDurationReview(production_for()).read(task.workspace_id, task_id, actor)

    @router.put("/api/creation-tasks/{task_id}/narration/duration")
    def adopt_narration_duration(task_id: str, payload: NarrationDurationAdoptionRequest, request: Request):
        task, _ = access_for()(task_id, request, write=True)
        actor = actor_for()(request, task.workspace_id)
        if actor is None:
            raise http_error_for()(status_code=403, detail="请以原制作成员身份采用交付时长。")
        try:
            return NarrationDurationReview(production_for()).adopt(task.workspace_id, task_id, actor,
                request_id=str(payload.request_id), review_id=payload.review_id)
        except PermissionError as exc:
            raise http_error_for()(status_code=403, detail=str(exc)) from exc
        except (ValueError, blocked_for()) as exc:
            raise http_error_for()(status_code=409, detail=str(exc)) from exc

    if auditions_for is not None:
        audition_url = '/api/creation-tasks/{task_id}/narration/sections/{section_id}/audition'

        @router.get(audition_url)
        def read_section_audition(task_id: str, section_id: str, request: Request, preview_run_id: str | None = None):
            task, _ = access_for()(task_id, request)
            actor = audition_actor_for(request, task.workspace_id)
            try:
                return auditions_for().status(task.workspace_id, task_id, section_id, actor, preview_run_id=preview_run_id)
            except KeyError as exc:
                raise http_error_for()(status_code=404, detail='当前作品段落不存在。') from exc
            except ValueError as exc:
                raise http_error_for()(status_code=409, detail=str(exc)) from exc

        @router.put(audition_url)
        def select_section_audition(task_id: str, section_id: str, payload: SectionAuditionSelectionRequest, request: Request):
            task, _ = access_for()(task_id, request, write=True)
            actor = audition_actor_for(request, task.workspace_id)
            try:
                return auditions_for().select(task.workspace_id, task_id, section_id, actor, payload)
            except KeyError as exc:
                raise http_error_for()(status_code=404, detail='当前段落或本人的试听不存在。') from exc
            except PermissionError as exc:
                raise http_error_for()(status_code=403, detail=str(exc)) from exc
            except (ValueError, authority_error_for()) as exc:
                raise http_error_for()(status_code=409, detail=str(exc)) from exc

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
