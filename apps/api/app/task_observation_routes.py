"""Task output observation HTTP; shared facts, original-request reconciliation and scheduling stay host-owned."""
from fastapi import APIRouter, Request
from app.schemas import CreationTaskObservationRequest

def build_task_observation_router(*, access_for, metadata_for, actor_for, case_for, json_response_for, prepare_for, runtime_for, receipt_for, output_error_for, http_error_for, route_names):
    router = APIRouter()

    def _task_observation_facts(task_id, request):
        task, member_id = access_for()(task_id, request)
        return metadata_for()(task, member_id)

    @router.get('/api/creation-tasks/{task_id}/observations')
    def get_creation_task_observations(task_id: str, request: Request):
        return _task_observation_facts(task_id, request)

    @router.post('/api/creation-tasks/{task_id}/observations')
    def observe_creation_task_output(task_id: str, request: Request, payload: CreationTaskObservationRequest):
        task, _member = access_for()(task_id, request, write=True)
        actor = actor_for()(request, task.workspace_id)
        try:
            previous = case_for()().original_request(task, actor, payload)
            if previous is not None:
                return json_response_for()(status_code=202, content=previous)
            source = prepare_for()(task_id, request, payload)
            runtime = runtime_for()()
            receipt = case_for()().submit(runtime, runtime.registrations['creation_job'].owner.task_media, task, actor, payload, source, receipt_for=receipt_for())
        except output_error_for() as exc:
            raise http_error_for()(status_code=exc.status_code, detail=exc.detail) from exc
        except PermissionError as exc:
            raise http_error_for()(status_code=403, detail=str(exc)) from exc
        except (ValueError, KeyError) as exc:
            raise http_error_for()(status_code=422, detail=str(exc)) from exc
        return json_response_for()(status_code=202, content=receipt)
    router.routes = [route for route in router.routes if route.name in route_names]
    return router
