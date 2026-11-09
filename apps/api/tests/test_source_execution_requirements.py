"""Task-specific route and definite pre-submit refusal, without media execution."""
from dataclasses import replace
from threading import Event
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.assistant.task_business import TaskBusiness
from app.assistant.task_business_adapter import TaskBusinessAdapter
from app.assistant.task_business_errors import TaskBusinessRejected
from app.creation.execution_control import CreationExecutionControl, task_execution_requirements
from app.device_preparation_application import DevicePreparationConflict
from test_source_task_use_case import source_case as source_case


CPU_SHAPE = dict(width_px=16, height_px=16, frame_rate=1, duration_seconds=1,
                 clip_count=1, audio_input_count=1, sample_rate=8000, channels=1)


def prepared_source(case):
    receipt = case.use_case.create(case.command, case.actor)
    task = case.repo.task_records.get_creation_task(case.actor.workspace_id, receipt['task_id'])
    plan = case.repo.plan_records.get_creation_plan(case.actor.workspace_id, task.plan_id)
    return task, plan


def database_snapshot(repo):
    with repo._connect() as conn:
        return list(conn.iterdump())


def test_source_edit_route_does_not_confuse_editor_stage_or_completed_output_with_pending_work(source_case, monkeypatch):
    case = source_case
    task, plan = prepared_source(case)
    before = database_snapshot(case.repo)
    control = CreationExecutionControl(case.repo, SimpleNamespace(latest=lambda *_: None), None, case.verify)
    expected = {'task_kind': 'source_video_edit', 'next_required_stage': 'render',
                'not_applicable_preparation_kinds': ['narration', 'montage']}
    assert task.stage == 'montage' and task.confirmed_script_id is None and task.narration_cache_key is None
    preview = control.preview(task.workspace_id, task.id)
    status = control.status_summary(task.workspace_id, task.id)
    assert preview['task_execution'] == status['task_execution'] == expected
    assert preview['requirements']['can_continue'] is True
    assert status['job'] is None and status['current_stage'] is None
    assert task_execution_requirements(replace(task, output_ready=True), plan)['next_required_stage'] is None
    assert task_execution_requirements(task, replace(plan, source_media=None)) == {
        'task_kind': 'scripted_creation', 'next_required_stage': None,
        'not_applicable_preparation_kinds': []}
    from app import main
    from app.creation.execution_capabilities import execution_capability_facts
    monkeypatch.setattr(main, 'production_repo', case.repo)
    monkeypatch.setattr(main, 'device_runtime', None)
    capabilities = main._task_execution_capability_facts(task)
    assert capabilities.pop('task_execution') == expected
    assert capabilities == execution_capability_facts(budget=None, registrations={})
    assert database_snapshot(case.repo) == before


def preparation_host(case, task, dispatch):
    business = object.__new__(TaskBusiness)
    business.production = case.repo
    business.device_preparation = dispatch
    # This test fixes the already-authorized task at the business boundary;
    # the actual source bootstrap above uses the isolated roster and SQL owner.
    def check(*args, **kwargs):
        assert case.verify(case.actor) == 'ready'
        return case.actor, task, {}, {'input_id': 'input'}, None
    business.check = check
    host = TaskBusinessAdapter(business)
    def invoke(operation, arguments):
        return host(operation, arguments, call_id='call', tool_call_id='tool',
                    source_binding={'turn_id': 'turn', 'input_id': 'input'},
                    cancel_event=Event(), deadline_monotonic=float('inf'))
    return business, invoke


@pytest.mark.parametrize('kind', ['narration', 'montage'])
def test_source_inapplicable_preparation_is_not_submitted_through_host_and_cpu_remains_available(source_case, kind):
    case = source_case
    task, _ = prepared_source(case)
    calls = []
    business, invoke = preparation_host(case, task, lambda action, **values:
        calls.append((action, values)) or {'id': 'original-work', 'state': 'queued'})
    before = database_snapshot(case.repo)
    shape = {'task_id': task.id} if kind == 'montage' else {
        'voice_profile_id': 'voice', 'voice_version': 'v1', 'text': 'Hello.',
        'language': 'en', 'output_duration_seconds': 1, 'pronunciation_references': []}
    rejected = invoke('prepare_device_resources', {'payload': {
        'request_id': str(uuid4()), 'kind': kind, 'shape': shape}})
    assert rejected['ok'] is False
    assert rejected['failure']['status_code'] == 409
    assert rejected['failure']['error'] == 'device_preparation_not_applicable'
    assert rejected['failure']['submission_effect'] == 'not_submitted'
    assert rejected['failure']['task_id'] == task.id
    assert calls == [] and database_snapshot(case.repo) == before
    result = invoke('prepare_device_resources', {'payload': {
        'request_id': str(uuid4()), 'kind': 'cpu', 'shape': CPU_SHAPE}})
    assert result['ok'] is True and calls[0][1]['kind'] == 'cpu'
    assert calls[0][1]['actor'] is case.actor
    for operation in ('read_device_preparation', 'finalize_device_preparation'):
        assert invoke(operation, {'request_id': 'original-work'})['ok'] is True
    assert [action for action, _ in calls] == ['prepare', 'status', 'finalize']
    assert database_snapshot(case.repo) == before
    def revoked(*args, **kwargs):
        raise TaskBusinessRejected('original source revoked')
    business.check = revoked
    assert invoke('prepare_device_resources', {'payload': {
        'request_id': str(uuid4()), 'kind': 'cpu', 'shape': CPU_SHAPE}})['failure']['status_code'] == 403
    assert len(calls) == 3


@pytest.mark.parametrize('error', [DevicePreparationConflict('original state unresolved'), RuntimeError('response unavailable')])
def test_dispatched_preparation_failure_cannot_claim_not_submitted(source_case, error):
    case = source_case
    task, _ = prepared_source(case)
    calls = []
    def dispatch(*args, **kwargs):
        calls.append(kwargs)
        raise error
    _, invoke = preparation_host(case, task, dispatch)
    result = invoke('prepare_device_resources', {'payload': {
        'request_id': str(uuid4()), 'kind': 'cpu', 'shape': CPU_SHAPE}})
    assert result['ok'] is False and len(calls) == 1
    assert result['failure']['status_code'] == (409 if isinstance(error, DevicePreparationConflict) else 503)
    assert result['failure'].get('submission_effect') != 'not_submitted'
