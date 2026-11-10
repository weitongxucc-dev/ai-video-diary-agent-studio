"""Video-template HTTP and current readable-version resolution.

The existing use case and production owner retain source, settings, version/CAS
and persistence rules. Shared task/output/subtitle readers remain host ports.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Callable, Protocol

from fastapi import APIRouter, HTTPException, Request
from app.creation.video_template_use_case import VideoTemplateRejected, VideoTemplateUseCase
from app.creation.video_template_records import VideoTemplate
from app.schemas import ContentVisibilityRequest, SaveVideoTemplateRequest, VideoTemplateVersionRequest, VisualTemplateCopyRequest
from app.creation.visual_template_contracts import VisualTemplate


class VideoTemplateHTTPRecords(Protocol):
    def get_video_template(self, workspace_id, template_id, version): ...
    def list_video_templates(self, workspace_id, *, viewer_user_id=None, local_diagnostic=False): ...
    def set_video_template_visibility(self, workspace_id, template_id, visibility): ...


class VideoTemplateHTTPAdapter:
    def __init__(self, *, case_for: Callable[[], VideoTemplateUseCase],
                 records_for: Callable[[], VideoTemplateHTTPRecords],
                 workspace_for, reader_for, write_guard, task_for, plan_for,
                 output_for, tracks_for):
        self._case_for = case_for
        self._records_for = records_for
        self._workspace_for = workspace_for
        self._reader_for = reader_for
        self._write_guard = write_guard
        self._task_for = task_for
        self._plan_for = plan_for
        self._output_for = output_for
        self._tracks_for = tracks_for


    def read_template(self, request: Request, workspace_id: str, template_id: str, version: int) -> VideoTemplate:
        case = self._case_for()
        item = case.builtin_template(workspace_id, template_id, version)
        if item is not None:
            return item
        try:
            item = self._records_for().get_video_template(workspace_id, template_id, version)
            user_id, local_diagnostic = self._reader_for(request, workspace_id)
            case.require_readable(item, template_id, reader_user_id=user_id, local_diagnostic=local_diagnostic)
            return item
        except KeyError as exc:
            raise HTTPException(status_code=404, detail='当前工作区找不到这个模板版本。') from exc

    def set_video_template_visibility_route(self, template_id: str, request: Request, payload: ContentVisibilityRequest) -> dict[str, object]:
        """显式共享或收回一个用户模板；返回同一列的回读值。"""
        denied = self._write_guard(request)
        if denied is not None:
            return denied
        workspace_id = self._workspace_for(request)
        user_id, _ = self._reader_for(request, workspace_id)
        versions = self._records_for().list_video_templates(workspace_id, viewer_user_id=user_id)
        previous = next((item for item in versions if item.id == template_id), None)
        if previous is None:
            raise HTTPException(status_code=404, detail='当前工作区找不到这个模板。')
        if user_id is None or previous.owner_user_id != user_id:
            raise HTTPException(status_code=403, detail='只有这个模板的所有者可以修改可见范围。')
        try:
            template = self._records_for().set_video_template_visibility(workspace_id, template_id, payload.visibility)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail='video template not found') from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {'item': asdict(template)}

    def list_video_templates(self, request: Request) -> dict[str, object]:
        workspace_id = self._workspace_for(request)
        user_id, local_diagnostic = self._reader_for(request, workspace_id)
        return {'items': [{**asdict(item), 'can_edit': item.origin == 'user' and user_id is not None and (item.owner_user_id == user_id)} for item in [*self._case_for().builtin_templates(workspace_id), *self._records_for().list_video_templates(workspace_id, viewer_user_id=user_id, local_diagnostic=local_diagnostic)]]}

    def create_video_template_version(self, template_id: str, request: Request, payload: VideoTemplateVersionRequest) -> dict[str, object]:
        workspace_id = self._workspace_for(request)
        user_id, _diagnostic = self._reader_for(request, workspace_id)
        previous = self.read_template(request, workspace_id, template_id, payload.expected_version)
        case = self._case_for()
        try:
            saved = case.append_version(previous, expected_version=payload.expected_version, name=payload.name, style_instruction=payload.style_instruction, user_id=user_id, visual=payload.visual)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except VideoTemplateRejected as exc:
            status = {'field_builtin_readonly': 409, 'field_not_owner': 403, 'persist_conflict': 409}[exc.code]
            failure = HTTPException(status_code=status, detail=exc.message)
            if exc.__cause__ is not None:
                raise failure from exc.__cause__
            raise failure from None
        return {**asdict(saved), 'can_edit': True}

    def save_visual_copy(self, request, payload):
        denied = self._write_guard(request)
        if denied is not None:
            return denied
        workspace_id = self._workspace_for(request)
        user_id, _ = self._reader_for(request, workspace_id)
        previous = self.read_template(request, workspace_id, payload.source_template_id, payload.source_template_version)
        try:
            saved = self._case_for().save_visual_copy(previous,
                template_id=f'template_{payload.request_id.hex}', name=payload.name.strip(),
                visual=payload.visual, user_id=user_id)
        except (ValueError, VideoTemplateRejected) as exc:
            raise HTTPException(409, str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        return {**asdict(saved), 'can_edit': True}

    def save_creation_task_template(self, task_id: str, request: Request, payload: SaveVideoTemplateRequest) -> dict[str, object]:
        workspace_id = self._workspace_for(request)
        case = self._case_for()
        task = self._task_for(workspace_id, task_id)
        plan = self._plan_for(workspace_id, task)
        try:
            montage = case.rendered_source(workspace_id, task_id, task)
            try:
                output = self._output_for(workspace_id, task_id, subtitle=False)
                case.require_nonempty_output(output)
            except (HTTPException, OSError) as exc:
                raise HTTPException(status_code=409, detail='当前成片文件不可用，请恢复文件或重新渲染后再保存模板。') from exc
            case.check_save_target(payload.template_id, payload.expected_version)
            if payload.template_id:
                previous = self.read_template(request, workspace_id, payload.template_id, payload.expected_version)
                case.check_save_template_origin(previous)
                reader_id, _ = self._reader_for(request, workspace_id)
                case.require_save_owner(previous, reader_id=reader_id)
            tracks = self._tracks_for(workspace_id, task, montage=montage)
            template = case.build_from_task(workspace_id, task_id, task, plan, montage, tracks, name=payload.name, style_instruction=payload.style_instruction, template_id=payload.template_id, expected_version=payload.expected_version)
            _, template_local_diagnostic = self._reader_for(request, self._workspace_for(request))
            try:
                return asdict(case.persist_task_template(template, verified_owner_user_id=self._reader_for(request, self._workspace_for(request))[0], local_diagnostic=template_local_diagnostic))
            except ValueError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
        except VideoTemplateRejected as exc:
            status = {'task_not_rendered': 409, 'target_pair_incomplete': 422, 'task_builtin_overwrite': 409, 'task_not_owner': 403, 'persist_conflict': 409}[exc.code]
            failure = HTTPException(status_code=status, detail=exc.message)
            if exc.__cause__ is not None:
                raise failure from exc.__cause__
            raise failure from None


def build_video_template_router(adapter_for, route_names: tuple[str, ...] | None = None) -> APIRouter:
    """Preserve original route positions among other HTTP families."""
    router = APIRouter()

    @router.put('/api/video-templates/{template_id}/visibility')
    def set_video_template_visibility_route(template_id: str, request: Request, payload: ContentVisibilityRequest) -> dict[str, object]:
        return adapter_for().set_video_template_visibility_route(template_id, request, payload)

    @router.get('/api/video-templates')
    def list_video_templates(request: Request) -> dict[str, object]:
        return adapter_for().list_video_templates(request)

    @router.post('/api/video-templates/{template_id}/versions')
    def create_video_template_version(template_id: str, request: Request, payload: VideoTemplateVersionRequest) -> dict[str, object]:
        return adapter_for().create_video_template_version(template_id, request, payload)

    @router.get('/api/video-templates/{template_id}')
    def read_video_template(template_id: str, version: int, request: Request):
        adapter = adapter_for()
        workspace = adapter._workspace_for(request)
        item = adapter.read_template(request, workspace, template_id, version)
        user, _ = adapter._reader_for(request, workspace)
        return {**asdict(item), 'can_edit': item.origin == 'user' and user is not None and item.owner_user_id == user,
            'visual_schema': VisualTemplate.model_json_schema()}

    @router.post('/api/video-templates')
    def save_visual_template(payload: VisualTemplateCopyRequest, request: Request):
        return adapter_for().save_visual_copy(request, payload)

    @router.post('/api/video-templates/visual-preview')
    def visual_template_preview(payload: VisualTemplate, request: Request):
        from app.media.template_document import template_document
        from app.creation.visual_template_catalog import trusted_inline_visual
        adapter = adapter_for()
        adapter._reader_for(request, adapter._workspace_for(request))
        if not trusted_inline_visual(payload):
            return {'document': None, 'reason': '自定义源码请保存为模板，在作品中通过预览或重新输出运行；草稿已保留。'}
        try:
            return {'document': template_document(payload)}
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.post('/api/creation-tasks/{task_id}/templates')
    def save_creation_task_template(task_id: str, request: Request, payload: SaveVideoTemplateRequest) -> dict[str, object]:
        return adapter_for().save_creation_task_template(task_id, request, payload)

    if route_names is not None:
        router.routes = [route for route in router.routes if route.name in route_names]
    return router
