"""Persistent media receipts without a device driver; declarations are not worker evidence."""
import hashlib
import json
import sqlite3
import subprocess
import wave
from dataclasses import replace
from uuid import uuid4
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request
from pydantic import ValidationError
from PIL import Image

import app.main as main
from app.artifact_store import ArtifactStore
from app.auth import SupabaseAuthContext
from app.config import Settings
from app.conversation_repository import ConversationRepository
from app.creation.contracts import ArtifactRecord, CreationTask, JobRun, TaskEvent
from app.creation.plan_contracts import CreationPlan
from app.creation.task_authorization import TaskAuthorizationUseCase
from app.creation_execution import CreationExecutionActor, CreationJobOwnerVerifier, CreationJobUnitExecutor
from app.device_runtime import DeviceRuntime, OwnerRouter, OwnerRegistration
from app.device_scheduler import DeviceRequestReadAccess, DeviceScheduler
from app.host_resource_probes import CpuAudioBudget
from app.local_members import LocalMemberService
from app.member_permits import MemberDeviceAccess
from app.models import utc_now
from app.production import ProductionRepository
from app.task_audio import TASK_MEDIA_KIND, media_digest


def test_cold_media_result_reads_preserve_currentness_privacy_and_history(tmp_path, monkeypatch):
    violations = []

    def forbidden(*args, **kwargs):
        violations.append('execution-or-media')
        pytest.fail('receipt reads must not construct or start execution, probe or decode media')

    # AnyIO transport threads are allowed; actual device/authorization drivers are forbidden.
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(wave, 'open', forbidden)
    monkeypatch.setattr(Image, 'open', forbidden)
    monkeypatch.setattr(Image, 'new', forbidden)
    monkeypatch.setattr(Image.Image, 'save', forbidden)
    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', forbidden)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, 'handle_async_request', forbidden)
    for name in ('__init__', 'start', 'run_once', 'wake'):
        monkeypatch.setattr(DeviceRuntime, name, forbidden)
    monkeypatch.setattr(TaskAuthorizationUseCase, 'start', forbidden)
    monkeypatch.setattr(DeviceScheduler, '__init__', forbidden)
    for name in ('_creation_runtime', '_build_device_components', 'recover_interrupted_quality_checks',
                 '_start_device_runtime', '_wake_assistant_continuations', 'run_task_turn', 'run_plan_turn'):
        monkeypatch.setattr(main, name, forbidden)
    settings = Settings(database_path=str(tmp_path / 'studio.sqlite3'),
        video_inbox_path=str(tmp_path / 'library'), video_output_path=str(tmp_path / 'output'),
        model_root_path=str(tmp_path / 'models'), temporary_root_path=str(tmp_path / 'temporary'),
        draft_output_path=str(tmp_path / 'drafts'), jianying_draft_root_path=str(tmp_path / 'jianying'),
        obsidian_vault_path=None, hermes_radar_path=None, local_whisper_python_path=None,
        local_whisper_cli_path=None, local_whisper_model_path=None, model_runtime_python_path=None,
        native_portrait_worker_path=None, supabase_url=None, supabase_publishable_key=None)
    monkeypatch.setattr(main, 'settings', settings)
    monkeypatch.setattr(main, 'auth_context', SupabaseAuthContext(settings))
    monkeypatch.setattr(main, 'device_runtime', None)
    monkeypatch.setattr(main, 'automation_runtime', None)
    # Explicit pure configuration input, not a replacement for facts, seal or authorization.
    audio_budget = CpuAudioBudget(2, 65536, 4., 4., 48000, 2, 65536, 1, 3.,
        64 * 1024**2, 16, .1, 64 * 1024**2, 65536)
    monkeypatch.setattr(main, '_device_resource_profile',
        lambda: SimpleNamespace(budget=SimpleNamespace(cpu_audio=audio_budget)))
    repository = ProductionRepository(tmp_path / 'studio.sqlite3', tmp_path / 'library')
    members = LocalMemberService(repository.database_path)
    member, token = members.whoami(None)
    other, other_token = members.join(members.create_invite_code(), 'Other reader')
    conversations = ConversationRepository(repository.database_path)
    store = ArtifactStore(tmp_path / 'output')
    for name, value in (('production_repo', repository), ('local_members_service', members),
                        ('conversation_repo', conversations), ('artifact_store', store)):
        monkeypatch.setattr(main, name, value)
    plan = CreationPlan(id='plan', workspace_id='local', goal='Read original receipts', name='Receipt metadata',
        target_count=1, language='zh-CN', aspect_ratio='9:16', duration_target_seconds=4,
        knowledge_references=[], source_references=[], selected_voice=None, pronunciation_references=[],
        approved_asset_count=0, approved_clip_count=0, material_rule_summary='', model_snapshot=[],
        confirmation_points=[], blockers=[], preflight_fingerprint='declared-receipt')
    task = CreationTask('task', 'local', plan.id, 'Receipt metadata', 'knowledge_explanation')
    _, task = repository.plan_confirmation.confirm_creation_plan(plan, task,
        verified_owner_user_id=member.member_id)
    _, second_task = repository.plan_confirmation.confirm_creation_plan(
        replace(plan, id='second-plan', creation_task_id=None),
        replace(task, id='second-task', plan_id='second-plan'), verified_owner_user_id=member.member_id)
    actor = CreationExecutionActor('local', member.member_id)
    authorizations = main._task_authorizations_for_goal()
    authorizations.ensure(task, actor)
    staged = settings.effective_temporary_root_path / 'declared-input.bin'
    staged.parent.mkdir(parents=True)
    staged.write_bytes(b'opaque staged input; metadata seal only')
    turn_id, conversation_id = str(uuid4()), str(uuid4())
    arguments = {'audio': {'staged_input': staged.name}, 'payload': {},
        '_typed_source': {'assistant_turn_id': turn_id, 'conversation_id': conversation_id}}
    # recording_save seal only stats the bounded staged input; it never decodes it.
    facts, resources = main._task_media_facts('local', task.id, 'recording_save', arguments, actor,
        task_authorizations=authorizations)
    declaration = {'operation': 'recording_save', 'arguments': arguments, 'result_kind': 'recording',
                   'input_facts': facts, 'resources': resources, **actor.identity_payload()}
    digest = media_digest(declaration)

    def declared_job(job_id):
        job = repository.job_records.create_job_run(JobRun(job_id, 'local', task.id, TASK_MEDIA_KIND,
            'declared:' + job_id, digest))
        repository.execution_records.append_task_event(TaskEvent('event_' + job.id + '_start',
            'local', task.id, job.id, 'task_media_submitted', {**declaration, 'input_digest': digest}))
        return job

    job = declared_job('pending-job')
    now = utc_now()
    with repository._connect() as connection:
        connection.execute('''INSERT INTO device_work_requests
            (id,workspace_id,member_id,owner_type,owner_id,input_digest,unit_key,source_refs_json,
             work_class,policy,first_ready_at_utc,state,wait_reason,resources_json,created_at,updated_at)
             VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            ('declared-request', 'local', member.member_id, 'creation_job', job.id, digest,
             'task_media:' + digest, '[]', 'preview_dependency', 'asap', now.isoformat(),
             'blocked', 'resource_busy', json.dumps(resources), now.isoformat(), now.isoformat()))
    media, requests = main._media_result_read_components()
    request = requests._get('declared-request')
    with repository._connect() as connection:
        media.record_admission(connection, request, SimpleNamespace(verdict=False, declared=resources,
            checks={'swap_used_bytes': {'actual': 20, 'maximum': 10, 'passed': False}},
            observed_interval_seconds=.1), now)
    # Reopen all read-side facts; no schema/runtime constructor is used to obtain request views.
    repository = ProductionRepository(repository.database_path, repository.video_library_path)
    monkeypatch.setattr(main, 'production_repo', repository)
    media, requests = main._media_result_read_components()
    assert media.task_authorizations is not None
    assert main._media_script_use_case(task_id=task.id,
        task_authorizations=media.task_authorizations).recipe_tools.task_authorizations is media.task_authorizations

    def snapshot():
        databases = {}
        for path in tmp_path.rglob('*'):
            if path.is_file() and path.suffix in {'.sqlite', '.sqlite3', '.db'}:
                with sqlite3.connect(f'file:{path}?mode=ro', uri=True) as connection:
                    tables = [row[0] for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
                    databases[str(path.relative_to(tmp_path))] = {
                        name: sorted(connection.execute('SELECT * FROM "' + name.replace('"', '""') + '"').fetchall(), key=repr)
                        for name in tables}
        return databases, {str(path.relative_to(tmp_path)): path.read_bytes() if path.is_file() else None
                           for path in tmp_path.rglob('*')}

    before = snapshot()
    missing = tmp_path / 'never-created.sqlite'
    with pytest.raises(sqlite3.OperationalError):
        DeviceRequestReadAccess(missing, owners=requests.owners)._get('missing')
    assert not missing.exists() and snapshot() == before
    client = TestClient(main.app, headers={'X-Local-Member-Token': token})  # No lifespan.
    url = '/api/creation-tasks/task/media-jobs/' + job.id
    pending = client.get(url)
    assert pending.status_code == 200, pending.text
    pending = pending.json()
    assert pending['status'] == 'queued' and pending['device_request_id'] == request.id
    assert pending['resource_wait']['context'] == 'pending_request'
    assert client.get('/api/creation-tasks/task/media-jobs').json()['items'] == [pending]
    assert main._goal_media_metadata(task, actor, job.id) == pending
    related_request = Request({'type': 'http', 'app': main.app, 'method': 'GET', 'path': url, 'query_string': b'',
        'headers': [(b'x-local-member-token', token.encode())]})
    related = main._related_media_result(task.id, related_request, turn_id, conversation_id, pending)
    assert related.model_dump(mode='json') == pending
    with pytest.raises(main.HTTPException) as unrelated:
        main._related_media_result(task.id, related_request, str(uuid4()), conversation_id, pending)
    assert unrelated.value.status_code == 502
    with monkeypatch.context() as schema_gate:
        schema_gate.setattr(main, '_media_result_read_components', forbidden)
        with pytest.raises(ValidationError):
            main._related_media_result(task.id, related_request, turn_id, conversation_id, {})
    assert client.get(url, headers={'X-Local-Member-Token': other_token}).status_code == 404
    assert client.get(url.replace('/task/', '/second-task/')).status_code == 404
    assert client.get('/api/creation-tasks/second-task/media-jobs?cursor=' + job.id).status_code == 404
    assert snapshot() == before
    # Shared policy: the writer router snapshots the mapping, then resolves its current owner.
    access = MemberDeviceAccess(members, identity_verifier=main._verify_creation_identity)
    owner = CreationJobOwnerVerifier(repository, verify_identity=main._verify_creation_identity, task_media=media)
    executor = CreationJobUnitExecutor(repository, render_action=forbidden, render_is_valid=forbidden, task_media=media)
    registration = OwnerRegistration(owner, executor)
    declared_registrations = {'creation_job': registration}
    router = OwnerRouter(declared_registrations, access)
    declared_registrations.clear()
    assert router.can_view_detail(request, actor) is True
    router.registrations.clear()
    assert router.can_view_detail(request, actor) is False
    router.registrations['creation_job'] = registration
    assert router.can_view_detail(request, actor) is True
    summary_requests = DeviceRequestReadAccess(repository.database_path, owners=router)
    with monkeypatch.context() as summary:
        # Runtime registration remains present; only the independent permission router lacks the owner.
        summary.setattr(main, 'device_runtime', SimpleNamespace(registrations={'creation_job': registration},
            scheduler=summary_requests, last_error=None, running=False, start=forbidden,
            task_authorizations=media.task_authorizations))
        router.registrations.clear()
        assert summary_requests.task_media_result_request('local', job.id, digest, actor) == (None, {})
        redacted = client.get(url).json()
        assert redacted['device_request_id'] is None and redacted['resource_wait'] is None
        assert request.id not in str(redacted)
        assert snapshot() == before
        router.registrations['creation_job'] = registration
    # Controlled healthy existing binding: exact object reuse, with no start of the stopped driver.
    with monkeypatch.context() as warm:
        warm.setattr(main, 'device_runtime', SimpleNamespace(registrations={'creation_job': registration},
            scheduler=requests, last_error=None, running=False, start=forbidden,
            task_authorizations=media.task_authorizations))
        assert main._media_result_read_components() == (media, requests)
        assert client.get(url).json() == pending
    for registrations, error in (({'creation_job': registration}, RuntimeError('declared stopped driver')),
                                 ({}, None)):
        with monkeypatch.context() as unavailable:
            unavailable.setattr(main, 'device_runtime', SimpleNamespace(registrations=registrations,
                scheduler=requests, last_error=error, running=False, start=forbidden))
            response = client.get(url)
            assert response.status_code == 503
            assert response.json()['detail'] == '整机制作许可已停止，须核查许可后才能继续'
            assert snapshot() == before
    assert snapshot() == before

    repository.job_records.finish_job_run('local', job.id, status='cancelled', failure_reason='declared-cancel',
        impact='Declared historical cancellation; current timeline unchanged', repair_action='Read the original request')
    before = snapshot()
    terminal = client.get(url).json()
    assert terminal['status'] == 'cancelled' and terminal['resource_wait']['context'] == 'history'
    assert terminal['resource_wait']['observation_id'] == pending['resource_wait']['observation_id']
    assert snapshot() == before

    # Declare an existing successful metadata result, not a real recording or worker completion.
    success = declared_job('saved-job')
    repository.job_records.claim_job_run('local', success.id)
    reference = 'artifacts/task/declared-output.bin'
    store.publish_bytes(reference, b'declared opaque output')
    output = store.resolve_file(reference)
    info = output.stat()
    repository.execution_records.create_artifact_record(ArtifactRecord('declared-artifact', 'local', task.id,
        'recording', reference, hashlib.sha256(output.read_bytes()).hexdigest(), info.st_size, success.id, []))
    repository.execution_records.append_task_event(TaskEvent('event_' + success.id + '_result', 'local', task.id,
        success.id, 'task_media_result', {'result': {'reference': reference}}))
    repository.execution_records.append_task_event(TaskEvent('event_' + success.id + '_verified', 'local', task.id,
        success.id, 'task_media_verified', {'outputs': [{'reference': reference, 'size_bytes': info.st_size,
            'mtime_ns': info.st_mtime_ns, 'ctime_ns': info.st_ctime_ns}]}))
    repository.job_records.finish_job_run('local', success.id, status='succeeded', output_refs=[reference])
    success_url = '/api/creation-tasks/task/media-jobs/' + success.id
    before = snapshot()
    saved = client.get(success_url).json()
    assert saved['status'] == 'succeeded' and saved['result'] == {'reference': reference}
    assert snapshot() == before
    output.unlink()
    before = snapshot()
    invalid = client.get(success_url).json()
    assert invalid['status'] == 'failed' and invalid['failure_reason'] == 'result_invalid' and invalid['result'] is None
    assert repository.job_records.get_job_run('local', success.id).status == 'succeeded'
    assert snapshot() == before
    staged.write_bytes(b'changed declared source')
    before = snapshot()
    assert client.get(success_url).json()['status'] == 'stale'
    assert client.get(url).json()['status'] == 'cancelled'
    assert snapshot() == before
    assert main.device_runtime is None and violations == []
    client.close()
