"""Read-only creation-task summaries from the authoritative version/edit history."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING

from app.creation.contracts import EditOperation, TimelineVersion

if TYPE_CHECKING:
    from app.creation.contracts import CreationTask
    from app.production import ProductionRepository


def history_edit_head(
    repository: ProductionRepository,
    workspace_id: str,
    version_id: str | None,
    *,
    for_redo: bool,
) -> tuple[TimelineVersion | None, EditOperation | None]:
    # Render-only versions have no edit. Control versions point to the exact
    # restored snapshot, so repeated undo/redo walks the same immutable history.
    while version_id:
        version = repository.timeline_records.get_timeline_version(workspace_id, version_id)
        if version.created_by_operation_id is None:
            version_id = version.parent_version_id
            continue
        operation = repository.edit_records.get_edit_operation(workspace_id, version.created_by_operation_id)
        if for_redo and operation.operation_type == "RedoTimelineVersion" and operation.payload.get("target_undo_operation_id"):
            # A scoped redo restores an original edit's content. Continue the
            # pending redo chain from the exact undo it consumed, rather than
            # treating that restored edit as the end of the control history.
            undone = repository.edit_records.get_edit_operation(workspace_id, operation.payload["target_undo_operation_id"])
            version_id = undone.base_version_id
            continue
        follow_restore = operation.operation_type == "RedoTimelineVersion" or (
            not for_redo and operation.operation_type == "UndoTimelineVersion"
        )
        if follow_restore:
            version_id = operation.payload.get("restore_version_id")
            continue
        return version, operation
    return None, None


def undo_target_versions(
    repository: ProductionRepository, workspace_id: str, task: CreationTask
) -> tuple[TimelineVersion | None, TimelineVersion | None]:
    try:
        changed, _ = history_edit_head(
            repository, workspace_id, task.current_timeline_version_id, for_redo=False
        )
        if changed is None or changed.parent_version_id is None:
            return None, None
        return changed, repository.timeline_records.get_timeline_version(workspace_id, changed.parent_version_id)
    except KeyError:
        return None, None


def redo_target_versions(
    repository: ProductionRepository, workspace_id: str, task: CreationTask
) -> tuple[TimelineVersion | None, TimelineVersion | None]:
    try:
        current, operation = history_edit_head(
            repository, workspace_id, task.current_timeline_version_id, for_redo=True
        )
        if current is None or operation.operation_type != "UndoTimelineVersion":
            return None, None
        target_id = operation.payload.get("base_version_id")
        if not isinstance(target_id, str):
            return None, None
        return current, repository.timeline_records.get_timeline_version(workspace_id, target_id)
    except KeyError:
        return None, None


@dataclass(frozen=True)
class TaskReadProjection:
    summary: dict[str, object]
    current_version: TimelineVersion | None


def project_task_summary(repository: ProductionRepository, task: CreationTask) -> TaskReadProjection:
    current = None
    if task.current_timeline_version_id is not None:
        try:
            current = repository.timeline_records.get_timeline_version(
                task.workspace_id, task.current_timeline_version_id
            )
        except KeyError:
            current = None
    return TaskReadProjection(
        summary={
            **asdict(task),
            "timeline_sequence": current.sequence if current else 0,
            "can_undo": undo_target_versions(repository, task.workspace_id, task)[0] is not None,
            "can_redo": redo_target_versions(repository, task.workspace_id, task)[0] is not None,
            "can_revise_script": task.can_revise_script,
        },
        current_version=current,
    )
