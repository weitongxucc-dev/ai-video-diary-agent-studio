"""HTTP transport for the original draft-generation action and its receipt."""
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request

from app.conversation_repository import ConversationTurnRequestConflict
from app.creation.source_errors import SourcePreflightError
from app.schemas import CreationTaskScriptGenerateRequest, CreationTaskScriptGenerationResponse


def _result(action):
    try:
        return action()
    except ConversationTurnRequestConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(404, "文案生成请求不存在或当前不可读取") from exc
    except SourcePreflightError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


def build_script_generation_router(*, accept, read, execute):
    router = APIRouter()

    @router.post("/api/creation-tasks/{task_id}/scripts/generate", status_code=202,
                 response_model=CreationTaskScriptGenerationResponse)
    def generate(task_id: str, request: Request, payload: CreationTaskScriptGenerateRequest,
                 background_tasks: BackgroundTasks):
        result, dispatch = _result(lambda: accept(request, task_id, str(payload.request_id)))
        # Only the transaction which first accepted the request may dispatch it.
        # Replays expose the original receipt, including an interrupted request.
        if dispatch is not None:
            background_tasks.add_task(execute, dispatch)
        return result

    @router.get("/api/creation-tasks/{task_id}/scripts/generation-requests/{request_id}",
                response_model=CreationTaskScriptGenerationResponse)
    def status(task_id: str, request_id: UUID, request: Request):
        return _result(lambda: read(request, task_id, str(request_id)))

    return router
