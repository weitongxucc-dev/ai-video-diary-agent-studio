"""Fork and advance a candidate without writing the adopted creation state."""
from contextlib import closing
from dataclasses import replace
import hashlib
import json
from uuid import uuid4

from app.creation.contracts import TaskEvent, TimelineVersion
from app.creation.editing.clips import ClipPreviewCommand, prepare_clip_edit
from app.creation.editing.metadata_use_case import refresh_montage_review_state
from app.creation.task_input_reads import require_creation_task
from app.montage import creation_task_montage_plan_from_payload


def candidate_crop_snapshot(base_snapshot, plan, clip):
    montage = base_snapshot["montage"]
    refresh_montage_review_state(plan)
    # The new cut can carry the original track inputs, but no proof of
    # publication or rendered output belonging to its parent.
    publication_keys = {
        "edit_operation_id", "invalidated_refs", "output_refs", "transform_run_id",
        "audio_delivery", "reused_job_id", "creation_render_request_id",
        "render_input_digest", "render_input_timeline_version_id",
        "render_input_subtitle_tracks", "render_input_subtitles_visible",
    }
    snapshot = {key: value for key, value in base_snapshot.items() if key not in publication_keys}
    snapshot["cover_asset_id"] = None
    snapshot["montage"] = {
        **montage,
        "clips": [
            {**item, "fit_mode": clip.fit_mode, "crop_x": clip.crop_x, "crop_y": clip.crop_y}
            if item["id"] == clip.id else item
            for item in montage["clips"]
        ],
        "output_path": None,
        "subtitle_path": None,
        "status": plan.status,
        "message": "候选裁切已保存。",
    }
    return snapshot


def candidate_adoption_receipt_id(candidate):
    scope = [candidate.workspace_id, candidate.task_id, candidate.id]
    return "event_candidate_adoption_" + hashlib.sha256(json.dumps(scope).encode()).hexdigest()


def candidate_adoption_receipt(repository, candidate, *, connection):
    try:
        return repository.execution_records.get_task_event(
            candidate.workspace_id, candidate_adoption_receipt_id(candidate), connection=connection,
        )
    except KeyError:
        return None


class TimelineCandidateUseCase:
    def __init__(self, repository):
        self.repository = repository
        self.versions = repository.timeline_records

    @staticmethod
    def _owned_task(connection, task, member_id):
        current = require_creation_task(connection, task.workspace_id, task.id)
        if current.owner_user_id != member_id:
            raise PermissionError("只有作品所有者可查看或编辑剪辑候选")
        return current

    def _head(self, connection, candidate):
        return self.versions.get_candidate_version(candidate, candidate.head_version_id, connection=connection)

    def list(self, task, member_id, *, limit, after_id):
        with closing(self.repository.connection_factory()) as conn, conn:
            conn.execute("BEGIN")
            self._owned_task(conn, task, member_id)
            return self.versions.list_candidates(task.workspace_id, task.id, limit=limit, after_id=after_id, connection=conn)

    def read(self, task, member_id, candidate_id):
        with closing(self.repository.connection_factory()) as conn, conn:
            conn.execute("BEGIN")
            self._owned_task(conn, task, member_id)
            candidate = self.versions.get_candidate(task.workspace_id, task.id, candidate_id, connection=conn)
            receipt = candidate_adoption_receipt(self.repository, candidate, connection=conn)
            return candidate, self._head(conn, candidate), receipt.payload["result"] if receipt else None

    def fork(self, task, member_id, candidate_id, *, expected_version_id, expected_sequence):
        with closing(self.repository.connection_factory()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            current = self._owned_task(conn, task, member_id)
            try:
                candidate = self.versions.get_candidate(task.workspace_id, task.id, candidate_id, connection=conn)
            except KeyError:
                candidate = None
            base_id = candidate.base_version_id if candidate is not None else current.current_timeline_version_id
            if base_id is None or base_id != expected_version_id:
                raise ValueError("创建候选所依据的正式版本已变化")
            base = self.versions.get_timeline_version(task.workspace_id, base_id, connection=conn)
            if base.task_id != task.id or base.sequence != expected_sequence:
                raise ValueError("创建候选所依据的正式版本已变化")
            if not isinstance(base.snapshot.get("montage"), dict):
                raise ValueError("正式版本没有可编辑的故事板")
            if candidate is None:
                candidate = self.versions.create_candidate(base, candidate_id, connection=conn)
            return candidate, self._head(conn, candidate)

    def change_crop(self, task, member_id, candidate_id, *, expected_version_id, expected_sequence,
                    request_id, clip_id, fit_mode, crop_x, crop_y):
        command = ClipPreviewCommand(task.id, expected_version_id, "ChangeCrop", [clip_id],
            {"fit_mode": fit_mode, "crop_x": crop_x, "crop_y": crop_y}, "调整候选镜头画面适配")
        receipt_id = "event_candidate_edit_" + hashlib.sha256(json.dumps(
            [task.workspace_id, task.id, member_id, request_id], separators=(",", ":")).encode()).hexdigest()
        inputs = {
            "candidate_id": candidate_id,
            "base_version_id": command.base_version_id,
            "base_sequence": expected_sequence,
            "operation_type": command.operation_type,
            "clip_id": clip_id,
            **command.values,
        }
        with closing(self.repository.connection_factory()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            self._owned_task(conn, task, member_id)
            candidate = self.versions.get_candidate(task.workspace_id, task.id, candidate_id, connection=conn)
            records = self.repository.execution_records
            try:
                receipt = records.get_task_event(task.workspace_id, receipt_id, connection=conn)
            except KeyError:
                receipt = None
            if receipt is not None:
                if receipt.payload["inputs"] != inputs:
                    raise ValueError("该候选编辑请求已绑定其他内容")
                version = self.versions.get_candidate_version(candidate, receipt.payload["result_version_id"], connection=conn)
                return candidate, version
            base = self._head(conn, candidate)
            if (base.id, base.sequence) != (command.base_version_id, expected_sequence):
                raise ValueError("候选时间线已变化，请重新读取候选")
            montage = base.snapshot["montage"]
            try:
                plan = creation_task_montage_plan_from_payload(montage)
                _before, (clip,) = prepare_clip_edit(plan, command)
                # Validate the actual candidate clip's source, independent of
                # later changes to the adopted task's clip list.
                self.repository.clip_sources.facts(task.workspace_id, task.id, clip_ids=command.clip_ids,
                    replacement=None, user_id=member_id, local_diagnostic=False, montage=montage, connection=conn)
            except KeyError as exc:
                raise ValueError("候选镜头或其来源已不可用") from exc
            except OSError as exc:
                raise ValueError("候选镜头的源文件不可读取") from exc
            snapshot = candidate_crop_snapshot(base.snapshot, plan, clip)
            version = TimelineVersion(f"timeline_{uuid4().hex}", task.workspace_id, task.id, base.id,
                base.sequence + 1, snapshot, created_by_operation_id=None)
            self.versions.advance_candidate(candidate, version, connection=conn)
            records.append_task_event(TaskEvent(receipt_id, task.workspace_id, task.id, None,
                "timeline_candidate_edit_saved", {"member_id": member_id, "request_id": request_id,
                    "inputs": inputs, "result_version_id": version.id}), connection=conn)
            return replace(candidate, head_version_id=version.id), version
