"""Generic JobRun facts and original transitions; business settlement stays with callers."""
from __future__ import annotations

from contextlib import nullcontext
from datetime import datetime
import json
import sqlite3
from typing import Literal

from app.creation.contracts import JobRun as _JobRun, validate_logical_reference as _validate_logical_reference
from app.models import utc_now


def _job_run_values(run: _JobRun) -> tuple[object, ...]:
    return (
        run.id,
        run.workspace_id,
        run.task_id,
        run.kind,
        run.idempotency_key,
        run.input_digest,
        run.status,
        run.progress,
        run.attempt,
        json.dumps(run.output_refs, ensure_ascii=False),
        run.failure_reason,
        run.impact,
        run.repair_action,
        run.created_at.isoformat(),
        run.started_at.isoformat() if run.started_at else None,
        run.finished_at.isoformat() if run.finished_at else None,
        run.updated_at.isoformat(),
    )


def _validate_new_job_run(run: _JobRun) -> None:
    if not all(
        value.strip()
        for value in (
            run.id,
            run.workspace_id,
            run.task_id,
            run.kind,
            run.idempotency_key,
            run.input_digest,
        )
    ):
        raise ValueError("任务运行记录缺少必要标识或输入摘要")
    if run.status != "queued" or run.progress != 0 or run.attempt != 0:
        raise ValueError("新任务运行记录必须从 queued、0% 且未尝试开始")
    if run.started_at is not None or run.finished_at is not None:
        raise ValueError("新任务运行记录不能已有开始或结束时间")
    if run.output_refs or any(
        value is not None for value in (run.failure_reason, run.impact, run.repair_action)
    ):
        raise ValueError("新任务运行记录不能预先包含输出或失败结果")



class JobRunRecordStore:
    def __init__(self, connection_factory, *, require_task, decode_reference_list,
                 revision_token, registered_job_output_refs):
        self._connect = connection_factory
        self._require_creation_task = require_task
        self._decode_reference_list = decode_reference_list
        self._revision_token = revision_token
        self._registered_job_output_refs = registered_job_output_refs

    def create_job_run(self, run: _JobRun, *, connection: sqlite3.Connection | None = None) -> _JobRun:
        _validate_new_job_run(run)
        with (self._connect() if connection is None else nullcontext(connection)) as conn:
            self._require_creation_task(conn, run.workspace_id, run.task_id)
            existing = conn.execute(
                """
                SELECT * FROM job_runs
                WHERE workspace_id = ? AND idempotency_key = ?
                """,
                (run.workspace_id, run.idempotency_key),
            ).fetchone()
            if existing is not None:
                persisted = self.job_run_from_row(existing)
                if (
                    persisted.task_id != run.task_id
                    or persisted.kind != run.kind
                    or persisted.input_digest != run.input_digest
                ):
                    raise ValueError("幂等键已绑定到不同的任务输入")
                return persisted
            try:
                conn.execute(
                    """
                    INSERT INTO job_runs
                        (id, workspace_id, task_id, kind, idempotency_key,
                         input_digest, status, progress, attempt, output_refs_json,
                         failure_reason, impact, repair_action, created_at,
                         started_at, finished_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    _job_run_values(run),
                )
            except sqlite3.IntegrityError as exc:
                raced = conn.execute(
                    "SELECT * FROM job_runs WHERE workspace_id = ? AND idempotency_key = ?",
                    (run.workspace_id, run.idempotency_key),
                ).fetchone()
                if raced is not None:
                    persisted = self.job_run_from_row(raced)
                    if (
                        persisted.task_id != run.task_id
                        or persisted.kind != run.kind
                        or persisted.input_digest != run.input_digest
                    ):
                        raise ValueError("幂等键已绑定到不同的任务输入") from exc
                    return persisted
                raise ValueError("任务运行记录 ID 已存在") from exc
        return run if connection is not None else self.get_job_run(run.workspace_id, run.id)


    def get_job_run(self, workspace_id: str, job_id: str, *, connection=None) -> _JobRun:
        with nullcontext(connection) if connection is not None else self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM job_runs WHERE workspace_id = ? AND id = ?",
                (workspace_id, job_id),
            ).fetchone()
        if row is None:
            raise KeyError(job_id)
        return self.job_run_from_row(row)


    def get_job_run_by_idempotency_key(
        self, workspace_id: str, idempotency_key: str
    ) -> _JobRun | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM job_runs
                WHERE workspace_id = ? AND idempotency_key = ?
                """,
                (workspace_id, idempotency_key),
            ).fetchone()
        return self.job_run_from_row(row) if row is not None else None


    def list_job_runs(self, workspace_id: str, task_id: str) -> list[_JobRun]:
        with self._connect() as conn:
            self._require_creation_task(conn, workspace_id, task_id)
            rows = conn.execute(
                """
                SELECT * FROM job_runs
                WHERE workspace_id = ? AND task_id = ? ORDER BY created_at ASC, id ASC
                """,
                (workspace_id, task_id),
            ).fetchall()
        return [self.job_run_from_row(row) for row in rows]


    def list_pending_task_media_jobs(self, kind: str):
        with self._connect() as conn:
            pending_media = conn.execute("SELECT * FROM job_runs WHERE kind=? AND status IN ('queued','interrupted','running','cancelled')", (kind,)).fetchall()
        return (self.job_run_from_row(row) for row in pending_media)


    def claim_job_run(self, workspace_id: str, job_id: str, *, connection: sqlite3.Connection | None = None) -> _JobRun:
        with (self._connect() if connection is None else nullcontext(connection)) as conn:
            row = conn.execute(
                "SELECT * FROM job_runs WHERE workspace_id = ? AND id = ?",
                (workspace_id, job_id),
            ).fetchone()
            if row is None:
                raise KeyError(job_id)
            current = self.job_run_from_row(row)
            if current.status not in {"queued", "interrupted"}:
                raise ValueError("任务运行记录当前不可领取")
            now = utc_now()
            changed = conn.execute(
                """
                UPDATE job_runs
                SET status = 'running', attempt = ?, started_at = ?, finished_at = NULL,
                    failure_reason = NULL, impact = NULL, repair_action = NULL,
                    updated_at = ?
                WHERE workspace_id = ? AND id = ? AND status = ?
                """,
                (
                    current.attempt + 1,
                    now.isoformat(),
                    now.isoformat(),
                    workspace_id,
                    job_id,
                    current.status,
                ),
            ).rowcount
            if changed != 1:
                raise ValueError("任务运行记录已被其他执行者领取")
            row = conn.execute("SELECT * FROM job_runs WHERE workspace_id=? AND id=?", (workspace_id, job_id)).fetchone()
            return self.job_run_from_row(row)


    def update_job_run_progress(
        self, workspace_id: str, job_id: str, progress: int
    ) -> _JobRun:
        if not 0 <= progress <= 100:
            raise ValueError("任务进度必须在 0 到 100 之间")
        with self._connect() as conn:
            row = conn.execute(
                "SELECT progress, status FROM job_runs WHERE workspace_id = ? AND id = ?",
                (workspace_id, job_id),
            ).fetchone()
            if row is None:
                raise KeyError(job_id)
            if str(row["status"]) != "running":
                raise ValueError("只能更新运行中任务的进度")
            if progress < int(row["progress"]):
                raise ValueError("任务进度不能倒退")
            now = utc_now()
            changed = conn.execute(
                """
                UPDATE job_runs SET progress = ?, updated_at = ?
                WHERE workspace_id = ? AND id = ? AND status = 'running'
                """,
                (progress, now.isoformat(), workspace_id, job_id),
            ).rowcount
            if changed != 1:
                raise ValueError("任务运行记录已结束，进度未写入")
        return self.get_job_run(workspace_id, job_id)


    def finish_job_run(
        self,
        workspace_id: str,
        job_id: str,
        *,
        status: Literal["succeeded", "failed", "cancelled", "interrupted"],
        output_refs: list[str] | None = None,
        failure_reason: str | None = None,
        impact: str | None = None,
        repair_action: str | None = None,
        connection: sqlite3.Connection | None = None, write_guard=None,
    ) -> _JobRun:
        if status != "succeeded" and not all(
            value is not None and value.strip()
            for value in (failure_reason, impact, repair_action)
        ):
            raise ValueError("未成功的任务必须记录原因、影响和修复动作")
        refs = output_refs or []
        for reference in refs:
            _validate_logical_reference(reference, "产物输出引用")
        with (self._connect() if connection is None else nullcontext(connection)) as conn:
            if write_guard is not None:
                if not conn.in_transaction:
                    conn.execute("BEGIN IMMEDIATE")
                write_guard(conn)
            row = conn.execute(
                "SELECT status, task_id FROM job_runs WHERE workspace_id = ? AND id = ?",
                (workspace_id, job_id),
            ).fetchone()
            if row is None:
                raise KeyError(job_id)
            current_status = str(row["status"])
            allowed_sources = {"running"}
            if status == "cancelled":
                allowed_sources.update({"queued", "interrupted"})
            if current_status not in allowed_sources:
                raise ValueError("任务运行记录当前不可结束")
            if status == "succeeded" and refs:
                registered_refs = self._registered_job_output_refs(conn, workspace_id, str(row["task_id"]), job_id)
                missing_refs = [ref for ref in refs if ref not in registered_refs]
                if missing_refs:
                    raise ValueError("成功任务的输出必须先登记为可追溯产物")
            now = utc_now()
            progress = 100 if status == "succeeded" else None
            changed = conn.execute(
                """
                UPDATE job_runs
                SET status = ?, progress = COALESCE(?, progress), output_refs_json = ?,
                    failure_reason = ?, impact = ?, repair_action = ?,
                    finished_at = ?, updated_at = ?
                WHERE workspace_id = ? AND id = ? AND status = ?
                """,
                (
                    status,
                    progress,
                    json.dumps(refs, ensure_ascii=False),
                    None if status == "succeeded" else failure_reason,
                    None if status == "succeeded" else impact,
                    None if status == "succeeded" else repair_action,
                    now.isoformat(),
                    now.isoformat(),
                    workspace_id,
                    job_id,
                    current_status,
                ),
            ).rowcount
            if changed != 1:
                raise ValueError("任务运行记录已被其他执行者结束")
            row = conn.execute("SELECT * FROM job_runs WHERE workspace_id=? AND id=?", (workspace_id, job_id)).fetchone()
            return self.job_run_from_row(row)


    def interrupt_running_job_runs(self, workspace_id: str | None = None) -> int:
        now = utc_now().isoformat()
        with self._connect() as conn:
            workspace_clause = "" if workspace_id is None else " AND workspace_id = ?"
            parameters = (
                (now, now) if workspace_id is None else (now, now, workspace_id)
            )
            return conn.execute(
                """
                UPDATE job_runs
                SET status = 'interrupted', failure_reason = 'process_interrupted',
                    impact = '本次执行未产生可信的成功结果。',
                    repair_action = '重新领取该任务并从已持久化输入恢复。',
                    finished_at = ?, updated_at = ?
                WHERE status = 'running'
                """
                + workspace_clause,
                parameters,
            ).rowcount


    def reopen_failed_job_run(self, workspace_id, job_id, *, expected_updated_at, expected_attempt, connection):
        if not connection.in_transaction:
            raise ValueError('恢复失败作业必须属于原调度事务')
        changed=connection.execute("""UPDATE job_runs SET status='queued',finished_at=NULL,updated_at=?
            WHERE workspace_id=? AND id=? AND status='failed' AND updated_at=? AND attempt=?""",
            (utc_now().isoformat(),workspace_id,job_id,self._revision_token(expected_updated_at),expected_attempt)).rowcount
        if changed!=1:
            raise ValueError('失败作业状态、版本或尝试序号已变化，未恢复')
        return self.get_job_run(workspace_id,job_id,connection=connection)


    @staticmethod
    def job_belongs(connection, workspace_id, task_id, job_id):
        row = connection.execute(
            "SELECT task_id FROM job_runs WHERE workspace_id = ? AND id = ?",
            (workspace_id, job_id),
        ).fetchone()
        return row is not None and str(row["task_id"]) == task_id


    def job_run_from_row(self, row: sqlite3.Row) -> _JobRun:
        return _JobRun(
            id=str(row["id"]),
            workspace_id=str(row["workspace_id"]),
            task_id=str(row["task_id"]),
            kind=str(row["kind"]),
            idempotency_key=str(row["idempotency_key"]),
            input_digest=str(row["input_digest"]),
            status=str(row["status"]),  # type: ignore[arg-type]
            progress=int(row["progress"]),
            attempt=int(row["attempt"]),
            output_refs=self._decode_reference_list(str(row["output_refs_json"])),
            failure_reason=(
                str(row["failure_reason"])
                if row["failure_reason"] is not None
                else None
            ),
            impact=str(row["impact"]) if row["impact"] is not None else None,
            repair_action=(
                str(row["repair_action"])
                if row["repair_action"] is not None
                else None
            ),
            created_at=datetime.fromisoformat(str(row["created_at"])),
            started_at=(
                datetime.fromisoformat(str(row["started_at"]))
                if row["started_at"] is not None
                else None
            ),
            finished_at=(
                datetime.fromisoformat(str(row["finished_at"]))
                if row["finished_at"] is not None
                else None
            ),
            updated_at=datetime.fromisoformat(str(row["updated_at"])),
        )


    @staticmethod
    def has_open_task_job(connection, workspace_id, task_id):
        return connection.execute(
            "SELECT 1 FROM job_runs WHERE workspace_id=? AND task_id=? AND status IN ('queued','running','interrupted') LIMIT 1",
            (workspace_id, task_id),
        ).fetchone() is not None
