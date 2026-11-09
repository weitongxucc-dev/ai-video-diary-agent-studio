from __future__ import annotations

from dataclasses import asdict, replace
from datetime import datetime
import hashlib
import json

from app.creation.contracts import CreationTask as _CreationTask, TaskEvent as _TaskEvent
from app.creation.plan_contracts import CreationPlanPronunciationReference as _CreationPlanPronunciationReference
from app.creation.plan_contracts import creation_plan_payload
from app.creation.script_contracts import _assistant_reply_request_event_id
from app.materials.contracts import MediaAuthorityError as _MediaAuthorityError
from app.models import utc_now
from app.persistence_json import _json_payload, _json_object
from app.source_policy import knowledge_item_readable as _content_readable


class NarrationVoiceSelectionTransaction:
    def __init__(self, connection_factory, *, require_task, decode_plan, voice_reference,
                 pronunciation_entries, append_event, decode_job, revision_value):
        self._connect = connection_factory
        self._require_task = require_task
        self._decode_plan = decode_plan
        self.creation_plan_voice_reference = voice_reference
        self.list_applicable_pronunciation_entries = pronunciation_entries
        self._append_event = append_event
        self._decode_job = decode_job
        self._revision_value = revision_value

    @staticmethod
    def _narration_voice_selection_owner(task: _CreationTask, member_id: str) -> None:
        if not member_id or not _content_readable(user_id=member_id, local_diagnostic=False,
                visibility=task.visibility, owner_user_id=task.owner_user_id):
            raise KeyError(task.id)
        if task.owner_user_id and task.owner_user_id != member_id:
            raise PermissionError("当前成员不能更改此任务的配音声音。")

    def get_creation_task_narration_voice_selection(
        self, workspace_id: str, task_id: str, request_id: str, *, member_id: str,
    ) -> dict[str, object]:
        with self._connect() as conn:
            task = self._require_task(conn, workspace_id, task_id)
            self._narration_voice_selection_owner(task, member_id)
            event_id = _assistant_reply_request_event_id("task_narration_voice_selected", workspace_id, request_id)
            row = conn.execute("SELECT task_id,payload_json FROM task_events WHERE workspace_id=? AND id=? AND event_type='task_narration_voice_selected'",
                (workspace_id, event_id)).fetchone()
            if row is None or row["task_id"] != task_id:
                raise KeyError(request_id)
            payload = json.loads(row["payload_json"])
            if payload["member_id"] != member_id:
                raise KeyError(request_id)
            return payload["receipt"]

    def select_creation_task_narration_voice(
        self,
        *,
        workspace_id: str,
        task_id: str,
        member_id: str,
        request_id: str,
        voice_profile_id: str,
        voice_version: str,
        expected_plan_updated_at: datetime | str,
        expected_task_updated_at: datetime | str,
        queued_job_rebind=None,
        voice_source_preparation=None,
        write_guard=None,
    ) -> dict[str, object]:
        """Select one fixed voice and record its original request in the same transaction."""
        if not request_id.strip():
            raise ValueError("选择配音声音需要稳定请求身份。")
        inputs = {"task_id": task_id, "member_id": member_id, "voice_profile_id": voice_profile_id,
            "voice_version": voice_version, "expected_plan_updated_at": self._revision_value(expected_plan_updated_at),
            "expected_task_updated_at": self._revision_value(expected_task_updated_at)}
        fingerprint = hashlib.sha256(json.dumps(inputs, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        event_id = _assistant_reply_request_event_id("task_narration_voice_selected", workspace_id, request_id)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if write_guard is not None:
                write_guard(conn)
            task = self._require_task(conn, workspace_id, task_id)
            self._narration_voice_selection_owner(task, member_id)
            previous = conn.execute("SELECT task_id,payload_json FROM task_events WHERE id=?", (event_id,)).fetchone()
            if previous is not None:
                saved = json.loads(previous["payload_json"])
                if previous["task_id"] != task_id or saved["fingerprint"] != fingerprint:
                    raise ValueError("此声音采用请求已用于不同输入，请查询原请求。")
                return saved["receipt"]
            plan_row = conn.execute(
                "SELECT payload FROM creation_plans WHERE workspace_id = ? AND id = ?",
                (workspace_id, task.plan_id),
            ).fetchone()
            if plan_row is None:
                raise KeyError(task.plan_id)
            plan = self._decode_plan(json.loads(str(plan_row["payload"])))
            if plan.creation_task_id != task.id:
                raise ValueError("创作方案与任务不一致")
            if (not _content_readable(user_id=member_id, local_diagnostic=False,
                    visibility=plan.visibility, owner_user_id=plan.owner_user_id)
                    or plan.owner_user_id and plan.owner_user_id != member_id):
                raise PermissionError("当前成员不能更改此创作方案的配音声音。")
            if (self._revision_value(plan.updated_at) != inputs["expected_plan_updated_at"]
                    or self._revision_value(task.updated_at) != inputs["expected_task_updated_at"]):
                raise ValueError("方案或任务版本已变化，请读取当前版本后重新选择声音。")
            if task.stage != "narration" or task.stage_status not in {
                "not_started",
                "failed",
            }:
                raise ValueError("当前任务不能选择配音声音")
            active_jobs = conn.execute("SELECT * FROM job_runs WHERE workspace_id=? AND task_id=? AND status IN ('queued','running','interrupted')",
                    (workspace_id, task_id)).fetchall()
            if active_jobs and (queued_job_rebind is None or len(active_jobs) != 1):
                raise ValueError("此任务仍有未结束的制作作业；先等作业结算或取消，再选择配音声音。")
            try:
                if voice_source_preparation is None:
                    selected_voice = self.creation_plan_voice_reference(workspace_id, voice_profile_id, voice_version,
                        viewer_user_id=member_id)
                else:
                    selected_voice = voice_source_preparation['selected_voice']
                    if (selected_voice.profile_id, selected_voice.version) != (voice_profile_id, voice_version):
                        raise ValueError('本次来源准备不属于所选声音')
                entries = self.list_applicable_pronunciation_entries(workspace_id=workspace_id,
                    person_profile_id=selected_voice.person_profile_id, episode_id=task.id)
            except (KeyError, ValueError, PermissionError, OSError) as exc:
                raise _MediaAuthorityError(reason="voice_reference_unavailable", impact="声音引用未更新，未调用模型。",
                    repair_action=str(exc)) from exc
            if selected_voice.language not in {{"zh-CN":"zh", "en-US":"en", "zh-CN/en-US":"zh-en"}[plan.language], "zh-en"}:
                raise _MediaAuthorityError(reason="voice_language_unavailable", impact="声音语言不匹配。",
                    repair_action="选择匹配方案语言的声音。")
            pronunciation_references = [_CreationPlanPronunciationReference(id=entry.id, version=entry.version,
                scope=entry.scope, scope_reference=entry.scope_reference, language=entry.language) for entry in entries]

            now = utc_now()
            previous_plan = replace(plan)
            previous_voice = asdict(plan.selected_voice) if plan.selected_voice else None
            plan.selected_voice = selected_voice
            plan.pronunciation_references = pronunciation_references
            plan.blockers = [
                blocker
                for blocker in plan.blockers
                if not (
                    blocker.stage == "narration"
                    and blocker.reason
                    in {
                        "voice_authorization_missing",
                        "voice_identity_missing",
                        "voice_language_unavailable",
                        "voice_model_unavailable",
                        "voice_reference_wav_unavailable",
                    }
                )
            ]
            plan.updated_at = now
            task.stage_status = "not_started"
            task.status = "running"
            task.recovery_point = "narration"
            task.stage_reason = None
            task.stage_impact = None
            task.stage_repair_action = None
            task.stage_retryable = None
            task.updated_at = now
            conn.execute(
                "UPDATE creation_plans SET payload = ?, updated_at = ? WHERE workspace_id = ? AND id = ?",
                (_json_object(creation_plan_payload(plan)), now.isoformat(), workspace_id, plan.id),
            )
            conn.execute(
                "UPDATE creation_tasks SET payload = ?, updated_at = ? WHERE workspace_id = ? AND id = ?",
                (_json_payload(task), now.isoformat(), workspace_id, task.id),
            )
            receipt = {"request_id": request_id, "workspace_id": workspace_id, "member_id": member_id,
                "task_id": task.id, "plan_id": plan.id, "status": "selected", "recorded_at": now.isoformat(),
                "expected_plan_updated_at": inputs["expected_plan_updated_at"],
                "expected_task_updated_at": inputs["expected_task_updated_at"],
                "plan_updated_at": now.isoformat(), "task_updated_at": now.isoformat(),
                "previous_voice": previous_voice, "selected_voice": asdict(selected_voice),
                "pronunciation_references": [asdict(item) for item in pronunciation_references],
                "confirmed_script_id": task.confirmed_script_id}
            self._append_event(_TaskEvent(event_id, workspace_id, task_id, None, "task_narration_voice_selected",
                {"fingerprint": fingerprint, "member_id": member_id, "receipt": receipt}), connection=conn)
            if active_jobs:
                queued_job_rebind(conn, self._decode_job(active_jobs[0]), previous_plan, event_id)
            return receipt
