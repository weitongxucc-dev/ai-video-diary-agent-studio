"""Confirm one bounded window in its original cross-object transaction."""
from __future__ import annotations

import json
from dataclasses import asdict
from typing import Literal

from app.creation.contracts import TaskEvent as _TaskEvent
from app.creation.request_contracts import CreationRequestConflict
from app.creation.script_contracts import (
    CreationTaskScript as _CreationTaskScript,
    CreationTaskScriptSection as _CreationTaskScriptSection,
    ScriptGenerationRun as _ScriptGenerationRun,
)
from app.creation.topic_contracts import (
    TopicCandidate as _TopicCandidate,
    TopicGenerationRun as _TopicGenerationRun,
)
from app.models import utc_now
from app.source_policy import content_owner, knowledge_item_readable as _content_readable


class CreationRequestAdoptionUseCase:
    def __init__(self, connection_factory, *, records, decode_plan, decode_task,
                 prepare_plan, insert_plan, read_asset, decode_asset, material_eligible,
                 voice_reference, person_reference, write_task,
                 topic_records, script_records, execution_records,
                 evidence_refs_for=None):
        self._connect = connection_factory
        self.records = records
        self._decode_plan, self._decode_task = decode_plan, decode_task
        self.prepare_plan, self.insert_plan = prepare_plan, insert_plan
        self._read_asset, self._decode_asset = read_asset, decode_asset
        self._material_eligible = material_eligible
        self._voice_reference, self._person_reference = voice_reference, person_reference
        self._write_task = write_task
        self.topic_records, self.script_records = topic_records, script_records
        self.execution_records = execution_records
        self._evidence_refs_for = evidence_refs_for

    def confirm_creation_request_window(self, workspace_id: str, request_id: str, window_id: str, *,
                                       expected_revision: int, input_fingerprint: str,
                                       verified_owner_user_id: str | None = None, local_diagnostic: bool = False,
                                       adoption_kind: Literal["manual", "delegated_system"] = "manual") -> dict:
        from app.creation.intent_use_case import CreationIntentUseCase
        owner = content_owner(verified_owner_user_id)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self.records.require_request(conn,workspace_id,request_id,owner,local_diagnostic)
            window = self.records.read_window(conn, request_id, window_id)
            if window is None:
                raise KeyError(window_id)
            if window["input_fingerprint"] != input_fingerprint:
                raise CreationRequestConflict("规划窗口输入摘要已变化，未确认任务")
            if window["status"] == "confirmed":
                return self.records.header(conn,row)
            self.records.check_revision(row,expected_revision)
            if window["status"] != "proposed" or row["current_window_id"] != window_id:
                raise CreationRequestConflict("该规划窗口已失效，请审阅当前窗口")
            intent = json.loads(row["requirements_json"]).get("creation_intent")
            adoption = CreationIntentUseCase.adoption(intent, kind=adoption_kind, member_id=owner,
                version=window["input_fingerprint"])
            for item in self.records.read_window_items(conn, window_id):
                plan = self._decode_plan(json.loads(item["plan_payload"]))
                task = self._decode_task(json.loads(item["task_payload"]))
                plan.creation_intent = intent
                CreationIntentUseCase.use_provided_copy(plan, intent)
                plan,task = self.prepare_plan(plan,task,verified_owner_user_id=owner,local_diagnostic=local_diagnostic)
                for match in plan.material_matches:
                    asset_row = self._read_asset(
                        conn, workspace_id, match.asset_ref.removeprefix("asset:"),
                    )
                    if asset_row is None:
                        raise ValueError("规划窗口引用的素材已不存在")
                    asset = self._decode_asset(json.loads(asset_row["payload"]))
                    segment = next((part for part in asset.segments if part.id == match.segment_ref.removeprefix("segment:")),None)
                    if not _content_readable(user_id=owner,local_diagnostic=local_diagnostic,visibility=asset_row["visibility"],owner_user_id=asset.owner_user_id):
                        raise PermissionError("当前成员已无权使用规划窗口的素材")
                    if segment is None or not self._material_eligible(asset,segment,workspace_id,automation_only=True):
                        raise ValueError("规划窗口引用的素材文件或使用资格已变化")
                    if match.evidence_ref_ids and self._evidence_refs_for is not None:
                        current_evidence = {
                            item.id for item in self._evidence_refs_for(
                                workspace_id, asset.id, segment.id,
                            )
                        }
                        if not set(match.evidence_ref_ids) <= current_evidence:
                            raise ValueError("规划窗口引用的素材证据已失效，请重新检索并准备方案")
                if plan.selected_voice:
                    self._voice_reference(workspace_id,plan.selected_voice.profile_id,plan.selected_voice.version,viewer_user_id=owner,local_diagnostic=local_diagnostic)
                if plan.selected_person:
                    self._person_reference(workspace_id,plan.selected_person.profile_id,plan.selected_person.version,viewer_user_id=owner,local_diagnostic=local_diagnostic)
                plan.adoption = {**adoption, "version": plan.preflight_fingerprint,
                    "window_input_fingerprint": window["input_fingerprint"], "plan_id": plan.id, "preflight_fingerprint": plan.preflight_fingerprint,
                    "request_id": request_id, "request_revision": row["revision"], "window_id": window_id,
                    "material_adoption": [
                        {
                            **asdict(match),
                            "adopted_at": utc_now().isoformat(),
                            "adoption_kind": adoption_kind,
                            "adopted_by": owner,
                        }
                        for match in plan.material_matches
                    ]}
                self.insert_plan(conn,plan,task)
                self._apply_creation_intent(conn, plan, task, intent, adoption)
            now = utc_now().isoformat()
            self.records.mark_confirmed(conn, request_id, window_id, now)
            return self.records.header(conn,self.records.require_request(conn,workspace_id,request_id,owner,local_diagnostic))


    def _apply_creation_intent(self, conn, plan, task, intent, adoption):
        """Adopt provided topic/copy inside the same Plan + Task transaction."""
        from app.creation.intent_use_case import CreationIntentUseCase
        CreationIntentUseCase.use_provided_copy(plan, intent)
        final = intent.get("final_script") if intent else None
        title = (intent.get("topic_title") or (final["title"] if final else None)) if intent else None
        now = utc_now()
        if title and not task.selected_topic_id:
            topic_run = _TopicGenerationRun(f"provided_topic_run_{task.id}", task.workspace_id, task.id, plan.id,
                "succeeded", {"operation": "provided_topic", "provider": "none"}, "provided-topic/v1", [], started_at=now, finished_at=now)
            topic = _TopicCandidate(f"provided_topic_{task.id}", task.workspace_id, task.id, topic_run.id,
                title, "", "", [], 0, selection_status="confirmed", created_at=now)
            self.topic_records.save_provided_topic(conn, topic_run, topic)
            task.selected_topic_id = topic.id
            task.stage, task.stage_status, task.recovery_point = "script", "not_started", "script"
            task.status, task.progress = "running", max(task.progress, 20)
        if final:
            script = _CreationTaskScript(f"provided_script_{task.id}", task.workspace_id, task.id,
                f"provided_script_run_{task.id}", task.selected_topic_id, final["title"], plan.language,
                [_CreationTaskScriptSection(f"provided_section_{task.id}", final["title"], final["text"], "", [])],
                selection_status="confirmed", created_at=now, updated_at=now, confirmed_at=now,
                source_reply_reference=final.get("source_reply_reference"), locked_body_digest=final["body_digest"])
            script.adoption = {**CreationIntentUseCase.adoption(intent, kind=adoption["kind"],
                member_id=plan.owner_user_id, version=script.updated_at.isoformat()),
                "script_id": script.id, "body_digest": final["body_digest"]}
            run = _ScriptGenerationRun(script.run_id, task.workspace_id, task.id, plan.id, task.selected_topic_id,
                "succeeded", {"operation": "provided_final_copy", "provider": "none"}, "provided-copy/v1", [],
                started_at=now, finished_at=now, run_kind="provided_copy", source_reply_reference=script.source_reply_reference)
            self.script_records.save_provided_copy(conn,run,script)
            task.confirmed_script_id = script.id
            task.stage, task.stage_status, task.recovery_point = "narration", "not_started", "narration"
            task.status, task.progress = "running", max(task.progress, 40)
        if title or final:
            task.updated_at = now
            self._write_task(conn, task)
        self.execution_records.append_task_event(_TaskEvent(f"creation_adopted_{task.id}", task.workspace_id, task.id, None,
            "creation_intent_adopted", {"plan_id": plan.id, "plan_adoption": plan.adoption,
                "script_id": task.confirmed_script_id, "script_adoption": script.adoption if final else None}), connection=conn)
