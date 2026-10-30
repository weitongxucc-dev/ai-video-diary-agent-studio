"""Read-only historical timeline HTTP and projections; persistence, shared access and artifact ownership remain host-owned."""
from typing import Literal
from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse
from app.creation.contracts import CreationTask, TimelineVersion
from app.schemas import TimelineVersionSummaryResponse, TimelineVersionArtifactResponse, TimelineVersionListResponse, TimelineVersionDetailResponse

def build_timeline_history_router(*, production_for, summary_type_for, history_error_for, http_error_for, cover_for, store_for, quote_for, artifact_type_for, not_generated_for, missing_for, workspace_for, task_for, list_type_for, version_for, shot_type_for, segment_type_for, detail_type_for, subtitle_type_for, asdict_for, cover_type_for, readable_for, media_types_for, file_response_for, route_names):
    router = APIRouter()

    def _history_montage(version: TimelineVersion) -> dict[str, object]:
        montage = version.snapshot.get('montage')
        return montage if isinstance(montage, dict) else {}

    def _history_subtitle_tracks(workspace_id: str, task_id: str, version: TimelineVersion):
        return production_for().subtitle_records.list_subtitle_tracks(workspace_id, task_id, timeline_version_id=version.id)

    def _history_summary(task: CreationTask, version: TimelineVersion, workspace_id: str) -> TimelineVersionSummaryResponse:
        try:
            return summary_type_for().model_validate(production_for().timeline_history.summary(workspace_id, task.id, version.id))
        except history_error_for() as exc:
            raise http_error_for()(status_code=409, detail=str(exc)) from exc

    def _history_artifact_refs(version: TimelineVersion, workspace_id: str, task_id: str) -> dict[str, str | None]:
        """Collect this version's own artifact refs, never the task's current output."""
        montage = _history_montage(version)
        tracks = _history_subtitle_tracks(workspace_id, task_id, version)
        subtitle_ref = next((track.artifact_ref for track in tracks if track.status == 'valid' and track.artifact_ref), None) or montage.get('subtitle_path')
        cover_ref = None
        cover = cover_for()(workspace_id, task_id, version)
        if cover is not None and cover.status == 'valid':
            cover_ref = cover.artifact_ref
        return {'video': montage.get('output_path') if isinstance(montage.get('output_path'), str) else None, 'subtitles': subtitle_ref if isinstance(subtitle_ref, str) else None, 'cover': cover_ref}

    def _history_artifact_state(logical_ref: str | None) -> Literal['available', 'not_generated', 'missing']:
        if not logical_ref:
            return 'not_generated'
        try:
            store_for().resolve_file(logical_ref)
        except (FileNotFoundError, ValueError):
            return 'missing'
        return 'available'

    def _history_artifact_url(task_id: str, version: TimelineVersion, kind: str) -> str:
        return f"/api/creation-tasks/{quote_for()(task_id, safe='')}/timeline/versions/{quote_for()(version.id, safe='')}/artifacts/{kind}"

    def _history_artifacts(task_id: str, version: TimelineVersion, refs: dict[str, str | None]) -> list[TimelineVersionArtifactResponse]:
        return [artifact_type_for()(kind=kind, status=status, url=_history_artifact_url(task_id, version, kind) if status == 'available' else None, reason=None if status == 'available' else not_generated_for()[kind] if status == 'not_generated' else missing_for()[kind]) for kind, status in ((kind, _history_artifact_state(ref)) for kind, ref in refs.items())]

    @router.get('/api/creation-tasks/{task_id}/timeline/versions', response_model=TimelineVersionListResponse)
    def list_creation_task_timeline_versions(task_id: str, request: Request, limit: int=Query(20, ge=1, le=50), before_sequence: int | None=Query(None, ge=1)) -> TimelineVersionListResponse:
        workspace_id = workspace_for()(request)
        task = task_for()(workspace_id, task_id)
        try:
            return list_type_for().model_validate(production_for().timeline_history.page(workspace_id, task.id, limit=limit, before_sequence=before_sequence))
        except history_error_for() as exc:
            raise http_error_for()(status_code=409, detail=str(exc)) from exc

    @router.get('/api/creation-tasks/{task_id}/timeline/versions/{version_id}', response_model=TimelineVersionDetailResponse)
    def get_creation_task_timeline_version(task_id: str, version_id: str, request: Request) -> TimelineVersionDetailResponse:
        workspace_id = workspace_for()(request)
        task = task_for()(workspace_id, task_id)
        version = version_for()(workspace_id, task_id, version_id)
        montage = _history_montage(version)
        clips = montage.get('clips')
        cue_list = montage.get('cues') if isinstance(montage.get('cues'), list) else []
        cues_by_id = {cue['id']: cue for cue in cue_list if isinstance(cue, dict) and 'id' in cue}
        shots = [shot_type_for()(index=index + 1, id=str(clip.get('id', '')), narration_text=cues_by_id[clip['cue_id']].get('text') if isinstance(cues_by_id.get(clip.get('cue_id')), dict) else None, start_seconds=float(clip.get('timeline_start_seconds') or 0.0), end_seconds=float(clip.get('timeline_end_seconds') or 0.0), description='；'.join((str(reason) for reason in clip['match_reasons'])) if isinstance(clip.get('match_reasons'), list) and clip['match_reasons'] else None) for index, clip in enumerate(clips if isinstance(clips, list) else []) if isinstance(clip, dict)]
        segments = version.snapshot.get('narration_segments')
        narration_segments = [segment_type_for()(index=index + 1, id=str(segment.get('id', '')), text=str(segment.get('text', '')), duration_seconds=segment.get('duration_seconds') if isinstance(segment.get('duration_seconds'), (int, float)) else None, status=segment.get('status') if isinstance(segment.get('status'), str) else None) for index, segment in enumerate(segments if isinstance(segments, list) else []) if isinstance(segment, dict)]
        tracks = _history_subtitle_tracks(workspace_id, task_id, version)
        cover = cover_for()(workspace_id, task_id, version)
        return detail_type_for()(task_id=task.id, version=_history_summary(task, version, workspace_id), shots=shots, narration_segments=narration_segments, subtitle_tracks=[subtitle_type_for().model_validate(asdict_for()(track)) for track in tracks], cover_asset=cover_type_for().model_validate(asdict_for()(cover)) if cover is not None else None, artifacts=_history_artifacts(task_id, version, _history_artifact_refs(version, workspace_id, task_id)))

    @router.get('/api/creation-tasks/{task_id}/timeline/versions/{version_id}/artifacts/{kind}')
    def get_creation_task_timeline_version_artifact(task_id: str, version_id: str, kind: Literal['video', 'subtitles', 'cover'], request: Request) -> FileResponse:
        workspace_id = workspace_for()(request)
        readable_for()(request, task_id)
        version = version_for()(workspace_id, task_id, version_id)
        logical_ref = _history_artifact_refs(version, workspace_id, task_id)[kind]
        if logical_ref is None:
            raise http_error_for()(status_code=404, detail=not_generated_for()[kind])
        if _history_artifact_state(logical_ref) == 'missing':
            raise http_error_for()(status_code=404, detail=missing_for()[kind])
        path = store_for().resolve_file(logical_ref)
        if kind == 'cover':
            media_type = 'image/jpeg' if path.suffix.lower() in {'.jpg', '.jpeg'} else 'image/png'
        else:
            media_type = media_types_for()[kind]
        return file_response_for()(path, media_type=media_type, filename=path.name)
    router.routes = [route for route in router.routes if route.name in route_names]
    return router
