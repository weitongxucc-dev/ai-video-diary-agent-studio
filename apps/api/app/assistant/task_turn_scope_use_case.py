"""Task-assistant stage, selected-object and playhead eligibility.

Readers remain lazy and independent, matching the original caller snapshots.
This owner neither authorizes the viewer nor freezes or writes a turn.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from app.creation.contracts import CreationTask
from app.creation.script_draft_use_case import ScriptDraftRejected
from app.creation.editing.metadata_use_case import InvalidMontage, effective_audio_timeline
from app.montage import creation_task_montage_plan_from_payload


class TaskTurnScopeRejected(Exception):
    def __init__(self, status_code, detail):
        super().__init__(str(detail))
        self.status_code, self.detail = status_code, detail


@dataclass(frozen=True)
class TaskTurnSelection:
    context_stage: str
    selected_object_refs: list[str]
    playhead_ms: int | None


@dataclass(frozen=True)
class TaskTurnScopeUseCase:
    read_script_inputs: Callable
    read_narration: Callable
    read_segments: Callable
    read_montage: Callable
    read_version: Callable
    read_subtitles: Callable
    read_covers: Callable

    def resolve(self, workspace_id: str, task: CreationTask,
                selection: TaskTurnSelection) -> tuple[str, list[str]]:
        stage = selection.context_stage
        if task.stage == "topic":
            if stage != "topic":
                raise TaskTurnScopeRejected(status_code=409, detail="当前任务只能在选题阶段使用对话")
            if selection.selected_object_refs or selection.playhead_ms is not None:
                raise TaskTurnScopeRejected(status_code=422, detail="选题对话不能选择剪辑对象")
            return "topic", []
        if task.stage == "script" or task.confirmed_script_id is None or stage == "script" and task.can_revise_script:
            if stage != "script":
                raise TaskTurnScopeRejected(status_code=409, detail="当前任务尚未完成文案确认，不能使用剪辑对话")
            try:
                self.read_script_inputs(workspace_id, task.id)
            except ScriptDraftRejected as exc:
                raise TaskTurnScopeRejected(exc.status_code, exc.detail) from exc
            if selection.selected_object_refs or selection.playhead_ms is not None:
                raise TaskTurnScopeRejected(status_code=422, detail="文案对话不能选择剪辑对象")
            return "script", []
        if stage in {"topic", "script"} or task.stage not in {"narration", "montage", "review"}:
            raise TaskTurnScopeRejected(status_code=409, detail="当前任务不能在所选阶段使用对话")
        if task.stage == "narration" and stage != "narration":
            raise TaskTurnScopeRejected(status_code=409, detail="当前任务尚未生成可修改的故事板")

        # The task-scoped conversation can read confirmed-task facts and invoke
        # the explicit execution actions before any narration artifact exists.
        # Object edits still pass through the strict selected-ref checks below;
        # preview_edit's existing endpoint remains the authority for edit scope.
        if not selection.selected_object_refs and selection.playhead_ms is None:
            return stage, []

        narration = (self.read_narration(workspace_id, task.narration_cache_key)
                     if task.narration_cache_key else None)
        if stage == "narration":
            if narration is None or narration.task_id != task.id or narration.script_id != task.confirmed_script_id:
                raise TaskTurnScopeRejected(status_code=409, detail="当前任务没有可修改的配音")
            refs = {f"narration:{segment.id}" for segment in self.read_segments(
                workspace_id, task.id, task.confirmed_script_id,
            ) if segment.status == "ready"}
        else:
            try:
                montage = creation_task_montage_plan_from_payload(
                    self.read_montage(workspace_id, task.id)
                )
            except KeyError as exc:
                raise TaskTurnScopeRejected(status_code=409, detail="当前任务没有可修改的故事板") from exc
            if (montage.task_id != task.id or montage.script_id != task.confirmed_script_id
                    or task.montage_plan_id != montage.id):
                raise TaskTurnScopeRejected(status_code=409, detail="当前故事板不属于已确认文案")
            version = None
            if task.current_timeline_version_id:
                try:
                    version = self.read_version(workspace_id, task.current_timeline_version_id)
                except KeyError as exc:
                    raise TaskTurnScopeRejected(status_code=409, detail="当前任务的时间线版本不可用") from exc
                if version.task_id != task.id:
                    raise TaskTurnScopeRejected(status_code=409, detail="当前时间线不属于此任务")
            if stage == "review" and (montage.status != "rendered" or not montage.output_path):
                raise TaskTurnScopeRejected(status_code=409, detail="当前任务没有可查看的成片")
            refs = {f"clip:{clip.id}" for clip in montage.clips}
            if stage in {"montage", "review"}:
                refs.update(f"overlay:{overlay['id']}" for overlay in montage.graphic_overlays)
                audio = effective_audio_timeline(montage)
                refs.update(f"audio:{track.id}" for track in audio.tracks)
                refs.update(f"audio:{clip.id}" for track in audio.tracks for clip in track.clips)
                if narration is not None and narration.task_id == task.id and narration.script_id == task.confirmed_script_id:
                    refs.update(f"narration:{segment.id}" for segment in self.read_segments(
                        workspace_id, task.id, task.confirmed_script_id,
                    ) if segment.status == "ready")
            else:
                if version is None:
                    raise TaskTurnScopeRejected(status_code=409, detail="当前任务没有可修改的字幕时间线")
                try:
                    current_tracks = self.read_subtitles(
                        workspace_id, task, current_version=version, montage=montage,
                    )
                except InvalidMontage as exc:
                    raise TaskTurnScopeRejected(status_code=422, detail=str(exc)) from exc
                tracks = [track for track in current_tracks if track.status == "valid" and track.task_id == task.id
                          and track.timeline_version_id == version.id]
                if not tracks:
                    raise TaskTurnScopeRejected(status_code=409, detail="当前任务没有可修改的字幕")
                refs.update(f"subtitle-track:{track.id}" for track in tracks)
                refs.update(f"cue:{cue.id}" for track in tracks for cue in track.cues)
                covers = self.read_covers(
                    workspace_id, task.id, timeline_version_id=version.id,
                )
                refs.update(f"cover:{cover.id}" for cover in covers)
                if montage.status == "rendered" and montage.output_path:
                    refs.add(f"cover:{task.id}")
            if selection.playhead_ms is not None and selection.playhead_ms > round(montage.duration_seconds * 1000):
                raise TaskTurnScopeRejected(status_code=422, detail="播放位置超出当前任务的时间线")
        if any(ref not in refs for ref in selection.selected_object_refs):
            raise TaskTurnScopeRejected(status_code=422, detail="选中对象不属于当前任务或阶段")
        return stage, list(selection.selected_object_refs)
