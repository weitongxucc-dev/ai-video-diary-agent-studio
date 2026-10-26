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
)


def build_script_library_router(*, list_headers, read_header, read_sections, save_entry, reuse_entry, read_request):
    router = APIRouter()

    @router.post('/api/production/script-library', response_model=ScriptLibrarySaveResponse)
    def save(request: Request, payload: ScriptLibrarySaveRequest):
        return _write_response(lambda: save_entry(request, payload))

    @router.post('/api/production/script-library/{entry_id}/reuse', response_model=ScriptLibraryReuseResponse)
    def reuse(entry_id: str, request: Request, payload: ScriptLibraryReuseRequest):
        return _write_response(lambda: reuse_entry(entry_id, request, payload))

    @router.get('/api/production/script-library-requests/{request_id}',
                response_model=ScriptLibrarySaveResponse | ScriptLibraryReuseResponse)
    def result(request_id: UUID, request: Request, operation: Literal['save', 'reuse']):
        return _write_response(lambda: read_request(request, str(request_id), operation))

    @router.get('/api/production/script-library', response_model=ScriptLibraryHeaderListResponse)
    def entries(request: Request, q: str = '', category: str = '',
                after_created_at: datetime | None = None, after_entry_id: str | None = None,
                limit: int = Query(default=SCRIPT_SECTION_WINDOW_LIMIT, ge=1, le=SCRIPT_SECTION_WINDOW_LIMIT)):
        return _write_response(lambda: list_headers(request, query=q, category=category,
            after_created_at=after_created_at.isoformat() if after_created_at else None,
            after_entry_id=after_entry_id, limit=limit))

    @router.get('/api/production/script-library/{entry_id}', response_model=ScriptLibraryEntryHeaderResponse)
    def header(entry_id: str, request: Request):
        return _write_response(lambda: read_header(request, entry_id))

    @router.get('/api/production/script-library/{entry_id}/sections',
                response_model=ScriptLibrarySectionWindowResponse)
    def sections(entry_id: str, request: Request, after_ordinal: int = Query(default=0, ge=0),
                 limit: int = Query(default=SCRIPT_SECTION_WINDOW_LIMIT, ge=1, le=SCRIPT_SECTION_WINDOW_LIMIT),
                 expected_updated_at: datetime | None = None):
        return _write_response(lambda: read_sections(request, entry_id,
            after_ordinal=after_ordinal, limit=limit, expected_updated_at=expected_updated_at))

    return router
