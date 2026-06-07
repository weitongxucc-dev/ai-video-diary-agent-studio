from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, HTTPException, Request

from app.routers.context import AppContext
from app.runtime_settings import apply_runtime_settings_update, is_local_client_host
from app.schemas import RuntimeSettingsResponse, RuntimeSettingsUpdateRequest


def create_router(get_context: Callable[[], AppContext]) -> APIRouter:
    router = APIRouter()

    @router.get("/api/settings/runtime", response_model=RuntimeSettingsResponse)
    def get_runtime_settings() -> RuntimeSettingsResponse:
        context = get_context()
        return RuntimeSettingsResponse.model_validate(
            context.settings.safe_runtime_settings()
        )

    @router.post("/api/settings/runtime", response_model=RuntimeSettingsResponse)
    def update_runtime_settings(
        request: Request,
        payload: RuntimeSettingsUpdateRequest,
    ) -> RuntimeSettingsResponse:
        client_host = request.client.host if request.client else None
        if not is_local_client_host(client_host):
            raise HTTPException(
                status_code=403,
                detail="runtime settings can only be updated from a local client",
            )

        context = get_context()
        if context.runtime_env_path is None or context.reload_runtime_dependencies is None:
            raise HTTPException(status_code=500, detail="runtime settings are unavailable")
        apply_runtime_settings_update(
            context.runtime_env_path,
            payload.model_dump(),
            context.reload_runtime_dependencies,
        )
        return RuntimeSettingsResponse.model_validate(
            get_context().settings.safe_runtime_settings()
        )

    return router
