from dataclasses import asdict, replace

import pytest

from app.creation.narration_pcm import narration_reference_facts
from app.creation_execution import next_creation_intent, submit_creation_stage
from app.host_resource_probes import narration_write_volumes_valid
from app.narration_resource_preparation import narration_preparation_shape_digest
from app.profiles.contracts import profile_cache_identity
from test_creation_execution import _narration_units_case, _segment_units_case, _submit_section_revision


def _prepared_case(tmp_path, *, changed=None, case=None):
    if case is None:
        case = _narration_units_case(tmp_path, local_policy=True, duration_seconds=900)
    task = case.repo.task_records.get_creation_task('local', case.task_id)
    plan = case.repo.plan_records.get_creation_plan('local', task.plan_id)
    inputs, reference, _ = narration_reference_facts(case.repo, task, plan)
    source = case.repo.creation_narration_source_binding('local', case.job.id).payload
    shape = dict(voice_identity=profile_cache_identity(inputs.voice), reference=asdict(reference),
        reference_sha256=source['sha256'], reference_text=inputs.voice.reference_text, language='zh',
        pronunciation_dictionary=())
    if changed == 'reference_text':
        shape['reference_text'] += '另一份参考文案'
    elif changed == 'language':
        shape['language'] = 'en'
    elif changed == 'dictionary':
        shape['pronunciation_dictionary'] = ({'id': 'different-entry', 'version': 'v1'},)
    elif changed == 'source_bytes':
        shape['reference_sha256'] = 'e' * 64
    budget = replace(case.owner.narration_budget, input_scope='single_call_prepared_v1',
        preparation_backend_identity='a' * 64,
        preparation_shape_digest=narration_preparation_shape_digest(**shape),
        preparation_receipt_sha256='d' * 64)
    paths = {'database': str(case.repo.database_path.parent),
        'temporary': str(tmp_path / 'configured-runtime-temporary'),
        'model_cache': str(tmp_path / 'configured-runtime-cache'), 'output': str(tmp_path)}
    case.owner.narration_budget = case.stage.narration_budget = budget
    case.owner.narration_runtime_facts = lambda ws, tid: {
        'backend_identity': ('b' if changed == 'backend' else 'a') * 64, 'write_paths': paths}
    if changed == 'missing_runtime':
        case.owner.narration_runtime_facts = None
    return case, budget, paths


def test_prepared_owner_uses_real_shape_and_single_unit_bounds_for_long_goal(tmp_path):
    case, budget, paths = _prepared_case(tmp_path)
    submit_creation_stage(case.execution, case.scheduler, case.job, case.actor)
    grant = case.scheduler.pick_and_grant()
    request = case.scheduler._get(grant.request_id)
    assert request.resources['input_scope'] == 'single_call_prepared_v1'
    assert request.resources['preparation_shape_digest'] == budget.preparation_shape_digest
    assert request.resources['duration_seconds'] == budget.max_duration_seconds < 900
    assert request.resources['write_volumes']['paths'] == paths
    assert narration_write_volumes_valid(request.resources, budget)
    case.executor.execute(grant, case.scheduler)
    assert len(case.generated) == 1
    usage = case.repo.creation_narration_usage('local', case.job.id)
    assert usage['used_output_bytes'] > 0
    assert usage['scope']['authorization']['scope_id'] == 'isolated-local-goal'
    following = case.scheduler.pick_and_grant()
    assert following is not None
    assert case.scheduler._get(following.request_id).resources['section_id'] == 'section_2'
    case.scheduler.close()


@pytest.mark.parametrize('changed', ['reference_text', 'language', 'dictionary', 'source_bytes',
                                    'backend', 'missing_runtime'])
def test_prepared_budget_cannot_self_certify_different_authorized_inputs(tmp_path, changed):
    case, _, _ = _prepared_case(tmp_path, changed=changed)
    with pytest.raises(ValueError, match='形状|后端|实际执行器'):
        next_creation_intent(case.repo, case.scheduler, case.job)
    assert not case.generated
    usage = case.repo.creation_narration_usage('local', case.job.id)
    assert usage['used_output_bytes'] == 0 and not usage['unsettled']
    case.scheduler.close()


def test_prepared_segment_revision_consumes_same_real_runtime_port(tmp_path, monkeypatch):
    case, budget, paths = _prepared_case(tmp_path, case=_segment_units_case(tmp_path, monkeypatch))
    case.segments.narration_runtime_facts = case.owner.narration_runtime_facts
    submit_creation_stage(case.execution, case.scheduler, case.job, case.actor)
    case.executor.execute(case.scheduler.pick_and_grant(), case.scheduler)
    case.executor.execute(case.scheduler.pick_and_grant(), case.scheduler)
    _, revision, _ = _submit_section_revision(case)
    grant = case.scheduler.pick_and_grant()
    request = case.scheduler._get(grant.request_id)
    assert request.owner_id == revision['job_id'] and request.unit_key.startswith('segment_revision:')
    assert request.resources['preparation_shape_digest'] == budget.preparation_shape_digest
    assert request.resources['write_volumes']['paths'] == paths
    assert narration_write_volumes_valid(request.resources, budget)
    case.executor.execute(grant, case.scheduler)
    assert len(case.generated) == 2
    following = case.scheduler.pick_and_grant()
    assert following is not None and following.unit_key.startswith('segment_video:section_1:')
    case.scheduler.close()
