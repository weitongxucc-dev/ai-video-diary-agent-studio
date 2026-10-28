"""Concrete video-template business rules using canonical repository and value owners."""
from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

from app.content_templates import list_content_templates
from app.creation.contracts import SubtitleTrack
from app.creation.editing.metadata_use_case import default_subtitle_style
from app.montage import CreationTaskMontagePlan, creation_task_montage_plan_from_payload
from app.production import ProductionRepository, VideoTemplate
from app.creation.plan_contracts import CreationPlan
from app.source_policy import knowledge_item_readable as content_readable
from app.creation.contracts import CreationTask
from app.visual_effects import reusable_color_settings


VideoTemplateRejectionCode = Literal["field_builtin_readonly", "field_not_owner", "task_not_rendered",
    "target_pair_incomplete", "task_builtin_overwrite", "task_not_owner", "persist_conflict"]


class VideoTemplateRejected(RuntimeError):
    def __init__(self, code: VideoTemplateRejectionCode, message: str):
        super().__init__(message)
        self.code, self.message = code, message


class VideoTemplateUseCase:
    def __init__(self, production: ProductionRepository):
        self.production = production

    def builtin_templates(self, workspace_id: str) -> list[VideoTemplate]:
        return [VideoTemplate(
            id=f"builtin_{item.template_id}", workspace_id=workspace_id, version=1,
            name=item.name, origin="built_in",
            settings={"language": "zh-CN", "aspect_ratio": "9:16", "duration_target_seconds": 60,
                      "script_structure": item.script_structure, "style_instruction": item.scenario,
                      "fit_mode": "fill", "subtitle_style": default_subtitle_style(), "voice": None},
        ) for item in list_content_templates()]

    def builtin_template(self, workspace_id: str, template_id: str, version: int) -> VideoTemplate | None:
        for item in self.builtin_templates(workspace_id):
            if (item.id, item.version) == (template_id, version):
                return item
        return None

    @staticmethod
    def require_readable(template: VideoTemplate, template_id: str, *,
                         reader_user_id: str | None, local_diagnostic: bool) -> None:
        if not content_readable(user_id=reader_user_id, local_diagnostic=local_diagnostic,
                                visibility=template.visibility, owner_user_id=template.owner_user_id):
            raise KeyError(template_id)

    def append_version(self, previous: VideoTemplate, *, expected_version: int, name: str,
                       style_instruction: str, user_id: str | None) -> VideoTemplate:
        if previous.origin != "user":
            raise VideoTemplateRejected("field_builtin_readonly", "内置模板只读，请从真实任务另存我的模板。")
        if user_id is None or previous.owner_user_id != user_id:
            raise VideoTemplateRejected("field_not_owner", "只有这个模板的所有者可以编辑并保存新版本。")
        updated = replace(previous,version=expected_version+1,name=name,created_at=datetime.now(UTC),
                          settings={**previous.settings,"style_instruction":style_instruction})
        return self._save(updated, verified_owner_user_id=user_id, local_diagnostic=False)

    def rendered_source(self, workspace_id: str, task_id: str, task: CreationTask) -> CreationTaskMontagePlan:
        try:
            montage = creation_task_montage_plan_from_payload(self.production.get_creation_task_montage_plan(workspace_id, task_id))
        except KeyError as exc:
            raise VideoTemplateRejected("task_not_rendered", "先生成并检查一条成片，再保存模板。") from exc
        if montage.status != "rendered" or not montage.output_path or not task.current_timeline_version_id:
            raise VideoTemplateRejected("task_not_rendered", "先生成并检查一条成片，再保存模板。")
        return montage

    @staticmethod
    def require_nonempty_output(output: Path) -> None:
        if output.stat().st_size == 0:
            raise OSError("empty output")

    @staticmethod
    def check_save_target(template_id: str | None, expected_version: int | None) -> None:
        if (template_id is None) != (expected_version is None):
            raise VideoTemplateRejected("target_pair_incomplete", "更新模板时需要同时指定模板和当前版本。")

    @staticmethod
    def check_save_template_origin(previous: VideoTemplate) -> None:
        if previous.origin != "user":
            raise VideoTemplateRejected("task_builtin_overwrite", "内置模板不可覆盖，请另存为我的模板。")

    @staticmethod
    def require_save_owner(previous: VideoTemplate, *, reader_id: str | None) -> None:
        if reader_id is None or previous.owner_user_id != reader_id:
            raise VideoTemplateRejected("task_not_owner", "只有这个模板的所有者可以保存新版本。")

    def build_from_task(self, workspace_id: str, task_id: str, task: CreationTask, plan: CreationPlan,
                        montage: CreationTaskMontagePlan, tracks: list[SubtitleTrack], *,
                        name: str, style_instruction: str, template_id: str | None,
                        expected_version: int | None) -> VideoTemplate:
        source_track = next((item for item in tracks if item.source_language is None), None)
        scripts = self.production.script_records.list_creation_task_scripts(workspace_id, task_id)
        script = next((item for item in scripts if item.id == task.confirmed_script_id), None)
        color_settings = [reusable_color_settings(clip.visual_effects) for clip in montage.clips]
        common_color = color_settings[0] if color_settings and all(
            item == color_settings[0] for item in color_settings
        ) else None
        template = VideoTemplate(
            id=template_id or f"template_{uuid4().hex[:12]}", workspace_id=workspace_id,
            version=(expected_version or 0) + 1, name=name.strip(), origin="user",
            source_task_id=task_id, source_timeline_version_id=task.current_timeline_version_id,
            settings={"language": plan.language, "aspect_ratio": montage.aspect_ratio,
                      "duration_target_seconds": plan.duration_target_seconds,
                      "presenter_share_target": plan.presenter_share_target,
                      "lipsync_quality": plan.lipsync_quality,
                      "voice": {"profile_id": plan.selected_voice.profile_id, "version": plan.selected_voice.version} if plan.selected_voice else None,
                      "script_section_count": len(script.sections) if script else None,
                      "style_instruction": style_instruction.strip(),
                      "fit_mode": "fit" if all(clip.fit_mode == "fit" for clip in montage.clips) else "fill",
                      "color_style": common_color,
                      "subtitle_style": dict(source_track.style) if source_track else default_subtitle_style()},
        )
        return template

    def persist_task_template(self, template: VideoTemplate, *, verified_owner_user_id: str | None,
                              local_diagnostic: bool) -> VideoTemplate:
        return self._save(template, verified_owner_user_id=verified_owner_user_id,
                          local_diagnostic=local_diagnostic)

    def _save(self, template: VideoTemplate, *, verified_owner_user_id: str | None,
              local_diagnostic: bool) -> VideoTemplate:
        try:
            return self.production.save_video_template(template,
                verified_owner_user_id=verified_owner_user_id, local_diagnostic=local_diagnostic)
        except ValueError as exc:
            raise VideoTemplateRejected("persist_conflict", str(exc)) from exc
