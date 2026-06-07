from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter
from fastapi.responses import Response

from app.diagnostics import diagnostics_report_bytes, diagnostics_status
from app.routers.context import AppContext
from app.schemas import DiagnosticsStatusResponse


def create_router(get_context: Callable[[], AppContext]) -> APIRouter:
    router = APIRouter()

    @router.get("/api/diagnostics/status", response_model=DiagnosticsStatusResponse)
    def get_diagnostics_status() -> DiagnosticsStatusResponse:
        context = get_context()
        return DiagnosticsStatusResponse.model_validate(
            diagnostics_status(context.settings, context.repo)
        )

    @router.get("/api/diagnostics/report")
    def download_diagnostics_report() -> Response:
        context = get_context()
        return Response(
            content=diagnostics_report_bytes(context.settings, context.repo),
            media_type="application/zip",
            headers={
                "Content-Disposition": 'attachment; filename="ai-video-diary-diagnostics.zip"'
            },
        )

    return router
