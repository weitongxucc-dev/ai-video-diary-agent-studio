"""Document record owner; Task and Plan writes stay behind explicit owner ports."""

from __future__ import annotations
from contextlib import nullcontext
from dataclasses import asdict, replace
from datetime import datetime
from typing import Literal
from uuid import uuid4
import hashlib
import json
import sqlite3
from app.models import utc_now
from app.source_policy import content_owner
from app.persistence_json import _json_payload, _json_object, _with_datetime
from app.creation.contracts import TaskEvent, CreationTask
from app.creation.script_contracts import (
    ScriptGenerationRun,
    CreationTaskScript,
    CreationTaskScriptSection,
    ScriptLibraryEntry,
    ScriptLibrarySection,
    _validated_assistant_reply_reference,
    _assistant_reply_input_references,
    _assistant_reply_request_event_id,
)
from app.creation.script_windows import (
    SCRIPT_SECTION_WINDOW_LIMIT,
    ScriptWindowConflict,
    write_script_sections,
    read_script_sections,
    script_revision,
    script_layout_digest,
    section_digest,
    script_patch_digest,
    script_patch_event_id,
    script_confirmation_digest,
    upsert_script_sections,
)


def _revision_value(value):
    result = value.isoformat() if isinstance(value, datetime) else value.strip()
    if not result:
        raise ValueError("对象修订令牌不能为空")
    return result


def _json_object_from_text(value):
    decoded = json.loads(value)
    if not isinstance(decoded, dict):
        raise ValueError("持久化 JSON 对象无效")
    return decoded


class ScriptRecordStore:
    def __init__(
        self,
        connection_factory,
        *,
        read_task,
        require_task,
        decode_plan,
        read_topic,
        transition_task,
        create_library_task,
        knowledge_inputs_current,
        append_event,
        get_event,
        decode_event,
    ):
        self._connect = connection_factory
        self.get_creation_task = read_task
        self._require_creation_task = require_task
        self._creation_plan_from_payload = decode_plan
        self._read_topic = read_topic
        self._transition_task = transition_task
        self._create_library_task = create_library_task
        self._knowledge_inputs_current = knowledge_inputs_current
        self.append_task_event = append_event
        self.get_task_event = get_event
        self._task_event_from_row = decode_event

    def save_provided_copy(self, conn, run, script):
        if not conn.in_transaction:
            raise ValueError("原文定稿存储必须属于调用方原事务")
        if (
            run.run_kind != "provided_copy"
            or run.status != "succeeded"
            or (run.workspace_id, run.task_id, run.id, run.topic_id)
            != (script.workspace_id, script.task_id, script.run_id, script.topic_id)
        ):
            raise ValueError("原文定稿与运行身份不一致")
        conn.execute(
            "INSERT INTO script_generation_runs VALUES(?,?,?,?,?,?,?,?)",
            (
                run.id,
                run.workspace_id,
                run.task_id,
                run.plan_id,
                run.topic_id,
                _json_payload(run),
                run.started_at.isoformat(),
                script.updated_at.isoformat(),
            ),
        )
        conn.execute(
            "INSERT INTO creation_task_scripts VALUES(?,?,?,?,?,?)",
            (
                script.id,
                script.workspace_id,
                script.task_id,
                script.run_id,
                self._script_payload(conn, script),
                script.updated_at.isoformat(),
            ),
        )

    def script_knowledge_inputs_current(self, workspace_id: str, task_id: str, script_id: str) -> bool:
        with self._connect() as conn:
            task = self._require_creation_task(conn, workspace_id, task_id)
            return self._script_knowledge_inputs_current(conn, task, script_id)

    def _script_knowledge_inputs_current(
        self, conn: sqlite3.Connection, task: CreationTask, script_id: str
    ) -> bool:
        row = conn.execute(
            "SELECT r.payload,json_extract(s.payload,'$.reused_from_entry_id') AS library_id FROM creation_task_scripts s LEFT JOIN script_generation_runs r ON r.id=s.run_id "
            "AND r.workspace_id=s.workspace_id AND r.task_id=s.task_id WHERE s.workspace_id=? AND s.task_id=? AND s.id=?",
            (task.workspace_id, task.id, script_id),
        ).fetchone()
        if row is None:
            raise KeyError(script_id)
        if row["library_id"]:
            return (
                conn.execute(
                    "SELECT 1 FROM script_library_entries WHERE workspace_id=? AND id=? AND json_extract(payload,'$._sections_storage')='rows'",
                    (task.workspace_id, row["library_id"]),
                ).fetchone()
                is not None
            )
        references = (
            json.loads(row["payload"])["input_references"] if row["payload"] else []
        )
        return self._knowledge_inputs_current(
            conn, task.workspace_id, task.id, task.plan_id, references
        )

    def generation_run_state(self, connection, workspace_id, task_id, run_id):
        if not connection.in_transaction:
            raise ValueError("生成事实读取必须属于调用方快照")
        row = connection.execute(
            "SELECT payload FROM script_generation_runs WHERE workspace_id=? AND task_id=? AND id=?",
            (workspace_id, task_id, run_id),
        ).fetchone()
        if row is None:
            raise KeyError(run_id)
        run = self._script_generation_run_from_payload(json.loads(row["payload"]))
        rows = connection.execute(
            "SELECT payload FROM creation_task_scripts WHERE workspace_id=? AND task_id=? AND run_id=? LIMIT 2",
            (workspace_id, task_id, run_id),
        ).fetchall()
        if len(rows) > 1:
            raise ScriptWindowConflict("原生成运行绑定了不唯一的稿件，未猜测最新稿")
        script = None
        if rows:
            script = json.loads(rows[0]["payload"])
            if script.pop("_sections_storage", None) != "rows" or "sections" in script:
                raise ScriptWindowConflict("生成稿未采用唯一正文行存储")
            count = connection.execute(
                "SELECT count(*) FROM creation_task_script_sections WHERE workspace_id=? AND script_id=?",
                (workspace_id, script["id"]),
            ).fetchone()[0]
            script.update(
                sections=None, sections_loaded=False, sections_total_count=count
            )
        return {"run": run, "script": script}

    def attach_generation_window(self, run_id, script, *, write_guard, result_write):
        if not script.sections or len(script.sections) > SCRIPT_SECTION_WINDOW_LIMIT:
            raise ValueError("首窗必须是有限的实际候选")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            write_guard(conn)
            state = self.generation_run_state(
                conn, script.workspace_id, script.task_id, run_id
            )
            task = self._require_creation_task(
                conn, script.workspace_id, script.task_id
            )
            run = state["run"]
            if (
                state["script"] is not None
                or run.status != "running"
                or run.run_kind != "generation"
                or script.run_id != run.id
                or script.topic_id != run.topic_id
                or task.selected_topic_id != run.topic_id
                or task.plan_id != run.plan_id
                or task.stage != "script"
                or task.stage_status != "running"
                or task.confirmed_script_id
            ):
                raise ScriptWindowConflict("首窗不属于当前可继续的原生成运行")
            allowed = {r["reference_id"] for r in run.input_references}
            if any(
                not set(part.evidence_reference_ids).issubset(allowed)
                for part in script.sections
            ):
                raise ValueError("首窗引用了原冻结输入以外的来源")
            script.draft_complete = False
            conn.execute(
                "INSERT INTO creation_task_scripts VALUES (?,?,?,?,?,?)",
                (
                    script.id,
                    script.workspace_id,
                    script.task_id,
                    script.run_id,
                    self._script_payload(conn, script),
                    script.updated_at.isoformat(),
                ),
            )
            result_write(conn, script)
            return script

    def start_script_generation(
        self, run: ScriptGenerationRun, *, write_guard=None, connection=None
    ) -> tuple[CreationTask, ScriptGenerationRun]:
        if run.run_kind != "generation" or run.source_reply_reference is not None:
            raise ValueError("对话回复移交不能伪装成模型生成运行")
        if run.status != "running":
            raise ValueError("文案运行必须以 running 状态开始")
        with self._connect() if connection is None else nullcontext(connection) as conn:
            if connection is None:
                conn.execute("BEGIN IMMEDIATE")
            if write_guard is not None:
                write_guard(conn)
            row = conn.execute(
                "SELECT payload FROM creation_tasks WHERE workspace_id = ? AND id = ?",
                (run.workspace_id, run.task_id),
            ).fetchone()
            if row is None:
                raise KeyError(run.task_id)
            task = self._require_creation_task(conn, run.workspace_id, run.task_id)
            if task.plan_id != run.plan_id or task.selected_topic_id != run.topic_id:
                raise ValueError("文案运行与已确认选题不一致")
            if not self._knowledge_inputs_current(
                conn, run.workspace_id, run.task_id, run.plan_id, run.input_references
            ):
                raise ValueError("任务已追加资料，请重新读取全部固定引用后生成新稿。")
            duration_repair = (
                task.needs_script_duration_repair and task.can_revise_script
            )
            if not duration_repair and (
                task.stage != "script"
                or task.stage_status not in {"not_started", "failed"}
            ):
                raise ValueError("当前创作任务不允许再次开始生成文案")
            now = utc_now()
            conn.execute(
                """
                INSERT INTO script_generation_runs
                    (id, workspace_id, task_id, plan_id, topic_id, payload, started_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run.id,
                    run.workspace_id,
                    run.task_id,
                    run.plan_id,
                    run.topic_id,
                    _json_payload(run),
                    run.started_at.isoformat(),
                    now.isoformat(),
                ),
            )
            self._transition_task(conn, task, "started", now)
        if connection is not None:
            return task, run
        return (
            self.get_creation_task(run.workspace_id, run.task_id),
            self.get_script_generation_run(run.workspace_id, run.id),
        )

    def transfer_completed_assistant_reply_to_script(
        self,
        workspace_id: str,
        task_id: str,
        *,
        request_id: str,
        reply_reference: dict[str, str],
        plan_conversion_request_id: str | None = None,
        reply_text: str,
        expected_plan_id: str,
        expected_plan_updated_at: datetime | str,
        expected_topic_id: str,
        expected_task_updated_at: datetime | str,
        input_references: list[dict[str, object]],
        script_id: str,
        run_id: str,
        title: str,
        language: Literal["zh-CN", "en-US", "zh-CN/en-US"],
        sections: list[CreationTaskScriptSection],
        replace_existing_script_id: str | None = None,
        expected_script_updated_at: datetime | str | None = None,
        verified_owner_user_id: str | None = None,
    ) -> tuple[CreationTask, ScriptGenerationRun, CreationTaskScript]:
        """Persist an explicit reply-to-draft transfer against current task facts."""
        reference = _validated_assistant_reply_reference(reply_reference)
        if (
            not request_id.strip()
            or not reply_text.strip()
            or not title.strip()
            or not sections
        ):
            raise ValueError("移交文案需要请求身份、回复正文、标题和段落")
        if (
            hashlib.sha256(reply_text.encode("utf-8")).hexdigest()
            != reference["response_digest"]
        ):
            raise ValueError("对话回复正文已变化，请重新读取当前回复")
        if any(
            not section.id.strip()
            or not section.title.strip()
            or not section.content.strip()
            for section in sections
        ):
            raise ValueError("移交文案段落必须包含标题和正文")
        if "".join(section.content for section in sections) != reply_text:
            raise ValueError("移交草稿必须保留当前对话回复正文")
        references_by_id = _assistant_reply_input_references(input_references)
        evidence_ids = {
            reference_id
            for section in sections
            for reference_id in section.evidence_reference_ids
        }
        if not evidence_ids.issubset(references_by_id):
            raise ValueError("移交文案引用了当前任务输入之外的资料")

        request_payload: dict[str, object] = {
            "workspace_id": workspace_id,
            "task_id": task_id,
            "reply_reference": reference,
            "plan_conversion_request_id": plan_conversion_request_id,
            "expected_plan_id": expected_plan_id,
            "expected_plan_updated_at": _revision_value(expected_plan_updated_at),
            "expected_topic_id": expected_topic_id,
            "expected_task_updated_at": _revision_value(expected_task_updated_at),
            "input_references": input_references,
            "script_id": script_id,
            "run_id": run_id,
            "title": title,
            "language": language,
            "sections": [asdict(section) for section in sections],
            "replace_existing_script_id": replace_existing_script_id,
            "expected_script_updated_at": (
                _revision_value(expected_script_updated_at)
                if expected_script_updated_at is not None
                else None
            ),
            "owner_user_id": content_owner(verified_owner_user_id),
        }
        request_payload.pop("script_id")
        request_payload.pop("run_id")
        # Revision tokens fence a new write, but a retry may observe revisions
        # advanced by that same successful write. Keep them out of the stable
        # request identity so the persisted result can be replayed after readback.
        fingerprint_payload = {
            key: value
            for key, value in request_payload.items()
            if key
            not in {
                "expected_plan_updated_at",
                "expected_task_updated_at",
                "expected_script_updated_at",
            }
        }
        request_fingerprint = hashlib.sha256(
            _json_object(fingerprint_payload).encode("utf-8")
        ).hexdigest()
        event_id = _assistant_reply_request_event_id(
            "assistant_reply_script_transferred",
            workspace_id,
            request_id,
        )
        replay_ids: tuple[str, str] | None = None
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            prior = conn.execute(
                "SELECT * FROM task_events WHERE id = ?",
                (event_id,),
            ).fetchone()
            if prior is not None:
                event = self._task_event_from_row(prior)
                if (
                    event.event_type != "assistant_reply_script_transferred"
                    or event.workspace_id != workspace_id
                    or event.task_id != task_id
                    or event.payload.get("request_fingerprint") != request_fingerprint
                ):
                    raise ValueError("回复移交请求身份已用于不同的任务或输入")
                replay_ids = (
                    str(event.payload["run_id"]),
                    str(event.payload["script_id"]),
                )
            else:
                task = self._require_creation_task(conn, workspace_id, task_id)
                if task.plan_id != expected_plan_id:
                    raise ValueError("回复移交的创作方案已变化")
                plan_row = conn.execute(
                    "SELECT payload FROM creation_plans WHERE workspace_id = ? AND id = ?",
                    (workspace_id, task.plan_id),
                ).fetchone()
                if plan_row is None:
                    raise KeyError(task.plan_id)
                plan = self._creation_plan_from_payload(
                    json.loads(str(plan_row["payload"]))
                )
                if _revision_value(plan.updated_at) != _revision_value(
                    expected_plan_updated_at
                ):
                    raise ValueError("创作方案版本已变化，请重新读取后移交")
                if task.selected_topic_id != expected_topic_id:
                    raise ValueError("回复移交的已确认选题已变化")
                if task.confirmed_script_id is not None:
                    raise ValueError("已确认文案不能被对话回复移交覆盖")
                try:
                    topic = self._read_topic(
                        workspace_id, task_id, expected_topic_id, connection=conn,
                    )
                except KeyError as exc:
                    raise ValueError("回复移交选题不属于当前任务") from exc
                if topic.selection_status != "confirmed":
                    raise ValueError("回复移交前必须确认当前选题")
                if task.stage != "script" or task.stage_status not in {
                    "not_started",
                    "waiting_for_user",
                }:
                    raise ValueError("当前创作任务不在可移交的文案阶段")
                if _revision_value(task.updated_at) != _revision_value(
                    expected_task_updated_at
                ):
                    raise ValueError("创作任务版本已变化，请重新读取后移交")
                if plan_conversion_request_id is not None:
                    self._require_assistant_reply_plan_event(
                        conn,
                        workspace_id=workspace_id,
                        task_id=task_id,
                        plan_id=plan.id,
                        request_id=plan_conversion_request_id,
                        reply_reference=reference,
                        reply_owner_user_id=content_owner(verified_owner_user_id),
                    )
                self._validate_assistant_reply_inputs(plan, references_by_id)
                if not self._knowledge_inputs_current(
                    conn, workspace_id, task_id, plan.id, input_references
                ):
                    raise ValueError(
                        "任务已追加资料，请重新读取全部固定引用后另存回复文案。"
                    )

                current_scripts = conn.execute(
                    "SELECT payload FROM creation_task_scripts WHERE workspace_id = ? AND task_id = ? ORDER BY updated_at DESC, id DESC",
                    (workspace_id, task_id),
                ).fetchall()
                latest = (
                    self._creation_task_script_from_payload(
                        json.loads(str(current_scripts[0]["payload"])), connection=conn
                    )
                    if current_scripts
                    else None
                )
                if latest is not None and not self._script_knowledge_inputs_current(
                    conn, task, latest.id
                ):
                    if (
                        replace_existing_script_id is not None
                        or expected_script_updated_at is not None
                    ):
                        raise ValueError(
                            "任务已追加资料，旧稿仅保留为历史；请基于当前资料另存新稿。"
                        )
                    latest = None
                if latest is not None:
                    if (
                        replace_existing_script_id != latest.id
                        or expected_script_updated_at is None
                    ):
                        raise ValueError("当前已有文案草稿，请明确选择替换")
                    if latest.selection_status != "draft":
                        raise ValueError("已有文案不是可替换的待确认草稿")
                    if _revision_value(latest.updated_at) != _revision_value(
                        expected_script_updated_at
                    ):
                        raise ValueError("文案草稿已变化，请重新读取后确认替换")
                    script = latest
                    script_id = latest.id
                    script.run_id = run_id
                    script.topic_id = expected_topic_id
                    script.title = title.strip()
                    script.language = language
                    script.sections = sections
                    script.selection_status = "draft"
                    script.confirmed_at = None
                    script.source_reply_reference = reference
                else:
                    if (
                        replace_existing_script_id is not None
                        or expected_script_updated_at is not None
                    ):
                        raise ValueError("没有当前草稿可按指定修订替换")
                    script = CreationTaskScript(
                        id=script_id,
                        workspace_id=workspace_id,
                        task_id=task_id,
                        run_id=run_id,
                        topic_id=expected_topic_id,
                        title=title.strip(),
                        language=language,
                        sections=sections,
                        source_reply_reference=reference,
                    )

                now = utc_now()
                run = ScriptGenerationRun(
                    id=run_id,
                    workspace_id=workspace_id,
                    task_id=task_id,
                    plan_id=plan.id,
                    topic_id=topic.id,
                    status="succeeded",
                    route_snapshot={
                        "operation": "explicit_assistant_reply_transfer",
                        "provider": "none",
                    },
                    prompt_schema_version="assistant-reply-transfer/v1",
                    input_references=list(input_references),
                    started_at=now,
                    finished_at=now,
                    run_kind="assistant_reply_transfer",
                    source_reply_reference=reference,
                )
                script.updated_at = now
                conn.execute(
                    "INSERT INTO script_generation_runs (id, workspace_id, task_id, plan_id, topic_id, payload, started_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        run.id,
                        workspace_id,
                        task_id,
                        plan.id,
                        topic.id,
                        _json_payload(run),
                        now.isoformat(),
                        now.isoformat(),
                    ),
                )
                if latest is None:
                    conn.execute(
                        "INSERT INTO creation_task_scripts (id, workspace_id, task_id, run_id, payload, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            script.id,
                            workspace_id,
                            task_id,
                            run.id,
                            self._script_payload(conn, script),
                            now.isoformat(),
                        ),
                    )
                else:
                    conn.execute(
                        "UPDATE creation_task_scripts SET run_id = ?, payload = ?, updated_at = ? WHERE workspace_id = ? AND task_id = ? AND id = ?",
                        (
                            run.id,
                            self._script_payload(conn, script),
                            now.isoformat(),
                            workspace_id,
                            task_id,
                            script.id,
                        ),
                    )
                self._transition_task(conn, task, "transferred", now)
                self.append_task_event(
                    TaskEvent(
                        id=event_id,
                        workspace_id=workspace_id,
                        task_id=task_id,
                        job_id=None,
                        event_type="assistant_reply_script_transferred",
                        payload={
                            "request_id": request_id,
                            "request_fingerprint": request_fingerprint,
                            "reply_reference": reference,
                            "plan_conversion_request_id": plan_conversion_request_id,
                            "plan_id": plan.id,
                            "topic_id": topic.id,
                            "task_id": task_id,
                            "run_id": run.id,
                            "script_id": script.id,
                            "expected_task_updated_at": _revision_value(
                                expected_task_updated_at
                            ),
                            "expected_script_updated_at": (
                                _revision_value(expected_script_updated_at)
                                if expected_script_updated_at is not None
                                else None
                            ),
                        },
                    ),
                    connection=conn,
                )
        if replay_ids is not None:
            return (
                self.get_creation_task(workspace_id, task_id),
                self.get_script_generation_run(workspace_id, replay_ids[0]),
                self.get_creation_task_script(workspace_id, replay_ids[1]),
            )
        return (
            self.get_creation_task(workspace_id, task_id),
            self.get_script_generation_run(workspace_id, run_id),
            self.get_creation_task_script(workspace_id, script_id),
        )

    def _require_assistant_reply_plan_event(
        self,
        conn: sqlite3.Connection,
        *,
        workspace_id: str,
        task_id: str,
        plan_id: str,
        request_id: str,
        reply_reference: dict[str, str],
        reply_owner_user_id: str | None,
    ) -> None:
        event_id = _assistant_reply_request_event_id(
            "assistant_reply_plan_confirmed",
            workspace_id,
            request_id,
        )
        row = conn.execute(
            "SELECT payload_json FROM task_events WHERE workspace_id = ? AND task_id = ? AND id = ? AND event_type = 'assistant_reply_plan_confirmed'",
            (workspace_id, task_id, event_id),
        ).fetchone()
        payload = (
            _json_object_from_text(str(row["payload_json"])) if row is not None else {}
        )
        if not (
            payload.get("plan_id") == plan_id
            and payload.get("reply_reference") == reply_reference
            and payload.get("reply_owner_user_id") == reply_owner_user_id
        ):
            raise ValueError("当前方案没有绑定这条已完成对话回复")

    @staticmethod
    def _validate_assistant_reply_inputs(
        plan,
        input_references: dict[str, dict[str, object]],
    ) -> None:
        expected: dict[str, dict[str, str]] = {}
        for item in plan.knowledge_references:
            reference_id = f"knowledge:{item.id}:{item.version}"
            expected[reference_id] = {
                "kind": "knowledge",
                "object_id": item.id,
                "version_or_identity": item.version,
                "title": item.title,
            }
        for item in plan.source_references:
            reference_id = f"source:{item.id}"
            expected[reference_id] = {
                "kind": "source",
                "object_id": item.id,
                "version_or_identity": item.identity,
                "title": item.title,
            }
        for reference_id, item in input_references.items():
            if not reference_id or item != expected.get(reference_id):
                raise ValueError("文案输入来源与当前方案的固定版本不一致")

    def save_reviewed_script_generation(
        self, run, script, *, write_guard=None, result_write=None
    ):
        """Persist an already-reviewed candidate and its original receipt atomically."""
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if write_guard is not None:
                write_guard(conn)
            self.start_script_generation(run, connection=conn)
            return self.complete_script_generation(
                run.id, script, connection=conn, result_write=result_write
            )

    def complete_script_generation(
        self,
        run_id: str,
        script: CreationTaskScript,
        *,
        write_guard=None,
        result_write=None,
        connection=None,
    ) -> tuple[CreationTask, ScriptGenerationRun, CreationTaskScript]:
        if not script.title.strip() or not script.sections:
            raise ValueError("文案必须包含标题和正文")
        if any(
            not section.id.strip()
            or not section.title.strip()
            or not section.content.strip()
            or not section.visual_prompt.strip()
            for section in script.sections
        ):
            raise ValueError("文案段落必须包含画面建议和来源证据")
        with self._connect() if connection is None else nullcontext(connection) as conn:
            if connection is None:
                conn.execute("BEGIN IMMEDIATE")
            if write_guard is not None:
                write_guard(conn)
            row = conn.execute(
                "SELECT payload FROM script_generation_runs WHERE id = ?", (run_id,)
            ).fetchone()
            if row is None:
                raise KeyError(run_id)
            run = self._script_generation_run_from_payload(
                json.loads(str(row["payload"]))
            )
            if run.status != "running":
                raise ValueError("文案运行不是可完成状态")
            if run.run_kind != "generation":
                raise ValueError("回复移交记录不能作为模型生成运行完成")
            task_row = conn.execute(
                "SELECT payload FROM creation_tasks WHERE workspace_id = ? AND id = ?",
                (run.workspace_id, run.task_id),
            ).fetchone()
            if task_row is None:
                raise KeyError(run.task_id)
            task = self._require_creation_task(conn, run.workspace_id, run.task_id)
            if (
                script.workspace_id != run.workspace_id
                or script.task_id != run.task_id
                or script.run_id != run.id
                or script.topic_id != run.topic_id
                or task.stage != "script"
                or task.stage_status != "running"
            ):
                raise ValueError("文案与当前创作任务不一致")
            allowed_references = {
                str(item["reference_id"])
                for item in run.input_references
                if isinstance(item, dict) and item.get("reference_id")
            }
            used_references = {
                reference_id
                for section in script.sections
                for reference_id in section.evidence_reference_ids
            }
            if not used_references.issubset(allowed_references):
                raise ValueError("文案引用了本次模型输入之外的资料")
            now = utc_now()
            run.status = "succeeded"
            run.route_snapshot = {
                **run.route_snapshot,
                "availability": "succeeded",
                "checked_at": now,
            }
            run.finished_at = now
            script.updated_at = now
            conn.execute(
                "UPDATE script_generation_runs SET payload = ?, updated_at = ? WHERE id = ?",
                (_json_payload(run), now.isoformat(), run.id),
            )
            conn.execute(
                """
                INSERT INTO creation_task_scripts
                    (id, workspace_id, task_id, run_id, payload, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    script.id,
                    script.workspace_id,
                    script.task_id,
                    script.run_id,
                    self._script_payload(conn, script),
                    now.isoformat(),
                ),
            )
            self._transition_task(conn, task, "saved", now)
            if result_write is not None:
                result_write(conn, script)
        if connection is not None:
            return task, run, script
        return (
            self.get_creation_task(run.workspace_id, run.task_id),
            self.get_script_generation_run(run.workspace_id, run.id),
            self.get_creation_task_script(run.workspace_id, script.id),
        )

    def fail_script_generation(
        self,
        run_id: str,
        *,
        reason: str,
        impact: str,
        repair_action: str,
        retryable: bool,
        provider_attempted: bool = False,
        provider_failed: bool = True,
        connection=None,
    ) -> tuple[CreationTask, ScriptGenerationRun]:
        if not all(value.strip() for value in (reason, impact, repair_action)):
            raise ValueError("文案失败必须包含原因、影响和修复动作")
        with self._connect() if connection is None else nullcontext(connection) as conn:
            row = conn.execute(
                "SELECT payload FROM script_generation_runs WHERE id = ?", (run_id,)
            ).fetchone()
            if row is None:
                raise KeyError(run_id)
            run = self._script_generation_run_from_payload(
                json.loads(str(row["payload"]))
            )
            if run.status != "running":
                raise ValueError("文案运行不是可失败状态")
            task_row = conn.execute(
                "SELECT payload FROM creation_tasks WHERE workspace_id = ? AND id = ?",
                (run.workspace_id, run.task_id),
            ).fetchone()
            if task_row is None:
                raise KeyError(run.task_id)
            task = self._require_creation_task(conn, run.workspace_id, run.task_id)
            now = utc_now()
            run.status = "failed"
            run.reason = reason
            run.impact = impact
            run.repair_action = repair_action
            run.retryable = retryable
            run.finished_at = now
            if provider_attempted:
                run.route_snapshot = {
                    **run.route_snapshot,
                    "availability": "failed" if provider_failed else "succeeded",
                    "checked_at": now,
                }
            conn.execute(
                "UPDATE script_generation_runs SET payload = ?, updated_at = ? WHERE id = ?",
                (_json_payload(run), now.isoformat(), run.id),
            )
            self._transition_task(
                conn,
                task,
                "failed",
                now,
                reason=reason,
                impact=impact,
                repair_action=repair_action,
                retryable=retryable,
            )
        if connection is not None:
            return task, run
        return (
            self.get_creation_task(run.workspace_id, run.task_id),
            self.get_script_generation_run(run.workspace_id, run.id),
        )

    def get_script_generation_run(
        self, workspace_id: str, run_id: str, *, connection=None
    ) -> ScriptGenerationRun:
        if connection is not None:
            row = connection.execute(
                'SELECT payload FROM script_generation_runs WHERE workspace_id=? AND id=?',
                (workspace_id, run_id),
            ).fetchone()
            if row is None:
                raise KeyError(run_id)
            return self._script_generation_run_from_payload(json.loads(row[0]))
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM script_generation_runs WHERE workspace_id = ? AND id = ?",
                (workspace_id, run_id),
            ).fetchone()
        if row is None:
            raise KeyError(run_id)
        return self._script_generation_run_from_payload(json.loads(str(row["payload"])))

    def latest_script_generation_run(
        self, workspace_id: str, task_id: str
    ) -> ScriptGenerationRun | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT payload FROM script_generation_runs
                WHERE workspace_id = ? AND task_id = ?
                ORDER BY started_at DESC LIMIT 1
                """,
                (workspace_id, task_id),
            ).fetchone()
        return (
            self._script_generation_run_from_payload(json.loads(str(row["payload"])))
            if row is not None
            else None
        )

    def list_creation_task_scripts(
        self, workspace_id: str, task_id: str
    ) -> list[CreationTaskScript]:
        with self._connect() as conn:
            conn.execute("BEGIN")
            rows = conn.execute(
                "SELECT payload FROM creation_task_scripts WHERE workspace_id=? AND task_id=? ORDER BY updated_at DESC",
                (workspace_id, task_id),
            ).fetchall()
            return [
                self._creation_task_script_from_payload(
                    json.loads(str(row["payload"])), connection=conn
                )
                for row in rows
            ]

    def get_creation_task_script(
        self, workspace_id: str, script_id: str
    ) -> CreationTaskScript:
        with self._connect() as conn:
            conn.execute("BEGIN")
            row = conn.execute(
                "SELECT payload FROM creation_task_scripts WHERE workspace_id=? AND id=?",
                (workspace_id, script_id),
            ).fetchone()
            if row is None:
                raise KeyError(script_id)
            return self._creation_task_script_from_payload(
                json.loads(str(row["payload"])), connection=conn
            )

    def read_execution_script_metadata(self, connection, workspace_id, script_id):
        """Decode the original header defaults without reading any body rows."""
        from app.creation.execution_input import ExecutionScriptHeader
        if not connection.in_transaction:
            raise ValueError("执行输入读取必须属于同一快照事务")
        row = connection.execute(
            "SELECT payload FROM creation_task_scripts WHERE workspace_id=? AND id=?",
            (workspace_id, script_id),
        ).fetchone()
        if row is None:
            raise KeyError(script_id)
        fields = json.loads(row["payload"])
        if fields.pop("_sections_storage", None) != "rows" or "sections" in fields:
            raise ValueError("文案正文存储版本无效，请先完成正式行化迁移")
        # Use the canonical dataclass solely to decode defaults and reject unknown
        # metadata. This temporary value never leaves the codec as a full script.
        decoded = CreationTaskScript(**_with_datetime(fields, "created_at", "updated_at", "confirmed_at"), sections=[])
        fields = asdict(decoded)
        del fields["sections"]
        count = connection.execute(
            "SELECT COUNT(*) FROM creation_task_script_sections WHERE workspace_id=? AND script_id=?",
            (workspace_id, script_id),
        ).fetchone()[0]
        header = ExecutionScriptHeader(decoded.id, decoded.task_id, decoded.title,
            decoded.selection_status, decoded.updated_at, count, decoded.workspace_id, decoded.language)
        return header, fields

    def iter_execution_script_sections(self, connection, workspace_id, script_id):
        """Yield canonical sections in the caller's existing snapshot, one at a time."""
        if not connection.in_transaction:
            raise ValueError("执行正文遍历必须属于同一快照事务")
        cursor = connection.execute(
            "SELECT section_json FROM creation_task_script_sections WHERE workspace_id=? AND script_id=? ORDER BY ordinal",
            (workspace_id, script_id),
        )
        try:
            for row in cursor:
                yield CreationTaskScriptSection(**json.loads(row["section_json"]))
        finally:
            cursor.close()

    def active_creation_task_script_headers(self, workspace_id, task_id):
        with self._connect() as conn:
            conn.execute("BEGIN")
            task = self._require_creation_task(conn, workspace_id, task_id)
            rows = conn.execute(
                """SELECT s.payload,s.updated_at,
                (SELECT COUNT(*) FROM creation_task_script_sections p
                 WHERE p.workspace_id=s.workspace_id AND p.script_id=s.id) AS total_count
                FROM creation_task_scripts s WHERE s.workspace_id=? AND s.task_id=?
                AND (s.id=? OR s.id=(SELECT id FROM creation_task_scripts
                    WHERE workspace_id=? AND task_id=? ORDER BY updated_at DESC,id DESC LIMIT 1))
                ORDER BY s.updated_at DESC,s.id DESC""",
                (
                    workspace_id,
                    task_id,
                    task.confirmed_script_id,
                    workspace_id,
                    task_id,
                ),
            ).fetchall()
            result = []
            for row in rows:
                metadata = json.loads(row["payload"])
                if (
                    metadata.pop("_sections_storage", None) != "rows"
                    or "sections" in metadata
                ):
                    raise ValueError("文案正文存储版本无效，请先完成正式行化迁移")
                total = row["total_count"]
                metadata.update(
                    sections=None, sections_loaded=False, sections_total_count=total
                )
                result.append(metadata)
            return result

    def read_creation_task_script_sections(
        self,
        workspace_id,
        task_id,
        script_id,
        *,
        expected_updated_at=None,
        after_ordinal=0,
        limit=SCRIPT_SECTION_WINDOW_LIMIT,
    ):
        with self._connect() as conn:
            conn.execute("BEGIN")
            self._require_creation_task(conn, workspace_id, task_id)
            return read_script_sections(
                conn,
                workspace_id,
                task_id,
                script_id,
                expected_updated_at=expected_updated_at,
                after_ordinal=after_ordinal,
                limit=limit,
            )

    def confirmed_creation_task_script_digest(
        self,
        workspace_id: str,
        task_id: str,
        script_id: str | None = None,
    ) -> str | None:
        task = self.get_creation_task(workspace_id, task_id)
        current_script_id = script_id or task.confirmed_script_id
        if not current_script_id or task.confirmed_script_id != current_script_id:
            return None
        try:
            script = self.get_creation_task_script(workspace_id, current_script_id)
        except KeyError:
            return None
        if (
            script.task_id != task_id
            or script.selection_status != "confirmed"
            or not script.sections
        ):
            return None
        manifest = {
            "script_id": script.id,
            "sections": [
                {"id": section.id, "content": section.content}
                for section in script.sections
            ],
        }
        return hashlib.sha256(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()

    def record_assistant_script_save(
        self, script, source, request_digest, *, connection
    ):
        identity = hashlib.sha256(
            json.dumps(
                [script.id, script.updated_at.isoformat(), source], sort_keys=True
            ).encode()
        ).hexdigest()
        event_id = "event_assistant_script_saved_" + identity
        try:
            return self.get_task_event(
                script.workspace_id, event_id, connection=connection
            )
        except KeyError:
            return self.append_task_event(
                TaskEvent(
                    event_id,
                    script.workspace_id,
                    script.task_id,
                    None,
                    "assistant_script_saved",
                    {
                        "script_id": script.id,
                        "updated_at": script.updated_at.isoformat(),
                        "request_digest": request_digest,
                        "source": source,
                    },
                ),
                connection=connection,
            )

    def _mutable_assistant_script_metadata(
        self,
        conn,
        workspace_id,
        task_id,
        script_id,
        sections,
        expected_updated_at,
        *,
        generation_run_id=None,
    ):
        task = self._require_creation_task(conn, workspace_id, task_id)
        row = conn.execute(
            "SELECT payload FROM creation_task_scripts WHERE workspace_id=? AND task_id=? AND id=?",
            (workspace_id, task_id, script_id),
        ).fetchone()
        if row is None:
            raise KeyError(script_id)
        metadata = json.loads(str(row["payload"]))
        if metadata.get("_sections_storage") != "rows" or "sections" in metadata:
            raise ValueError("文案尚未完成行化迁移")
        if expected_updated_at is None:
            raise ValueError("更新文案草稿必须提供当前 updated_at")
        if _revision_value(metadata["updated_at"]) != _revision_value(
            expected_updated_at
        ):
            raise ScriptWindowConflict("文案草稿修订已变化，请重新读取后再保存")
        if not self._script_knowledge_inputs_current(conn, task, script_id):
            raise ValueError(
                "任务已追加资料，旧稿仅保留为历史；请基于当前资料另存新稿。"
            )
        if (
            task.stage != "script"
            or task.stage_status
            != ("running" if generation_run_id else "waiting_for_user")
            or task.confirmed_script_id is not None
            or metadata["selection_status"] != "draft"
            or (
                not metadata.get("reused_from_entry_id")
                and metadata["topic_id"] != task.selected_topic_id
            )
        ):
            raise ValueError("当前创作任务不允许修改这份待确认文案")
        if (
            metadata.get("source_reply_reference") is None
            and not metadata.get("reused_from_entry_id")
            and any(not section.visual_prompt.strip() for section in sections)
        ):
            raise ValueError("文案草稿必须包含标题、正文、画面建议和来源证据")
        if metadata.get("reused_from_entry_id"):
            if (
                task.scenario != "script_library_reuse"
                or metadata["run_id"]
                or metadata["topic_id"]
            ):
                raise ValueError("库稿来源身份与原任务不符")
            row = conn.execute(
                "SELECT payload FROM creation_plans WHERE workspace_id=? AND id=?",
                (workspace_id, task.plan_id),
            ).fetchone()
            plan = self._creation_plan_from_payload(json.loads(row["payload"]))
            allowed = {
                f"knowledge:{r.id}:{r.version}" for r in plan.knowledge_references
            } | {f"source:{r.id}" for r in plan.source_references}
            if any(
                not set(part.evidence_reference_ids).issubset(allowed)
                for part in sections
            ):
                raise ValueError("库稿引用了当前任务以外的来源")
            return metadata
        if generation_run_id:
            run_row = conn.execute(
                "SELECT payload FROM script_generation_runs WHERE workspace_id=? AND task_id=? AND id=? AND json_extract(payload,'$.status')='running'",
                (workspace_id, task_id, generation_run_id),
            ).fetchone()
        else:
            run_row = conn.execute(
                "SELECT payload FROM script_generation_runs WHERE workspace_id=? AND task_id=? "
                "AND json_extract(payload, '$.status')='succeeded' "
                "ORDER BY started_at DESC, id DESC LIMIT 1",
                (workspace_id, task_id),
            ).fetchone()
        if run_row is None:
            raise ValueError("这份文案没有已完成的生成记录")
        run = self._script_generation_run_from_payload(
            json.loads(str(run_row["payload"]))
        )
        if (
            metadata["run_id"] != run.id
            or run.topic_id != task.selected_topic_id
            or run.plan_id != task.plan_id
        ):
            raise ValueError("这份文案不是当前任务的最新草稿")
        allowed_references = {
            str(item["reference_id"])
            for item in run.input_references
            if isinstance(item, dict) and item.get("reference_id")
        }
        used_references = {
            reference_id
            for section in sections
            for reference_id in section.evidence_reference_ids
        }
        if not used_references.issubset(allowed_references):
            raise ValueError("文案引用了本次模型输入之外的资料")
        return metadata

    def update_assistant_script_draft(
        self,
        workspace_id: str,
        task_id: str,
        script_id: str,
        *,
        title: str,
        sections: list[CreationTaskScriptSection],
        expected_updated_at: datetime | str | None = None,
        write_guard=None,
        result_write=None,
    ) -> CreationTaskScript:
        """Revise only the current unconfirmed assistant draft in its existing run."""
        if (
            not title.strip()
            or not sections
            or any(
                not section.id.strip()
                or not section.title.strip()
                or not section.content.strip()
                for section in sections
            )
        ):
            raise ValueError("文案草稿必须包含标题和正文")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if write_guard is not None:
                write_guard(conn)
            self._mutable_assistant_script_metadata(
                conn, workspace_id, task_id, script_id, sections, expected_updated_at
            )
            row = conn.execute(
                "SELECT payload FROM creation_task_scripts WHERE workspace_id=? AND task_id=? AND id=?",
                (workspace_id, task_id, script_id),
            ).fetchone()
            script = self._creation_task_script_from_payload(
                json.loads(row["payload"]), connection=conn
            )
            script.title = title.strip()
            script.sections = sections
            script.draft_complete = True
            script.updated_at = utc_now()
            conn.execute(
                "UPDATE creation_task_scripts SET payload=?, updated_at=? WHERE workspace_id=? AND task_id=? AND id=?",
                (
                    self._script_payload(conn, script),
                    script.updated_at.isoformat(),
                    workspace_id,
                    task_id,
                    script_id,
                ),
            )
            if result_write is not None:
                result_write(conn, script)
        return script

    @staticmethod
    def _window_claim_key(task_id, script_id, operation, request_id, member_id):
        return (
            "script_claim_"
            + hashlib.sha256(
                json.dumps(
                    [task_id, script_id, operation, request_id, member_id]
                ).encode()
            ).hexdigest()
        )

    def claim_script_window_request(
        self,
        workspace_id,
        task_id,
        script_id,
        operation,
        request_id,
        member_id,
        input_digest,
        *,
        submitted_input,
        generation_request_id=None,
        write_guard=None,
    ):
        key = self._window_claim_key(
            task_id, script_id, operation, request_id, member_id
        )
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if write_guard:
                write_guard(conn)
            self._require_creation_task(conn, workspace_id, task_id)
            previous = conn.execute(
                "SELECT payload_json FROM task_events WHERE workspace_id=? AND task_id=? AND id=?",
                (workspace_id, task_id, key),
            ).fetchone()
            if previous:
                if json.loads(previous["payload_json"])["input_digest"] != input_digest:
                    raise ScriptWindowConflict("同一文案请求不能改变输入，请读取原请求")
                return False
            self.append_task_event(
                TaskEvent(
                    key,
                    workspace_id,
                    task_id,
                    None,
                    "script_window_request_claimed",
                    {
                        "generation_request_id": str(generation_request_id)
                        if generation_request_id is not None
                        else None,
                        "operation": operation,
                        "script_id": script_id,
                        "request_id": request_id,
                        "member_id": member_id,
                        "input_digest": input_digest,
                        "submitted_input": submitted_input,
                    },
                ),
                connection=conn,
            )
            return True

    def end_script_window_request_with_error(
        self,
        workspace_id,
        task_id,
        script_id,
        operation,
        request_id,
        member_id,
        *,
        error_detail=None,
    ):
        key = (
            self._window_claim_key(task_id, script_id, operation, request_id, member_id)
            + "_error"
        )
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if (
                conn.execute(
                    "SELECT 1 FROM task_events WHERE workspace_id=? AND task_id=? AND id=?",
                    (workspace_id, task_id, key),
                ).fetchone()
                is None
            ):
                self.append_task_event(
                    TaskEvent(
                        key,
                        workspace_id,
                        task_id,
                        None,
                        "script_window_request_error",
                        {
                            "operation": operation,
                            "request_id": request_id,
                            "member_id": member_id,
                            "status": "failed",
                            "error_detail": error_detail,
                        },
                    ),
                    connection=conn,
                )

    def pending_script_window_request(
        self,
        workspace_id,
        task_id,
        script_id,
        operation,
        request_id,
        member_id,
        *,
        include_input=False,
    ):
        key = self._window_claim_key(
            task_id, script_id, operation, request_id, member_id
        )
        with self._connect() as conn:
            self._require_creation_task(conn, workspace_id, task_id)
            row = conn.execute(
                "SELECT payload_json FROM task_events WHERE workspace_id=? AND task_id=? AND id=?",
                (workspace_id, task_id, key),
            ).fetchone()
            if row is None:
                return None
            error = conn.execute(
                "SELECT payload_json FROM task_events WHERE workspace_id=? AND task_id=? AND id=?",
                (workspace_id, task_id, key + "_error"),
            ).fetchone()
            result = {
                "task_id": task_id,
                "request_id": request_id,
                "operation": operation,
                "status": "failed" if error else "unknown",
                "submission_effect": "unknown",
                "message": "原请求尚无完整成功回执；请核对原请求，不自动重提模型核查。",
            }
            result["generation_request_id"] = json.loads(row["payload_json"]).get(
                "generation_request_id"
            )
            result["submitted_input_available"] = True
            if include_input:
                result["submitted_input"] = json.loads(row["payload_json"])[
                    "submitted_input"
                ]
            if error:
                result["error_detail"] = json.loads(error["payload_json"])[
                    "error_detail"
                ]
            if script_id is not None:
                result["script_id"] = script_id
            return result

    def script_window_start_result(self, workspace_id, task_id, request_id, member_id):
        key = (
            "script_start_"
            + hashlib.sha256(
                json.dumps([task_id, request_id, member_id]).encode()
            ).hexdigest()
        )
        with self._connect() as conn:
            self._require_creation_task(conn, workspace_id, task_id)
            row = conn.execute(
                "SELECT payload_json FROM task_events WHERE workspace_id=? AND task_id=? AND id=?",
                (workspace_id, task_id, key),
            ).fetchone()
            return json.loads(row["payload_json"]) if row else None

    def record_script_window_start(
        self,
        script,
        request_id,
        member_id,
        request_digest,
        binding_digest,
        *,
        connection,
        generation_request_id=None,
    ):
        key = (
            "script_start_"
            + hashlib.sha256(
                json.dumps([script.task_id, request_id, member_id]).encode()
            ).hexdigest()
        )
        result = {
            "task_id": script.task_id,
            "script_id": script.id,
            "request_id": request_id,
            "status": "saved",
            "updated_at": script.updated_at.isoformat(),
            "total_count": len(script.sections),
            "affected_section_ids": [part.id for part in script.sections],
        }
        self._record_script_section_review(
            connection,
            script.workspace_id,
            script.task_id,
            script.id,
            script.sections,
            binding_digest=binding_digest,
            layout_digest=script_layout_digest(
                connection, script.workspace_id, script.id
            ),
        )
        self.append_task_event(
            TaskEvent(
                key,
                script.workspace_id,
                script.task_id,
                None,
                "script_window_started",
                {
                    "request_id": request_id,
                    "generation_request_id": generation_request_id,
                    "request_digest": request_digest,
                    "member_id": member_id,
                    "result": result,
                },
            ),
            connection=connection,
        )
        return result

    def saved_script_review_context(
        self, workspace_id, task_id, script_id, expected_updated_at
    ):
        with self._connect() as conn:
            conn.execute("BEGIN")
            self._require_creation_task(conn, workspace_id, task_id)
            row = conn.execute(
                "SELECT payload,updated_at FROM creation_task_scripts WHERE workspace_id=? AND task_id=? AND id=?",
                (workspace_id, task_id, script_id),
            ).fetchone()
            if row is None:
                raise KeyError(script_id)
            if _revision_value(row["updated_at"]) != _revision_value(
                expected_updated_at
            ):
                raise ScriptWindowConflict("文案版本已变化，请重新读取后确认")
            total = conn.execute(
                "SELECT COUNT(*) FROM creation_task_script_sections WHERE workspace_id=? AND script_id=?",
                (workspace_id, script_id),
            ).fetchone()[0]
            return {
                "metadata": json.loads(row["payload"]),
                "total_count": total,
                "layout_digest": script_layout_digest(conn, workspace_id, script_id),
            }

    def record_saved_script_section_review(
        self,
        workspace_id,
        task_id,
        script_id,
        sections,
        expected_updated_at,
        *,
        binding_digest,
        layout_digest,
        write_guard=None,
    ):
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if write_guard:
                write_guard(conn)
            self._require_creation_task(conn, workspace_id, task_id)
            row = conn.execute(
                "SELECT updated_at FROM creation_task_scripts WHERE workspace_id=? AND task_id=? AND id=?",
                (workspace_id, task_id, script_id),
            ).fetchone()
            if row is None:
                raise KeyError(script_id)
            if (
                _revision_value(row["updated_at"])
                != _revision_value(expected_updated_at)
                or script_layout_digest(conn, workspace_id, script_id) != layout_digest
            ):
                raise ScriptWindowConflict("核查期间文案已变化，没有记录过期覆盖")
            for part in sections:
                stored = conn.execute(
                    "SELECT digest FROM creation_task_script_sections WHERE workspace_id=? AND script_id=? AND section_id=?",
                    (workspace_id, script_id, part.id),
                ).fetchone()
                if stored is None or stored["digest"] != section_digest(asdict(part)):
                    raise ScriptWindowConflict(
                        "核查正文与已保存段落不一致，没有记录覆盖"
                    )
            self._record_script_section_review(
                conn,
                workspace_id,
                task_id,
                script_id,
                sections,
                binding_digest=binding_digest,
                layout_digest=layout_digest,
            )

    def generation_window_requests_pending(
        self,
        workspace_id,
        task_id,
        generation_request_id,
        member_id,
        *,
        connection=None,
    ):
        with (
            nullcontext(connection)
            if connection is not None
            else self._connect() as conn
        ):
            row = conn.execute(
                """SELECT 1 FROM task_events c
                WHERE c.workspace_id=? AND c.task_id=? AND c.event_type='script_window_request_claimed'
                AND json_extract(c.payload_json,'$.generation_request_id')=?
                AND json_extract(c.payload_json,'$.member_id')=?
                AND json_extract(c.payload_json,'$.operation') IN ('start','patch')
                AND NOT EXISTS (SELECT 1 FROM task_events r WHERE r.workspace_id=c.workspace_id AND r.task_id=c.task_id
                    AND r.event_type IN ('script_window_started','assistant_script_window_saved')
                    AND json_extract(r.payload_json,'$.generation_request_id')=json_extract(c.payload_json,'$.generation_request_id')
                    AND json_extract(r.payload_json,'$.member_id')=json_extract(c.payload_json,'$.member_id')
                    AND json_extract(r.payload_json,'$.request_id')=json_extract(c.payload_json,'$.request_id'))
                AND NOT EXISTS (SELECT 1 FROM task_events e WHERE e.workspace_id=c.workspace_id AND e.task_id=c.task_id AND e.id=c.id||'_error')
                LIMIT 1""",
                (workspace_id, task_id, str(generation_request_id), member_id),
            ).fetchone()
            return row is not None

    def generation_draft_result(
        self, workspace_id, task_id, script_id, request_id, member_id
    ):
        key = (
            "generation_draft_"
            + hashlib.sha256(
                json.dumps([script_id, request_id, member_id]).encode()
            ).hexdigest()
        )
        with self._connect() as conn:
            self._require_creation_task(conn, workspace_id, task_id)
            row = conn.execute(
                "SELECT payload_json FROM task_events WHERE workspace_id=? AND task_id=? AND id=?",
                (workspace_id, task_id, key),
            ).fetchone()
            return json.loads(row["payload_json"]) if row else None

    def finish_generation_draft(
        self,
        workspace_id,
        task_id,
        script_id,
        *,
        run_id,
        generation_request_id,
        expected_updated_at,
        request_id,
        member_id,
        binding_digest,
        layout_digest,
        write_guard,
        result_write,
    ):
        request_digest = hashlib.sha256(
            json.dumps(
                [str(generation_request_id), _revision_value(expected_updated_at)]
            ).encode()
        ).hexdigest()
        key = (
            "generation_draft_"
            + hashlib.sha256(
                json.dumps([script_id, request_id, member_id]).encode()
            ).hexdigest()
        )
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            write_guard(conn)
            if self.generation_window_requests_pending(
                workspace_id, task_id, generation_request_id, member_id, connection=conn
            ):
                raise ScriptWindowConflict(
                    "原生成请求仍有未知窗口写入，不能跨越它完成草稿"
                )
            state = self.generation_run_state(conn, workspace_id, task_id, run_id)
            task = self._require_creation_task(conn, workspace_id, task_id)
            metadata = state["script"]
            run = state["run"]
            if (
                metadata is None
                or metadata["id"] != script_id
                or run.status != "running"
                or metadata["selection_status"] != "draft"
                or task.confirmed_script_id
                or task.stage != "script"
                or task.stage_status != "running"
            ):
                raise ScriptWindowConflict("原生成请求不是可完成的未采用草稿")
            if _revision_value(metadata["updated_at"]) != _revision_value(
                expected_updated_at
            ):
                raise ScriptWindowConflict("全稿版本已变化")
            if not self._script_knowledge_inputs_current(conn, task, script_id):
                raise ScriptWindowConflict("原生成输入已变化")
            row = conn.execute(
                "SELECT payload FROM creation_plans WHERE workspace_id=? AND id=?",
                (workspace_id, task.plan_id),
            ).fetchone()
            plan = self._creation_plan_from_payload(json.loads(row["payload"]))
            body_digest, count = self._validated_saved_review(
                conn,
                workspace_id,
                task_id,
                script_id,
                metadata,
                plan,
                binding_digest,
                layout_digest,
            )
            now = utc_now()
            metadata.pop("sections")
            metadata.pop("sections_loaded")
            metadata.pop("sections_total_count")
            metadata.update(
                _sections_storage="rows",
                draft_complete=True,
                updated_at=now.isoformat(),
            )
            conn.execute(
                "UPDATE creation_task_scripts SET payload=?,updated_at=? WHERE workspace_id=? AND task_id=? AND id=?",
                (
                    _json_object(metadata),
                    now.isoformat(),
                    workspace_id,
                    task_id,
                    script_id,
                ),
            )
            run.status = "succeeded"
            run.finished_at = now
            conn.execute(
                "UPDATE script_generation_runs SET payload=?,updated_at=? WHERE workspace_id=? AND task_id=? AND id=?",
                (_json_payload(run), now.isoformat(), workspace_id, task_id, run_id),
            )
            self._transition_task(conn, task, "saved", now)
            result_write(conn, run_id=run_id, script_id=script_id, draft_ready=True)
            result = {
                "generation_request_id": str(generation_request_id),
                "request_id": request_id,
                "task_id": task_id,
                "run_id": run_id,
                "script_id": script_id,
                "updated_at": now.isoformat(),
                "status": "draft_ready",
                "body_digest": body_digest,
            }
            self.append_task_event(
                TaskEvent(
                    key,
                    workspace_id,
                    task_id,
                    None,
                    "script_generation_draft_ready",
                    {
                        "generation_request_id": str(generation_request_id),
                        "request_digest": request_digest,
                        "result": result,
                    },
                ),
                connection=conn,
            )
            return result

    def script_confirm_saved_result(
        self, workspace_id, task_id, script_id, request_id, member_id
    ):
        key = (
            "script_confirm_"
            + hashlib.sha256(
                json.dumps([script_id, request_id, member_id]).encode()
            ).hexdigest()
        )
        with self._connect() as conn:
            self._require_creation_task(conn, workspace_id, task_id)
            row = conn.execute(
                "SELECT payload_json FROM task_events WHERE workspace_id=? AND task_id=? AND id=?",
                (workspace_id, task_id, key),
            ).fetchone()
            return json.loads(row["payload_json"]) if row else None

    def confirmed_script_adoption_facts(
        self, workspace_id, task_id, script_id, expected_updated_at
    ):
        from app.creation.script_windows import ScriptAdoptionFacts

        with self._connect() as conn:
            conn.execute("BEGIN")
            task = self._require_creation_task(conn, workspace_id, task_id)
            row = conn.execute(
                "SELECT payload FROM creation_task_scripts WHERE workspace_id=? AND task_id=? AND id=?",
                (workspace_id, task_id, script_id),
            ).fetchone()
            if row is None:
                raise KeyError(script_id)
            metadata = json.loads(row["payload"])
            if (
                task.confirmed_script_id != script_id
                or metadata["selection_status"] != "confirmed"
                or script_revision(metadata["updated_at"])
                != script_revision(expected_updated_at)
            ):
                raise ScriptWindowConflict("采用结果与当前确认版不一致，未记录错误来源")
            digest = hashlib.sha256()
            for item in conn.execute(
                "SELECT json_extract(section_json,'$.content') FROM creation_task_script_sections WHERE workspace_id=? AND script_id=? ORDER BY ordinal",
                (workspace_id, script_id),
            ):
                digest.update(item[0].encode())
            if metadata["adoption"]["body_digest"] != digest.hexdigest():
                raise ScriptWindowConflict("确认正文与采用摘要不一致")
            return ScriptAdoptionFacts(
                script_id,
                task_id,
                metadata["selection_status"],
                script_revision(metadata["updated_at"]),
                metadata["adoption"],
                digest.hexdigest(),
            )

    def _validated_saved_review(
        self,
        conn,
        workspace_id,
        task_id,
        script_id,
        metadata,
        plan,
        binding_digest,
        layout_digest,
    ):
        count = conn.execute(
            "SELECT COUNT(*) FROM creation_task_script_sections WHERE workspace_id=? AND script_id=?",
            (workspace_id, script_id),
        ).fetchone()[0]
        required = plan.template_settings.get("script_section_count")
        if count == 0 or (required is not None and count != required):
            raise ValueError("真实全稿段数与采用要求不符，不能采用部分正文")
        if script_layout_digest(conn, workspace_id, script_id) != layout_digest:
            raise ScriptWindowConflict("稿件顺序已变化，核查覆盖已失效")
        uncovered = conn.execute(
            """SELECT 1 FROM creation_task_script_sections s
            WHERE s.workspace_id=? AND s.script_id=? AND NOT EXISTS (
                SELECT 1 FROM task_events e,json_each(e.payload_json,'$.sections') p
                WHERE e.workspace_id=s.workspace_id AND e.task_id=? AND e.event_type='script_sections_reviewed'
                AND json_extract(e.payload_json,'$.script_id')=s.script_id
                AND json_extract(e.payload_json,'$.binding_digest')=? AND json_extract(e.payload_json,'$.layout_digest')=?
                AND json_extract(p.value,'$.id')=s.section_id AND json_extract(p.value,'$.digest')=s.digest) LIMIT 1""",
            (workspace_id, script_id, task_id, binding_digest, layout_digest),
        ).fetchone()
        if uncovered:
            raise ScriptWindowConflict(
                "仍有实际正文未完成当前来源和要求下的核查，不能采用"
            )
        digest = hashlib.sha256()
        for row in conn.execute(
            "SELECT json_extract(section_json,'$.content') AS content,json_extract(section_json,'$.visual_prompt') AS visual FROM creation_task_script_sections WHERE workspace_id=? AND script_id=? ORDER BY ordinal",
            (workspace_id, script_id),
        ):
            if not row["content"].strip() or (
                not metadata.get("reused_from_entry_id") and not row["visual"].strip()
            ):
                raise ValueError("完整正文或画面建议缺失")
            digest.update(row["content"].encode())
        return digest.hexdigest(), count

    def confirm_reviewed_script(
        self,
        workspace_id,
        task_id,
        script_id,
        *,
        expected_updated_at,
        request_id,
        member_id,
        binding_digest,
        layout_digest,
        adoption_kind="manual",
        write_guard=None,
    ):
        request_digest = script_confirmation_digest(expected_updated_at, adoption_kind)
        key = (
            "script_confirm_"
            + hashlib.sha256(
                json.dumps([script_id, request_id, member_id]).encode()
            ).hexdigest()
        )
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if write_guard:
                write_guard(conn)
            prior = conn.execute(
                "SELECT payload_json FROM task_events WHERE workspace_id=? AND task_id=? AND id=?",
                (workspace_id, task_id, key),
            ).fetchone()
            if prior:
                saved = json.loads(prior["payload_json"])
                if saved["request_digest"] != request_digest:
                    raise ScriptWindowConflict("同一确认请求不能更换输入")
                return saved["result"]
            task, metadata, plan, adoption = self._script_adoption_context(
                conn,
                workspace_id,
                task_id,
                script_id,
                expected_updated_at,
                adoption_kind,
                member_id,
            )
            if metadata.get("locked_body_digest"):
                raise ValueError("此正文为锁定定稿，请沿原采用结果继续，不重新采用")
            if (
                task.stage != "script"
                or task.stage_status != "waiting_for_user"
                or task.confirmed_script_id is not None
                or metadata["selection_status"] != "draft"
            ):
                raise ScriptWindowConflict("当前稿件不处于待确认状态")
            body_digest, count = self._validated_saved_review(
                conn,
                workspace_id,
                task_id,
                script_id,
                metadata,
                plan,
                binding_digest,
                layout_digest,
            )
            now = utc_now()
            metadata.update(
                selection_status="confirmed",
                confirmed_at=now.isoformat(),
                updated_at=now.isoformat(),
                draft_complete=True,
                adoption={
                    **adoption,
                    "script_id": script_id,
                    "version": now.isoformat(),
                    "body_digest": body_digest,
                },
            )
            self._persist_script_adoption(conn, task, script_id, metadata, now)
            result = {
                "task_id": task_id,
                "script_id": script_id,
                "request_id": request_id,
                "updated_at": now.isoformat(),
                "status": "confirmed",
                "total_count": count,
            }
            self.append_task_event(
                TaskEvent(
                    key,
                    workspace_id,
                    task_id,
                    None,
                    "script_saved_confirmed",
                    {
                        "request_digest": request_digest,
                        "member_id": member_id,
                        "result": result,
                    },
                ),
                connection=conn,
            )
            return result

    def script_patch_request_result(
        self, workspace_id, task_id, script_id, request_id, member_id
    ):
        with self._connect() as conn:
            self._require_creation_task(conn, workspace_id, task_id)
            row = conn.execute(
                "SELECT payload_json FROM task_events WHERE workspace_id=? AND task_id=? AND id=?",
                (
                    workspace_id,
                    task_id,
                    script_patch_event_id(script_id, request_id, member_id),
                ),
            ).fetchone()
            return json.loads(row["payload_json"]) if row else None

    def script_section_patch_context(
        self,
        workspace_id,
        task_id,
        script_id,
        sections,
        expected_updated_at,
        *,
        insert_after_section_id=None,
        generation_run_id=None,
    ):
        if not 1 <= len(sections) <= SCRIPT_SECTION_WINDOW_LIMIT or len(
            {part.id for part in sections}
        ) != len(sections):
            raise ValueError("文案修改窗口大小或段落ID无效")
        with self._connect() as conn:
            conn.execute("BEGIN")
            metadata = self._mutable_assistant_script_metadata(
                conn,
                workspace_id,
                task_id,
                script_id,
                sections,
                expected_updated_at,
                generation_run_id=generation_run_id,
            )
            ids = [part.id for part in sections]
            slots = ",".join("?" for _ in ids)
            existing = {
                row["section_id"]
                for row in conn.execute(
                    f"SELECT section_id FROM creation_task_script_sections WHERE workspace_id=? AND script_id=? AND section_id IN ({slots})",
                    (workspace_id, script_id, *ids),
                )
            }
            new_ids = [part.id for part in sections if part.id not in existing]
            total = conn.execute(
                "SELECT COUNT(*) FROM creation_task_script_sections WHERE workspace_id=? AND script_id=?",
                (workspace_id, script_id),
            ).fetchone()[0]
            return {
                "metadata": metadata,
                "total_count": total + len(new_ids),
                "layout_digest": script_layout_digest(
                    conn,
                    workspace_id,
                    script_id,
                    insert_ids=new_ids,
                    insert_after_section_id=insert_after_section_id,
                ),
            }

    def _record_script_section_review(
        self,
        conn,
        workspace_id,
        task_id,
        script_id,
        sections,
        *,
        binding_digest,
        layout_digest,
    ):
        from app.creation.script_windows import SCRIPT_REVIEW_PROTOCOL_VERSION

        facts = {
            "script_id": script_id,
            "binding_digest": binding_digest,
            "layout_digest": layout_digest,
            "review_protocol": SCRIPT_REVIEW_PROTOCOL_VERSION,
            "sections": [
                {"id": part.id, "digest": section_digest(asdict(part))}
                for part in sections
            ],
        }
        event_id = (
            "script_review_"
            + hashlib.sha256(json.dumps(facts, sort_keys=True).encode()).hexdigest()
        )
        if (
            conn.execute(
                "SELECT 1 FROM task_events WHERE workspace_id=? AND task_id=? AND id=?",
                (workspace_id, task_id, event_id),
            ).fetchone()
            is None
        ):
            self.append_task_event(
                TaskEvent(
                    event_id,
                    workspace_id,
                    task_id,
                    None,
                    "script_sections_reviewed",
                    facts,
                ),
                connection=conn,
            )

    def script_sections_have_review(
        self,
        workspace_id,
        task_id,
        script_id,
        sections,
        *,
        binding_digest,
        layout_digest,
    ):
        with self._connect() as conn:
            self._require_creation_task(conn, workspace_id, task_id)
            for part in sections:
                row = conn.execute(
                    """SELECT 1 FROM task_events e,json_each(e.payload_json,'$.sections') p
                    WHERE e.workspace_id=? AND e.task_id=? AND e.event_type='script_sections_reviewed'
                    AND json_extract(e.payload_json,'$.script_id')=? AND json_extract(e.payload_json,'$.binding_digest')=?
                    AND json_extract(e.payload_json,'$.layout_digest')=? AND json_extract(p.value,'$.id')=?
                    AND json_extract(p.value,'$.digest')=? LIMIT 1""",
                    (
                        workspace_id,
                        task_id,
                        script_id,
                        binding_digest,
                        layout_digest,
                        part.id,
                        section_digest(asdict(part)),
                    ),
                ).fetchone()
                if row is None:
                    return False
            return True

    def patch_assistant_script_sections(
        self,
        workspace_id,
        task_id,
        script_id,
        *,
        sections,
        expected_updated_at,
        request_id,
        member_id,
        insert_after_section_id=None,
        write_guard=None,
        review_binding=None,
        generation_run_id=None,
        generation_request_id=None,
    ):
        if not 1 <= len(sections) <= SCRIPT_SECTION_WINDOW_LIMIT:
            raise ValueError("单次文案修改超出窗口，请分次保存同一稿件")
        if (
            not isinstance(member_id, str)
            or not member_id
            or not isinstance(request_id, str)
            or not request_id
        ):
            raise ValueError("文案窗口保存必须绑定当前身份与原请求")
        if expected_updated_at is None:
            raise ValueError("文案窗口保存必须提供当前稿件版本")
        if any(
            not part.id.strip() or not part.title.strip() or not part.content.strip()
            for part in sections
        ):
            raise ValueError("文案窗口必须包含稳定段落ID、标题和正文")
        inputs = {
            "sections": [asdict(part) for part in sections],
            "expected_updated_at": _revision_value(expected_updated_at),
            "insert_after_section_id": insert_after_section_id,
        }
        request_digest = script_patch_digest(
            inputs["sections"], expected_updated_at, insert_after_section_id
        )
        event_id = script_patch_event_id(script_id, request_id, member_id)
        if generation_run_id and write_guard is None:
            raise ValueError("生成窗口修改必须有原请求事务复验")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if write_guard is not None:
                write_guard(conn)
            self._require_creation_task(conn, workspace_id, task_id)
            receipt = conn.execute(
                "SELECT payload_json FROM task_events WHERE workspace_id=? AND task_id=? AND id=?",
                (workspace_id, task_id, event_id),
            ).fetchone()
            if receipt is not None:
                recorded = json.loads(receipt["payload_json"])
                if recorded["request_digest"] != request_digest:
                    raise ValueError("同一文案请求不能更换输入；请查询原请求结果")
                return recorded["result"]
            metadata = self._mutable_assistant_script_metadata(
                conn,
                workspace_id,
                task_id,
                script_id,
                sections,
                expected_updated_at,
                generation_run_id=generation_run_id,
            )
            result = upsert_script_sections(
                conn,
                workspace_id,
                script_id,
                inputs["sections"],
                insert_after_section_id=insert_after_section_id,
            )
            now = utc_now()
            metadata["updated_at"] = now.isoformat()
            conn.execute(
                "UPDATE creation_task_scripts SET payload=?,updated_at=? WHERE workspace_id=? AND task_id=? AND id=?",
                (
                    json.dumps(metadata, ensure_ascii=False),
                    now.isoformat(),
                    workspace_id,
                    task_id,
                    script_id,
                ),
            )
            if review_binding is not None:
                self._record_script_section_review(
                    conn,
                    workspace_id,
                    task_id,
                    script_id,
                    sections,
                    binding_digest=review_binding,
                    layout_digest=script_layout_digest(conn, workspace_id, script_id),
                )
            result.update(
                task_id=task_id,
                script_id=script_id,
                request_id=request_id,
                updated_at=now.isoformat(),
                status="saved",
            )
            self.append_task_event(
                TaskEvent(
                    event_id,
                    workspace_id,
                    task_id,
                    None,
                    "assistant_script_window_saved",
                    {
                        "member_id": member_id,
                        "request_id": request_id,
                        "generation_request_id": generation_request_id,
                        "request_digest": request_digest,
                        "result": result,
                    },
                ),
                connection=conn,
            )
            return result

    def save_script_library_entry_header(
        self,
        workspace_id,
        *,
        source_script_id,
        name,
        category,
        request_id=None,
        member_id=None,
        request_digest=None,
        expected_source_updated_at=None,
        write_guard=None,
    ):
        from app.creation.script_windows import write_library_sections, library_header

        if not name.strip():
            raise ValueError("请为保存的文案命名")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if request_id is not None:
                if (
                    not member_id
                    or request_digest is None
                    or expected_source_updated_at is None
                ):
                    raise ValueError("保存请求必须绑定真实成员、原输入和源版本")
                old = self.library_save_request_result(
                    workspace_id, request_id, member_id, connection=conn
                )
                if old:
                    if old["request_digest"] != request_digest:
                        raise ScriptWindowConflict("原保存请求不能更换输入")
                    return old["result"]
            if write_guard is not None:
                write_guard(conn)
            row = conn.execute(
                "SELECT task_id,payload FROM creation_task_scripts WHERE workspace_id=? AND id=?",
                (workspace_id, source_script_id),
            ).fetchone()
            if row is None:
                raise KeyError(source_script_id)
            meta = json.loads(row["payload"])
            if expected_source_updated_at is not None and _revision_value(
                meta["updated_at"]
            ) != _revision_value(expected_source_updated_at):
                raise ScriptWindowConflict("源正文版本已变化，没有保存过期副本")
            if (
                meta.get("_sections_storage") != "rows"
                or meta["selection_status"] != "confirmed"
            ):
                raise ValueError("只能保存已确认的行化文案")
            self._require_creation_task(conn, workspace_id, row["task_id"])
            previous = conn.execute(
                "SELECT id,json_extract(payload,'$.version') AS version FROM script_library_entries WHERE workspace_id=? AND source_task_id=? AND name=? ORDER BY created_at DESC,id DESC LIMIT 1",
                (workspace_id, row["task_id"], name.strip()),
            ).fetchone()
            entry = ScriptLibraryEntry(
                id=f"script_library_{uuid4().hex[:12]}",
                workspace_id=workspace_id,
                name=name.strip(),
                category=category.strip(),
                source_script_id=source_script_id,
                source_task_id=row["task_id"],
                source_run_id=meta["run_id"],
                source_topic_id=meta["topic_id"],
                version=previous["version"] + 1 if previous else 1,
                supersedes_entry_id=previous["id"] if previous else None,
                title=meta["title"],
                language=meta["language"],
                sections=[],
            )
            data = asdict(entry)
            data.pop("sections")
            data["_sections_storage"] = "rows"
            conn.execute(
                "INSERT INTO script_library_entries VALUES (?,?,?,?,?,?)",
                (
                    entry.id,
                    workspace_id,
                    entry.source_task_id,
                    entry.name,
                    _json_object(data),
                    entry.created_at.isoformat(),
                ),
            )
            rows = conn.execute(
                "SELECT section_json FROM creation_task_script_sections WHERE workspace_id=? AND script_id=? ORDER BY ordinal",
                (workspace_id, source_script_id),
            )

            def parts():
                for row in rows:
                    part = json.loads(row["section_json"])
                    yield {
                        "id": part["id"],
                        "title": part["title"],
                        "content": part["content"],
                        "visual_prompt": part["visual_prompt"],
                        "reference_ids": part["evidence_reference_ids"],
                    }

            write_library_sections(conn, workspace_id, entry.id, parts())
            header = library_header(conn, workspace_id, entry.id)
            if request_id is None:
                return header
            result = {
                "request_id": request_id,
                "status": "saved",
                "entry_id": entry.id,
                "version": entry.version,
                "updated_at": header["updated_at"],
                "source_task_id": entry.source_task_id,
                "source_script_id": source_script_id,
                "source_updated_at": meta["updated_at"],
                "entry": header,
            }
            key = (
                "library_save_"
                + hashlib.sha256(
                    json.dumps([workspace_id, request_id, member_id]).encode()
                ).hexdigest()
            )
            self.append_task_event(
                TaskEvent(
                    key,
                    workspace_id,
                    entry.source_task_id,
                    None,
                    "script_library_saved",
                    {"request_digest": request_digest, "result": result},
                ),
                connection=conn,
            )
            return result

    def save_script_library_entry(
        self, workspace_id, *, source_script_id, name, category
    ):
        header = self.save_script_library_entry_header(
            workspace_id,
            source_script_id=source_script_id,
            name=name,
            category=category,
        )
        return self.get_script_library_entry(workspace_id, header["id"])

    def _script_library_entry_from_payload(self, data, *, connection=None):
        if data.pop("_sections_storage", None) != "rows" or "sections" in data:
            raise ValueError("文案库必须先完成正式正文行迁移")
        with (
            nullcontext(connection)
            if connection is not None
            else self._connect() as conn
        ):
            rows = conn.execute(
                "SELECT section_json FROM script_library_sections WHERE workspace_id=? AND entry_id=? ORDER BY ordinal",
                (data["workspace_id"], data["id"]),
            )
            return ScriptLibraryEntry(
                **_with_datetime(data, "created_at", "updated_at"),
                sections=[
                    ScriptLibrarySection(**json.loads(row["section_json"]))
                    for row in rows
                ],
            )

    def get_script_library_entry(self, workspace_id, entry_id):
        with self._connect() as conn:
            conn.execute("BEGIN")
            row = conn.execute(
                "SELECT payload FROM script_library_entries WHERE workspace_id=? AND id=?",
                (workspace_id, entry_id),
            ).fetchone()
            if row is None:
                raise KeyError(entry_id)
            return self._script_library_entry_from_payload(
                json.loads(row["payload"]), connection=conn
            )

    def read_script_library_entry_header(
        self, workspace_id, entry_id, *, connection=None
    ):
        from app.creation.script_windows import library_header

        with (
            nullcontext(connection)
            if connection is not None
            else self._connect() as conn
        ):
            if not conn.in_transaction:
                conn.execute("BEGIN")
            return library_header(conn, workspace_id, entry_id)

    def read_script_library_sections(self, workspace_id, entry_id, **window):
        from app.creation.script_windows import read_library_sections

        with self._connect() as conn:
            conn.execute("BEGIN")
            return read_library_sections(conn, workspace_id, entry_id, **window)

    def script_library_reference_ids(self, workspace_id, entry_id):
        from app.creation.script_windows import library_header

        with self._connect() as conn:
            conn.execute("BEGIN")
            library_header(conn, workspace_id, entry_id)
            rows = conn.execute(
                "SELECT DISTINCT r.value FROM script_library_sections p,json_each(p.section_json,'$.reference_ids') r WHERE p.workspace_id=? AND p.entry_id=? ORDER BY r.value",
                (workspace_id, entry_id),
            )
            for row in rows:
                yield row[0]

    def list_script_library_entries(self, workspace_id, *, query="", category=""):
        with self._connect() as conn:
            conn.execute("BEGIN")
            rows = conn.execute(
                "SELECT payload FROM script_library_entries WHERE workspace_id=? ORDER BY created_at DESC,id DESC",
                (workspace_id,),
            ).fetchall()
            entries = [
                self._script_library_entry_from_payload(
                    json.loads(row["payload"]), connection=conn
                )
                for row in rows
            ]
        keyword = query.strip().casefold()
        return [
            e
            for e in entries
            if (not category or e.category == category)
            and (
                not keyword
                or keyword
                in " ".join(
                    [e.name, e.title, *(p.content for p in e.sections)]
                ).casefold()
            )
        ]

    def list_script_library_entry_headers(
        self,
        workspace_id,
        *,
        query="",
        category="",
        after_created_at=None,
        after_entry_id=None,
        limit=SCRIPT_SECTION_WINDOW_LIMIT,
    ):
        from app.creation.script_windows import library_header

        if type(limit) is not int or not 1 <= limit <= SCRIPT_SECTION_WINDOW_LIMIT:
            raise ValueError("请使用合法库列表窗口")
        if (after_created_at is None) != (after_entry_id is None):
            raise ValueError("库列表续页需要完整排序游标")
        with self._connect() as conn:
            conn.execute("BEGIN")
            conn.create_function(
                "library_casefold",
                1,
                lambda value: value.casefold(),
                deterministic=True,
            )
            clauses = ["e.workspace_id=?"]
            args = [workspace_id]
            if category:
                clauses.append("json_extract(e.payload,'$.category')=?")
                args.append(category)
            if query.strip():
                clauses.append(
                    "(instr(library_casefold(e.name||' '||json_extract(e.payload,'$.title')),?)>0 OR EXISTS(SELECT 1 FROM script_library_sections p WHERE p.workspace_id=e.workspace_id AND p.entry_id=e.id AND instr(library_casefold(json_extract(p.section_json,'$.content')),?)>0))"
                )
                args.extend([query.strip().casefold()] * 2)
            if after_created_at is not None:
                clauses.append("(e.created_at,e.id)<(?,?)")
                args.extend([after_created_at, after_entry_id])
            rows = conn.execute(
                "SELECT e.id,e.created_at FROM script_library_entries e WHERE "
                + " AND ".join(clauses)
                + " ORDER BY e.created_at DESC,e.id DESC LIMIT ?",
                (*args, limit + 1),
            ).fetchall()
            page = rows[:limit]
            more = len(rows) > limit
            cursor = (
                {
                    "after_created_at": page[-1]["created_at"],
                    "after_entry_id": page[-1]["id"],
                }
                if more
                else None
            )
            return {
                "items": [
                    library_header(conn, workspace_id, row["id"]) for row in page
                ],
                "next": cursor,
            }

    def read_creation_task_script_header(self, workspace_id, script_id):
        with self._connect() as conn:
            conn.execute("BEGIN")
            row = conn.execute(
                "SELECT payload FROM creation_task_scripts WHERE workspace_id=? AND id=?",
                (workspace_id, script_id),
            ).fetchone()
            if row is None:
                raise KeyError(script_id)
            meta = json.loads(row["payload"])
            if meta.pop("_sections_storage", None) != "rows" or "sections" in meta:
                raise ScriptWindowConflict("源文案未采用唯一正文行存储")
            count = conn.execute(
                "SELECT count(*) FROM creation_task_script_sections WHERE workspace_id=? AND script_id=?",
                (workspace_id, script_id),
            ).fetchone()[0]
            return {
                **meta,
                "sections": None,
                "sections_loaded": False,
                "sections_total_count": count,
            }

    def library_save_request_result(
        self, workspace_id, request_id, member_id, *, connection=None
    ):
        key = (
            "library_save_"
            + hashlib.sha256(
                json.dumps([workspace_id, request_id, member_id]).encode()
            ).hexdigest()
        )
        with (
            nullcontext(connection)
            if connection is not None
            else self._connect() as conn
        ):
            row = conn.execute(
                "SELECT payload_json FROM task_events WHERE workspace_id=? AND id=?",
                (workspace_id, key),
            ).fetchone()
            return json.loads(row["payload_json"]) if row else None

    def library_reuse_request_result(
        self, workspace_id, request_id, member_id, *, connection=None
    ):
        key = (
            "library_reuse_"
            + hashlib.sha256(
                json.dumps([workspace_id, request_id, member_id]).encode()
            ).hexdigest()
        )
        with (
            nullcontext(connection)
            if connection is not None
            else self._connect() as conn
        ):
            row = conn.execute(
                "SELECT payload_json FROM task_events WHERE workspace_id=? AND id=?",
                (workspace_id, key),
            ).fetchone()
            return json.loads(row["payload_json"]) if row else None

    def reuse_library_rows(
        self,
        workspace_id,
        entry_id,
        *,
        plan,
        expected_updated_at,
        request_id,
        member_id,
        request_digest,
        write_guard,
    ):
        from app.creation.script_windows import library_header, section_digest

        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            old = self.library_reuse_request_result(
                workspace_id, request_id, member_id, connection=conn
            )
            if old:
                if old["request_digest"] != request_digest:
                    raise ScriptWindowConflict("原复用请求不能更换输入")
                return old["result"]
            write_guard(conn)
            header = library_header(conn, workspace_id, entry_id)
            if _revision_value(header["updated_at"]) != _revision_value(
                expected_updated_at
            ):
                raise ScriptWindowConflict("文案库版本已变化")
            if plan.workspace_id != workspace_id or plan.language != header["language"]:
                raise ValueError("复用要求与真实库来源不符")
            task = self._create_library_task(conn, workspace_id, plan, utc_now())
            script = CreationTaskScript(
                id="script_" + uuid4().hex[:12],
                workspace_id=workspace_id,
                task_id=task.id,
                run_id="",
                topic_id="",
                title=header["title"],
                language=header["language"],
                sections=[],
                reused_from_entry_id=entry_id,
            )
            rows = conn.execute(
                "SELECT ordinal,section_json FROM script_library_sections WHERE workspace_id=? AND entry_id=? ORDER BY ordinal",
                (workspace_id, entry_id),
            )
            for row in rows:
                part = json.loads(row["section_json"])
                section = {
                    "id": "section_" + uuid4().hex[:12],
                    "title": part["title"],
                    "content": part["content"],
                    "visual_prompt": part["visual_prompt"],
                    "evidence_reference_ids": part["reference_ids"],
                }
                conn.execute(
                    "INSERT INTO creation_task_script_sections VALUES (?,?,?,?,?,?)",
                    (
                        workspace_id,
                        script.id,
                        section["id"],
                        row["ordinal"],
                        json.dumps(
                            section,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                        section_digest(section),
                    ),
                )
            conn.execute(
                "INSERT INTO creation_task_scripts VALUES (?,?,?,?,?,?)",
                (
                    script.id,
                    workspace_id,
                    task.id,
                    "",
                    self._script_payload(conn, script, write_sections=False),
                    script.updated_at.isoformat(),
                ),
            )
            result = {
                "request_id": request_id,
                "entry_id": entry_id,
                "entry_version": header["version"],
                "task_id": task.id,
                "script_id": script.id,
                "updated_at": script.updated_at.isoformat(),
                "status": "saved",
            }
            event_id = (
                "library_reuse_"
                + hashlib.sha256(
                    json.dumps([workspace_id, request_id, member_id]).encode()
                ).hexdigest()
            )
            self.append_task_event(
                TaskEvent(
                    event_id,
                    workspace_id,
                    task.id,
                    None,
                    "script_library_reused",
                    {"request_digest": request_digest, "result": result},
                ),
                connection=conn,
            )
            return result

    def reuse_script_library_entry(
        self,
        workspace_id: str,
        entry_id: str,
        *,
        plan,
    ) -> tuple[CreationTask, CreationTaskScript]:
        entry = self.get_script_library_entry(workspace_id, entry_id)
        if plan.workspace_id != workspace_id or plan.language != entry.language:
            raise ValueError("复用方案必须属于当前工作区并保留文案语言")
        now = utc_now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            task = self._create_library_task(conn, workspace_id, plan, now)
            draft = CreationTaskScript(
                id=f"script_{uuid4().hex[:12]}",
                workspace_id=workspace_id,
                task_id=task.id,
                run_id="",
                topic_id="",
                title=entry.title,
                language=entry.language,
                sections=[
                    CreationTaskScriptSection(
                        id=f"section_{uuid4().hex[:12]}",
                        title=section.title,
                        content=section.content,
                        visual_prompt=section.visual_prompt,
                        evidence_reference_ids=list(section.reference_ids),
                    )
                    for section in entry.sections
                ],
                reused_from_entry_id=entry.id,
            )
            conn.execute(
                "INSERT INTO creation_task_scripts (id, workspace_id, task_id, run_id, payload, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    draft.id,
                    workspace_id,
                    task.id,
                    "",
                    self._script_payload(conn, draft),
                    draft.updated_at.isoformat(),
                ),
            )
        return task, draft

    def _demote_other_confirmed_scripts(
        self,
        conn: sqlite3.Connection,
        *,
        workspace_id: str,
        task_id: str,
        confirmed_script_id: str,
        updated_at: datetime,
    ) -> None:
        rows = conn.execute(
            """
            SELECT id, payload FROM creation_task_scripts
            WHERE workspace_id = ? AND task_id = ? AND id != ?
            """,
            (workspace_id, task_id, confirmed_script_id),
        ).fetchall()
        for row in rows:
            metadata = json.loads(row["payload"])
            if metadata["selection_status"] != "confirmed":
                continue
            metadata.update(
                selection_status="draft",
                confirmed_at=None,
                updated_at=updated_at.isoformat(),
            )
            conn.execute(
                "UPDATE creation_task_scripts SET payload=?,updated_at=? WHERE id=?",
                (
                    json.dumps(metadata, ensure_ascii=False),
                    updated_at.isoformat(),
                    row["id"],
                ),
            )

    def _script_adoption_context(
        self,
        conn,
        workspace_id,
        task_id,
        script_id,
        expected_updated_at,
        adoption_kind,
        member_id,
    ):
        task_row = conn.execute(
            "SELECT payload FROM creation_tasks WHERE workspace_id = ? AND id = ?",
            (workspace_id, task_id),
        ).fetchone()
        script_row = conn.execute(
            "SELECT payload FROM creation_task_scripts WHERE workspace_id = ? AND task_id = ? AND id = ?",
            (workspace_id, task_id, script_id),
        ).fetchone()
        if task_row is None or script_row is None:
            raise KeyError(script_id)
        task = self._require_creation_task(conn, workspace_id, task_id)
        metadata = json.loads(str(script_row["payload"]))
        if metadata.get("_sections_storage") != "rows" or "sections" in metadata:
            raise ScriptWindowConflict("文案未采用当前唯一正文存储，不能混用旧快照确认")
        if expected_updated_at is None:
            raise ValueError("确认文案必须提供当前 updated_at")
        if _revision_value(metadata["updated_at"]) != _revision_value(
            expected_updated_at
        ):
            raise ScriptWindowConflict("文案草稿修订已变化，请重新读取后再确认")
        if conn.execute(
            "SELECT 1 FROM job_runs WHERE workspace_id=? AND task_id=? AND status IN ('queued','running','interrupted') LIMIT 1",
            (workspace_id, task_id),
        ).fetchone():
            raise ValueError(
                "此任务仍有未结束的制作作业；先等作业结算或取消，再确认修订文案。"
            )
        if (
            task.narration_cache_key
            or task.montage_plan_id
            or task.current_timeline_version_id
            or task.output_ready
        ):
            raise ValueError("此任务已有配音或剪辑结果，不能通过此入口改写文案。")
        if not self._script_knowledge_inputs_current(conn, task, script_id):
            raise ValueError(
                "任务已追加资料，不能采用旧资料生成的草稿；请基于当前资料另存新稿。"
            )
        from app.creation.intent_use_case import CreationIntentUseCase

        plan_row = conn.execute(
            "SELECT payload FROM creation_plans WHERE workspace_id=? AND id=?",
            (workspace_id, task.plan_id),
        ).fetchone()
        plan = self._creation_plan_from_payload(json.loads(plan_row["payload"]))
        adoption = CreationIntentUseCase.adoption(
            plan.creation_intent,
            kind=adoption_kind,
            member_id=content_owner(member_id) or task.owner_user_id,
            version=metadata["updated_at"],
        )
        return task, metadata, plan, adoption

    def _persist_script_adoption(self, conn, task, script_id, metadata, now):
        self._demote_other_confirmed_scripts(
            conn,
            workspace_id=task.workspace_id,
            task_id=task.id,
            confirmed_script_id=script_id,
            updated_at=now,
        )
        conn.execute(
            "UPDATE creation_task_scripts SET payload=?,updated_at=? WHERE workspace_id=? AND task_id=? AND id=?",
            (
                json.dumps(metadata, ensure_ascii=False),
                now.isoformat(),
                task.workspace_id,
                task.id,
                script_id,
            ),
        )
        self._transition_task(conn, task, "adopted", now, script_id=script_id)

    def confirm_creation_task_script(
        self,
        workspace_id: str,
        task_id: str,
        script_id: str,
        *,
        title: str,
        sections: list[CreationTaskScriptSection],
        expected_updated_at: datetime | str | None = None,
        adoption_kind: Literal["manual", "delegated_system"] = "manual",
        verified_owner_user_id: str | None = None,
        write_guard=None,
        result_write=None,
    ) -> tuple[CreationTask, CreationTaskScript]:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if write_guard is not None:
                write_guard(conn)
            task, metadata, plan, adoption = self._script_adoption_context(
                conn,
                workspace_id,
                task_id,
                script_id,
                expected_updated_at,
                adoption_kind,
                verified_owner_user_id,
            )
            script = self._creation_task_script_from_payload(metadata, connection=conn)
            if not script.draft_complete:
                raise ValueError(
                    "分窗草稿尚未完成全稿采用核查，请使用完整稿确认入口；没有采用部分正文"
                )
            required_count = plan.template_settings.get("script_section_count")
            if required_count is not None and len(sections) != required_count:
                raise ValueError("实际完整正文段数与已采用模板不符，不能确认部分稿件")
            if script.locked_body_digest:
                if (
                    hashlib.sha256(
                        "".join(part.content for part in sections).encode()
                    ).hexdigest()
                    != script.locked_body_digest
                ):
                    raise ValueError(
                        "此文案已按定稿锁定，不能通过重复确认改写正文；请明确采用新的定稿。"
                    )
                return task, script
            if (
                not title.strip()
                or not sections
                or any(
                    not part.content.strip()
                    or (
                        not script.reused_from_entry_id
                        and not part.visual_prompt.strip()
                    )
                    for part in sections
                )
            ):
                raise ValueError("确认文案必须包含正文、画面建议和来源证据")
            if task.confirmed_script_id and task.confirmed_script_id != script.id:
                current = conn.execute(
                    "SELECT payload FROM creation_task_scripts WHERE workspace_id=? AND task_id=? AND id=?",
                    (workspace_id, task_id, task.confirmed_script_id),
                ).fetchone()
                if current and json.loads(current["payload"]).get("locked_body_digest"):
                    raise ValueError("当前定稿已锁定，不能用其他候选文案替换。")
            confirmed_script_repair = (
                task.confirmed_script_id == script_id
                and script.selection_status == "confirmed"
                and task.can_revise_script
            )
            if (
                task.confirmed_script_id == script_id
                and script.selection_status == "confirmed"
                and not confirmed_script_repair
            ):
                self._demote_other_confirmed_scripts(
                    conn,
                    workspace_id=workspace_id,
                    task_id=task_id,
                    confirmed_script_id=script_id,
                    updated_at=utc_now(),
                )
                return task, script
            if not confirmed_script_repair and (
                task.stage != "script" or task.stage_status != "waiting_for_user"
            ):
                raise ValueError("当前创作任务不在等待确认文案状态")
            allowed_references = {
                reference_id
                for original in script.sections
                for reference_id in original.evidence_reference_ids
            }
            used_references = {
                reference_id
                for section in sections
                for reference_id in section.evidence_reference_ids
            }
            if not used_references.issubset(allowed_references):
                raise ValueError("编辑后的文案引用了生成时未使用的资料")
            now = utc_now()
            script.title = title.strip()
            script.sections = sections
            script.selection_status = "confirmed"
            script.confirmed_at = now
            script.updated_at = now
            script.adoption = {
                **adoption,
                "script_id": script.id,
                "version": now.isoformat(),
                "body_digest": hashlib.sha256(
                    "".join(part.content for part in script.sections).encode()
                ).hexdigest(),
            }
            self._persist_script_adoption(
                conn,
                task,
                script.id,
                json.loads(self._script_payload(conn, script)),
                now,
            )
            if result_write is not None:
                result_write(conn, script)
        return self.get_creation_task(
            workspace_id, task_id
        ), self.get_creation_task_script(workspace_id, script_id)

    @staticmethod
    def _script_generation_run_from_payload(
        data: dict[str, object],
    ) -> ScriptGenerationRun:
        return ScriptGenerationRun(**_with_datetime(data, "started_at", "finished_at"))

    def _script_payload(self, connection, script, *, write_sections=True):
        if write_sections:
            write_script_sections(
                connection,
                script.workspace_id,
                script.id,
                (asdict(section) for section in script.sections),
            )
        metadata = json.loads(_json_payload(replace(script, sections=[])))
        del metadata["sections"]
        metadata["_sections_storage"] = "rows"
        return json.dumps(metadata, ensure_ascii=False)

    def _creation_task_script_from_payload(
        self, data: dict[str, object], *, connection=None
    ) -> CreationTaskScript:
        if data.pop("_sections_storage", None) != "rows" or "sections" in data:
            raise ValueError("文案正文存储版本无效，请先完成正式行化迁移")
        with (
            nullcontext(connection)
            if connection is not None
            else self._connect() as conn
        ):
            section_rows = conn.execute(
                """SELECT section_json FROM creation_task_script_sections
                WHERE workspace_id=? AND script_id=? ORDER BY ordinal""",
                (data["workspace_id"], data["id"]),
            ).fetchall()
            return CreationTaskScript(
                **_with_datetime(data, "created_at", "updated_at", "confirmed_at"),
                sections=[
                    CreationTaskScriptSection(**json.loads(row["section_json"]))
                    for row in section_rows
                ],
            )
