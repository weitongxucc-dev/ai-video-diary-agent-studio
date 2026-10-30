"""Current task output downloads; version qualification, artifact ownership and subtitle rendering stay host-owned."""
from typing import Literal
from fastapi import APIRouter, Request, Response
from fastapi.responses import FileResponse

def build_task_output_router(*, readable_for, output_case_for, workspace_for, output_error_for, http_error_for, file_response_for, tracks_for, track_converter_for, replace_for, bilingual_for, srt_for, response_for, production_for, store_for, route_names):
    router = APIRouter()

    @router.get('/api/creation-tasks/{task_id}/output')
    def get_creation_task_output(task_id: str, request: Request, expected_version_id: str | None=None) -> FileResponse:
        readable_for()(request, task_id)
        return _creation_task_download_response(task_id, request, expected_version_id, subtitle=False)

    @router.get('/api/creation-tasks/{task_id}/output/subtitles')
    def get_creation_task_subtitles(task_id: str, request: Request, expected_version_id: str | None=None) -> FileResponse:
        readable_for()(request, task_id)
        return _creation_task_download_response(task_id, request, expected_version_id, subtitle=True)

    def _creation_task_download_response(task_id: str, request: Request, expected_version_id: str | None, *, subtitle: bool) -> FileResponse:
        try:
            result = output_case_for()().download(workspace_for()(request), task_id, expected_version_id=expected_version_id, subtitle=subtitle)
        except output_error_for() as exc:
            raise http_error_for()(status_code=exc.status_code, detail=exc.detail) from exc
        headers = {'Cache-Control': 'no-store'}
        if result.version_id is not None:
            headers.update({'X-Timeline-Version-Id': result.version_id, 'X-Timeline-Sequence': str(result.sequence)})
        return file_response_for()(result.path, media_type='application/x-subrip' if subtitle else 'video/mp4', filename=f'{task_id}.srt' if subtitle else f'{task_id}.mp4', headers=headers)

    @router.get('/api/creation-tasks/{task_id}/output/selected-subtitles')
    def get_creation_task_selected_subtitles(task_id: str, request: Request, expected_version_id: str, mode: Literal['zh', 'en', 'zh-en'], zh_track_id: str | None=None, en_track_id: str | None=None) -> Response:
        workspace_id = workspace_for()(request)
        task = readable_for()(request, task_id)
        if not task.current_timeline_version_id or task.current_timeline_version_id != expected_version_id:
            raise http_error_for()(status_code=409, detail='当前字幕版本已变化，请重新选择后保存。')
        required = {'zh': ('zh-CN',), 'en': ('en-US',), 'zh-en': ('zh-CN', 'en-US')}[mode]
        requested = {'zh-CN': zh_track_id, 'en-US': en_track_id}
        if any((not requested[language] for language in required)):
            raise http_error_for()(status_code=422, detail='请选择要保存的字幕轨。')
        if any((requested[language] for language in {'zh-CN', 'en-US'} - set(required))):
            raise http_error_for()(status_code=422, detail='字幕语言与所选保存方式不一致。')
        tracks = tracks_for()(workspace_id, task)
        selected = {}
        for language in required:
            track = next((item for item in tracks if item.id == requested[language]), None)
            if track is None or track.language != language or track.task_id != task_id or (track.timeline_version_id != expected_version_id):
                raise http_error_for()(status_code=409, detail='所选字幕轨已变化，请重新选择后保存。')
            if track.status != 'valid' or not track.cues:
                raise http_error_for()(status_code=409, detail='所选字幕轨目前不可保存，请先检查或重新生成字幕。')
            selected[language] = track_converter_for()(track)
        try:
            if mode == 'zh-en':
                chinese = selected['zh-CN']
                if selected['en-US'].source_language != 'zh-CN':
                    raise ValueError('英文字幕与当前中文字幕没有来源关联')
                chinese = replace_for()(chinese, style=replace_for()(chinese.style, bilingual_order='source_then_translation'))
                content = bilingual_for()(chinese, selected['en-US'])
            else:
                content = srt_for()(selected[required[0]])
        except ValueError as exc:
            raise http_error_for()(status_code=409, detail=f'字幕内容或时间无法保存：{exc}') from exc
        if not content.strip():
            raise http_error_for()(status_code=409, detail='当前字幕文件为空，请检查字幕后重试。')
        return response_for()(content=content, media_type='application/x-subrip; charset=utf-8')

    @router.get('/api/creation-tasks/{task_id}/covers/{cover_id}/image')
    def get_creation_task_cover_image(task_id: str, cover_id: str, request: Request) -> FileResponse:
        workspace_id = workspace_for()(request)
        readable_for()(request, task_id)
        try:
            cover = production_for().cover_records.get_cover_asset(workspace_id, task_id, cover_id)
        except KeyError as exc:
            raise http_error_for()(status_code=404, detail='creation task cover not found') from exc
        if cover.status != 'valid' or not cover.artifact_ref:
            raise http_error_for()(status_code=404, detail='creation task cover is not available')
        registered = next((artifact for artifact in production_for().execution_records.list_artifact_records(workspace_id, task_id) if artifact.logical_ref == cover.artifact_ref and artifact.kind == 'cover_image'), None)
        if registered is None:
            raise http_error_for()(status_code=409, detail='creation task cover is not a registered artifact')
        try:
            path = store_for().resolve_file(cover.artifact_ref)
        except (FileNotFoundError, ValueError) as exc:
            raise http_error_for()(status_code=404, detail='creation task cover not found') from exc
        media_type = 'image/jpeg' if path.suffix.lower() in {'.jpg', '.jpeg'} else 'image/png'
        return file_response_for()(path, media_type=media_type, filename=path.name)
    router.routes = [route for route in router.routes if route.name in route_names]
    return router
