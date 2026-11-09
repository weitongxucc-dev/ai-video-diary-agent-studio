"""HTTP adapters for library windows and recoverable library requests."""
from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Query, Request

from app.creation.script_routes import _write_response
from app.creation.script_windows import SCRIPT_SECTION_WINDOW_LIMIT
from app.schemas import (
    ScriptLibraryEntryHeaderResponse, ScriptLibraryHeaderListResponse, ScriptLibrarySectionWindowResponse,
    ScriptLibrarySaveRequest, ScriptLibrarySaveResponse, ScriptLibraryReuseRequest, ScriptLibraryReuseResponse,
    ScriptLibrarySourceResponse,
)


def build_script_library_router(*, use_case, workspace_id):
    router = APIRouter()

    @router.post('/api/production/script-library', response_model=ScriptLibrarySaveResponse)
    def save(request: Request, payload: ScriptLibrarySaveRequest):
        return _write_response(lambda: use_case(request).save_entry(workspace_id(request), payload))

    @router.post('/api/production/script-library/{entry_id}/reuse', response_model=ScriptLibraryReuseResponse)
    def reuse(entry_id: str, request: Request, payload: ScriptLibraryReuseRequest):
        return _write_response(lambda: use_case(request).reuse_entry(workspace_id(request), entry_id, payload))

    @router.get('/api/production/script-library-requests/{request_id}',
                response_model=ScriptLibrarySaveResponse | ScriptLibraryReuseResponse)
    def result(request_id: UUID, request: Request, operation: Literal['save', 'reuse']):
        return _write_response(lambda: use_case(request).read_request(workspace_id(request), str(request_id), operation))

    @router.get('/api/production/script-library', response_model=ScriptLibraryHeaderListResponse)
    def entries(request: Request, q: str = '', category: str = '',
                after_created_at: datetime | None = None, after_entry_id: str | None = None,
                limit: int = Query(default=SCRIPT_SECTION_WINDOW_LIMIT, ge=1, le=SCRIPT_SECTION_WINDOW_LIMIT)):
        return _write_response(lambda: use_case(request).list_headers(workspace_id(request), query=q, category=category,
            after_created_at=after_created_at.isoformat() if after_created_at else None,
            after_entry_id=after_entry_id, limit=limit))

    @router.get('/api/production/script-library/{entry_id}', response_model=ScriptLibraryEntryHeaderResponse)
    def header(entry_id: str, request: Request):
        return _write_response(lambda: use_case(request).read_header(workspace_id(request), entry_id))

    @router.get('/api/production/script-library/{entry_id}/sections',
                response_model=ScriptLibrarySectionWindowResponse)
    def sections(entry_id: str, request: Request, after_ordinal: int = Query(default=0, ge=0),
                 limit: int = Query(default=SCRIPT_SECTION_WINDOW_LIMIT, ge=1, le=SCRIPT_SECTION_WINDOW_LIMIT),
                 expected_updated_at: datetime | None = None):
        return _write_response(lambda: use_case(request).read_sections(workspace_id(request), entry_id,
            after_ordinal=after_ordinal, limit=limit, expected_updated_at=expected_updated_at))

    @router.get('/api/production/script-library/{entry_id}/sources',
                response_model=list[ScriptLibrarySourceResponse])
    def sources(entry_id: str, request: Request):
        return _write_response(lambda: use_case(request).read_sources(workspace_id(request), entry_id))

    return router
