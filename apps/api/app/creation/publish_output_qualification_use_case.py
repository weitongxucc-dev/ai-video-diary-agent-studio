"""Publish output qualification from canonical owners and explicitly staged values."""
from __future__ import annotations

from pathlib import Path

from app.creation.contracts import TimelineVersion
from app.creation.contracts import CreationTask
from app.production import ProductionRepository
from app.source_policy import knowledge_item_readable as content_readable


class PublishOutputQualificationUseCase:
    def __init__(self, production: ProductionRepository):
        self.production = production

    def task_for_user(self, workspace_id: str, task_id: str, user_id: str) -> CreationTask:
        task = self.production.get_creation_task(workspace_id, task_id)
        if not content_readable(
            user_id=user_id, local_diagnostic=False,
            visibility=task.visibility, owner_user_id=task.owner_user_id,
        ):
            raise PermissionError("当前成员无权查看此创作任务")
        return task

    def source_version(self, workspace_id: str, task_id: str, user_id: str) -> tuple[CreationTask, TimelineVersion]:
        task = self.production.get_creation_task(workspace_id, task_id)
        if not content_readable(
            user_id=user_id, local_diagnostic=False,
            visibility=task.visibility, owner_user_id=task.owner_user_id,
        ):
            raise PermissionError("当前成员无权发布此创作任务")
        if not task.output_ready or not task.current_timeline_version_id:
            raise ValueError("当前创作任务尚无可发布的有效成片")
        version = self.production.get_timeline_version(workspace_id, task.current_timeline_version_id)
        if version.task_id != task.id:
            raise ValueError('当前时间线版本没有已验证的有效成片')
        return task, version

    @staticmethod
    def require_render_valid(valid: bool) -> None:
        if not valid:
            raise ValueError('当前时间线版本没有已验证的有效成片')

    @staticmethod
    def output_reference(version: TimelineVersion) -> str:
        ref = version.snapshot.get("montage", {}).get("output_path")
        if not isinstance(ref, str) or ref not in version.snapshot.get("output_refs", []):
            raise ValueError("当前时间线未登记成片")
        return ref

    def qualified_output(self, workspace_id: str, task_id: str, task: CreationTask,
                         version: TimelineVersion, ref: str, path: Path) -> dict[str, object]:
        artifact = next((item for item in self.production.execution_records.list_artifact_records(workspace_id, task_id)
                         if item.logical_ref == ref and item.kind == "rendered_video"), None)
        if artifact is None or not path.is_file() or path.stat().st_size != artifact.size_bytes:
            raise ValueError("当前成片文件与已登记产物不一致")
        return {"task": task, "timeline_version_id": version.id, "logical_ref": ref,
                "path": str(path), "sha256": artifact.sha256, "size_bytes": artifact.size_bytes}
