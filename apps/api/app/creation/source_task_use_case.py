"""Adopt a source-video range or verify the same source's revised binding."""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import stat
from uuid import UUID

from app.lipsync_models import LATENTSYNC_16_MODEL_ID
from app.audio_timeline import AudioTimeline, TimelineAudioClip, TimelineAudioTrack, with_resource_audio_tracks
from app.creation.contracts import CreationTask, TaskEvent, TimelineVersion
from app.creation.plan_contracts import CreationPlan
from app.creation_execution import CreationExecutionActor
from app.materials.contracts import material_usage_eligibility
from app.materials.source_files import MaterialSourceFileAccess
from app.models import utc_now
from app.montage import CreationTaskMontagePlan, MontageClip, creation_task_montage_plan_to_payload
from app.source_policy import knowledge_item_readable


@dataclass(frozen=True)
class SourceTaskCommand:
    request_id: str
    asset_id: str
    expected_asset_updated_at: datetime
    source_start_seconds: float
    source_end_seconds: float
    name: str
    aspect_ratio: str
    preserve_audio: bool


@dataclass(frozen=True)
class SourceRevisionRecoveryCommand:
    request_id: str
    expected_timeline_version_id: str
    expected_timeline_sequence: int
    asset_id: str
    expected_bound_asset_updated_at: datetime
    expected_asset_updated_at: datetime


class SourceTaskConflict(ValueError):
    """The original request or selected source revision no longer matches."""


class SourceTaskIdentityUnavailable(RuntimeError):
    """Identity could not be observed; this does not establish revocation."""


_BINDING_PROOF_FIELDS = {"asset_updated_at", "file_signature"}


def _same_source_selection(original, current):
    return {k: v for k, v in original.items() if k not in _BINDING_PROOF_FIELDS} == {
        k: v for k, v in current.items() if k not in _BINDING_PROOF_FIELDS}


def _original_receive_proof(conn, workspace_id, owner_id, digest, original):
    row = conn.execute(
        "SELECT payload FROM material_discovery_receipts WHERE workspace_id=? AND owner_user_id=? AND digest=?",
        (workspace_id, owner_id, digest),
    ).fetchone()
    if row is None:
        return None
    receipt = json.loads(row[0])
    revision = receipt.get("revision")
    if receipt.get("asset_id") != original["asset_id"] or not isinstance(revision, list) or len(revision) != 5:
        return None
    signature = dict(zip(("inode", "size_bytes", "mtime_ns", "ctime_ns"), map(str, revision[1:])))
    if signature != original["file_signature"]:
        return None
    return receipt


def source_task_binding_matches(conn, *, workspace_id, task_id, plan_id, member_id, source_event_id, original, current):
    """Read the original adoption or an explicitly recovered proof, without opening media."""
    if current is None:
        return False
    if original == current:
        return True
    if not _same_source_selection(original, current):
        return False
    rows = conn.execute(
        "SELECT payload_json FROM task_events WHERE workspace_id=? AND task_id=? "
        "AND event_type='source_revision_recovered' AND json_extract(payload_json,'$.source_event_id')=?",
        (workspace_id, task_id, source_event_id),
    )
    for row in rows:
        event = json.loads(row[0])
        proof = event["proof"]
        result = event["receipt"]
        received = _original_receive_proof(conn, workspace_id, proof["owner_user_id"], proof["sha256"], original)
        if (event["member_id"] == member_id and result["task_id"] == task_id and result["plan_id"] == plan_id
                and event["original_source_media"] == original and event["source_media"] == current
                and received is not None and received == proof["receive_receipt"]):
            return True
    return False


class SourceTaskUseCase:
    def __init__(self, repository, verify_identity):
        self.repository = repository
        self.verify_identity = verify_identity

    @staticmethod
    def _event_id(request_id, actor):
        identity = {"workspace_id": actor.workspace_id, **actor.identity_payload()}
        value = [identity, str(UUID(request_id))]
        key = hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
        return f"source_task_{key}"

    @staticmethod
    def _member(conn, actor):
        if actor.device_id is None:
            if actor.workspace_id != "local" or conn.execute(
                "SELECT 1 FROM local_members WHERE member_id=?", (actor.member_id,)
            ).fetchone() is None:
                raise PermissionError("原本地成员已不存在，不能采用源视频")

    def _receipt(self, conn, request_id, actor):
        self._member(conn, actor)
        try:
            return self.repository.execution_records.get_task_event(
                actor.workspace_id, self._event_id(request_id, actor), connection=conn)
        except KeyError:
            return None

    def read(self, request_id: str, actor: CreationExecutionActor):
        """Query only the original identity's receipt; never inspect its media."""
        with self.repository._connect() as conn:
            event = self._receipt(conn, request_id, actor)
        return event.payload["receipt"] if event is not None else None

    def _recovery_receipt(self, conn, task_id, request_id, actor, command=None):
        self._member(conn, actor)
        event_id = "revision_" + self._event_id(request_id, actor)
        try:
            event = self.repository.execution_records.get_task_event(actor.workspace_id, event_id, connection=conn)
        except KeyError:
            return None
        if event.task_id != task_id or event.event_type != "source_revision_recovered":
            if command is None:
                return None
            raise SourceTaskConflict("原复核请求已绑定其他作品")
        if command is not None and event.payload["inputs"] != command:
            raise SourceTaskConflict("原复核请求已绑定其他版本或素材修订")
        return event.payload["receipt"]

    def read_recovery(self, task_id, request_id, actor):
        with self.repository._connect() as conn:
            return self._recovery_receipt(conn, task_id, request_id, actor)

    def _recovery_target(self, conn, workspace_id, member_id, task_id, command):
        repo = self.repository
        task = repo.task_records.get_creation_task(workspace_id, task_id, connection=conn)
        plan = repo.plan_records.read_creation_plan(conn, workspace_id, task.plan_id)
        if task.owner_user_id != member_id or plan.owner_user_id != member_id:
            raise PermissionError("只有原作品所有者可以复核原片绑定")
        if plan.creation_task_id != task.id or task.confirmed_script_id is not None or plan.source_media is None:
            raise SourceTaskConflict("当前作品缺少可复核的原片绑定")
        if task.current_timeline_version_id != command.expected_timeline_version_id:
            raise SourceTaskConflict("时间线已变化，请重新打开作品后复核")
        version = repo.timeline_records.get_timeline_version(workspace_id, task.current_timeline_version_id, connection=conn)
        if version.task_id != task.id or version.sequence != command.expected_timeline_sequence:
            raise SourceTaskConflict("时间线已变化，请重新打开作品后复核")
        source = plan.source_media
        if (source["asset_id"] != command.asset_id
                or datetime.fromisoformat(source["asset_updated_at"]) != command.expected_bound_asset_updated_at):
            raise SourceTaskConflict("作品的原片绑定已变化，请重新读取")
        asset = repo.get_asset(workspace_id, command.asset_id, connection=conn)
        if not knowledge_item_readable(user_id=member_id, local_diagnostic=False,
                visibility=asset.visibility, owner_user_id=asset.owner_user_id):
            raise PermissionError("当前成员无权使用该原片素材")
        eligibility = material_usage_eligibility(asset, purpose="manual",
            expected_asset_version=command.expected_asset_updated_at)
        if not eligibility.eligible:
            raise SourceTaskConflict(eligibility.reason)
        if asset.updated_at == command.expected_bound_asset_updated_at:
            raise SourceTaskConflict("作品已绑定当前素材修订")
        start, end = source["source_start_seconds"], source["source_end_seconds"]
        montage = version.snapshot.get("montage") or {}
        clips = montage.get("clips") or []
        if (asset.media_type != "video" or asset.duration_seconds is None or not math.isfinite(asset.duration_seconds)
                or not 0 <= start < end <= asset.duration_seconds
                or montage.get("script_id") is not None or not clips
                or any(clip.get("asset_id") != asset.id or clip.get("source_path") != source["source_ref"]
                    or clip.get("segment_id") is not None or clip.get("person_render_id")
                    or not start <= clip["source_start_seconds"] < clip["source_end_seconds"] <= end
                    for clip in clips)):
            raise SourceTaskConflict("当前作品的原片区间无法复核")
        reference = repo.workspace_media_reference_for_asset(workspace_id, asset.id, connection=conn)
        if reference != source["source_ref"]:
            raise SourceTaskConflict("当前素材与作品登记的原片引用不一致")
        path = repo.resolve_workspace_media_reference(workspace_id, reference, require_registered=True, connection=conn)
        if path != Path(asset.path):
            raise SourceTaskConflict("当前登记原片不在已配置的素材目录")
        rows = conn.execute(
            "SELECT id,payload_json FROM task_events WHERE workspace_id=? AND task_id=? AND event_type='source_task_created'",
            (workspace_id, task.id),
        ).fetchall()
        if len(rows) != 1:
            raise SourceTaskConflict("缺少唯一的原片采用回执，无法复核")
        adopted = json.loads(rows[0][1])
        receipt = adopted["receipt"]
        original = receipt["source_media"]
        digest = hashlib.sha256(json.dumps({"inputs": adopted["inputs"], "source": adopted["source"]},
            sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        if (receipt["task_id"] != task.id or receipt["plan_id"] != plan.id
                or adopted["source"]["member_id"] != member_id
                or receipt["request_id"] != adopted["inputs"]["request_id"]
                or adopted["input_digest"] != digest or (plan.adoption or {}).get("version") != digest
                or (plan.adoption or {}).get("authorization_source") != adopted["source"]
                or not _same_source_selection(original, source)):
            raise SourceTaskConflict("当前原片区间与原采用回执不一致")
        return plan, asset, path, rows[0][0], original

    @staticmethod
    def _verify_current_file(observed, source, asset):
        signature = MaterialSourceFileAccess.material_copy_revision(observed)
        if (not stat.S_ISREG(observed.st_mode) or observed.st_size <= 0 or signature != source["file_signature"]
                or observed.st_size != asset.size_bytes
                or datetime.fromtimestamp(observed.st_mtime, timezone.utc) != asset.modified_at):
            raise SourceTaskConflict("当前原片文件身份已变化，无法复核")
        return observed.st_dev, signature

    def recover_revision(self, task_id: str, command: SourceRevisionRecoveryCommand, actor: CreationExecutionActor):
        workspace_id, member_id = actor.workspace_id, actor.user_id or actor.member_id
        request_id = str(UUID(command.request_id))
        inputs = {**asdict(command), "request_id": request_id,
                  "expected_bound_asset_updated_at": command.expected_bound_asset_updated_at.isoformat(),
                  "expected_asset_updated_at": command.expected_asset_updated_at.isoformat()}
        with self.repository._connect() as conn:
            conn.execute("BEGIN")
            receipt = self._recovery_receipt(conn, task_id, request_id, actor, inputs)
            if receipt is not None:
                return receipt
            target = self._recovery_target(conn, workspace_id, member_id, task_id, command)
        plan, asset, path, source_event_id, original = target
        observed = self._verify_current_file(path.stat(), plan.source_media, asset)
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        with os.fdopen(os.open(path, flags), "rb") as stream:
            if self._verify_current_file(os.fstat(stream.fileno()), plan.source_media, asset) != observed:
                raise SourceTaskConflict("打开时原片已变化")
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
            if self._verify_current_file(os.fstat(stream.fileno()), plan.source_media, asset) != observed:
                raise SourceTaskConflict("复核期间原片文件已变化")
        with self.repository._connect() as conn:
            conn.execute("BEGIN")
            proof = _original_receive_proof(conn, workspace_id, asset.owner_user_id, digest, original)
        if proof is None:
            raise SourceTaskConflict("当前文件无法与原接收摘要及采用指纹匹配")
        verdict = self.verify_identity(actor)
        if verdict == "unknown":
            raise SourceTaskIdentityUnavailable("成员或设备身份暂不可核实，未复核原片")
        if verdict != "ready":
            raise PermissionError("当前成员或设备身份已失效")
        with self.repository._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            receipt = self._recovery_receipt(conn, task_id, request_id, actor, inputs)
            if receipt is not None:
                return receipt
            if self._recovery_target(conn, workspace_id, member_id, task_id, command) != target:
                raise SourceTaskConflict("原片绑定、素材登记或使用权限在复核期间已变化")
            if _original_receive_proof(conn, workspace_id, asset.owner_user_id, digest, original) != proof:
                raise SourceTaskConflict("原接收证明在复核期间已变化")
            if self._verify_current_file(path.stat(), plan.source_media, asset) != observed:
                raise SourceTaskConflict("复核期间原片文件已变化")
            if conn.execute("SELECT 1 FROM job_runs WHERE workspace_id=? AND task_id=? "
                    "AND status IN ('queued','running') LIMIT 1", (workspace_id, task_id)).fetchone():
                raise SourceTaskConflict("作品仍有制作作业进行中，请等待结束后复核")
            revised = {**plan.source_media, "asset_updated_at": asset.updated_at.isoformat(),
                       "file_signature": observed[1]}
            result = {"request_id": request_id, "status": "recovered", "task_id": task_id, "plan_id": plan.id,
                "timeline_version_id": command.expected_timeline_version_id, "timeline_sequence": command.expected_timeline_sequence,
                "asset_id": asset.id, "previous_asset_updated_at": plan.source_media["asset_updated_at"],
                "asset_updated_at": revised["asset_updated_at"]}
            self.repository.plan_records.write_plan_payload(conn, workspace_id,
                replace(plan, source_media=revised, updated_at=utc_now()))
            self.repository.execution_records.append_task_event(TaskEvent(
                "revision_" + self._event_id(request_id, actor), workspace_id, task_id, None, "source_revision_recovered",
                {"member_id": member_id, "source_event_id": source_event_id, "inputs": inputs, "receipt": result,
                 "original_source_media": original, "source_media": revised,
                 "proof": {"owner_user_id": asset.owner_user_id, "sha256": digest, "receive_receipt": proof}}),
                connection=conn)
        return result

    def create(self, command: SourceTaskCommand, actor: CreationExecutionActor, *, source=None, write_guard=None):
        request_id = str(UUID(command.request_id))
        inputs = {**asdict(command), "request_id": request_id,
                  "expected_asset_updated_at": command.expected_asset_updated_at.isoformat()}
        authority = source if source is not None else {"kind": "manual_action", "member_id": actor.user_id or actor.member_id}
        fingerprint = hashlib.sha256(json.dumps({"inputs": inputs, "source": authority},
            sort_keys=True, ensure_ascii=False).encode()).hexdigest()

        def replay(event):
            if event.payload["input_digest"] != fingerprint:
                raise SourceTaskConflict("原请求 UUID 已绑定其他源视频、区间或采用来源")
            return event.payload["receipt"]

        # A committed result stays queryable even after its source is offline.
        with self.repository._connect() as conn:
            previous = self._receipt(conn, request_id, actor)
        if previous is not None:
            return replay(previous)
        if (not command.name.strip() or command.aspect_ratio not in {"9:16", "16:9", "1:1"}
                or type(command.preserve_audio) is not bool
                or any(not math.isfinite(value) for value in (command.source_start_seconds, command.source_end_seconds))
                or not 0 <= command.source_start_seconds < command.source_end_seconds):
            raise ValueError("源视频名称、画幅或明确区间无效")
        with self.repository._connect() as conn:
            self._member(conn, actor)
            asset = self.repository.get_asset(actor.workspace_id, command.asset_id, connection=conn)
            asset_facts = tuple(conn.execute(
                "SELECT payload,visibility,updated_at FROM material_assets WHERE workspace_id=? AND id=?",
                (actor.workspace_id, command.asset_id)).fetchone())
        reader = actor.user_id or actor.member_id
        readable = knowledge_item_readable(user_id=reader, local_diagnostic=False,
            visibility=asset.visibility, owner_user_id=asset.owner_user_id)
        if not readable:
            raise PermissionError("当前成员无权采用所选源视频")
        eligibility = material_usage_eligibility(asset, purpose="automatic" if source is not None
            and source.get("selected_asset_id") != asset.id else "manual",
            expected_asset_version=command.expected_asset_updated_at)
        if not eligibility.eligible:
            raise SourceTaskConflict(eligibility.reason)
        if (asset.media_type != "video" or asset.duration_seconds is None
                or not math.isfinite(asset.duration_seconds) or asset.duration_seconds <= 0):
            raise ValueError("源视频真实时长尚不可读取，不能猜测可用区间")
        if command.source_end_seconds > asset.duration_seconds:
            raise ValueError("所选区间超过源视频的已登记真实时长")
        reference = self.repository.workspace_media_reference_for_asset(actor.workspace_id, asset.id)
        path = self.repository.resolve_workspace_media_reference(actor.workspace_id, reference, require_registered=True)
        observed = path.stat()
        if (not stat.S_ISREG(observed.st_mode) or observed.st_size <= 0
                or observed.st_size != asset.size_bytes
                or datetime.fromtimestamp(observed.st_mtime, timezone.utc) != asset.modified_at):
            raise SourceTaskConflict("源视频文件与原资产修订不一致")
        signature = self.repository.material_source_files.material_copy_revision(observed)
        verdict = self.verify_identity(actor)
        if verdict == "unknown":
            raise SourceTaskIdentityUnavailable("成员或设备身份暂不可核实，未创建原声任务")
        if verdict != "ready":
            raise PermissionError("当前成员或设备身份尚未核实，未创建原声任务")

        event_id = self._event_id(request_id, actor)
        key = event_id.removeprefix("source_task_")
        task_id, plan_id = f"task_source_{key}", f"plan_source_{key}"
        montage_id, version_id = f"montage_source_{key}", f"timeline_source_{key}"
        duration = command.source_end_seconds - command.source_start_seconds
        source_media = {"asset_id": asset.id, "asset_updated_at": asset.updated_at.isoformat(),
            "source_ref": reference, "source_start_seconds": command.source_start_seconds,
            "source_end_seconds": command.source_end_seconds, "aspect_ratio": command.aspect_ratio,
            "preserve_audio": command.preserve_audio, "file_signature": signature}
        plan = CreationPlan(id=plan_id, workspace_id=actor.workspace_id, goal=command.name.strip(),
            name=command.name.strip(), target_count=1, language="zh-CN", aspect_ratio=command.aspect_ratio,
            duration_target_seconds=duration, lipsync_quality="high", lipsync_model_id=LATENTSYNC_16_MODEL_ID,
            knowledge_references=[], source_references=[], selected_voice=None,
            pronunciation_references=[], approved_asset_count=1, approved_clip_count=0,
            material_rule_summary="用户明确选择的原视频区间；未进行自动选片或语义分析。",
            model_snapshot=[], confirmation_points=[], blockers=[], preflight_fingerprint=fingerprint,
            owner_user_id=reader, source_media=source_media,
            adoption={"kind": "delegated_system" if source is not None else "manual",
                      "operator": "system" if source is not None else "member", "member_id": reader,
                      "version": fingerprint, "authorization_source": authority})
        task = CreationTask(task_id, actor.workspace_id, plan_id, plan.name, "source_video_edit",
            stage="montage", stage_status="waiting_for_user", status="waiting_for_user",
            recovery_point="montage", owner_user_id=reader)
        audio = AudioTimeline(tracks=[])
        if command.preserve_audio:
            audio = AudioTimeline(tracks=[TimelineAudioTrack(id="audio-original", name="原声", role="original",
                clips=[TimelineAudioClip(id="audio-source", source_ref=reference, name=asset.file_name,
                    source_start_ms=round(command.source_start_seconds * 1000),
                    source_end_ms=round(command.source_end_seconds * 1000), timeline_start_ms=0)])])
        montage = CreationTaskMontagePlan(id=montage_id, workspace_id=actor.workspace_id, task_id=task_id,
            script_id=None, script_title="", status="ready", cues=[],
            clips=[MontageClip(id="clip-source", cue_id=None, asset_id=asset.id, segment_id=None,
                source_path=reference, source_start_seconds=command.source_start_seconds,
                source_end_seconds=command.source_end_seconds, timeline_start_seconds=0,
                timeline_end_seconds=duration, match_score=0, match_reasons=["用户明确选择原片区间"],
                needs_confirmation=False, role="a_roll", manually_selected=True, fit_mode="fit")],
            duration_seconds=duration, low_confidence_clip_count=0, aspect_ratio=command.aspect_ratio,
            audio_timeline=with_resource_audio_tracks(audio))
        payload = creation_task_montage_plan_to_payload(montage)
        receipt = {"request_id": request_id, "status": "created", "plan_id": plan_id, "task_id": task_id,
                   "montage_plan_id": montage_id, "timeline_version_id": version_id, "source_media": source_media}
        with self.repository._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            previous = self._receipt(conn, request_id, actor)
            if previous is not None:
                return replay(previous)
            if write_guard is not None:
                write_guard(conn)
            current = conn.execute(
                "SELECT payload,visibility,updated_at FROM material_assets WHERE workspace_id=? AND id=?",
                (actor.workspace_id, asset.id)).fetchone()
            claim = conn.execute("SELECT workspace_id FROM workspace_media_references WHERE reference=?",
                (reference,)).fetchone()
            if current is None or tuple(current) != asset_facts or claim is None or claim[0] != actor.workspace_id:
                raise SourceTaskConflict("源视频的修订、权限或登记在采用前已变化")
            self.repository.plan_records.insert_confirmed_creation_plan(conn, plan, task)
            self.repository.montage_plan_adoption.save_creation_task_montage_plan(
                actor.workspace_id, task_id, montage_id, payload, connection=conn, expected_current_version_id=None)
            self.repository.timeline_records.create_timeline_version(TimelineVersion(version_id, actor.workspace_id,
                task_id, None, 1, {"montage": payload, "subtitle_track_ids": []}, None),
                connection=conn, expected_current_version_id=None)
            self.repository.execution_records.append_task_event(TaskEvent(event_id, actor.workspace_id, task_id,
                None, "source_task_created", {"input_digest": fingerprint, "inputs": inputs,
                    "source": authority, "receipt": receipt}), connection=conn)
        return receipt
