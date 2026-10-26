"""HTTP adapters for version-bound script windows."""
from fastapi import APIRouter, HTTPException, Query, Request
from typing import Literal
from uuid import UUID

from app.creation.script_windows import SCRIPT_SECTION_WINDOW_LIMIT, ScriptWindowConflict
from app.creation.script_draft_use_case import ScriptDraftRejected
from app.schemas import (
    CreationTaskScriptSectionsPatchRequest, CreationTaskScriptWindowStartRequest,
    CreationTaskScriptConfirmSavedRequest,
)


def _write_response(action):
    try:
        return action()
    except ScriptDraftRejected as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(404, '文案不存在或不属于当前任务') from exc
    except ScriptWindowConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


def build_script_sections_router(*, readable_task, read_sections, patch_sections=None, start_window=None,
                                confirm_saved=None, read_request=None):
    router = APIRouter()

    @router.get('/api/creation-tasks/{task_id}/scripts/{script_id}/sections')
    def sections(task_id: str, script_id: str, request: Request,
                 after_ordinal: int = Query(default=0, ge=0),
                 limit: int = Query(default=SCRIPT_SECTION_WINDOW_LIMIT, ge=1,
                                    le=SCRIPT_SECTION_WINDOW_LIMIT),
                 expected_updated_at: str | None = None):
        task = readable_task(request, task_id)
        if after_ordinal and not expected_updated_at:
            raise HTTPException(status_code=422, detail='继续读取文案须携带首个窗口的版本')
        try:
            return read_sections(task.workspace_id, task.id, script_id,
                                 after_ordinal=after_ordinal, limit=limit,
                                 expected_updated_at=expected_updated_at)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail='文案不存在或不属于当前任务') from exc
        except ScriptWindowConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.patch('/api/creation-tasks/{task_id}/scripts/{script_id}/sections')
    def patch(task_id: str, script_id: str, request: Request,
              payload: CreationTaskScriptSectionsPatchRequest):
        if patch_sections is None:
            raise HTTPException(503, '文案窗口写入用例尚未装配')
        return _write_response(lambda: patch_sections(request, task_id, script_id, payload))

    @router.post('/api/creation-tasks/{task_id}/scripts/windows')
    def start(task_id: str, request: Request, payload: CreationTaskScriptWindowStartRequest):
        if start_window is None:
            raise HTTPException(503, '文案首窗写入用例尚未装配')
        return _write_response(lambda: start_window(request, task_id, payload))

    @router.post('/api/creation-tasks/{task_id}/scripts/{script_id}/confirm-saved')
    def confirm(task_id: str, script_id: str, request: Request, payload: CreationTaskScriptConfirmSavedRequest):
        if confirm_saved is None:
            raise HTTPException(503, '完整文案采用用例尚未装配')
        return _write_response(lambda: confirm_saved(request, task_id, script_id, payload))

    @router.get('/api/creation-tasks/{task_id}/script-window-requests/{request_id}')
    def result(task_id: str, request_id: UUID, request: Request,
               operation: Literal['start', 'patch', 'confirm'], script_id: str | None = None,
               include_input: bool = False):
        if read_request is None:
            raise HTTPException(503, '文案请求查询尚未装配')
        return _write_response(lambda: read_request(request, task_id, str(request_id), operation, script_id,
                                                    include_input=include_input))

    return router
