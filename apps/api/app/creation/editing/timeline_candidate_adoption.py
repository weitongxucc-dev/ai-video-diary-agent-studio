"""Confirm saved candidate crops through the formal edit transaction."""
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
    clip_id: str | None

    def binding(self):
        values = asdict(self)
        if self.clip_id is None:
            del values["clip_id"]
            values["all_crops"] = True
        return values


_CROP_FIELDS = ("fit_mode", "crop_x", "crop_y")


def _crop(clip):
    return {field: getattr(clip, field) for field in _CROP_FIELDS}


class TimelineCandidateAdoptionUseCase(TimelineCandidateUseCase):
    def __init__(self, metadata):
        super().__init__(metadata.repository)
        self.metadata = metadata

    def _sources(self, conn, task, member_id, montage, clips, *, all_crops):
        sources = []
        for clip in clips:
            try:
                source, = self.repository.clip_sources.facts(task.workspace_id, task.id,
                    clip_ids=[clip.id], replacement=None, user_id=member_id, local_diagnostic=False,
                    montage=montage, connection=conn)
                if all_crops:
                    asset = self.repository.get_asset(task.workspace_id, clip.asset_id, connection=conn)
                    source = {**source, "asset_version": asset.updated_at.isoformat(),
                        "owner_user_id": asset.owner_user_id, "visibility": asset.visibility,
                        "clip_id": clip.id, "source_window": [clip.source_start_seconds, clip.source_end_seconds],
                        "timeline_window": [clip.timeline_start_seconds, clip.timeline_end_seconds]}
            except (KeyError, OSError) as exc:
                raise ValueError("候选镜头的实际来源已不可用，未采用。") from exc
            if (clip.source_path != source["reference"] or not
                    source["start_seconds"] <= clip.source_start_seconds < clip.source_end_seconds <= source["end_seconds"]):
                raise ValueError("候选镜头已超出可用素材范围，未采用。")
            sources.append(source)
        return sources

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
        by_id = {clip.id: clip for clip in candidate_plan.clips}
        all_crops = target.clip_id is None
        if all_crops:
            clip_ids = [clip.id for clip in plan.clips if clip.id in by_id and _crop(clip) != _crop(by_id[clip.id])]
            if not 1 <= len(clip_ids) <= 2:
                raise ValueError("一次可以采用一到两个已保存的素材镜头裁切。")
        else:
            clip_ids = [target.clip_id]
        invalidations = logical_montage_output_refs(plan)
        expected = base.snapshot
        changes, clips = [], []
        for index, before in enumerate(plan.clips, 1):
            if before.id not in clip_ids:
                continue
            crop_clip = by_id.get(before.id)
            if crop_clip is None or before.person_render_id or crop_clip.person_render_id:
                raise ValueError("当前只支持采用素材镜头的裁切。")
            crop = _crop(crop_clip)
            _, (after,) = prepare_clip_edit(plan, ClipPreviewCommand(
                task.id, base.id, "ChangeCrop", [before.id], crop, "采用候选裁切",
            ))
            expected = candidate_crop_snapshot(expected, plan, after)
            plan.clips[index - 1] = after
            changes.append({"clip_id": before.id, "clip_number": index, "before": _crop(before), "after": crop})
            clips.append(after)
        # Replay just the supported crops and compare the entire candidate
        # snapshot, including unselected shots and publication invalidations.
        if len(changes) != len(clip_ids) or expected != head.snapshot:
            raise ValueError("此候选包含所选镜头裁切以外的改动，未采用。")
        sources = self._sources(conn, task, member_id, candidate_montage, clips, all_crops=all_crops)
        selection = {"task_id": task.id, **target.binding()}
        if all_crops:
            selection["changes"] = changes
        else:
            selection.update(changes[0])
        evidence = [task.workspace_id, member_id, selection, sources if all_crops else sources[0]]
        selection["selection_digest"] = hashlib.sha256(json.dumps(evidence, sort_keys=True).encode()).hexdigest()
        return selection, base, plan, invalidations, (candidate_montage, clips, sources)

    def prepare(self, task, member_id, target):
        with closing(self.repository.connection_factory()) as conn, conn:
            conn.execute("BEGIN")
            current = self._owned_task(conn, task, member_id)
            candidate = self.versions.get_candidate(task.workspace_id, task.id, target.candidate_id, connection=conn)
            if candidate_adoption_receipt(self.repository, candidate, connection=conn) is not None:
                raise ValueError("此候选已经正式采用过。")
            selection, *_ = self._prepare(conn, current, member_id, candidate, target)
            return selection

    def confirm(self, task, member_id, target, *, request_id, selection_digest):
        identity = {"member_id": member_id, "local_diagnostic": False}
        inputs = {"member_id": member_id, "request_id": request_id,
            "selection_digest": selection_digest, **target.binding()}
        with closing(self.repository.connection_factory()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            current = self._owned_task(conn, task, member_id)
            candidate = self.versions.get_candidate(task.workspace_id, task.id, target.candidate_id, connection=conn)
            receipt = candidate_adoption_receipt(self.repository, candidate, connection=conn)
            if receipt is not None:
                if receipt.payload["inputs"] == inputs:
                    return receipt.payload["result"]
                raise ValueError("此候选已经正式采用过，不能再次采用。")
            selection, base, plan, invalidations, (montage, clips, sources) = self._prepare(
                conn, current, member_id, candidate, target)
            if selection["selection_digest"] != selection_digest:
                raise ValueError("候选裁切或素材来源已变化，请重新核对采用内容。")
            all_crops = target.clip_id is None
            if all_crops:
                edits = {"changes": selection["changes"]}
                summary = f"采用候选第 {target.candidate_sequence} 版的 {len(clips)} 处镜头裁切"
                preview = edits
            else:
                edits = {"clip_id": target.clip_id, **selection["after"]}
                summary = f"采用候选第 {target.candidate_sequence} 版的镜头 {selection['clip_number']} 裁切"
                preview = {"before": selection["before"], "after": selection["after"]}
            payload = {"base_version_id": base.id, **edits, "candidate_adoption": {
                **target.binding(), "selection_digest": selection_digest, "request_id": request_id}}
            command = metadata_edit_command(current, identity, "ChangeCrop", payload)
            refs = [*(f"clip:{clip.id}" for clip in clips), f"timeline:{base.id}"]
            intent, proposal = make_metadata_proposal(command, refs, summary, payload)
            request_key = metadata_request_identity({"workspace_id": task.workspace_id, "task_id": task.id, **inputs})
            intent = replace(intent, id=f"intent_candidate_adoption_{request_key}")
            proposal = replace(proposal, id=f"proposal_candidate_adoption_{request_key}", intent_id=intent.id,
                invalidated_refs=invalidations, preview=preview)
            self.repository.edit_records.create_edit_intent(intent, connection=conn, source=None)
            self.repository.edit_records.create_edit_proposal(proposal, connection=conn)
            prepare_changed_montage(plan, message="候选裁切已采用，可以重新生成成片。")

            def verify_sources(connection):
                if self._sources(connection, current, member_id, montage, clips, all_crops=True) != sources:
                    raise ValueError("候选裁切的素材来源已变化，请重新核对采用内容。")

            operation = self.metadata.persist_montage_edit(workspace_id=task.workspace_id, task=current,
                proposal=proposal, base_version=base, updated_plan=plan, operation_type="ChangeCrop",
                operation_payload=payload, actor_identity=identity, connection=conn,
                verify_sources=verify_sources if all_crops else None)
            result = {"task_id": task.id, **target.binding(), "operation_id": operation.id,
                "result_version_id": operation.result_version_id, "result_sequence": base.sequence + 1}
            if all_crops:
                result["changes"] = selection["changes"]
            self.repository.execution_records.append_task_event(TaskEvent(
                candidate_adoption_receipt_id(candidate), task.workspace_id, task.id, None,
                "timeline_candidate_adopted", {"inputs": inputs, "result": result},
            ), connection=conn)
            return result
