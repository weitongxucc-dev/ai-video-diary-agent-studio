"""Consistent execution metadata snapshots with explicit connection ownership."""
from __future__ import annotations

from contextlib import closing, contextmanager, nullcontext

from app.creation.task_input_reads import require_creation_task


class ExecutionInputReader:
    def __init__(self, *, connection_factory, read_plan, read_script_metadata, iter_script_sections):
        self._connect = connection_factory
        self._read_plan = read_plan
        self._read_script_metadata = read_script_metadata
        self._iter_script_sections = iter_script_sections

    @contextmanager
    def creation_execution_read(self, connection=None):
        """Own a read snapshot, or borrow an already active owner transaction."""
        with (nullcontext(connection) if connection is not None else closing(self._connect())) as conn:
            if connection is None:
                conn.execute("BEGIN")
            elif not conn.in_transaction:
                raise ValueError("执行输入读取必须复用已开启的事务")
            yield conn

    def read_creation_execution_snapshot(self, workspace_id, task_id, *, connection=None):
        """Finish metadata, ordered body digest and count in one read transaction."""
        from app.creation.execution_input import ExecutionInputSnapshot, execution_input_digest, validate_execution_relationships
        with self.creation_execution_read(connection) as conn:
            task = require_creation_task(conn, workspace_id, task_id)
            plan = self._read_plan(conn, workspace_id, task.plan_id)
            if plan.source_media is not None:
                validate_execution_relationships(task, None, plan)
                return ExecutionInputSnapshot(task, plan, None, execution_input_digest(None, plan, ()))
            if not task.confirmed_script_id:
                raise ValueError("请先确认实际文案")
            header, fields = self._read_script_metadata(conn, workspace_id, task.confirmed_script_id)
            digest = execution_input_digest(fields, plan,
                self._iter_script_sections(conn, workspace_id, header.id))
            return ExecutionInputSnapshot(task, plan, header, digest)
