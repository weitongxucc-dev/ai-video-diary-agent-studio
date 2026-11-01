"""Task listing, workspace and visibility HTTP; reads, projections and persistence retain original owners."""
from fastapi import APIRouter, HTTPException, Request
from app.schemas import CreationTasksResponse, CreationTaskWorkspaceResponse, ContentVisibilityRequest
from dataclasses import asdict
from app.creation.contracts import CreationTask
from app.creation.task_workspace_read_use_case import TaskWorkspaceNotFound, TaskWorkspaceSubtitleInvalid

class TaskWorkspaceResponseAdapter:
    def __init__(self, *, reader_for, encode_plan, encode_narration, encode_person_render, encode_montage):
        self.reader_for = reader_for
        self.encode_plan = encode_plan
        self.encode_narration = encode_narration
        self.encode_person_render = encode_person_render
        self.encode_montage = encode_montage

    def response(
        self,
        workspace_id: str,
        task: CreationTask,
    ) -> CreationTaskWorkspaceResponse:
        reader = self.reader_for()
        try:
            facts = reader.read(workspace_id, task)
        except TaskWorkspaceNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except TaskWorkspaceSubtitleInvalid as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        plan = facts.plan
        selected_topic = facts.selected_topic
        topic_evidence = facts.topic_evidence
        artifact = facts.artifact
        montage = facts.montage
        task_read = facts.task_read
        person_provider = facts.person_provider
        person_render_available = facts.person_render_available
        subtitle_tracks = facts.subtitle_tracks
        cover_assets = facts.cover_assets
        translation_provider = facts.translation_provider
        translation_failure = facts.translation_failure
        cover_frame_available = facts.cover_frame_available
        image_capability = facts.image_capability
        return CreationTaskWorkspaceResponse.model_validate(
            {
                "task": task_read.summary,
                "plan": self.encode_plan(plan).model_dump(),
                "selected_topic": (
                    {
                        "id": selected_topic.id,
                        "run_id": selected_topic.run_id,
                        "title": selected_topic.title,
                        "angle": selected_topic.angle,
                        "audience": selected_topic.audience,
                        "outline": selected_topic.outline,
                        "score": selected_topic.score,
                        "selection_status": selected_topic.selection_status,
                        "created_at": selected_topic.created_at,
                    }
                    if selected_topic is not None
                    else None
                ),
                "topic_evidence": [
                    {
                        "id": reference.id,
                        "candidate_id": reference.candidate_id,
                        "kind": reference.kind,
                        "reference_id": reference.reference_id,
                        "version_or_identity": reference.version_or_identity,
                        "title": reference.title,
                        "authorization_or_license": reference.authorization_or_license,
                        "source_url": reference.source_url,
                        "created_at": reference.created_at,
                    }
                    for reference in topic_evidence
                ],
                "scripts": reader.read_script_headers(workspace_id, task.id),
                "scripts_scope": "active",
                "available_voices": reader.voice_options(workspace_id, task, plan),
                "narration": (
                    self.encode_narration(task, artifact).model_dump()
                    if artifact is not None
                    else None
                ),
                "portrait_available": reader.portrait_available(workspace_id),
                "person_render_capability": {
                    "available": person_render_available,
                    "model_id": person_provider.model_id if person_provider else "",
                    "message": person_provider.message if person_provider else "0%人物，本任务不需要口型能力。",
                    "repair_action": (
                        None
                        if person_render_available or person_provider is None
                        else "配置本地人物口型运行环境后重试。"
                    ),
                },
                "person_renders": [
                    self.encode_person_render(render).model_dump()
                    for render in reader.read_person_renders(workspace_id, task.id)
                ],
                "montage": (
                    self.encode_montage(montage).model_dump()
                    if montage is not None
                    else None
                ),
                "subtitle_tracks": [asdict(track) for track in subtitle_tracks],
                "cover_asset": asdict(cover_assets[-1]) if cover_assets else None,
                "subtitle_translation_capability": {
                    "available": bool(translation_provider and translation_provider.ready),
                    "model_id": translation_provider.model_id if translation_provider else None,
                    "message": (
                        f"上次翻译未完成：{translation_failure['failure_reason']}"
                        if translation_failure
                        else
                        translation_provider.message
                        if translation_provider
                        else "当前测试运行时未配置文字模型翻译能力。"
                    ),
                    "repair_action": (
                        translation_failure.get("repair_action")
                        if translation_failure
                        else
                        None
                        if translation_provider and translation_provider.ready
                        else translation_provider.message
                        if translation_provider
                        else "配置完整运行时设置后重试。"
                    ),
                },
                "cover_frame_capability": {
                    "available": cover_frame_available,
                    "model_id": "ffmpeg+Pillow" if cover_frame_available else None,
                    "message": (
                        "可以从当前成片选择真实画面生成封面。"
                        if cover_frame_available
                        else "先生成可播放成片，并确认本机 FFmpeg 可用。"
                    ),
                    "repair_action": (
                        None
                        if cover_frame_available
                        else "先完成成片渲染或修复 FFmpeg/FFprobe 环境。"
                    ),
                },
                "image_generation_capability": {
                    "available": False,
                    "model_id": None,
                    "message": image_capability.impact,
                    "repair_action": image_capability.remediation,
                },
            }
        )


def build_task_workspace_router(*, workspace_for, reader_for, production_for, private_for, list_response_for, summary_for, write_guard_for, http_error_for, workspace_response_for, task_for, readable_for, route_names):
    router = APIRouter()

    @router.get('/api/creation-tasks', response_model=CreationTasksResponse)
    def list_creation_tasks(request: Request) -> CreationTasksResponse:
        workspace_id = workspace_for()(request)
        viewer_user_id, local_diagnostic = reader_for()(request, workspace_id)
        tasks = production_for().task_records.list_creation_tasks(workspace_id, include_private=private_for()(request), viewer_user_id=viewer_user_id, local_diagnostic=local_diagnostic)
        return list_response_for()(items=[summary_for()(production_for(), item).summary for item in tasks], total_count=len(tasks))

    @router.put('/api/creation-tasks/{task_id}/visibility', response_model=CreationTaskWorkspaceResponse)
    def set_creation_task_visibility_route(task_id: str, request: Request, payload: ContentVisibilityRequest) -> CreationTaskWorkspaceResponse:
        """显式共享或收回一条创作任务；响应回读同一列。"""
        denied = write_guard_for()(request)
        if denied is not None:
            return denied
        workspace_id = workspace_for()(request)
        try:
            production_for().task_records.set_creation_task_visibility(workspace_id, task_id, payload.visibility)
        except KeyError as exc:
            raise http_error_for()(status_code=404, detail='creation task not found') from exc
        except ValueError as exc:
            raise http_error_for()(status_code=422, detail=str(exc)) from exc
        return workspace_response_for()(workspace_id, task_for()(workspace_id, task_id))

    @router.get('/api/creation-tasks/{task_id}', response_model=CreationTaskWorkspaceResponse)
    def get_creation_task_workspace(task_id: str, request: Request) -> CreationTaskWorkspaceResponse:
        workspace_id = workspace_for()(request)
        return workspace_response_for()(workspace_id, readable_for()(request, task_id))
    router.routes[:] = [route for route in router.routes if route.name in route_names]
    return router
