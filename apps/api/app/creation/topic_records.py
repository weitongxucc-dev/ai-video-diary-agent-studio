"""Topic record owner; cross-object writes use the caller's Task/event ports."""
from __future__ import annotations

from collections.abc import Callable
from contextlib import nullcontext
from typing import Literal, Protocol
import json
import sqlite3

from app.creation.contracts import CreationTask, TaskEvent
from app.creation.intent_use_case import CreationIntentUseCase
from app.creation.topic_contracts import (
    TopicCandidate,
    TopicEvidenceReference,
    TopicGenerationRun,
)
from app.models import utc_now
from app.persistence_json import _json_payload, _with_datetime
from app.source_policy import content_owner


class _TaskReader(Protocol):
    def __call__(self, workspace_id: str, task_id: str) -> CreationTask: ...


class _EventWriter(Protocol):
    def __call__(self, event: TaskEvent, *, connection: sqlite3.Connection) -> TaskEvent: ...


def topic_generation_run_from_payload(data: dict[str, object]) -> TopicGenerationRun:
    return TopicGenerationRun(
        **_with_datetime(data, "started_at", "finished_at")
    )


def topic_candidate_from_payload(data: dict[str, object]) -> TopicCandidate:
    return TopicCandidate(**_with_datetime(data, "created_at"))


def topic_evidence_reference_from_payload(
    data: dict[str, object],
) -> TopicEvidenceReference:
    return TopicEvidenceReference(**_with_datetime(data, "created_at"))


class TopicRecordStore:
    def __init__(
        self,
        connection_factory: Callable[[], sqlite3.Connection],
        *,
        read_task: _TaskReader,
        require_task: Callable[[sqlite3.Connection, str, str], CreationTask],
        write_task: Callable[[sqlite3.Connection, CreationTask], None],
        read_plan_intent: Callable[[sqlite3.Connection, str, str], dict | None],
        append_event: _EventWriter,
    ):
        self._connect = connection_factory
        self._read_task = read_task
        self._require_task = require_task
        self._write_task = write_task
        self._read_plan_intent = read_plan_intent
        self._append_event = append_event

    def start_topic_generation(
        self,
        run: TopicGenerationRun, *, write_guard=None) -> tuple[CreationTask, TopicGenerationRun]:
        """Persist the running attempt before the provider boundary is crossed."""
        if run.status != "running":
            raise ValueError("选题运行必须以 running 状态开始")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if write_guard is not None:
                write_guard(conn)
            task = self._require_task(conn, run.workspace_id, run.task_id)
            if task.plan_id != run.plan_id:
                raise ValueError("选题运行关联的方案与创作任务不一致")
            if task.stage != "topic" or task.stage_status not in {"not_started", "failed"}:
                raise ValueError("当前创作任务不允许再次开始选题")
            now = utc_now()
            task.stage_status = "running"
            task.status = "running"
            task.stage_reason = None
            task.stage_impact = None
            task.stage_repair_action = None
            task.stage_retryable = None
            task.updated_at = now
            conn.execute(
                """
                INSERT INTO topic_generation_runs
                    (id, workspace_id, task_id, plan_id, payload, started_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run.id,
                    run.workspace_id,
                    run.task_id,
                    run.plan_id,
                    _json_payload(run),
                    run.started_at.isoformat(),
                    now.isoformat(),
                ),
            )
            self._write_task(conn, task)
        return (
            self._read_task(run.workspace_id, run.task_id),
            self.get_topic_generation_run(run.workspace_id, run.id),
        )

    def complete_topic_generation(
        self,
        run_id: str,
        candidates: list[TopicCandidate],
        evidence_references: list[TopicEvidenceReference], *, write_guard=None) -> tuple[CreationTask, TopicGenerationRun, list[TopicCandidate], list[TopicEvidenceReference]]:
        """Commit validated candidates, their evidence, and the stage transition together."""
        if not candidates:
            raise ValueError("选题生成没有返回候选")
        candidate_ids = {candidate.id for candidate in candidates}
        if len(candidate_ids) != len(candidates):
            raise ValueError("选题候选标识重复")
        if any(reference.candidate_id not in candidate_ids for reference in evidence_references):
            raise ValueError("选题证据关联了不存在的候选")
        if any(
            not any(reference.candidate_id == candidate.id for reference in evidence_references)
            for candidate in candidates
        ):
            raise ValueError("每个选题候选都必须保留来源证据")

        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if write_guard is not None:
                write_guard(conn)
            row = conn.execute(
                """
                SELECT payload FROM topic_generation_runs
                WHERE id = ?
                """,
                (run_id,),
            ).fetchone()
            if row is None:
                raise KeyError(run_id)
            run = topic_generation_run_from_payload(json.loads(str(row["payload"])))
            if run.status != "running":
                raise ValueError("选题运行不是可完成状态")
            task = self._require_task(conn, run.workspace_id, run.task_id)
            if task.stage_status != "running":
                raise ValueError("创作任务的选题阶段不是运行中")
            for candidate in candidates:
                if (
                    candidate.workspace_id != run.workspace_id
                    or candidate.task_id != run.task_id
                    or candidate.run_id != run.id
                    or not candidate.title.strip()
                    or not candidate.angle.strip()
                    or not candidate.audience.strip()
                    or not candidate.outline
                    or not 0 <= candidate.score <= 100
                ):
                    raise ValueError("选题候选不符合当前任务的持久化契约")
            for reference in evidence_references:
                if (
                    reference.workspace_id != run.workspace_id
                    or reference.task_id != run.task_id
                    or not reference.reference_id.strip()
                    or not reference.version_or_identity.strip()
                ):
                    raise ValueError("选题证据不符合当前任务的持久化契约")

            now = utc_now()
            run.status = "succeeded"
            run.route_snapshot = {
                **run.route_snapshot,
                "availability": "succeeded",
                "checked_at": now,
            }
            run.finished_at = now
            task.stage_status = "waiting_for_user"
            task.status = "waiting_for_user"
            task.progress = max(task.progress, 15)
            task.updated_at = now
            conn.execute(
                """
                UPDATE topic_generation_runs SET payload = ?, updated_at = ? WHERE id = ?
                """,
                (_json_payload(run), now.isoformat(), run.id),
            )
            conn.executemany(
                """
                INSERT INTO topic_candidates
                    (id, workspace_id, task_id, run_id, payload, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        candidate.id,
                        candidate.workspace_id,
                        candidate.task_id,
                        candidate.run_id,
                        _json_payload(candidate),
                        candidate.created_at.isoformat(),
                    )
                    for candidate in candidates
                ],
            )
            conn.executemany(
                """
                INSERT INTO topic_evidence_references
                    (id, workspace_id, task_id, candidate_id, payload, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        reference.id,
                        reference.workspace_id,
                        reference.task_id,
                        reference.candidate_id,
                        _json_payload(reference),
                        reference.created_at.isoformat(),
                    )
                    for reference in evidence_references
                ],
            )
            self._write_task(conn, task)
        return (
            self._read_task(run.workspace_id, run.task_id),
            self.get_topic_generation_run(run.workspace_id, run.id),
            self.list_topic_candidates(run.workspace_id, run.task_id),
            self.list_topic_evidence_references(run.workspace_id, run.task_id),
        )

    def fail_topic_generation(
        self,
        run_id: str,
        *,
        reason: str,
        impact: str,
        repair_action: str,
        retryable: bool,
        provider_attempted: bool = False,
    ) -> tuple[CreationTask, TopicGenerationRun]:
        if not all(value.strip() for value in (reason, impact, repair_action)):
            raise ValueError("选题失败必须包含原因、影响和修复动作")
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM topic_generation_runs WHERE id = ?", (run_id,)
            ).fetchone()
            if row is None:
                raise KeyError(run_id)
            run = topic_generation_run_from_payload(json.loads(str(row["payload"])))
            if run.status != "running":
                raise ValueError("选题运行不是可失败状态")
            task = self._require_task(conn, run.workspace_id, run.task_id)
            now = utc_now()
            run.status = "failed"
            run.reason = reason
            run.impact = impact
            run.repair_action = repair_action
            run.retryable = retryable
            if provider_attempted:
                run.route_snapshot = {
                    **run.route_snapshot,
                    "availability": "failed",
                    "checked_at": now,
                }
            run.finished_at = now
            task.stage_status = "failed"
            task.status = "failed"
            task.recovery_point = "topic"
            task.stage_reason = reason
            task.stage_impact = impact
            task.stage_repair_action = repair_action
            task.stage_retryable = retryable
            task.updated_at = now
            conn.execute(
                "UPDATE topic_generation_runs SET payload = ?, updated_at = ? WHERE id = ?",
                (_json_payload(run), now.isoformat(), run.id),
            )
            self._write_task(conn, task)
        return (
            self._read_task(run.workspace_id, run.task_id),
            self.get_topic_generation_run(run.workspace_id, run.id),
        )

    def get_topic_generation_run(
        self, workspace_id: str, run_id: str
    ) -> TopicGenerationRun:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT payload FROM topic_generation_runs
                WHERE workspace_id = ? AND id = ?
                """,
                (workspace_id, run_id),
            ).fetchone()
        if row is None:
            raise KeyError(run_id)
        return topic_generation_run_from_payload(json.loads(str(row["payload"])))

    def latest_topic_generation_run(
        self, workspace_id: str, task_id: str
    ) -> TopicGenerationRun | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT payload FROM topic_generation_runs
                WHERE workspace_id = ? AND task_id = ?
                ORDER BY started_at DESC LIMIT 1
                """,
                (workspace_id, task_id),
            ).fetchone()
        return (
            topic_generation_run_from_payload(json.loads(str(row["payload"])))
            if row is not None
            else None
        )

    def list_topic_candidates(
        self, workspace_id: str, task_id: str
    ) -> list[TopicCandidate]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT payload FROM topic_candidates
                WHERE workspace_id = ? AND task_id = ?
                ORDER BY created_at ASC
                """,
                (workspace_id, task_id),
            ).fetchall()
        return [
            topic_candidate_from_payload(json.loads(str(row["payload"])))
            for row in rows
        ]

    def list_topic_evidence_references(
        self, workspace_id: str, task_id: str
    ) -> list[TopicEvidenceReference]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT payload FROM topic_evidence_references
                WHERE workspace_id = ? AND task_id = ?
                ORDER BY created_at ASC
                """,
                (workspace_id, task_id),
            ).fetchall()
        return [
            topic_evidence_reference_from_payload(json.loads(str(row["payload"])))
            for row in rows
        ]

    def get_topic_evidence_reference(self, workspace_id, task_id, evidence_id, *, connection=None):
        with nullcontext(connection) if connection is not None else self._connect() as conn:
            row = conn.execute('SELECT payload FROM topic_evidence_references WHERE workspace_id=? AND task_id=? AND id=?',
                (workspace_id, task_id, evidence_id)).fetchone()
        if row is None:
            raise KeyError(evidence_id)
        return topic_evidence_reference_from_payload(json.loads(row['payload']))

    def confirm_topic_candidate(
        self, workspace_id: str, task_id: str, candidate_id: str, *, title: str | None = None,
        adoption_kind: Literal["manual", "delegated_system"] = "manual", verified_owner_user_id: str | None = None, write_guard=None) -> tuple[CreationTask, TopicCandidate]:
        if title is not None:
            title = title.strip()
            if not 1 <= len(title) <= 80:
                raise ValueError("选题标题需要填写 1–80 个字符")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if write_guard is not None:
                write_guard(conn)
            task = self._require_task(conn, workspace_id, task_id)
            intent = self._read_plan_intent(conn, workspace_id, task.plan_id)
            adoption = CreationIntentUseCase.adoption(intent, kind=adoption_kind,
                member_id=content_owner(verified_owner_user_id) or task.owner_user_id, version=candidate_id)
            candidate_row = conn.execute(
                """
                SELECT payload FROM topic_candidates
                WHERE workspace_id = ? AND task_id = ? AND id = ?
                """,
                (workspace_id, task_id, candidate_id),
            ).fetchone()
            if candidate_row is None:
                raise KeyError(candidate_id)
            candidate = topic_candidate_from_payload(
                json.loads(str(candidate_row["payload"]))
            )
            if (
                task.selected_topic_id == candidate_id
                and candidate.selection_status == "confirmed"
            ):
                if title is not None and title != candidate.title:
                    raise ValueError("选题已经确认，不能通过重复确认改写标题")
                return task, candidate
            if task.selected_topic_id is not None:
                raise ValueError("当前创作任务已经确认了另一个选题")
            if task.stage != "topic" or task.stage_status != "waiting_for_user":
                raise ValueError("当前创作任务不在等待确认选题状态")
            now = utc_now()
            candidate.selection_status = "confirmed"
            if title is not None:
                candidate.title = title
            task.selected_topic_id = candidate.id
            task.stage = "script"
            task.stage_status = "not_started"
            task.status = "running"
            task.progress = max(task.progress, 20)
            task.recovery_point = "script"
            task.stage_reason = None
            task.stage_impact = None
            task.stage_repair_action = None
            task.stage_retryable = None
            task.updated_at = now
            conn.execute(
                "UPDATE topic_candidates SET payload = ? WHERE id = ?",
                (_json_payload(candidate), candidate.id),
            )
            self._append_event(TaskEvent(f"topic_adopted_{candidate.id}", workspace_id, task_id, None,
                "topic_adopted", {"topic_id": candidate.id, "title": candidate.title, "adoption": adoption}), connection=conn)
            self._write_task(conn, task)
        return (
            self._read_task(workspace_id, task_id),
            next(
                candidate
                for candidate in self.list_topic_candidates(workspace_id, task_id)
                if candidate.id == candidate_id
            ),
        )

    def get_topic_candidate(self, workspace_id, task_id, candidate_id, *, connection=None):
        with nullcontext(connection) if connection is not None else self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM topic_candidates WHERE workspace_id = ? AND task_id = ? AND id = ?",
                (workspace_id, task_id, candidate_id),
            ).fetchone()
        if row is None:
            raise KeyError(candidate_id)
        return topic_candidate_from_payload(json.loads(str(row["payload"])))

    def save_provided_topic(self, connection, run: TopicGenerationRun, candidate: TopicCandidate):
        """Record the supplied topic within the existing Plan/Task adoption transaction."""
        if not connection.in_transaction:
            raise ValueError("给定选题存储必须属于方案任务原事务")
        connection.execute(
            "INSERT INTO topic_generation_runs VALUES(?,?,?,?,?,?,?)",
            (run.id, run.workspace_id, run.task_id, run.plan_id, _json_payload(run),
             run.started_at.isoformat(), run.finished_at.isoformat()),
        )
        connection.execute(
            "INSERT INTO topic_candidates VALUES(?,?,?,?,?,?)",
            (candidate.id, candidate.workspace_id, candidate.task_id, candidate.run_id,
             _json_payload(candidate), candidate.created_at.isoformat()),
        )
