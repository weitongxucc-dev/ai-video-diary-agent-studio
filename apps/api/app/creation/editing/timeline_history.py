"""Timeline summary facts and original-operation ownership, without storage or HTTP."""
from dataclasses import dataclass


class TimelineHistoryConflict(ValueError):
    pass


@dataclass(frozen=True)
class HistoryVersionFacts:
    workspace_id: str
    task_id: str
    id: str
    sequence: int
    parent_version_id: str | None
    created_at: str
    operation_id: str | None
    shot_count: int
    subtitle_track_count: int
    narration_segment_count: int


@dataclass(frozen=True)
class HistoryOperationFacts:
    workspace_id: str
    task_id: str
    id: str
    result_version_id: str | None
    operation_type: str
    target_refs: tuple[str, ...]
    source_request_id: str | None


def project_history_summary(version: HistoryVersionFacts, operation: HistoryOperationFacts | None,
                            *, current_version_id: str | None) -> dict:
    if version.operation_id is not None:
        if operation is None:
            raise TimelineHistoryConflict("历史版本的原修改记录不存在")
        if ((operation.workspace_id, operation.task_id, operation.id, operation.result_version_id)
                != (version.workspace_id, version.task_id, version.operation_id, version.id)):
            raise TimelineHistoryConflict("历史版本与原修改记录关联不一致")
    else:
        operation = None
    return {
        "id": version.id, "sequence": version.sequence, "parent_version_id": version.parent_version_id,
        "created_at": version.created_at, "is_current": version.id == current_version_id,
        "shot_count": version.shot_count, "subtitle_track_count": version.subtitle_track_count,
        "narration_segment_count": version.narration_segment_count,
        "operation_id": operation.id if operation else None,
        "operation_type": operation.operation_type if operation else None,
        "target_refs": list(operation.target_refs[:20]) if operation else [],
        "source_request_id": operation.source_request_id if operation else None,
    }


def project_history_page(task_id, current_version_id, versions, *, total_count, truncated):
    items = [project_history_summary(version, operation, current_version_id=current_version_id)
             for version, operation in versions]
    return {"task_id": task_id, "current_version_id": current_version_id, "items": items,
            "total_count": total_count, "truncated": truncated,
            "next_before_sequence": items[-1]["sequence"] if truncated else None}
