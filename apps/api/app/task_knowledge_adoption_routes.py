"""Fixed knowledge adoption HTTP; reference authority and plan/task/receipt transactions retain their original owner."""
from uuid import UUID
from fastapi import APIRouter, Request
from app.schemas import TaskKnowledgeAdoptionRequest, TaskKnowledgeAdoptionResponse

def build_task_knowledge_adoption_router(*, access_for, adoption_for, production_for, source_for, http_error_for, response_for):
    router = APIRouter()

    @router.post('/api/creation-tasks/{task_id}/knowledge-adoptions', response_model=TaskKnowledgeAdoptionResponse)
    def adopt_creation_task_knowledge(task_id: str, request: Request, payload: TaskKnowledgeAdoptionRequest) -> TaskKnowledgeAdoptionResponse:
        from app.creation.knowledge_adoption_use_case import KnowledgeAdoptionCommand
        task, member_id = access_for()(task_id, request, write=True)
        if payload.assistant_turn_id is not None:
            adoption_for()(task_id, request, str(payload.assistant_turn_id), draft_only=True)
        command = KnowledgeAdoptionCommand(str(payload.request_id), payload.expected_plan_updated_at, payload.expected_task_updated_at, tuple(((item.knowledge_item_id, item.version) for item in payload.knowledge_references)))
        try:
            receipt = production_for().knowledge_adoption.adopt(task.workspace_id, task_id, member_id, command, read_reference=source_for().get_knowledge_reference_readable_by)
        except KeyError as exc:
            raise http_error_for()(status_code=404, detail='没有找到可引用的固定资料版本或当前任务。') from exc
        except PermissionError as exc:
            raise http_error_for()(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise http_error_for()(status_code=409, detail=str(exc)) from exc
        return response_for().model_validate(receipt)

    @router.get('/api/creation-tasks/{task_id}/knowledge-adoptions/{request_id}', response_model=TaskKnowledgeAdoptionResponse)
    def get_creation_task_knowledge_adoption(task_id: str, request_id: UUID, request: Request) -> TaskKnowledgeAdoptionResponse:
        task, member_id = access_for()(task_id, request)
        try:
            receipt = production_for().knowledge_adoption.read_receipt(task.workspace_id, task_id, str(request_id), member_id=member_id)
        except KeyError as exc:
            raise http_error_for()(status_code=404, detail='没有找到此成员在当前任务中的原资料采用回执。') from exc
        except PermissionError as exc:
            raise http_error_for()(status_code=403, detail=str(exc)) from exc
        return response_for().model_validate(receipt)
    return router
