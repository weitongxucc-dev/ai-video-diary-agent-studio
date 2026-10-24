"""Bounded read-only timeline history queries over the existing database."""
import json

from app.creation.editing.timeline_history import (
    HistoryOperationFacts, HistoryVersionFacts, project_history_page, project_history_summary,
)


_VERSION_QUERY = """
SELECT v.workspace_id,v.task_id,v.id,v.sequence,v.parent_version_id,v.created_at,
       v.created_by_operation_id AS operation_id,
       o.id AS linked_operation_id,o.workspace_id AS operation_workspace_id,
       o.task_id AS operation_task_id,o.result_version_id,o.operation_type,p.target_refs_json,
       json_extract(o.payload_json,'$.request_id') AS source_request_id,
       coalesce(json_array_length(v.snapshot_json,'$.montage.clips'),0) AS shot_count,
       coalesce(json_array_length(v.snapshot_json,'$.subtitle_track_ids'),0) AS subtitle_track_count,
       coalesce(json_array_length(v.snapshot_json,'$.narration_segments'),0) AS narration_segment_count
FROM timeline_versions v
LEFT JOIN edit_operations o ON o.workspace_id=v.workspace_id AND o.id=v.created_by_operation_id
LEFT JOIN edit_proposals p ON p.workspace_id=o.workspace_id AND p.id=o.proposal_id
WHERE v.workspace_id=? AND v.task_id=?
"""


class TimelineHistoryReadRepository:
    def __init__(self, connection_factory):
        self._connect = connection_factory

    @staticmethod
    def _current_version(conn, workspace_id, task_id):
        row = conn.execute("SELECT json_extract(payload,'$.current_timeline_version_id') AS current_id "
                           "FROM creation_tasks WHERE workspace_id=? AND id=?",
                           (workspace_id, task_id)).fetchone()
        if row is None:
            raise KeyError(task_id)
        return row["current_id"]

    @staticmethod
    def _facts(row, *, subtitle_count=None):
        version = HistoryVersionFacts(
            workspace_id=row["workspace_id"], task_id=row["task_id"], id=row["id"],
            sequence=row["sequence"], parent_version_id=row["parent_version_id"],
            created_at=row["created_at"], operation_id=row["operation_id"], shot_count=row["shot_count"],
            subtitle_track_count=row["subtitle_track_count"] if subtitle_count is None else subtitle_count,
            narration_segment_count=row["narration_segment_count"])
        operation = None
        if row["linked_operation_id"] is not None:
            operation = HistoryOperationFacts(
                workspace_id=row["operation_workspace_id"], task_id=row["operation_task_id"],
                id=row["linked_operation_id"], result_version_id=row["result_version_id"],
                operation_type=row["operation_type"],
                target_refs=tuple(json.loads(row["target_refs_json"])) if row["target_refs_json"] else (),
                source_request_id=row["source_request_id"])
        return version, operation

    def page(self, workspace_id, task_id, *, limit=20, before_sequence=None):
        if not 1 <= limit <= 50 or before_sequence is not None and before_sequence < 1:
            raise ValueError("历史读取范围无效")
        with self._connect() as conn:
            current_id = self._current_version(conn, workspace_id, task_id)
            total = conn.execute("SELECT COUNT(*) FROM timeline_versions WHERE workspace_id=? AND task_id=?",
                                 (workspace_id, task_id)).fetchone()[0]
            rows = conn.execute(_VERSION_QUERY +
                " AND (? IS NULL OR v.sequence<?) ORDER BY v.sequence DESC LIMIT ?",
                (workspace_id, task_id, before_sequence, before_sequence, limit + 1)).fetchall()
        return project_history_page(task_id, current_id, [self._facts(row) for row in rows[:limit]],
                                    total_count=total, truncated=len(rows) > limit)

    def summary(self, workspace_id, task_id, version_id):
        with self._connect() as conn:
            current_id = self._current_version(conn, workspace_id, task_id)
            row = conn.execute(_VERSION_QUERY + " AND v.id=?",
                               (workspace_id, task_id, version_id)).fetchone()
            if row is None:
                raise KeyError(version_id)
            # Detail counts persisted tracks; old snapshots may not list their IDs.
            count = conn.execute("SELECT COUNT(*) FROM subtitle_tracks WHERE workspace_id=? AND task_id=? "
                                 "AND timeline_version_id=?", (workspace_id, task_id, version_id)).fetchone()[0]
        version, operation = self._facts(row, subtitle_count=count)
        return project_history_summary(version, operation, current_version_id=current_id)
