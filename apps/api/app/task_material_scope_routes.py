"""Task-only material scope HTTP requests and receipt access."""
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app.creation.material_scope_use_case import MaterialScopeCommand


class MaterialScopeAsset(BaseModel):
    model_config = ConfigDict(extra="forbid")
    asset_id: str = Field(min_length=1, max_length=120)
    expected_asset_updated_at: datetime


class MaterialScopeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    expected_plan_updated_at: datetime
    assets: list[MaterialScopeAsset] = Field(max_length=64)


def build_task_material_scope_router(*, access, case_for):
    router = APIRouter(prefix="/api/creation-tasks/{task_id}/material-scope")

    @router.put("")
    def save_material_scope(task_id: str, payload: MaterialScopeRequest, request: Request):
        task, member_id = access(task_id, request, write=True)
        command = MaterialScopeCommand(str(payload.request_id), payload.expected_plan_updated_at,
            tuple((asset.asset_id, asset.expected_asset_updated_at) for asset in payload.assets))
        try:
            return case_for().save(task.workspace_id, task_id, member_id, command)
        except KeyError as exc:
            raise HTTPException(404, "当前作品或所选素材不可读取。") from exc
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.get("/{request_id}")
    def read_material_scope_receipt(task_id: str, request_id: UUID, request: Request):
        task, member_id = access(task_id, request)
        try:
            return case_for().read(task.workspace_id, task_id, member_id, str(request_id))
        except KeyError as exc:
            raise HTTPException(404, "未找到此成员在该作品中的范围回执。") from exc
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc

    return router
