"""Task segment HTTP reads and original revision requests; section and revision transactions remain original owners."""
from fastapi import APIRouter, Query, Request

def build_task_segment_router(*, workspace_for, case_for, actor_for, http_error_for, automation_guard_for, origin_for, production_for, conversations_for, runtime_for, file_response_for, route_names):
    router = APIRouter()

    @router.get('/api/creation-tasks/{task_id}/segments')
    def creation_task_segments(task_id: str, request: Request, offset: int=Query(0, ge=0), limit: int=Query(30, ge=1, le=100)):
        ws = workspace_for()(request)
        try:
            return case_for()().list_segments(ws, task_id, actor_for()(request, ws), offset=offset, limit=limit)
        except PermissionError as exc:
            raise http_error_for()(status_code=403, detail=str(exc)) from exc
        except KeyError as exc:
            raise http_error_for()(status_code=404, detail='当前任务或段落不存在') from exc
        except ValueError as exc:
            raise http_error_for()(status_code=409, detail=str(exc)) from exc

    @router.get('/api/creation-tasks/{task_id}/segments/{section_id}/context')
    def creation_task_segment_context(task_id: str, section_id: str, request: Request):
        ws = workspace_for()(request)
        try:
            return case_for()().context(ws, task_id, section_id, actor_for()(request, ws))
        except PermissionError as exc:
            raise http_error_for()(status_code=403, detail=str(exc)) from exc
        except (KeyError, ValueError) as exc:
            raise http_error_for()(status_code=409, detail=str(exc)) from exc

    @router.get('/api/creation-tasks/{task_id}/segments/{section_id}/sources')
    def creation_task_segment_sources(task_id: str, section_id: str, request: Request, offset: int=Query(0, ge=0), limit: int=Query(8, ge=1, le=24), text_offset: int=Query(0, ge=0), max_chars: int=Query(3000, ge=1, le=8000)):
        ws = workspace_for()(request)
        try:
            return case_for()().sources(ws, task_id, section_id, actor_for()(request, ws), offset=offset, limit=limit, text_offset=text_offset, max_chars=max_chars)
        except PermissionError as exc:
            raise http_error_for()(status_code=403, detail=str(exc)) from exc
        except KeyError as exc:
            raise http_error_for()(status_code=404, detail='原段落证据不存在') from exc

    @router.post('/api/creation-tasks/{task_id}/segments/{section_id}/revision')
    def revise_creation_task_segment(task_id: str, section_id: str, payload: dict, request: Request):
        from app.schemas import NarrationRevisionRequest
        from pydantic import ValidationError
        ws = workspace_for()(request)
        automation_guard_for()(request, ws, task_id)
        try:
            actor, origin = origin_for()(task_id, request, payload)
            command = NarrationRevisionRequest.model_validate({key: value for key, value in payload.items() if key != 'assistant_turn_id'})
            if command.section_id != section_id:
                raise ValueError('路由与稳定段落ID不一致')
            review = production_for().execution_records.get_task_event(ws, command.semantic_review_ref)
            source = review.payload.get('source', {})
            if (source.get('conversation_id'), source.get('assistant_turn_id'), source.get('input_id') or source.get('assistant_turn_id')) != (origin['conversation_id'], origin['assistant_turn_id'], origin['input_id']):
                raise ValueError('语义回执不属于当前可信助手回合与原输入')
            case = case_for()()

            def commit_guard(conn):
                current = conversations_for()._task_turn_source(conn, origin['assistant_turn_id'], ws, actor.member_id, task_id)
                if (current['conversation_id'], current.get('input_id') or origin['assistant_turn_id']) != (origin['conversation_id'], origin['input_id']):
                    raise ValueError('提交时原输入已变化，未登记旧修改影响')
                case.verify_revision(ws, task_id, actor, command, connection=conn)
                conversations_for().record_input_business_effect(origin['assistant_turn_id'], ws, actor.member_id, input_id=command.input_id, request_id=command.request_id, status='queued', adoption_mode=command.adoption_mode, connection=conn)
            return case.submit_revision(ws, task_id, actor, command, runtime=runtime_for()(), write_guard=commit_guard)
        except PermissionError as exc:
            raise http_error_for()(status_code=403, detail=str(exc)) from exc
        except (KeyError, ValueError, ValidationError) as exc:
            raise http_error_for()(status_code=409, detail=str(exc)) from exc

    @router.get('/api/creation-tasks/{task_id}/segment-requests/{request_id}')
    def creation_task_segment_request(task_id: str, request_id: str, request: Request):
        ws = workspace_for()(request)
        try:
            return case_for()().read_request(ws, task_id, request_id, actor_for()(request, ws))
        except PermissionError as exc:
            raise http_error_for()(status_code=403, detail=str(exc)) from exc
        except KeyError as exc:
            raise http_error_for()(status_code=404, detail='原修改请求不存在') from exc

    @router.post('/api/creation-tasks/{task_id}/segment-requests/{request_id}/resume')
    def resume_creation_task_segment(task_id: str, request_id: str, request: Request):
        ws = workspace_for()(request)
        automation_guard_for()(request, ws, task_id)
        try:
            return case_for()().resume_request(ws, task_id, request_id, actor_for()(request, ws), runtime=runtime_for()())
        except PermissionError as exc:
            raise http_error_for()(status_code=403, detail=str(exc)) from exc
        except (KeyError, ValueError) as exc:
            raise http_error_for()(status_code=409, detail=str(exc)) from exc

    @router.get('/api/creation-tasks/{task_id}/segments/{section_id}/versions/{version}/video')
    def creation_task_segment_video(task_id: str, section_id: str, version: str, request: Request):
        ws = workspace_for()(request)
        try:
            path = case_for()().video_path(ws, task_id, section_id, version, actor_for()(request, ws))
            return file_response_for()(path, media_type='video/mp4')
        except PermissionError as exc:
            raise http_error_for()(status_code=403, detail=str(exc)) from exc
        except (KeyError, ValueError, FileNotFoundError) as exc:
            raise http_error_for()(status_code=404, detail='此段落版本的视频不可读取') from exc
    router.routes = [route for route in router.routes if route.name in route_names]
    return router
