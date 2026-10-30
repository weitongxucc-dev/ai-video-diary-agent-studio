"""Task assistant history HTTP; event persistence, filtering inputs and ordering retain their original owners."""
from fastapi import APIRouter, Request
from app.schemas import CreationTaskAssistantHistoryResponse

def build_task_assistant_history_router(*, workspace_for, task_for, response_for, production_for):
    router = APIRouter()

    @router.get('/api/creation-tasks/{task_id}/assistant/history', response_model=CreationTaskAssistantHistoryResponse)
    def get_creation_task_assistant_history(task_id: str, request: Request) -> CreationTaskAssistantHistoryResponse:
        workspace_id = workspace_for()(request)
        task_for()(workspace_id, task_id)
        return response_for().model_validate({'items': [{'role': event.payload.get('role'), 'message': event.payload.get('message'), 'stage': event.payload.get('stage'), 'script_id': event.payload.get('script_id'), 'at': event.created_at} for event in production_for().execution_records.list_task_events(workspace_id, task_id) if event.event_type == 'assistant_turn' and event.payload.get('role') in {'user', 'assistant'}]})
    return router
