"""Member-visible candidate discovery; domains retain evidence and usage policy."""
from typing import Literal
from fastapi import APIRouter, HTTPException, Query, Request


def build_candidate_query_router(*, query_for, workspace_for, reader_for, task_for):
    router = APIRouter()

    def context(request, task_id):
        workspace_id = workspace_for(request)
        user_id, diagnostic = reader_for(request, workspace_id)
        if task_id is not None:
            task_for(request, task_id)
        return workspace_id, user_id, diagnostic

    @router.get('/api/creation-candidates/search')
    def search_creation_candidates(request: Request, q: str = Query(..., min_length=1, max_length=120),
                                   limit_per_domain: int = Query(5, ge=1, le=20),
                                   task_id: str | None = None):
        workspace_id, user_id, diagnostic = context(request, task_id)
        try:
            return query_for().search(workspace_id, query=q, user_id=user_id,
                local_diagnostic=diagnostic, limit_per_domain=limit_per_domain, task_id=task_id)
        except KeyError as exc:
            raise HTTPException(404, '候选或关联任务不存在或不可读取') from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.get('/api/creation-candidates/{domain}/{object_id}')
    def read_creation_candidate(domain: Literal['material', 'knowledge', 'person', 'voice'],
                                object_id: str, request: Request,
                                version: str = Query(..., min_length=1, max_length=160),
                                task_id: str | None = None):
        workspace_id, user_id, diagnostic = context(request, task_id)
        try:
            return query_for().read(workspace_id, domain=domain, object_id=object_id, version=version,
                user_id=user_id, local_diagnostic=diagnostic, task_id=task_id)
        except KeyError as exc:
            raise HTTPException(404, '指定候选版本不存在或不可读取') from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    return router
