"""Member-bound voice sampling HTTP; reception and persistence stay with the use case."""
from dataclasses import asdict
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from app.profiles.voice_sampling import VoiceSamplingConflict, VoiceSamplingTask, MAX_SAMPLE_BYTES
from app.schemas import VoiceSamplingTaskCreateRequest


def build_voice_sampling_router(*, sampling_for, member_scope_for, companion_for,
                                provider_for) -> APIRouter:
    router = APIRouter()

    @router.post("/api/media/voice-sampling-tasks", response_model=VoiceSamplingTask, status_code=201)
    def create_voice_sampling_task(request: Request, payload: VoiceSamplingTaskCreateRequest):
        workspace_id, owner = member_scope_for(request)
        try:
            return sampling_for().create(
                workspace_id, owner, request_id=str(payload.request_id),
                display_name=payload.display_name, language=payload.language, purpose=payload.purpose,
            )
        except VoiceSamplingConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc


    @router.get("/api/media/voice-sampling-tasks")
    def list_voice_sampling_tasks(request: Request):
        workspace_id, owner = member_scope_for(request)
        items = sampling_for().list(workspace_id, owner)
        return {"items": [asdict(item) for item in items], "total_count": len(items)}


    @router.get("/api/media/voice-sampling-tasks/{task_id}", response_model=VoiceSamplingTask)
    def get_voice_sampling_task(task_id: str, request: Request):
        workspace_id, owner = member_scope_for(request)
        try:
            return sampling_for().get(workspace_id, owner, task_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="声音采样任务不存在或当前不可访问。") from exc


    @router.post("/api/media/voice-sampling-tasks/{task_id}/sample", response_model=VoiceSamplingTask)
    async def receive_voice_sampling_sample(
        task_id: str, request: Request,
        request_id: UUID = Form(...), text_version: str = Form(...),
        purpose: Literal["voice_profile"] = Form(...),
        source_device: Literal["desktop", "import", "mobile"] = Form(...),
        audio: UploadFile = File(...),
    ):
        if source_device == "mobile":
            companion_for(request)
        workspace_id, owner = member_scope_for(request)
        try:
            # Resolve ownership before reading an uploaded sample into memory.
            service = sampling_for()
            service.get(workspace_id, owner, task_id)
            content = await audio.read(MAX_SAMPLE_BYTES + 1)
            return service.receive(
                workspace_id, owner, task_id, request_id=str(request_id), text_version=text_version,
                purpose=purpose, source_device=source_device, content=content,
                model_id=provider_for().model_id if provider_for().ready else "",
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="声音采样任务不存在或当前不可访问。") from exc
        except VoiceSamplingConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(status_code=503, detail="声音样本未保存，请检查原素材目录和磁盘后重试。") from exc


    return router
