from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter

from app.content_templates import list_content_templates
from app.routers.context import AppContext
from app.runtime_health import runtime_health
from app.schemas import (
    ContentTemplatesResponse,
    RuntimeHealthResponse,
    SystemStatusResponse,
    WorkflowStatusResponse,
)


def create_router(get_context: Callable[[], AppContext]) -> APIRouter:
    router = APIRouter()

    @router.get("/api/system/status", response_model=SystemStatusResponse)
    def get_system_status() -> SystemStatusResponse:
        context = get_context()
        return SystemStatusResponse.model_validate(context.settings.public_status())

    @router.get("/api/workflow/status", response_model=WorkflowStatusResponse)
    def get_workflow_status() -> WorkflowStatusResponse:
        context = get_context()
        return WorkflowStatusResponse(
            framework="LangGraph",
            node_names=context.workflow.node_names(),
            human_review_nodes=context.workflow.human_review_nodes(),
        )

    @router.get("/api/content-templates", response_model=ContentTemplatesResponse)
    def get_content_templates() -> ContentTemplatesResponse:
        return ContentTemplatesResponse.model_validate(
            {"templates": list_content_templates()},
            from_attributes=True,
        )

    @router.get("/api/runtime/health", response_model=RuntimeHealthResponse)
    def get_runtime_health() -> RuntimeHealthResponse:
        context = get_context()
        return RuntimeHealthResponse.model_validate(runtime_health(context.settings))

    return router
