"""Explicit adoption of a registered video into an editable creation task."""
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from typing import Literal

from app.creation.source_task_use_case import (
    SourceRevisionRecoveryCommand, SourceTaskCommand, SourceTaskConflict, SourceTaskIdentityUnavailable,
)
from app.creation.request_workflow_use_case import CreationRequestWorkflowRejected


class SourceTaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    asset_id: str = Field(min_length=1)
    expected_asset_updated_at: datetime
    source_start_seconds: float = Field(ge=0, allow_inf_nan=False)
    source_end_seconds: float = Field(gt=0, allow_inf_nan=False)
    name: str = Field(min_length=1, max_length=200)
    aspect_ratio: Literal["9:16", "16:9", "1:1"]
    preserve_audio: bool = Field(strict=True)


class SourceRevisionRecoveryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    expected_timeline_version_id: str = Field(min_length=1, max_length=160)
    expected_timeline_sequence: int = Field(ge=1, strict=True)
    asset_id: str = Field(min_length=1, max_length=120)
    expected_bound_asset_updated_at: AwareDatetime
    expected_asset_updated_at: AwareDatetime


def build_source_task_router(*, actor_for, case_for, origin_for, input_for):
    router = APIRouter()

    @router.post("/api/source-tasks", status_code=201)
    def create_source_task(request: Request, payload: SourceTaskRequest, assistant_turn_id: UUID | None = None):
        actor = actor_for(request)
        values = payload.model_dump()
        values["request_id"] = str(payload.request_id)
        try:
            source, guard = origin_for(request, assistant_turn_id, actor, values["request_id"], payload.model_dump(mode="json"))
            return case_for().create(SourceTaskCommand(**values), actor, source=source, write_guard=guard)
        except CreationRequestWorkflowRejected as exc:
            raise HTTPException(exc.status_code, exc.detail) from exc
        except SourceTaskIdentityUnavailable as exc:
            raise HTTPException(503, str(exc)) from exc
        except SourceTaskConflict as exc:
            raise HTTPException(409, str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except (KeyError, FileNotFoundError) as exc:
            raise HTTPException(404, "所选源视频不存在或已离线") from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.get("/api/source-tasks/{request_id}")
    def read_source_task(request_id: UUID, request: Request):
        receipt = case_for().read(str(request_id), actor_for(request))
        if receipt is None:
            raise HTTPException(404, "原请求尚无已提交回执")
        return receipt

    @router.get("/api/source-tasks/{request_id}/input")
    def read_source_task_input(request_id: UUID, request: Request):
        return input_for(actor_for(request), str(request_id))

    @router.post("/api/creation-tasks/{task_id}/source-revision-recoveries")
    def recover_source_revision(task_id: str, request: Request, payload: SourceRevisionRecoveryRequest):
        actor = actor_for(request)
        if request.headers.get("x-assistant-input-id") or request.query_params.get("assistant_turn_id") is not None:
            raise HTTPException(409, "复核原片绑定需要由作品所有者主动发起")
        values = payload.model_dump()
        values["request_id"] = str(payload.request_id)
        try:
            return case_for().recover_revision(task_id, SourceRevisionRecoveryCommand(**values), actor)
        except SourceTaskIdentityUnavailable as exc:
            raise HTTPException(503, str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except (KeyError, FileNotFoundError) as exc:
            raise HTTPException(404, "作品或当前登记的原片不存在，或已离线") from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        except OSError as exc:
            raise HTTPException(409, "当前原片文件不可读取，未复核绑定") from exc

    @router.get("/api/creation-tasks/{task_id}/source-revision-recoveries/{request_id}")
    def read_source_revision_recovery(task_id: str, request_id: UUID, request: Request):
        try:
            receipt = case_for().read_recovery(task_id, str(request_id), actor_for(request))
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        if receipt is None:
            raise HTTPException(404, "原复核请求尚无已提交回执")
        return receipt

    return router
