"""Task editing and execution capability HTTP; shared fact readers retain their owners."""
from fastapi import APIRouter, Request

def build_task_capability_router(*, access_for, edit_facts_for, execution_facts_for):
    router = APIRouter()

    @router.get('/api/creation-tasks/{task_id}/assistant/edit-capabilities')
    def get_creation_task_edit_capabilities(task_id: str, request: Request):
        task, _member_id = access_for()(task_id, request)
        return edit_facts_for()(task)

    @router.get('/api/creation-tasks/{task_id}/assistant/execution-capabilities')
    def get_creation_task_execution_capabilities(task_id: str, request: Request):
        task, _member_id = access_for()(task_id, request)
        return execution_facts_for()(task)
    return router
