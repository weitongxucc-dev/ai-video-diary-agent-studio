"""Explicit adoption of a registered video into an editable creation task."""
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal

from app.creation.source_task_use_case import (
    SourceTaskCommand, SourceTaskConflict, SourceTaskIdentityUnavailable,
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

    return router
