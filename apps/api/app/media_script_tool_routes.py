"""Saved media-script tool HTTP adapters; registration and submission rules remain with their original owners."""
from uuid import UUID
from fastapi import APIRouter, Request
from app.schemas import MediaScriptToolSaveRequest, MediaScriptToolUpdateRequest, MediaScriptToolRevokeRequest, MediaScriptToolPreviewRequest, TaskMediaJobResponse

def build_media_script_tool_router(*, access_for, actor_for, conversations_for, origin_for, repository_for, tools_for, script_error_type_for, error_for, workspace_for, capabilities_for, command_for, runner_for, submission_for, json_response_for, http_error_type_for, route_names) -> APIRouter:
    router = APIRouter()

    @router.post('/api/creation-tasks/{task_id}/media-script-tools')
    def save_media_script_tool(task_id: str, request: Request, payload: MediaScriptToolSaveRequest):
        task, member_id = access_for()(task_id, request, write=True)
        actor = actor_for()(request, task.workspace_id)
        if payload.assistant_turn_id is not None:
            try:
                source = conversations_for().task_turn_source(str(payload.assistant_turn_id), task.workspace_id, member_id, task_id)
            except ValueError as exc:
                raise http_error_type_for()(status_code=409, detail=str(exc)) from exc
            origin_for()(request, {**source, 'assistant_turn_id': str(payload.assistant_turn_id)})
            version = repository_for().timeline_records.get_timeline_version(task.workspace_id, task.current_timeline_version_id) if task.current_timeline_version_id else None
            snapshot = source['context'].get('target_snapshot', {})
            if (snapshot.get('timeline_version_id'), snapshot.get('timeline_sequence')) != (task.current_timeline_version_id, version.sequence if version else 0):
                raise http_error_type_for()(status_code=409, detail='保存配方的原任务目标已变化')
        try:
            producer = repository_for().job_records.get_job_run(task.workspace_id, payload.producer_job_id)
            if producer.task_id != task_id:
                raise KeyError(payload.producer_job_id)
            return tools_for()().create_from_job(actor, producer.id, request_id=payload.request_id, name=payload.name, description=payload.description, parameter_schema=payload.parameter_schema)
        except (KeyError, PermissionError, script_error_type_for()) as exc:
            error_for()(exc)

    @router.get('/api/media-script-tools/requests/{request_id}')
    def get_media_script_tool_creation(request_id: UUID, request: Request):
        actor = actor_for()(request, workspace_for()(request))
        try:
            return tools_for()().get_created_for_request(actor, request_id)
        except (KeyError, PermissionError, script_error_type_for()) as exc:
            error_for()(exc)

    @router.get('/api/media-script-tools/capabilities')
    def media_script_tool_capabilities(request: Request):
        actor_for()(request, workspace_for()(request))
        return capabilities_for()()

    @router.get('/api/media-script-tools')
    def list_media_script_tools(request: Request, include_inactive: bool=False):
        actor = actor_for()(request, workspace_for()(request))
        try:
            values = tools_for()().list(actor, include_inactive=include_inactive, limit=25)
            return {'items': values[:24], 'truncated': len(values) > 24}
        except (KeyError, PermissionError, script_error_type_for()) as exc:
            error_for()(exc)

    @router.get('/api/media-script-tools/{tool_id}')
    def get_media_script_tool(tool_id: str, request: Request, version: int | None=None):
        actor = actor_for()(request, workspace_for()(request))
        try:
            return tools_for()().get(actor, tool_id, version=version)
        except (KeyError, PermissionError, script_error_type_for()) as exc:
            error_for()(exc)

    @router.patch('/api/media-script-tools/{tool_id}')
    def update_media_script_tool(tool_id: str, request: Request, payload: MediaScriptToolUpdateRequest):
        actor = actor_for()(request, workspace_for()(request))
        try:
            return tools_for()().update_from_job(actor, tool_id, expected_version=payload.expected_version, job_id=payload.producer_job_id, name=payload.name, description=payload.description, parameter_schema=payload.parameter_schema)
        except (KeyError, PermissionError, script_error_type_for()) as exc:
            error_for()(exc)

    @router.post('/api/media-script-tools/{tool_id}/revoke')
    def revoke_media_script_tool(tool_id: str, request: Request, payload: MediaScriptToolRevokeRequest):
        actor = actor_for()(request, workspace_for()(request))
        try:
            return tools_for()().revoke(actor, tool_id, expected_version=payload.expected_version)
        except (KeyError, PermissionError, script_error_type_for()) as exc:
            error_for()(exc)

    @router.post('/api/creation-tasks/{task_id}/media-script-tools/{tool_id}/preview', response_model=TaskMediaJobResponse)
    def preview_media_script_tool(task_id: str, tool_id: str, request: Request, payload: MediaScriptToolPreviewRequest):
        command = command_for()(task_id, payload, tool_id=tool_id)
        result = runner_for()(request, submission_for()().submit(command))
        return json_response_for()(status_code=result.status_code, content=result.body)
    router.routes = [route for route in router.routes if route.name in route_names]
    return router
