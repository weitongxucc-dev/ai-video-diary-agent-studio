"""Assistant fixed-version reference HTTP; shared snapshot and live grant qualification stay host-owned."""
from typing import Literal
from fastapi import APIRouter, Request

def build_assistant_reference_router(*, member_for, workspace_for, profile_snapshot_for, knowledge_snapshot_for, route_names):
    router = APIRouter()

    @router.get('/api/assistant/profile-references/{kind}/{profile_id}')
    def assistant_profile_reference(kind: Literal['person', 'voice'], profile_id: str, request: Request, version: str):
        member_for()(request, workspace_for()(request))
        return profile_snapshot_for()(request, kind, profile_id, version)

    @router.get('/api/assistant/knowledge-references/{knowledge_item_id}')
    def assistant_knowledge_reference(knowledge_item_id: str, request: Request, version: str, locator: str | None=None):
        member_for()(request, workspace_for()(request))
        return knowledge_snapshot_for()(request, knowledge_item_id, version, locator, include_content=True)
    router.routes = [route for route in router.routes if route.name in route_names]
    return router
