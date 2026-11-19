"""One confirmed script section overrides automatic presenter allocation."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from app.creation.plan_contracts import CreationPlanPresenterSelection
from app.models import utc_now


def validate_presenter_selection_version(plan, script):
    selection = plan.presenter_selection
    if selection is not None and (
        selection.script_id != script.id or selection.script_updated_at != script.updated_at
    ):
        raise ValueError("确认文案版本已变化，请重新指定出镜段落。")
    if selection is not None and (plan.selected_voice is None or (
        selection.voice_profile_id, selection.voice_version
    ) != (plan.selected_voice.profile_id, plan.selected_voice.version)):
        raise ValueError("采用的声音版本已变化，请重新指定出镜段落。")


def planned_presenter_sections(plan, script, timings):
    from app.montage import select_presenter_sections

    selection = plan.presenter_selection
    if selection is None:
        return select_presenter_sections(timings, plan.presenter_share_target)
    validate_presenter_selection_version(plan, script)
    if any(section_id == selection.section_id for section_id, _ in timings):
        return [selection.section_id]
    raise ValueError("指定的出镜段落缺少当前文案的配音，无法制作口型。")


class PresenterSelectionUseCase:
    def __init__(self, repository, *, connection_factory):
        self.repository = repository
        self._connect = connection_factory

    def save(
        self, workspace_id: str, task_id: str, *, member_id: str | None,
        script_id: str, expected_script_updated_at: datetime,
        expected_plan_updated_at: datetime, section_id: str | None,
    ):
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            task = self.repository.task_records.get_creation_task(workspace_id, task_id, connection=conn)
            if task.owner_user_id and task.owner_user_id != member_id:
                raise PermissionError("当前成员不能修改此任务")
            plan = self.repository.plan_records.read_creation_plan(conn, workspace_id, task.plan_id)
            if plan.creation_task_id != task.id or not plan.presenter_share_target or plan.selected_person is None:
                raise ValueError("请先在当前制作方案中确认出镜人物。")
            if task.confirmed_script_id != script_id:
                raise ValueError("请基于当前确认文案指定出镜段落。")
            script, _ = self.repository.script_records.read_execution_script_metadata(conn, workspace_id, script_id)
            if script.updated_at != expected_script_updated_at or script.selection_status != "confirmed":
                raise ValueError("确认文案版本已变化，请读取当前文案后重新选择。")
            selection = None
            if section_id is not None:
                self.repository.section_inputs.read_creation_section_input(task, script, section_id, connection=conn)
                if plan.selected_voice is None:
                    raise ValueError("请先采用已授权的声音版本。")
                selection = CreationPlanPresenterSelection(script.id, script.updated_at, section_id,
                    plan.selected_voice.profile_id, plan.selected_voice.version)
            if plan.presenter_selection == selection:
                return plan
            if plan.updated_at != expected_plan_updated_at:
                raise ValueError("制作方案已变化，请读取当前选择后再修改。")
            if task.montage_plan_id or task.current_timeline_version_id:
                raise ValueError("任务已有时间线，请在剪辑中调整出镜区间。")
            if task.stage_status == "running" or self.repository.job_records.has_open_task_job(conn, workspace_id, task_id):
                raise ValueError("当前仍有制作作业，请在作业结束后修改出镜段落。")
            saved = replace(plan, presenter_selection=selection, updated_at=utc_now())
            self.repository.plan_records.write_plan_payload(conn, workspace_id, saved)
            return saved
