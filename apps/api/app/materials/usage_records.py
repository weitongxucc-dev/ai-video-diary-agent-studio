"""Read material usage records with their task's current timeline head."""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
import json
import sqlite3

from app.materials.contracts import MaterialAsset


class MaterialUsageRecordReader:
    def __init__(
        self, *, connection_factory: Callable[[], sqlite3.Connection],
        read_asset: Callable[[str, str], MaterialAsset],
    ) -> None:
        self._connect = connection_factory
        self._read_asset = read_asset

    def list_segment_usage_records(
        self, workspace_id: str, asset_id: str, segment_id: str
    ) -> list[dict[str, object]]:
        """Read back what used one segment, naming the task it went into.

        Only rows already written by a real render are read, and only for the
        material and segment asked for inside the caller's workspace. Each row
        names the montage plan, its clip and the creation task behind it, so a
        material detail page can show a real traceback instead of a bare count.

        The usage table carries no timeline version, and a task keeps several
        snapshots that may all contain the same clip. Nearest time or highest
        sequence would therefore be a guess, so no version is claimed here: the
        task's current version is reported separately and flagged as current,
        and a task without one reports the version as unrecorded.
        """
        asset = self._read_asset(workspace_id, asset_id)
        if all(segment.id != segment_id for segment in asset.segments):
            raise KeyError(segment_id)
        with self._connect() as conn:
            try:
                usage_rows = conn.execute(
                """
                SELECT plan_id, clip_id, used_at, {task_column}, {timeline_column}
                FROM material_segment_usage
                WHERE workspace_id = ? AND asset_id = ? AND segment_id = ?
                ORDER BY used_at DESC
                """.format(task_column="task_id", timeline_column="timeline_version_id"),
                    (workspace_id, asset_id, segment_id),
                ).fetchall()
            except sqlite3.OperationalError as exc:
                if "no such column" not in str(exc):
                    raise
                usage_rows = conn.execute(
                    """
                    SELECT plan_id, clip_id, used_at, NULL AS task_id, NULL AS timeline_version_id
                    FROM material_segment_usage
                    WHERE workspace_id = ? AND asset_id = ? AND segment_id = ?
                    ORDER BY used_at DESC
                    """,
                    (workspace_id, asset_id, segment_id),
                ).fetchall()
            plan_rows = conn.execute(
                """
                SELECT id, task_id, payload
                FROM creation_task_montage_plans
                WHERE workspace_id = ?
                """,
                (workspace_id,),
            ).fetchall()
            task_rows = conn.execute(
                """
                SELECT id, payload
                FROM creation_tasks
                WHERE workspace_id = ?
                """,
                (workspace_id,),
            ).fetchall()

        plans: dict[str, dict[str, object]] = {}
        for row in plan_rows:
            payload = json.loads(str(row["payload"]))
            clips = {
                str(clip.get("id")): clip
                for clip in payload.get("clips", [])
                if isinstance(clip, dict)
            }
            plans[str(row["id"])] = {"task_id": str(row["task_id"]), "clips": clips}
        tasks = {str(row["id"]): json.loads(str(row["payload"])) for row in task_rows}

        records: list[dict[str, object]] = []
        for row in usage_rows:
            plan = plans.get(str(row["plan_id"]))
            clip = plan["clips"].get(str(row["clip_id"])) if plan else None
            recorded_task_id = str(row["task_id"]) if row["task_id"] else None
            task = tasks.get(recorded_task_id or str(plan["task_id"])) if plan else None
            recorded_version_id = str(row["timeline_version_id"]) if row["timeline_version_id"] else None
            current_version_id = task.get("current_timeline_version_id") if task else None
            records.append(
                {
                    "plan_id": str(row["plan_id"]),
                    "clip_id": str(row["clip_id"]),
                    "used_at": datetime.fromisoformat(str(row["used_at"])),
                    "task": {
                        "id": recorded_task_id or (str(plan["task_id"]) if plan else None),
                        "name": str(task.get("name") or "") if task else None,
                        "stage": str(task.get("stage") or "") if task else None,
                        "status": str(task.get("status") or "") if task else None,
                        "current_timeline_version_id": str(current_version_id)
                        if current_version_id
                        else None,
                    }
                    if plan
                    else None,
                    "role": str(clip.get("role")) if clip else None,
                    "manually_selected": bool(clip.get("manually_selected")) if clip else None,
                    "timeline_version_id": recorded_version_id or (str(current_version_id) if current_version_id else None),
                    "resolution": "recorded" if recorded_version_id else ("current" if current_version_id else "unknown"),
                }
            )
        return records
