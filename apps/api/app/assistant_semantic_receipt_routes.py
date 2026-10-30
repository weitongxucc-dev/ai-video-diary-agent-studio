"""Assistant input-understanding and narration-review HTTP; trusted input scope and durable receipt ownership remain host-owned."""
from fastapi import APIRouter, Request

def build_assistant_semantic_receipt_router(*, origin_for, conversations_for, production_for, http_error_for, route_names):
    router = APIRouter()

    @router.post('/api/creation-tasks/{task_id}/assistant/input-understanding')
    def creation_task_input_understanding(task_id: str, request: Request, payload: dict):
        actor, origin = origin_for()(task_id, request, payload)
        try:
            return conversations_for().record_input_understanding(origin['conversation_id'], actor.workspace_id, actor.member_id, origin['assistant_turn_id'], None if origin['input_id'] == origin['assistant_turn_id'] else origin['input_id'], **{key: value for key, value in payload.items() if key not in {'assistant_turn_id', 'input_id'}})
        except (ValueError, PermissionError) as exc:
            raise http_error_for()(status_code=409, detail=str(exc)) from exc

    @router.post('/api/creation-tasks/{task_id}/assistant/narration-semantic-review')
    def creation_task_narration_semantic_review(task_id: str, request: Request, payload: dict):
        from app.assistant.task_business import record_narration_semantic_review
        actor, origin = origin_for()(task_id, request, payload)
        task = production_for().task_records.get_creation_task(actor.workspace_id, task_id)

        def guard(conn):
            current = conversations_for()._task_turn_source(conn, origin['assistant_turn_id'], actor.workspace_id, actor.member_id, task_id)
            if (current.get('input_id') or origin['assistant_turn_id']) != origin['input_id']:
                raise ValueError('原输入已变化，不能登记旧语义比较')
        try:
            return record_narration_semantic_review(production_for(), task, origin, {key: value for key, value in payload.items() if key != 'assistant_turn_id'}, write_guard=guard)
        except (ValueError, PermissionError) as exc:
            raise http_error_for()(status_code=409, detail=str(exc)) from exc
    router.routes = [route for route in router.routes if route.name in route_names]
    return router
