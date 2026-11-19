import hashlib
import json
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
import wave

from fastapi.testclient import TestClient
import pytest

import app.main as main
from app.creation_execution import submit_creation_stage
from app.device_runtime import OwnerRegistration, build_device_runtime
from app.device_scheduler import RecoveryBlocked
from app.narration import VoicePreviewService
from app.profile_preview_execution import profile_preview_registration
from app.voice_clone import VoiceCloneResult
from _media_preview_test_fixtures import ControlledDeviceAccess, ControlledProcesses, ControlledResources
from test_creation_execution import _narration_units_case


@pytest.fixture
def audition(tmp_path, monkeypatch):
    case = _narration_units_case(tmp_path, start_job=False)
    repo = case.repo
    task = repo.task_records.get_creation_task('local', case.task_id)
    script = repo.script_records.get_creation_task_script('local', case.script_id)
    plan = repo.plan_records.get_creation_plan('local', task.plan_id)
    calls = []

    def generate(*, output_path, **options):
        calls.append(options['text'])
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(output_path), 'wb') as stream:
            stream.setparams((1, 2, 24000, 0, 'NONE', 'not compressed'))
            stream.writeframes(b'\x03\x00' * 153600)
        return VoiceCloneResult(str(output_path), 6.4, 24000)

    service = VoicePreviewService(tmp_path / 'previews-output', SimpleNamespace(
        ready=True, model_id=plan.selected_voice.model_id, supported_languages=('zh', 'en'),
        supports_pronunciation_dictionary=True, generate_granted=generate))
    case.scheduler.close()
    preview = profile_preview_registration(repo, voice_service=service, avatar_service=None,
        narration_budget=case.owner.narration_budget, presenter_budget=None,
        verify_identity=lambda actor: 'ready' if actor == case.actor else 'unauthorized')
    runtime = build_device_runtime(database_path=repo.database_path, device_lock_path=tmp_path / 'units.lock',
        device_access=ControlledDeviceAccess(), registrations={
            'media_preview': preview, 'creation_job': OwnerRegistration(case.owner, case.executor)},
        resources=ControlledResources(), processes=ControlledProcesses())
    actor = {'current': case.actor}
    monkeypatch.setattr(main, 'auth_context', SimpleNamespace(enabled=False))
    monkeypatch.setattr(main, 'production_repo', repo)
    monkeypatch.setattr(main, '_creation_runtime', lambda: runtime)
    monkeypatch.setattr(main, '_require_permit_actor', lambda *_: actor['current'])
    monkeypatch.setattr(main, '_content_identity_http_adapter', lambda: SimpleNamespace(
        reader=lambda *_: (actor['current'].member_id, False)))
    monkeypatch.setattr(main, '_exact_edit_access', lambda task_id, request, **kw: (
        repo.task_records.get_creation_task('local', task_id), actor['current'].member_id))
    monkeypatch.setattr(main, 'voice_preview_service', service)
    params = {'voice_profile_id': plan.selected_voice.profile_id, 'voice_version': plan.selected_voice.version,
        'language': 'zh', 'preview_text': script.sections[0].content, 'retry': False}
    client = TestClient(main.app)
    try:
        yield SimpleNamespace(case=case, repo=repo, plan=plan, script=script, client=client, calls=calls,
            service=service, params=params, runtime=runtime, actor=actor)
    finally:
        assert runtime.stop(1)


def route(s, index=0):
    return f'/api/creation-tasks/{s.case.task_id}/narration/sections/{s.script.sections[index].id}/audition'


def generate(s, *, saved=False, legacy=False):
    params = dict(s.params)
    if saved:
        params['saved_section'] = {'task_id': s.case.task_id, 'script_id': s.script.id, 'section_id': s.script.sections[0].id}
    response = s.client.post('/api/media/previews/voice', json=params)
    assert response.status_code == 200, response.text
    queued = response.json()
    assert queued['status'] == 'queued'
    assert s.runtime.run_once()
    with s.repo._connect() as conn:
        run = s.repo.preview_records.read('local', queued['id'], connection=conn)
        if legacy:
            row = conn.execute('SELECT payload FROM media_preview_attempts WHERE id=?', (run.id,)).fetchone()
            payload = json.loads(row['payload'])
            del payload['output_sha256']
            conn.execute('UPDATE media_preview_attempts SET payload=? WHERE id=?', (json.dumps(payload), run.id))
    assert run.status == 'succeeded', run
    return run


def selection_command(status, candidate=None, **changes):
    return {'request_id': str(uuid4()), 'preview_run_id': candidate['preview_run_id'] if candidate else None,
        'expected_output_sha256': candidate['output_sha256'] if candidate else None,
        'expected_script_id': status['script_id'], 'expected_script_updated_at': status['script_updated_at'],
        'expected_preview_run_id': status['selection']['preview_run_id'] if status['selection'] else None, **changes}


def test_legacy_private_preview_becomes_exact_production_pcm_once(audition):
    s = audition
    run = generate(s, legacy=True)
    status = s.client.get(route(s), params={'preview_run_id': run.id}).json()
    candidate, = status['candidates']
    assert status['available'] and status['preview_eligible']
    assert (candidate['sample_rate'], candidate['sample_count'], candidate['duration_seconds']) == (24000, 153600, 6.4)
    playback = s.client.get(candidate['output_url'])
    assert playback.status_code == 200 and hashlib.sha256(playback.content).hexdigest() == candidate['output_sha256']
    cmd = selection_command(status, candidate)
    selected = s.client.put(route(s), json=cmd)
    assert selected.status_code == 200, selected.text
    assert s.client.put(route(s), json=cmd).json() == selected.json()
    assert s.client.get(route(s, 1)).json()['candidates'] == []  # same text, different stable section
    assert len(s.calls) == 1
    segments = s.repo.narration_records.list_narration_segments('local', s.case.task_id, s.script.id)
    assert [(segment.status, segment.duration_seconds) for segment in segments] == [('ready', 6.4), ('pending', None)]
    assert s.repo.narration_artifacts.resolve_authorized_narration_audio_reference('local', segments[0].audio_reference).read_bytes() == playback.content
    snapshot = s.repo.execution_inputs.read_creation_execution_snapshot('local', s.case.task_id)
    job = s.case.execution.start('local', s.case.task_id, s.script.id, snapshot.input_digest, actor=s.case.actor)
    scheduler = s.runtime.scheduler
    submit_creation_stage(s.case.execution, scheduler, job, s.case.actor)
    grant = scheduler.pick_and_grant()
    unit = scheduler._get(grant.request_id)
    assert unit.resources['section_id'] == s.script.sections[1].id
    s.case.executor.execute(grant, scheduler)
    assert len(s.case.generated) == 1 and len(s.calls) == 1
    usage = s.repo.narration_units.creation_narration_usage('local', job.id)
    assert usage['used_output_bytes'] == 48044
    assert s.client.put(route(s), json=selection_command(selected.json())).status_code == 409
    verify = s.repo.narration_records.list_narration_segments('local', s.case.task_id, s.script.id)
    assert verify[0] == segments[0] and verify[1].status == 'ready'


def test_saved_scope_uses_current_text_after_adoption_and_can_clear_before_start(audition):
    s = audition
    with s.repo._connect() as conn:
        row = conn.execute('SELECT payload FROM creation_task_scripts WHERE id=?', (s.script.id,)).fetchone()
        header = json.loads(row['payload'])
        conn.execute('UPDATE creation_task_scripts SET payload=? WHERE id=?',
            (json.dumps({**header, 'selection_status': 'draft'}), s.script.id))
    run = generate(s, saved=True)
    update = (s.script.updated_at + timedelta(seconds=1)).isoformat()
    with s.repo._connect() as conn:
        conn.execute('UPDATE creation_task_scripts SET payload=?,updated_at=? WHERE id=?',
            (json.dumps({**header, 'updated_at': update}), update, s.script.id))
    status = s.client.get(route(s)).json()
    assert status['script_updated_at'] == update
    candidate, = status['candidates']
    assert candidate['preview_run_id'] == run.id
    stale = s.client.put(route(s), json=selection_command(status, candidate, expected_script_updated_at=s.script.updated_at.isoformat()))
    assert stale.status_code == 409
    selected = s.client.put(route(s), json=selection_command(status, candidate))
    assert selected.status_code == 200
    assert s.client.put(route(s), json=selection_command(status)).status_code == 409
    removed = s.client.put(route(s), json=selection_command(selected.json()))
    assert removed.status_code == 200 and removed.json()['selection'] is None
    segments = s.repo.narration_records.list_narration_segments('local', s.case.task_id, s.script.id)
    assert all(segment.status == 'pending' for segment in segments)
    assert Path(run.output_path).is_file()


def test_another_owner_and_changed_pcm_cannot_replace_the_heard_attempt(audition):
    from app.creation_execution import CreationExecutionActor

    s = audition
    run = generate(s)
    state = s.client.get(route(s)).json()
    candidate, = state['candidates']
    cmd = selection_command(state, candidate)
    s.actor['current'] = CreationExecutionActor('local', 'other-member')
    assert s.client.get(route(s)).status_code == 404
    assert s.client.get(candidate['output_url']).status_code == 404
    assert s.client.put(route(s), json=cmd).status_code == 404
    s.actor['current'] = s.case.actor
    path = Path(run.output_path)
    data = path.read_bytes()
    path.write_bytes(data[:-1] + bytes([data[-1] ^ 1]))
    assert s.client.get(candidate['output_url']).status_code == 409
    assert s.client.put(route(s), json=cmd).status_code == 409
    assert s.client.get(route(s)).json()['candidates'] == []
    assert not s.repo.narration_records.list_narration_segments('local', s.case.task_id, s.script.id)


def test_revoked_voice_withdraws_playback_and_refuses_adoption(audition):
    s = audition
    run = generate(s)
    state = s.client.get(route(s)).json()
    candidate, = state['candidates']
    authorization = s.repo.get_authorization('local', run.authorization_id, run.authorization_version)
    s.repo.save_authorization(replace(authorization, version='withdrawn', status='revoked'))
    unavailable = s.client.get(route(s)).json()
    assert not unavailable['available'] and unavailable['candidates'] == []
    assert s.client.get(candidate['output_url']).status_code == 403
    assert s.client.put(route(s), json=selection_command(state, candidate)).status_code == 403
    assert not s.repo.narration_records.list_narration_segments('local', s.case.task_id, s.script.id)


def test_revocation_after_adoption_stops_formal_narration(audition):
    s = audition
    run = generate(s, saved=True)
    state = s.client.get(route(s)).json()
    candidate, = state['candidates']
    assert s.client.put(route(s), json=selection_command(state, candidate)).status_code == 200
    snapshot = s.repo.execution_inputs.read_creation_execution_snapshot('local', s.case.task_id)
    job = s.case.execution.start('local', s.case.task_id, s.script.id, snapshot.input_digest, actor=s.case.actor)
    authorization = s.repo.get_authorization('local', run.authorization_id, run.authorization_version)
    s.repo.save_authorization(replace(authorization, version='withdrawn', status='revoked'))

    with pytest.raises(RecoveryBlocked, match='声音参考的使用限制或当前读取权限已变化'):
        submit_creation_stage(s.case.execution, s.runtime.scheduler, job, s.case.actor)
    with s.repo._connect() as conn:
        assert conn.execute('SELECT 1 FROM device_work_requests WHERE owner_id=?', (job.id,)).fetchone() is None
    assert not s.case.generated
