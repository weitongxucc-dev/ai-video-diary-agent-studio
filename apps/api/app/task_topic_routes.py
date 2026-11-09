"""Task topic HTTP; source preflight, generation facts and adoption transactions retain original owners."""
from fastapi import APIRouter, Request
from app.schemas import CreationTaskTopicsResponse, CreationTaskTopicGenerateRequest, CreationTaskTopicConfirmRequest

def build_task_topic_router(*, workspace_for, task_for, plan_for, route_for, preflight_error_for, source_inputs_for, response_for, automation_guard_for, draft_for, current_inputs_for, draft_error_for, http_error_for, adoption_for, production_for, reader_for, failure_for):
    router = APIRouter()

    @router.get('/api/creation-tasks/{task_id}/topics', response_model=CreationTaskTopicsResponse)
    def get_creation_task_topics(task_id: str, request: Request) -> CreationTaskTopicsResponse:
        workspace_id = workspace_for()(request)
        task = task_for()(workspace_id, task_id)
        plan = plan_for()(workspace_id, task)
        route_snapshot = route_for()(workspace_id)
        preflight_error: preflight_error_for() | None = None
        input_count = 0
        try:
            sources, _, _ = source_inputs_for()().prepare(workspace_id, task, plan, cloud_egress=route_snapshot['execution_location'] == 'cloud')
            input_count = len(sources)
            if route_snapshot['availability'] == 'unconfigured':
                preflight_error = preflight_error_for()(reason='capability_unavailable', impact='当前没有可调用的文字模型连接，不能开始选题。', repair_action='在本机运行时设置中配置 OpenAI-compatible 文字模型后重试。', retryable=True)
        except preflight_error_for() as exc:
            preflight_error = exc
        return response_for()(workspace_id, task, route_snapshot=route_snapshot, preflight_error=preflight_error, preflight_input_count=input_count)

    @router.post('/api/creation-tasks/{task_id}/topics/generate', response_model=CreationTaskTopicsResponse)
    def generate_creation_task_topics(task_id: str, request: Request, _payload: CreationTaskTopicGenerateRequest) -> CreationTaskTopicsResponse:
        workspace_id = workspace_for()(request)
        automation_guard_for()(request, workspace_id, task_id)
        task = task_for()(workspace_id, task_id)
        try:
            return draft_for()(workspace_id, validate_inputs=lambda ws, plan, refs: current_inputs_for()(request, ws, plan, refs)).generate_topics(workspace_id, task)
        except draft_error_for() as exc:
            raise http_error_for()(status_code=exc.status_code, detail=exc.detail) from exc

    @router.post('/api/creation-tasks/{task_id}/topics/{topic_id}/confirm', response_model=CreationTaskTopicsResponse)
    def confirm_creation_task_topic(task_id: str, topic_id: str, request: Request, payload: CreationTaskTopicConfirmRequest) -> CreationTaskTopicsResponse:
        workspace_id = workspace_for()(request)
        if payload.adoption_kind == 'delegated_system' and payload.assistant_turn_id is not None:
            adoption_for()(task_id, request, str(payload.assistant_turn_id))
        try:
            task, _ = production_for().topic_records.confirm_topic_candidate(workspace_id, task_id, topic_id, title=payload.title, adoption_kind=payload.adoption_kind, verified_owner_user_id=reader_for()(request, workspace_id)[0])
        except KeyError as exc:
            raise http_error_for()(status_code=404, detail='creation task topic not found') from exc
        except ValueError as exc:
            raise failure_for()(status_code=409, error='creation_task_topic_confirmation_conflict', reason='当前选题不能被确认。', impact='没有改变已确认的选题或任务阶段。', repair_action=str(exc), retryable=False) from exc
        return response_for()(workspace_id, task)
    return router
