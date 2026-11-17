"""Capture must borrow the owner transaction, including under SQLite exclusive locks."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import sqlite3

import pytest

from app.creation_execution import CreationJobOwnerVerifier
from app.creation.contracts import JobRun
from app.profiles.contracts import Authorization, PronunciationEntry, VoiceProfile
from tests.test_creation_execution import _seed


@pytest.mark.parametrize('mutation,verdict', [('authorization', 'unauthorized'), ('fixed_revision', 'stale'), ('pronunciation', 'stale')])
def test_narration_capture_borrows_exclusive_connection_and_fences_profiles(tmp_path, monkeypatch, mutation, verdict):
    repo, task_id, _, digest = _seed(tmp_path)
    grant = Authorization('voice_auth_1', 'v1', 'holder', ('voice',), ('narration',),
        datetime.now(timezone.utc) - timedelta(days=1), None, 'evidence:test', 'active', 'workspace_a')
    repo.save_authorization(grant)
    voice = VoiceProfile('voice_1', 'workspace_a', 'v1', 'zh', 'media://declared', 'text',
        person_profile_id='person_1', authorization=grant, visibility='shared')
    with repo._connect() as conn:
        repo.profile_records.save_received_voice(conn, voice)
    job = repo.job_records.create_job_run(JobRun('capture-job', 'workspace_a', task_id,
        'creation_task_narration', 'capture-job', digest))
    task = repo.task_records.get_creation_task('workspace_a', task_id)
    plan = repo.plan_records.get_creation_plan('workspace_a', task.plan_id)
    script = repo.script_records.get_creation_task_script('workspace_a', task.confirmed_script_id)
    owner = CreationJobOwnerVerifier(repo)
    monkeypatch.setattr(owner, '_snapshot_facts', lambda *_: (job,
        SimpleNamespace(task=task, plan=plan, script_header=script), {'member_id': 'member'}))
    monkeypatch.setattr(owner, 'authorization_verdict', lambda *_a, **_kw: 'ready')
    request = SimpleNamespace(workspace_id='workspace_a', owner_id=job.id,
        unit_key='narration:fixed', resources={})
    with repo._connect() as conn:
        conn.execute('BEGIN EXCLUSIVE')
        # Demonstrate the competing-read condition in an isolated test database.
        competing = sqlite3.connect(repo.database_path, timeout=0)
        try:
            with pytest.raises(sqlite3.OperationalError, match='locked'):
                competing.execute('SELECT * FROM versioned_voice_profiles').fetchall()
        finally:
            competing.close()
        def no_other_connection():
            pytest.fail('capture opened a competing connection')
        monkeypatch.setattr(repo, '_connect', no_other_connection)
        # ProfileRecordStore stores a bound factory; guard it separately.
        monkeypatch.setattr(repo.profile_records, '_connect', no_other_connection)
        observed = owner.capture_local(request, conn)
        assert observed(conn) == 'ready'
        # Neither frozen profile revision nor current grants/pronunciation may drift.
        if mutation == 'authorization':
            conn.execute("UPDATE media_authorizations SET payload=json_set(payload,'$.status','revoked')")
        elif mutation == 'fixed_revision':
            conn.execute("UPDATE versioned_voice_profiles SET payload=json_set(payload,'$.reference_text','changed')")
        else:
            import json
            from dataclasses import asdict
            word = PronunciationEntry('word', 'workspace_a', 'term', 'zh', 'sound', 'task', task_id, 'v1')
            conn.execute('INSERT INTO pronunciation_entries(workspace_id,id,version,payload,created_at) VALUES (?,?,?,?,?)',
                ('workspace_a', word.id, word.version, json.dumps(asdict(word)), datetime.now(timezone.utc).isoformat()))
        assert observed(conn) == verdict
        conn.rollback()
