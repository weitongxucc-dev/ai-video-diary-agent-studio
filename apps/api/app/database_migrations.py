from __future__ import annotations

import sqlite3
from app.materials.discovery_store import DISCOVERY_SCHEMA


PRODUCTION_SCHEMA_VERSION = 20


# The local_members schema is also self-provisioned by app.local_members so a
# fresh (test) database can use the service without running the full migration
# chain, which is not idempotent on an empty file (migration 10 alters a table
# created outside the migration list). Keep both sources in sync here.
LOCAL_MEMBERS_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS local_members (
    member_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('owner', 'member')),
    created_at TEXT NOT NULL
);
"""

LOCAL_MEMBERS_SEED_DDL = """
INSERT OR IGNORE INTO local_members (member_id, name, role, created_at)
VALUES (
    '00000000-0000-0000-0000-000000000001',
    '本机创作者',
    'owner',
    strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
);
"""


# Explicit local adoption also installs only this table inside its data transaction.
# It must not run the general startup migration chain against the reviewed input.
LOCAL_ADOPTION_RECEIPTS_DDL = """
CREATE TABLE IF NOT EXISTS local_adoption_receipts (
    operation_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL CHECK (workspace_id = 'local'),
    member_id TEXT NOT NULL,
    manifest_sha256 TEXT NOT NULL,
    manifest_json TEXT NOT NULL,
    result_json TEXT NOT NULL,
    applied_at TEXT NOT NULL
);
"""


VOICE_SAMPLING_TASKS_DDL = """
CREATE TABLE IF NOT EXISTS voice_sampling_tasks (
    workspace_id TEXT NOT NULL,
    id TEXT NOT NULL,
    owner_user_id TEXT NOT NULL,
    request_id TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (workspace_id, id),
    UNIQUE (workspace_id, owner_user_id, request_id)
);
"""


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
    (
        5,
        """
        CREATE TABLE IF NOT EXISTS person_renders (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            task_id TEXT NOT NULL,
            person_profile_id TEXT NOT NULL,
            person_version TEXT NOT NULL,
            voice_profile_id TEXT NOT NULL,
            voice_version TEXT NOT NULL,
            narration_segment_id TEXT NOT NULL,
            visual_reference TEXT NOT NULL,
            model_id TEXT NOT NULL,
            motion TEXT NOT NULL,
            expression TEXT NOT NULL,
            cache_key TEXT NOT NULL,
            status TEXT NOT NULL CHECK (
                status IN ('pending', 'running', 'ready', 'failed')
            ),
            output_path TEXT,
            duration_seconds REAL,
            alignment_evidence TEXT,
            failure_reason TEXT,
            impact TEXT,
            repair_action TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(workspace_id, task_id, cache_key)
        );
        CREATE INDEX IF NOT EXISTS idx_person_renders_task
            ON person_renders(workspace_id, task_id, created_at ASC, id ASC);
        CREATE INDEX IF NOT EXISTS idx_person_renders_narration
            ON person_renders(
                workspace_id,
                task_id,
                narration_segment_id,
                created_at ASC
            );
        CREATE TRIGGER IF NOT EXISTS person_renders_stable_identity
        BEFORE UPDATE ON person_renders
        WHEN NOT (
            OLD.id = NEW.id
            AND OLD.workspace_id = NEW.workspace_id
            AND OLD.task_id = NEW.task_id
            AND OLD.person_profile_id = NEW.person_profile_id
            AND OLD.person_version = NEW.person_version
            AND OLD.voice_profile_id = NEW.voice_profile_id
            AND OLD.voice_version = NEW.voice_version
            AND OLD.narration_segment_id = NEW.narration_segment_id
            AND OLD.visual_reference = NEW.visual_reference
            AND OLD.model_id = NEW.model_id
            AND OLD.motion = NEW.motion
            AND OLD.expression = NEW.expression
            AND OLD.cache_key = NEW.cache_key
            AND OLD.created_at = NEW.created_at
        )
        BEGIN
            SELECT RAISE(ABORT, 'person_render identity is immutable');
        END;
        """,
    ),
    (
        6,
        """
        ALTER TABLE person_renders ADD COLUMN artifact_ref TEXT;
        CREATE TRIGGER IF NOT EXISTS person_renders_artifact_ref_write_once
        BEFORE UPDATE ON person_renders
        WHEN OLD.artifact_ref IS NOT NULL
             AND NEW.artifact_ref IS NOT OLD.artifact_ref
        BEGIN
            SELECT RAISE(ABORT, 'person_render artifact ref is immutable');
        END;
        """,
    ),
    (
        7,
        """
        CREATE TABLE IF NOT EXISTS subtitle_tracks (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            task_id TEXT NOT NULL,
            timeline_version_id TEXT NOT NULL,
            language TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(workspace_id, task_id, timeline_version_id, language)
        );
        CREATE INDEX IF NOT EXISTS idx_subtitle_tracks_task
            ON subtitle_tracks(
                workspace_id,
                task_id,
                created_at ASC,
                id ASC
            );

        CREATE TABLE IF NOT EXISTS cover_assets (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            task_id TEXT NOT NULL,
            timeline_version_id TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(workspace_id, task_id, timeline_version_id)
        );
        CREATE INDEX IF NOT EXISTS idx_cover_assets_task
            ON cover_assets(
                workspace_id,
                task_id,
                created_at ASC,
                id ASC
            );
        """,
    ),
    (
        8,
        """
        CREATE TABLE device_work_requests (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            member_id TEXT,
            owner_type TEXT NOT NULL,
            owner_id TEXT NOT NULL,
            input_digest TEXT NOT NULL,
            unit_key TEXT NOT NULL,
            source_refs_json TEXT NOT NULL,
            work_class TEXT NOT NULL CHECK (work_class IN ('preview_dependency', 'normal_make', 'background')),
            policy TEXT NOT NULL CHECK (policy IN ('asap', 'idle', 'scheduled')),
            not_before_utc TEXT,
            window_json TEXT,
            first_ready_at_utc TEXT NOT NULL,
            eligible_since_utc TEXT,
            eligible_wait_seconds REAL NOT NULL DEFAULT 0,
            state TEXT NOT NULL CHECK (state IN ('waiting', 'blocked', 'granted', 'yield_requested', 'done', 'cancelled')),
            wait_reason TEXT,
            checkpoint_ref TEXT,
            resources_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(workspace_id, owner_type, owner_id, input_digest, unit_key)
        );
        CREATE INDEX idx_device_work_pending
            ON device_work_requests(state, work_class, first_ready_at_utc, id);

        CREATE TABLE device_permit (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            lock_key TEXT,
            generation INTEGER NOT NULL DEFAULT 0,
            request_id TEXT,
            token_hash TEXT,
            holder_json TEXT,
            children_json TEXT NOT NULL DEFAULT '[]',
            granted_at_utc TEXT,
            heartbeat_at_utc TEXT,
            state TEXT NOT NULL DEFAULT 'free' CHECK (state IN ('free', 'held', 'reconciling')),
            last_groups_json TEXT NOT NULL DEFAULT '{}',
            preview_burst INTEGER NOT NULL DEFAULT 0,
            normal_burst INTEGER NOT NULL DEFAULT 0
        );
        INSERT INTO device_permit (id) VALUES (1);

        CREATE TABLE device_session_activity (
            session_id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            member_id TEXT NOT NULL,
            last_heartbeat_at_utc TEXT NOT NULL,
            last_interaction_at_utc TEXT NOT NULL,
            playing INTEGER NOT NULL CHECK (playing IN (0, 1)),
            recording INTEGER NOT NULL CHECK (recording IN (0, 1)),
            editing INTEGER NOT NULL CHECK (editing IN (0, 1))
        );
        CREATE INDEX idx_device_session_heartbeat
            ON device_session_activity(last_heartbeat_at_utc);
        """,
    ),
    (
        9,
        """
        ALTER TABLE device_work_requests RENAME TO device_work_requests_v8;
        CREATE TABLE device_work_requests (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            member_id TEXT,
            owner_type TEXT NOT NULL,
            owner_id TEXT NOT NULL,
            input_digest TEXT NOT NULL,
            unit_key TEXT NOT NULL,
            source_refs_json TEXT NOT NULL,
            work_class TEXT NOT NULL CHECK (work_class IN ('preview_dependency', 'normal_make', 'background')),
            policy TEXT NOT NULL CHECK (policy IN ('asap', 'idle', 'scheduled')),
            not_before_utc TEXT,
            window_json TEXT,
            first_ready_at_utc TEXT NOT NULL,
            eligible_since_utc TEXT,
            eligible_wait_seconds REAL NOT NULL DEFAULT 0,
            state TEXT NOT NULL CHECK (state IN ('waiting', 'blocked', 'granted', 'yield_requested', 'done', 'cancelled', 'failed')),
            wait_reason TEXT,
            checkpoint_ref TEXT,
            resources_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            attempt_number INTEGER NOT NULL DEFAULT 1 CHECK (attempt_number >= 1),
            retry_not_before_utc TEXT,
            last_failure_reason TEXT,
            last_grant_generation INTEGER,
            UNIQUE(workspace_id, owner_type, owner_id, input_digest, unit_key)
        );
        INSERT INTO device_work_requests
            (id, workspace_id, member_id, owner_type, owner_id, input_digest, unit_key,
             source_refs_json, work_class, policy, not_before_utc, window_json,
             first_ready_at_utc, eligible_since_utc, eligible_wait_seconds, state,
             wait_reason, checkpoint_ref, resources_json, created_at, updated_at)
        SELECT id, workspace_id, member_id, owner_type, owner_id, input_digest, unit_key,
               source_refs_json, work_class, policy, not_before_utc, window_json,
               first_ready_at_utc, eligible_since_utc, eligible_wait_seconds, state,
               wait_reason, checkpoint_ref, resources_json, created_at, updated_at
        FROM device_work_requests_v8;
        DROP TABLE device_work_requests_v8;
        CREATE INDEX idx_device_work_pending
            ON device_work_requests(state, work_class, first_ready_at_utc, id);

        CREATE TABLE device_request_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id TEXT NOT NULL,
            attempt_number INTEGER NOT NULL,
            permit_generation INTEGER,
            event_type TEXT NOT NULL CHECK (event_type IN ('deferred', 'failed', 'retried')),
            reason TEXT NOT NULL,
            actor_id TEXT,
            prior_state TEXT NOT NULL,
            prior_checkpoint_ref TEXT,
            created_at_utc TEXT NOT NULL
        );
        CREATE INDEX idx_device_request_events_request
            ON device_request_events(request_id, id);
        """,
    ),
    (
        10,
        """
        ALTER TABLE material_ingestion_files ADD COLUMN provisional_revision TEXT;

        CREATE TABLE material_analysis_checkpoints (
            workspace_id TEXT NOT NULL,
            asset_id TEXT NOT NULL,
            source_sha256 TEXT NOT NULL,
            provisional_revision TEXT NOT NULL,
            unit_key TEXT NOT NULL,
            stage_input_digest TEXT NOT NULL,
            result_json TEXT,
            result_ref TEXT,
            completed_at TEXT NOT NULL,
            PRIMARY KEY (workspace_id, asset_id, provisional_revision, source_sha256, unit_key),
            CHECK (result_json IS NOT NULL OR result_ref IS NOT NULL)
        );
        CREATE INDEX idx_material_analysis_checkpoint_source
            ON material_analysis_checkpoints(workspace_id, asset_id, source_sha256, unit_key);
        """,
    ),
    (
        11,
        LOCAL_MEMBERS_TABLE_DDL + "\n" + LOCAL_MEMBERS_SEED_DDL,
    ),
    (
        12,
        """
        CREATE TABLE scheduled_tasks (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            task_type TEXT NOT NULL CHECK (task_type IN ('material_analysis', 'creation_render')),
            trigger_mode TEXT NOT NULL CHECK (trigger_mode IN ('idle', 'scheduled')),
            target_ref TEXT NOT NULL,
            not_before_utc TEXT,
            window_json TEXT,
            enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX idx_scheduled_tasks_workspace
            ON scheduled_tasks(workspace_id, enabled, created_at);
        """,
    ),
    # Version 13 marks the members-auth visibility change. Neither the member
    # token column nor the content visibility columns live in static SQL here:
    # both target tables that are created outside the migration list (the
    # members table is self-provisioned by app.local_members; creation_tasks and
    # material_assets are provisioned by ProductionRepository._init_database),
    # and the migration chain is also applied standalone (see test_device_runtime)
    # against a bare database lacking those tables. SQLite has no ADD COLUMN IF
    # NOT EXISTS and static SQL cannot PRAGMA-guard, so a plain ALTER here would
    # collide on a re-open or crash the standalone chain. The columns are added
    # by PRAGMA-guarded ensures in app.local_members._ensure_token_column and in
    # ProductionRepository._init_database instead.
    (
        13,
        "",
    ),
    (
        14,
        "",
    ),
    (
        15,
        """
        DROP TRIGGER IF EXISTS narration_segments_stable_identity;
        ALTER TABLE narration_segments RENAME TO narration_segments_v14;
        CREATE TABLE narration_segments (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            task_id TEXT NOT NULL,
            script_id TEXT NOT NULL,
            source_section_id TEXT NOT NULL,
            text TEXT NOT NULL,
            audio_reference TEXT,
            duration_seconds REAL,
            sample_rate INTEGER,
            model_id TEXT,
            voice_profile_id TEXT,
            voice_version TEXT,
            language TEXT NOT NULL,
            pronunciation_version TEXT NOT NULL,
            status TEXT NOT NULL CHECK (
                status IN ('pending', 'generating', 'ready', 'failed')
            ),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            source_kind TEXT NOT NULL DEFAULT 'generated' CHECK (
                source_kind IN ('generated', 'recorded')
            ),
            source_artifact_ref TEXT,
            source_sha256 TEXT,
            source_script_digest TEXT,
            UNIQUE(workspace_id, task_id, script_id, source_section_id)
        );
        INSERT INTO narration_segments (
            id, workspace_id, task_id, script_id, source_section_id, text,
            audio_reference, duration_seconds, sample_rate, model_id,
            voice_profile_id, voice_version, language, pronunciation_version,
            status, created_at, updated_at, source_kind, source_script_digest
        )
        SELECT id, workspace_id, task_id, script_id, source_section_id, text,
               audio_reference, duration_seconds, sample_rate, model_id,
               voice_profile_id, voice_version, language, pronunciation_version,
               status, created_at, updated_at, 'generated', NULL
        FROM narration_segments_v14;
        DROP TABLE narration_segments_v14;
        CREATE INDEX idx_narration_segments_task
            ON narration_segments(workspace_id, task_id, script_id, created_at ASC, id ASC);
        CREATE TRIGGER narration_segments_stable_identity
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
    (
        16,
        """
        CREATE TABLE automations (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            member_id TEXT NOT NULL,
            actor_json TEXT NOT NULL,
            name TEXT NOT NULL,
            goal TEXT NOT NULL,
            template_id TEXT NOT NULL,
            template_version INTEGER NOT NULL CHECK(template_version >= 1),
            template_snapshot_json TEXT NOT NULL,
            trigger_json TEXT NOT NULL,
            source_scope_json TEXT NOT NULL,
            revision INTEGER NOT NULL DEFAULT 1,
            paused INTEGER NOT NULL DEFAULT 0 CHECK(paused IN (0,1)),
            status TEXT NOT NULL CHECK(status IN ('active','paused','running','waiting_review','blocked','failed')),
            next_due_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX idx_automations_due ON automations(paused,next_due_at);
        CREATE INDEX idx_automations_member ON automations(workspace_id,member_id,created_at);
        CREATE TABLE automation_runs (
            id TEXT PRIMARY KEY,
            automation_id TEXT NOT NULL,
            workspace_id TEXT NOT NULL,
            member_id TEXT NOT NULL,
            automation_revision INTEGER NOT NULL,
            due_key TEXT NOT NULL,
            due_at TEXT NOT NULL,
            definition_json TEXT NOT NULL,
            input_snapshot_json TEXT,
            state TEXT NOT NULL,
            stage TEXT NOT NULL,
            reason_code TEXT,
            reason TEXT,
            repair_action TEXT,
            plan_id TEXT,
            task_id TEXT,
            job_id TEXT,
            confirmation_json TEXT NOT NULL DEFAULT '{}',
            execution_json TEXT NOT NULL DEFAULT '{}',
            revision INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(automation_id,due_key)
        );
        CREATE INDEX idx_automation_runs_scope ON automation_runs(workspace_id,member_id,automation_id,created_at);
        """,
    ),
    (
        17,
        """
        CREATE TABLE creation_requests (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            owner_user_id TEXT NOT NULL,
            request_key TEXT NOT NULL,
            input_fingerprint TEXT NOT NULL,
            creation_input_digest TEXT NOT NULL,
            goal TEXT NOT NULL,
            target_count INTEGER NOT NULL CHECK(target_count > 0 AND target_count <= 9007199254740991),
            output_relation TEXT,
            requirements_json TEXT NOT NULL,
            material_scope_json TEXT NOT NULL,
            clarification_questions_json TEXT NOT NULL,
            revision INTEGER NOT NULL CHECK(revision > 0),
            status TEXT NOT NULL CHECK(status IN ('awaiting_clarification','awaiting_plan','awaiting_confirmation','active')),
            current_window_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(workspace_id, owner_user_id, request_key)
        );
        CREATE INDEX idx_creation_requests_owner ON creation_requests(workspace_id,owner_user_id,created_at);
        CREATE TABLE creation_request_windows (
            id TEXT PRIMARY KEY,
            request_id TEXT NOT NULL REFERENCES creation_requests(id),
            ordinal_start INTEGER NOT NULL CHECK(ordinal_start > 0),
            input_fingerprint TEXT NOT NULL,
            request_revision INTEGER NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('proposed','confirmed','superseded')),
            created_at TEXT NOT NULL,
            confirmed_at TEXT
        );
        CREATE INDEX idx_creation_request_windows_request ON creation_request_windows(request_id,status,ordinal_start);
        CREATE TABLE creation_request_items (
            window_id TEXT NOT NULL REFERENCES creation_request_windows(id),
            ordinal INTEGER NOT NULL CHECK(ordinal > 0),
            plan_id TEXT NOT NULL,
            task_id TEXT NOT NULL,
            plan_payload TEXT NOT NULL,
            task_payload TEXT NOT NULL,
            PRIMARY KEY(window_id,ordinal),
            UNIQUE(window_id,plan_id),
            UNIQUE(window_id,task_id)
        );
        """,
    ),
    (18, LOCAL_ADOPTION_RECEIPTS_DDL),
    (19, VOICE_SAMPLING_TASKS_DDL),
    (20, DISCOVERY_SCHEMA),
)

# 「内容可见性」地基（父条目 WIL-110 R05，子 issue WIL-112 第①段）。
# 与 version 13 同一原因：这三张表都在迁移链之外创建（knowledge_items 由
# SourceRepository._init_database，creation_plans / video_templates 由
# ProductionRepository._init_database），迁移链本身也会被独立应用到裸库
# （见 test_device_runtime）。SQLite 没有 ADD COLUMN IF NOT EXISTS，静态 SQL
# 也无法 PRAGMA 守卫，所以这里不放静态 ALTER，改由下列 PRAGMA 守卫的 ensure
# 在各自 Repository 打开时幂等执行：表不存在就跳过（裸库安全），列已存在就
# 不重复加（重开安全）。
#
# 存量行回填 shared（不改动已有数据的语义）；表结构的 DEFAULT 是 private，
# 两者是不同的事，迁移回填不会把新建行的默认改回 shared。
CONTENT_VISIBILITY_TABLES = (
    "knowledge_items",
    "creation_plans",
    "video_templates",
    "creation_tasks",
    "material_assets",
)
CONTENT_VISIBILITY_LEGACY = "shared"
CONTENT_VISIBILITY_DEFAULT = "private"


def ensure_content_visibility_columns(conn: sqlite3.Connection) -> None:
    """给三张内容表补 visibility 列，幂等、可重复执行、裸库安全。

    - 表不存在：跳过（迁移链可在缺少这些表的裸库上独立运行）。
    - 列已存在：不重复 ALTER（SQLite 无 ADD COLUMN IF NOT EXISTS）。
    - 存量行：NULL 回填为 shared，保持「已有内容语义不变」。
    - 新建行：依赖建表 DDL 的 DEFAULT 'private'，本函数不改默认值。
    """
    for table in CONTENT_VISIBILITY_TABLES:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if exists is None:
            continue
        columns = {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})")}
        if "visibility" not in columns:
            # 关键：不带 DEFAULT 加列。SQLite 在 ADD COLUMN 带 DEFAULT 时会用该
            # 默认值回填所有存量行，那样老行会直接变成 private，与「存量行保持
            # shared」相反。所以先加一个无默认值的可空列，让老行保持 NULL，
            # 下面再把 NULL 显式回填为 shared。
            #
            # 新建行的 private 由两处承担：建表 DDL 的 DEFAULT 'private'
            # （fresh 库）与仓储层 INSERT 的显式赋值（升级库）。
            conn.execute(f"ALTER TABLE {table} ADD COLUMN visibility TEXT")
        # 存量行回填 shared：只动 NULL 的，不覆盖已显式写过的值。
        conn.execute(
            f"UPDATE {table} SET visibility = ? WHERE visibility IS NULL",
            (CONTENT_VISIBILITY_LEGACY,),
        )


def rollback_content_visibility_columns(conn: sqlite3.Connection) -> list[str]:
    """回滚路径：删掉三张表上由本迁移新增的 visibility 列。

    SQLite 3.35+ 支持 DROP COLUMN；更老的版本自动回退到「重建表」路线。
    正常路径不应调用它——列是 CREATE TABLE 的一部分，删列会让依赖它的写入路径
    立刻失败。提供它是为了满足「可逆迁移」：迁移确实可以被撤销。
    """
    removed: list[str] = []
    for table in CONTENT_VISIBILITY_TABLES:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if exists is None:
            continue
        columns = {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})")}
        if "visibility" not in columns:
            continue
        try:
            conn.execute(f"ALTER TABLE {table} DROP COLUMN visibility")
        except sqlite3.OperationalError:
            # 老版本 SQLite：把不含 visibility 的定义重建回去。
            _rebuild_table_without_visibility(conn, table)
        removed.append(table)
    conn.commit()
    return removed


def _rebuild_table_without_visibility(conn: sqlite3.Connection, table: str) -> None:
    """老版本 SQLite 的回滚兜底：按当前列定义重建表并去掉 visibility。"""
    columns = [str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})")]
    keep = [name for name in columns if name != "visibility"]
    joined = ", ".join(keep)
    conn.execute(f"ALTER TABLE {table} RENAME TO {table}_visibility_rollback")
    conn.execute(f"CREATE TABLE {table} ({joined})")
    conn.execute(f"INSERT INTO {table} ({joined}) SELECT {joined} FROM {table}_visibility_rollback")
    conn.execute(f"DROP TABLE {table}_visibility_rollback")


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
        # Older assembled databases can have migration markers for 1..14 but
        # lack the narration table originally created by migration 4. Restore
        # that real prerequisite before v15 transforms it; never stamp a missing
        # migration version to make the chain appear complete.
        if version == 15 and conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='narration_segments'"
        ).fetchone() is None:
            narration_schema = next(
                migration_sql for migration_version, migration_sql in _MIGRATIONS
                if migration_version == 4
            )
            try:
                conn.executescript("BEGIN IMMEDIATE;\n" + narration_schema + "\nCOMMIT;")
            except sqlite3.Error:
                conn.rollback()
                raise
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
    # Intake is created by ProductionRepository, outside this migration chain.
    # A standalone database may record version 14 before that table exists.
    # Ensure the columns on each repository open, including that later creation.
    if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='material_ingestion_files'"
    ).fetchone() is not None:
        columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(material_ingestion_files)")}
        with conn:
            for name in ("confirmed_revision", "confirmation_source", "copy_confirmed_at"):
                if name not in columns:
                    conn.execute(f"ALTER TABLE material_ingestion_files ADD COLUMN {name} TEXT")
