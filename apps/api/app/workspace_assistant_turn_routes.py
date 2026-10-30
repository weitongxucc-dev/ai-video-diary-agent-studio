"""Workspace planning and material turn HTTP; shared admission, planning and streaming owners remain unchanged."""
from fastapi import APIRouter, Request
from app.schemas import WorkspaceAssistantPlanTurnRequest, WorkspaceAssistantPlanTurnResponse, MaterialAssistantTurnRequest, MaterialAssistantTurnResponse

def build_workspace_assistant_turn_router(*, workspace_for, stream_for, plan_for, member_for, conversations_for, selection_for, plan_request_for, material_response_for, route_names):
    router = APIRouter()

    @router.post('/api/assistant/plan/turn', response_model=WorkspaceAssistantPlanTurnResponse)
    def workspace_assistant_plan_turn(request: Request, payload: WorkspaceAssistantPlanTurnRequest) -> WorkspaceAssistantPlanTurnResponse:
        if 'text/event-stream' in request.headers.get('accept', '').lower():
            workspace_id = workspace_for()(request)
            return stream_for()().response(lambda on_event: plan_for()(request, payload, on_event), workspace_id=workspace_id)
        return plan_for()(request, payload, None)

    @router.post('/api/assistant/material/turn', response_model=MaterialAssistantTurnResponse)
    def material_assistant_turn(request: Request, payload: MaterialAssistantTurnRequest) -> MaterialAssistantTurnResponse:
        if 'text/event-stream' in request.headers.get('accept', '').lower():
            workspace_id = workspace_for()(request)
            return stream_for()().response(lambda on_event: _material_assistant_turn(request, payload, on_event), workspace_id=workspace_id)
        return _material_assistant_turn(request, payload, None)

    def _material_assistant_turn(request: Request, payload: MaterialAssistantTurnRequest, on_event) -> MaterialAssistantTurnResponse:
        selection = None
        workspace_id = workspace_for()(request)
        member_id = member_for()(request, workspace_id)
        previous = conversations_for().request_context(str(payload.client_request_id), str(payload.conversation_id), workspace_id, member_id)
        if payload.selection is not None:
            selection, _asset = selection_for()(request, payload.selection.model_dump(mode='json', exclude_none=True), replay=previous is not None)
            if previous is not None:
                selection = previous['target_snapshot'].get('material_selection')
        response = plan_for()(request, plan_request_for()(**payload.model_dump(exclude={'selection'})), on_event, material_selection=selection)
        return material_response_for().model_validate(response.model_dump())
    router.routes[:] = [route for route in router.routes if route.name in route_names]
    return router
