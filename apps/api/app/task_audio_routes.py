"""Task audio HTTP and artifact access; ingress, editing and recording transactions remain owner-owned."""
from fastapi import APIRouter, Request, UploadFile, File
from pydantic import BaseModel, ConfigDict, Field
from app.audio_timeline import AudioEditRequest, AudioListenRequest
from app.editing_resource_catalog import resource_preview_path
from app.schemas import CreationTaskNarrationResponse, CreationTaskRecordingAdoptRequest, CreationTaskEditPreviewResponse


class CatalogAudioImportRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')

    resource_id: str = Field(min_length=1, max_length=160)
    version: int = Field(ge=1, strict=True)


def build_task_audio_router(*, catalog_for, submit_for, workspace_for, task_for, ingress_for, outputs_for, ingress_error_type_for, http_error_type_for, narration_for, records_for, media_kind_for, actor_for, runtime_for, actor_type_for, audio_for, file_response_for, preview_for, route_names) -> APIRouter:
    router = APIRouter()

    @router.post('/api/creation-tasks/{task_id}/audio/import')
    def import_creation_task_audio(task_id: str, request: Request, audio: UploadFile=File(...)):
        return submit_for()(task_id, request, 'audio_import', 'audio_source', audio=audio)

    @router.post('/api/creation-tasks/{task_id}/audio/resources')
    def import_creation_task_audio_resource(task_id: str, request: Request, payload: CatalogAudioImportRequest):
        item = next((item for item in catalog_for().resources
                     if item.id == payload.resource_id and item.version == payload.version), None)
        if item is None:
            raise http_error_type_for()(status_code=404, detail='资源版本不存在')
        if item.preview.kind != 'audio':
            raise http_error_type_for()(status_code=422, detail='此资源不是音频')
        if item.status != 'connected':
            raise http_error_type_for()(status_code=409, detail='音频资源尚未接通')
        path = resource_preview_path(item.id)
        if path is None:
            raise http_error_type_for()(status_code=404, detail='资源音频文件不存在')
        catalog_resource = {'id': item.id, 'version': item.version,
                            'provenance': [entry.model_dump(mode='json') for entry in item.provenance]}
        with path.open('rb') as stream:
            audio = UploadFile(file=stream, filename=f'{item.name}{path.suffix}')
            return submit_for()(task_id, request, 'audio_import', 'audio_source', audio=audio,
                                arguments={'catalog_resource': catalog_resource})

    @router.get('/api/creation-tasks/{task_id}/audio/recordings')
    def list_creation_task_recordings(task_id: str, request: Request):
        workspace_id = workspace_for()(request)
        task = task_for()(workspace_id, task_id)
        return ingress_for()().list_recordings(task, outputs=outputs_for()())

    @router.post('/api/creation-tasks/{task_id}/audio/recordings')
    def save_creation_task_recording(task_id: str, request: Request, audio: UploadFile=File(...)):
        return submit_for()(task_id, request, 'recording_save', 'recording', audio=audio)

    @router.post('/api/creation-tasks/{task_id}/audio/recordings/adopt', response_model=CreationTaskNarrationResponse)
    def adopt_creation_task_recording(task_id: str, request: Request, payload: CreationTaskRecordingAdoptRequest) -> CreationTaskNarrationResponse:
        workspace_id = workspace_for()(request)
        task = task_for()(workspace_id, task_id)
        try:
            task, record = ingress_for()().adopt_recording(task, script_id=payload.script_id, source_section_id=payload.source_section_id, source_ref=payload.source_ref, outputs=outputs_for()())
        except ingress_error_type_for() as exc:
            raise http_error_type_for()(status_code=exc.status_code, detail=exc.detail) from exc
        return narration_for()(task, record)

    def _require_task_media_artifact(record, request):
        job = records_for().job_records.get_job_run(record.workspace_id, record.producer_job_id)
        if job.kind != media_kind_for():
            return
        actor = actor_for()(request, record.workspace_id)
        runtime = runtime_for()()
        media = runtime.registrations['creation_job'].owner.task_media
        original, event = media.read(record.workspace_id, job.id)
        if actor_type_for().from_event(record.workspace_id, event) != actor or media.verify_identity(actor) != 'ready':
            raise KeyError(record.id)
        if original.status != 'succeeded' or not outputs_for()().published_outputs_current(original):
            raise KeyError(record.id)
        if record.kind not in {'audio_source', 'task_recording'} and media.check(original, event, actor) != 'ready':
            raise KeyError(record.id)

    @router.get('/api/creation-tasks/{task_id}/audio/artifacts/{artifact_id}')
    def get_creation_task_audio_artifact(task_id: str, artifact_id: str, request: Request):
        workspace_id = workspace_for()(request)
        task_for()(workspace_id, task_id)
        try:
            record = records_for().execution_records.get_artifact_record(workspace_id, artifact_id)
            if record.task_id != task_id:
                raise KeyError(artifact_id)
            _require_task_media_artifact(record, request)
            path = audio_for()().source(workspace_id, task_id, record.logical_ref, actor=actor_for()(request, workspace_id))
        except (KeyError, ValueError, FileNotFoundError, PermissionError) as exc:
            raise http_error_type_for()(status_code=404, detail='当前任务的音频不存在') from exc
        audio_media_types = {'.wav': 'audio/wav', '.webm': 'audio/webm', '.ogg': 'audio/ogg', '.m4a': 'audio/mp4', '.mp4': 'audio/mp4'}
        return file_response_for()(path, media_type=audio_media_types.get(path.suffix))

    @router.get('/api/creation-tasks/{task_id}/audio/waveform/{clip_id}')
    def get_creation_task_audio_waveform(task_id: str, clip_id: str, request: Request):
        return submit_for()(task_id, request, 'audio_waveform', 'waveform', arguments={'clip_id': clip_id})

    @router.post('/api/creation-tasks/{task_id}/audio/listen')
    def listen_creation_task_audio(task_id: str, request: Request, payload: AudioListenRequest):
        return submit_for()(task_id, request, 'audio_listen', 'audio_listen', arguments={'payload': payload.model_dump(mode='json', exclude_unset=True)})

    @router.post('/api/creation-tasks/{task_id}/audio/preview', response_model=CreationTaskEditPreviewResponse)
    def preview_creation_task_audio(task_id: str, request: Request, payload: AudioEditRequest):
        return preview_for()(task_id, request, payload, queue_on_required=True)
    router.routes = [route for route in router.routes if route.name in route_names]
    return router
