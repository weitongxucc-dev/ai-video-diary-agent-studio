"""Adopt one explicitly selected source-video range as an editable task."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import stat
from uuid import UUID

from app.audio_timeline import AudioTimeline, TimelineAudioClip, TimelineAudioTrack
from app.creation.contracts import CreationTask, TaskEvent, TimelineVersion
from app.creation.plan_contracts import CreationPlan
from app.creation_execution import CreationExecutionActor
from app.materials.contracts import material_usage_eligibility
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


class SourceTaskConflict(ValueError):
    """The original request or selected source revision no longer matches."""


class SourceTaskIdentityUnavailable(RuntimeError):
    """Identity could not be observed; this does not establish revocation."""


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
            duration_target_seconds=duration, knowledge_references=[], source_references=[], selected_voice=None,
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
            audio_timeline=audio)
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
