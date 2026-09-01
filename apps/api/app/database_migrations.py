from __future__ import annotations

import sqlite3


PRODUCTION_SCHEMA_VERSION = 4


_MIGRATIONS: tuple[tuple[int, str], ...] = (
    (
        1,
        """
        CREATE TABLE IF NOT EXISTS timeline_versions (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            task_id TEXT NOT NULL,
            parent_version_id TEXT,
            sequence INTEGER NOT NULL CHECK (sequence >= 1),
            snapshot_json TEXT NOT NULL,
            created_by_operation_id TEXT,
            created_at TEXT NOT NULL,
            UNIQUE(workspace_id, task_id, sequence)
        );
        CREATE INDEX IF NOT EXISTS idx_timeline_versions_task
            ON timeline_versions(workspace_id, task_id, sequence ASC);
        CREATE TRIGGER IF NOT EXISTS timeline_versions_no_update
        BEFORE UPDATE ON timeline_versions
        BEGIN
            SELECT RAISE(ABORT, 'timeline_versions are immutable');
        END;

        CREATE TABLE IF NOT EXISTS job_runs (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            task_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            input_digest TEXT NOT NULL,
            status TEXT NOT NULL CHECK (
                status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled', 'interrupted')
            ),
            progress INTEGER NOT NULL CHECK (progress BETWEEN 0 AND 100),
            attempt INTEGER NOT NULL CHECK (attempt >= 0),
            output_refs_json TEXT NOT NULL,
            failure_reason TEXT,
            impact TEXT,
            repair_action TEXT,
            created_at TEXT NOT NULL,
            started_at TEXT,
            finished_at TEXT,
            updated_at TEXT NOT NULL,
            UNIQUE(workspace_id, idempotency_key)
        );
        CREATE INDEX IF NOT EXISTS idx_job_runs_task
            ON job_runs(workspace_id, task_id, created_at ASC);
        CREATE INDEX IF NOT EXISTS idx_job_runs_status
            ON job_runs(workspace_id, status, updated_at ASC);

        CREATE TABLE IF NOT EXISTS artifact_records (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            task_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            logical_ref TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
            producer_job_id TEXT NOT NULL,
            input_refs_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(workspace_id, logical_ref)
        );
        CREATE INDEX IF NOT EXISTS idx_artifact_records_task
            ON artifact_records(workspace_id, task_id, created_at ASC);
        CREATE INDEX IF NOT EXISTS idx_artifact_records_job
            ON artifact_records(workspace_id, producer_job_id, created_at ASC);

        CREATE TABLE IF NOT EXISTS task_events (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            task_id TEXT NOT NULL,
            job_id TEXT,
            event_type TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_task_events_task
            ON task_events(workspace_id, task_id, created_at ASC, id ASC);
        CREATE INDEX IF NOT EXISTS idx_task_events_job
            ON task_events(workspace_id, job_id, created_at ASC, id ASC);
        """,
    ),
    (
        2,
        """
        CREATE TABLE IF NOT EXISTS transform_runs (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            task_id TEXT NOT NULL,
            job_id TEXT NOT NULL,
            transform_type TEXT NOT NULL,
            input_digest TEXT NOT NULL,
            input_refs_json TEXT NOT NULL,
            output_refs_json TEXT NOT NULL,
            engine TEXT NOT NULL,
            parameters_json TEXT NOT NULL,
            status TEXT NOT NULL CHECK (
                status IN ('running', 'succeeded', 'failed')
            ),
            started_at TEXT NOT NULL,
            finished_at TEXT,
            failure_reason TEXT,
            remediation TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_transform_runs_task
            ON transform_runs(workspace_id, task_id, started_at ASC, id ASC);
        CREATE INDEX IF NOT EXISTS idx_transform_runs_job
            ON transform_runs(workspace_id, job_id, started_at ASC, id ASC);

        CREATE TABLE IF NOT EXISTS dependency_edges (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            task_id TEXT NOT NULL,
            upstream_ref TEXT NOT NULL,
            downstream_ref TEXT NOT NULL,
            created_by_transform_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(
                workspace_id,
                task_id,
                upstream_ref,
                downstream_ref,
                created_by_transform_id
            )
        );
        CREATE INDEX IF NOT EXISTS idx_dependency_edges_task
            ON dependency_edges(workspace_id, task_id, created_at ASC, id ASC);
        CREATE INDEX IF NOT EXISTS idx_dependency_edges_transform
            ON dependency_edges(
                workspace_id,
                created_by_transform_id,
                created_at ASC,
                id ASC
            );
        """,
    ),
    (
        3,
        """
        CREATE TABLE IF NOT EXISTS edit_intents (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            task_id TEXT NOT NULL,
            raw_command TEXT NOT NULL,
            context_stage TEXT NOT NULL,
            selected_object_refs_json TEXT NOT NULL,
            playhead_ms INTEGER,
            intent_type TEXT NOT NULL,
            scope_json TEXT NOT NULL,
            constraints_json TEXT NOT NULL,
            confidence REAL NOT NULL CHECK (confidence BETWEEN 0.0 AND 1.0),
            status TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_edit_intents_task
            ON edit_intents(workspace_id, task_id, created_at ASC, id ASC);
        CREATE TRIGGER IF NOT EXISTS edit_intents_no_update
        BEFORE UPDATE ON edit_intents
        BEGIN
            SELECT RAISE(ABORT, 'edit_intents are immutable');
        END;

        CREATE TABLE IF NOT EXISTS edit_proposals (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            task_id TEXT NOT NULL,
            intent_id TEXT NOT NULL,
            summary TEXT NOT NULL,
            target_refs_json TEXT NOT NULL,
            operation_json TEXT NOT NULL,
            invalidated_refs_json TEXT NOT NULL,
            preview_json TEXT NOT NULL,
            external_effects_json TEXT NOT NULL,
            status TEXT NOT NULL CHECK (
                status IN ('proposed', 'confirmed', 'rejected', 'expired')
            ),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_edit_proposals_task
            ON edit_proposals(workspace_id, task_id, created_at ASC, id ASC);
        CREATE INDEX IF NOT EXISTS idx_edit_proposals_intent
            ON edit_proposals(workspace_id, intent_id, created_at ASC, id ASC);
        CREATE TRIGGER IF NOT EXISTS edit_proposals_valid_transition
        BEFORE UPDATE ON edit_proposals
        WHEN NOT (
            OLD.status = 'proposed'
            AND NEW.status IN ('confirmed', 'rejected', 'expired')
            AND OLD.id = NEW.id
            AND OLD.workspace_id = NEW.workspace_id
            AND OLD.task_id = NEW.task_id
            AND OLD.intent_id = NEW.intent_id
            AND OLD.summary = NEW.summary
            AND OLD.target_refs_json = NEW.target_refs_json
            AND OLD.operation_json = NEW.operation_json
            AND OLD.invalidated_refs_json = NEW.invalidated_refs_json
            AND OLD.preview_json = NEW.preview_json
            AND OLD.external_effects_json = NEW.external_effects_json
            AND OLD.created_at = NEW.created_at
        )
        BEGIN
            SELECT RAISE(ABORT, 'invalid edit_proposal transition');
        END;

        CREATE TABLE IF NOT EXISTS edit_operations (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            task_id TEXT NOT NULL,
            proposal_id TEXT NOT NULL,
            operation_type TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            base_version_id TEXT NOT NULL,
            result_version_id TEXT,
            status TEXT NOT NULL CHECK (
                status IN ('running', 'applied', 'failed')
            ),
            failure_reason TEXT,
            created_at TEXT NOT NULL,
            applied_at TEXT,
            UNIQUE(workspace_id, task_id, proposal_id)
        );
        CREATE INDEX IF NOT EXISTS idx_edit_operations_task
            ON edit_operations(workspace_id, task_id, created_at ASC, id ASC);
        CREATE TRIGGER IF NOT EXISTS edit_operations_applied_no_update
        BEFORE UPDATE ON edit_operations
        WHEN OLD.status = 'applied'
        BEGIN
            SELECT RAISE(ABORT, 'applied edit_operations are immutable');
        END;
        """,
    ),
    (
        4,
        """
        CREATE TABLE IF NOT EXISTS narration_segments (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            task_id TEXT NOT NULL,
            script_id TEXT NOT NULL,
            source_section_id TEXT NOT NULL,
            text TEXT NOT NULL,
            audio_reference TEXT,
            duration_seconds REAL,
            sample_rate INTEGER,
            model_id TEXT NOT NULL,
            voice_profile_id TEXT NOT NULL,
            voice_version TEXT NOT NULL,
            language TEXT NOT NULL,
            pronunciation_version TEXT NOT NULL,
            status TEXT NOT NULL CHECK (
                status IN ('pending', 'generating', 'ready', 'failed')
            ),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(workspace_id, task_id, script_id, source_section_id)
        );
        CREATE INDEX IF NOT EXISTS idx_narration_segments_task
            ON narration_segments(
                workspace_id,
                task_id,
                script_id,
                created_at ASC,
                id ASC
            );
        CREATE TRIGGER IF NOT EXISTS narration_segments_stable_identity
        BEFORE UPDATE ON narration_segments
        WHEN NOT (
            OLD.id = NEW.id
            AND OLD.workspace_id = NEW.workspace_id
            AND OLD.task_id = NEW.task_id
            AND OLD.script_id = NEW.script_id
            AND OLD.source_section_id = NEW.source_section_id
            AND OLD.created_at = NEW.created_at
        )
        BEGIN
            SELECT RAISE(ABORT, 'narration_segment identity is immutable');
        END;
        """,
    ),
)


def apply_production_migrations(conn: sqlite3.Connection) -> None:
    """Apply this repository's schema changes to the existing shared database."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS production_schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    applied = {
        int(row[0])
        for row in conn.execute(
            "SELECT version FROM production_schema_migrations ORDER BY version"
        )
    }
    unknown = [version for version in applied if version > PRODUCTION_SCHEMA_VERSION]
    if unknown:
        raise RuntimeError(
            f"production schema version {max(unknown)} is newer than supported "
            f"version {PRODUCTION_SCHEMA_VERSION}"
        )
    conn.commit()
    for version, sql in _MIGRATIONS:
        if version in applied:
            continue
        try:
            conn.executescript(
                "BEGIN IMMEDIATE;\n"
                + sql
                + "\nINSERT OR IGNORE INTO production_schema_migrations (version) "
                + f"VALUES ({version});\nCOMMIT;"
            )
        except sqlite3.Error:
            conn.rollback()
            raise
