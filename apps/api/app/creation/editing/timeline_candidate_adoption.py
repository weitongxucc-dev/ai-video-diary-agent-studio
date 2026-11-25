"""Confirm one saved candidate crop through the formal edit transaction."""
from contextlib import closing
from dataclasses import asdict, dataclass, replace
import hashlib
import json

from app.creation.contracts import TaskEvent
from app.creation.editing.clips import ClipPreviewCommand, prepare_clip_edit
from app.creation.editing.metadata import make_metadata_proposal, metadata_request_identity
from app.creation.editing.metadata_use_case import (
    logical_montage_output_refs, metadata_edit_command, prepare_changed_montage,
)
from app.creation.editing.timeline_candidates import (
    TimelineCandidateUseCase, candidate_adoption_receipt, candidate_adoption_receipt_id,
    candidate_crop_snapshot,
)
from app.montage import creation_task_montage_plan_from_payload


@dataclass(frozen=True)
class CandidateAdoptionTarget:
    candidate_id: str
    candidate_version_id: str
    candidate_sequence: int
    base_version_id: str
    base_sequence: int
    clip_id: str


_CROP_FIELDS = ("fit_mode", "crop_x", "crop_y")


def _crop(clip):
    return {field: getattr(clip, field) for field in _CROP_FIELDS}


class TimelineCandidateAdoptionUseCase(TimelineCandidateUseCase):
    def __init__(self, metadata):
        super().__init__(metadata.repository)
        self.metadata = metadata

    def _prepare(self, conn, task, member_id, candidate, target):
        if task.current_timeline_version_id != target.base_version_id or candidate.base_version_id != target.base_version_id:
            raise ValueError("候选所依据的正式版本已变化，未采用。")
        base = self.versions.get_candidate_version(candidate, candidate.base_version_id, connection=conn)
        head = self._head(conn, candidate)
        if base.sequence != target.base_sequence or (head.id, head.sequence) != (
            target.candidate_version_id, target.candidate_sequence,
        ):
            raise ValueError("正式版本或候选头已变化，请重新比较。")
        if head.id == base.id:
            raise ValueError("此候选尚未保存镜头裁切。")
        montage = self.repository.get_creation_task_montage_plan(task.workspace_id, task.id, connection=conn)
        # The live projection stamps its own save time after the immutable version.
        if montage != {**base.snapshot["montage"], "updated_at": montage["updated_at"]}:
            raise ValueError("当前故事板与候选基线已不同，请重新比较。")
        plan = creation_task_montage_plan_from_payload(montage)
        candidate_montage = head.snapshot["montage"]
        candidate_plan = creation_task_montage_plan_from_payload(candidate_montage)
        crop_clip = next((clip for clip in candidate_plan.clips if clip.id == target.clip_id), None)
        if crop_clip is None or crop_clip.person_render_id:
            raise ValueError("当前只支持采用一个素材镜头的裁切。")
        crop = _crop(crop_clip)
        try:
            before, (after,) = prepare_clip_edit(plan, ClipPreviewCommand(
                task.id, base.id, "ChangeCrop", [target.clip_id], crop, "采用候选裁切",
            ))
            # Compare the entire snapshot to the only supported edit, including
            # the ordinary candidate output invalidations, before any adoption.
            invalidations = logical_montage_output_refs(plan)
            if candidate_crop_snapshot(base.snapshot, plan, after) != head.snapshot:
                raise ValueError("此候选包含所选镜头裁切以外的改动，未采用。")
            (source,) = self.repository.clip_sources.facts(task.workspace_id, task.id,
                clip_ids=[target.clip_id], replacement=None, user_id=member_id,
                local_diagnostic=False, montage=candidate_montage, connection=conn)
        except (KeyError, OSError) as exc:
            raise ValueError("候选镜头的实际来源已不可用，未采用。") from exc
        if (after.source_path != source["reference"] or not
                source["start_seconds"] <= after.source_start_seconds < after.source_end_seconds <= source["end_seconds"]):
            raise ValueError("候选镜头已超出可用素材范围，未采用。")
        selection = {
            "task_id": task.id, **asdict(target),
            "clip_number": next(index for index, clip in enumerate(plan.clips, 1) if clip.id == target.clip_id),
            "before": _crop(before[0]), "after": crop,
        }
        evidence = [task.workspace_id, member_id, selection, source]
        selection["selection_digest"] = hashlib.sha256(json.dumps(evidence, sort_keys=True).encode()).hexdigest()
        plan.clips = [after if clip.id == target.clip_id else clip for clip in plan.clips]
        return selection, base, plan, invalidations

    def prepare(self, task, member_id, target):
        with closing(self.repository.connection_factory()) as conn, conn:
            conn.execute("BEGIN")
            current = self._owned_task(conn, task, member_id)
            candidate = self.versions.get_candidate(task.workspace_id, task.id, target.candidate_id, connection=conn)
            if candidate_adoption_receipt(self.repository, candidate, connection=conn) is not None:
                raise ValueError("此候选已经正式采用过。")
            selection, _, _, _ = self._prepare(conn, current, member_id, candidate, target)
            return selection

    def confirm(self, task, member_id, target, *, request_id, selection_digest):
        identity = {"member_id": member_id, "local_diagnostic": False}
        inputs = {"member_id": member_id, "request_id": request_id,
            "selection_digest": selection_digest, **asdict(target)}
        with closing(self.repository.connection_factory()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            current = self._owned_task(conn, task, member_id)
            candidate = self.versions.get_candidate(task.workspace_id, task.id, target.candidate_id, connection=conn)
            receipt = candidate_adoption_receipt(self.repository, candidate, connection=conn)
            if receipt is not None:
                if receipt.payload["inputs"] == inputs:
                    return receipt.payload["result"]
                raise ValueError("此候选已经正式采用过，不能再次采用。")
            selection, base, plan, invalidations = self._prepare(conn, current, member_id, candidate, target)
            if selection["selection_digest"] != selection_digest:
                raise ValueError("候选裁切或素材来源已变化，请重新核对采用内容。")
            payload = {
                "base_version_id": base.id, "clip_id": target.clip_id, **selection["after"],
                "candidate_adoption": {**asdict(target), "selection_digest": selection_digest, "request_id": request_id},
            }
            command = metadata_edit_command(current, identity, "ChangeCrop", payload)
            refs = [f"clip:{target.clip_id}", f"timeline:{base.id}"]
            intent, proposal = make_metadata_proposal(command, refs,
                f"采用候选第 {target.candidate_sequence} 版的镜头 {selection['clip_number']} 裁切", payload)
            request_key = metadata_request_identity({"workspace_id": task.workspace_id, "task_id": task.id, **inputs})
            intent = replace(intent, id=f"intent_candidate_adoption_{request_key}")
            proposal = replace(proposal, id=f"proposal_candidate_adoption_{request_key}", intent_id=intent.id,
                invalidated_refs=invalidations, preview={"before": selection["before"], "after": selection["after"]})
            self.repository.edit_records.create_edit_intent(intent, connection=conn, source=None)
            self.repository.edit_records.create_edit_proposal(proposal, connection=conn)
            prepare_changed_montage(plan, message="候选裁切已采用，可以重新生成成片。")
            operation = self.metadata.persist_montage_edit(workspace_id=task.workspace_id, task=current,
                proposal=proposal, base_version=base, updated_plan=plan, operation_type="ChangeCrop",
                operation_payload=payload, actor_identity=identity, connection=conn)
            result = {"task_id": task.id, **asdict(target), "operation_id": operation.id,
                "result_version_id": operation.result_version_id, "result_sequence": base.sequence + 1}
            self.repository.execution_records.append_task_event(TaskEvent(
                candidate_adoption_receipt_id(candidate), task.workspace_id, task.id, None,
                "timeline_candidate_adopted", {"inputs": inputs, "result": result},
            ), connection=conn)
            return result
