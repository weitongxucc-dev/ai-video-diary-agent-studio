"""HTTP adapter for continuing a known failed quality phase."""
from fastapi import APIRouter, HTTPException, Request

from app.device_scheduler import RecoveryBlocked
from app.device_runtime import RuntimeUnavailable
from app.schemas import QualityContinuationRequest, QualityContinuationReceipt


def build_quality_continuation_router(*, continue_check):
    router = APIRouter()

    @router.post('/api/creation-tasks/{task_id}/quality-check/continue',
                 response_model=QualityContinuationReceipt)
    def continue_phase(task_id: str, request: Request, payload: QualityContinuationRequest):
        try:
            return continue_check(request, task_id, payload)
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(404, '原质检作业不存在或不属于当前任务') from exc
        except (RecoveryBlocked, ValueError) as exc:
            raise HTTPException(409, str(exc)) from exc
        except RuntimeUnavailable as exc:
            raise HTTPException(503, str(exc)) from exc

    return router
