"""Production resume control and real queue/lineage with synthetic renderer I/O."""
from dataclasses import replace
from types import SimpleNamespace

import pytest

import app.main as main
from app.artifact_store import ArtifactStore
from app.audio_timeline import narration_audio_timeline
from app.creation.contracts import NarrationSegment, TaskEvent
from app.creation.execution_control import CreationExecutionControl, ExecutionControlRejected
from app.creation.plan_contracts import CreationPlanPresenterSelection
from app.creation_execution import CreationJobUnitExecutor, revision_digest, submit_creation_stage
from app.montage import creation_task_montage_plan_from_payload, creation_task_montage_plan_to_payload
from app.video_jobs import VideoJobRunner
from test_creation_execution import _render_version_fixture


@pytest.fixture
def failed_presenter(tmp_path, monkeypatch, request):
    repo, task_id, script_id, _, actor, execution, owner, scheduler, base, _ = _render_version_fixture(tmp_path)
    script = repo.script_records.get_creation_task_script('local', script_id)
    plan = repo.plan_records.get_creation_plan('local', 'plan_local')
    plan.presenter_share_target = 50
    plan.presenter_selection = CreationPlanPresenterSelection(
        script.id, script.updated_at, 'section_1', plan.selected_voice.profile_id, plan.selected_voice.version)
    with repo._connect() as conn:
        repo.plan_records.write_plan_payload(conn, 'local', plan)
    source_path = tmp_path / 'library' / 'source.mp4'
    presenter_path = tmp_path / 'library' / 'presenter.mp4'
    presenter_path.write_bytes(source_path.read_bytes())
    narration = NarrationSegment('fixture-narration', 'local', task_id, script_id, 'section_1',
        script.sections[0].content, 'narration://artifact/cache_1', 1, 24000, 'test', 'voice_1', 'v1', 'zh',
        'fixture', 'ready')
    repo.narration_records.save_narration_segments('local', task_id, script_id, [narration])
    montage = creation_task_montage_plan_from_payload(base.snapshot['montage'])
    montage.presenter_share_target = 50
    montage.cues[0].narration_segment_id = narration.id
    montage.audio_timeline = narration_audio_timeline(montage.narration_audio_path, 1)
    material = replace(montage.clips[0], source_end_seconds=.5, timeline_start_seconds=.5)
    presenter = replace(montage.clips[0], id='presenter_clip', asset_id='person:fixture-render',
        segment_id=None, source_path='person-render://fixture-render', person_render_id='fixture-render',
        source_end_seconds=.5, timeline_end_seconds=.5)
    montage.clips = [presenter, material]
    payload = creation_task_montage_plan_to_payload(montage)
    repo.montage_plan_adoption.save_creation_task_montage_plan('local', task_id, montage.id, payload)
    version = repo.timeline_records.create_timeline_version(replace(base, id='presenter-storyboard',
        parent_version_id=base.id, sequence=2, snapshot={'montage': payload}))
    # The person file is a fixture. Exercise real physical source binding and
    # failed/ready render lineage without running the person model.
    owner.presenter_models = {plan.lipsync_quality: 'fixture'}
    owner.presenter_budget = object()
    owner.presenter_cpu_render_limits = owner.cpu_render_limits
    owner.resolve_presenter_output = lambda _render: presenter_path
    monkeypatch.setattr(owner, 'person_valid', lambda _task, **_kwargs: True)
    monkeypatch.setattr('app.creation_execution.iter_presenter_inputs',
        lambda *args, **kwargs: [(narration, 'fixture-presenter-input')])
    monkeypatch.setattr('app.creation_execution.current_presenter_render',
        lambda *args, **kwargs: SimpleNamespace(id='fixture-render', duration_seconds=1))
    failure_reason = getattr(request, 'param', '进程组线程数 76 > 上限 20')
    mix_timeout = 120 if failure_reason == 'Command exceeded its unit deadline' else 20
    mix_policy = {'threads': 1, 'timeout_seconds': mix_timeout, 'source_duration_limit_seconds': 1,
                  'source_sample_rate_limit': 48000, 'source_channels_limit': 2}
    owner.audio_mix_facts = lambda *args, **kwargs: {'sources': [], 'resources': dict(mix_policy)}
    monkeypatch.setattr(main, 'artifact_store', ArtifactStore(tmp_path / 'artifacts'))
    monkeypatch.setattr(main, 'video_job_runner', VideoJobRunner(repo.job_records))
    monkeypatch.setattr(main, 'settings', SimpleNamespace(video_output_path=str(tmp_path / 'output'),
        video_inbox_path=str(tmp_path / 'library'), effective_temporary_root_path=tmp_path / 'temp',
        native_portrait_worker_path=None))
    render_stage = main._render_stage_use_case(repo)
    calls = []

    def synthesize(_workspace, current, _audio, *, job_staging_path, render_context, subtitle_tracks):
        render_context.check()
        calls.append(render_context.grant.request_id)
        if len(calls) == 1:
            raise OSError(failure_reason)
        (job_staging_path / 'preview.mp4').write_bytes(b'fixture-render-output')
        (job_staging_path / 'subtitles.srt').write_text('1\n00:00:00,000 --> 00:00:01,000\nfixture\n')
        return replace(current, status='rendered')

    monkeypatch.setattr(render_stage, 'render_montage', synthesize)
    monkeypatch.setattr('app.creation.render_stage_use_case._validate_rendered_media',
        lambda path, **kwargs: kwargs['scheduler'].assert_grant(kwargs['grant']))
    executor = CreationJobUnitExecutor(repo,
        render_action=lambda w, t, context: render_stage.render(w, t, render_context=context),
        render_is_valid=lambda w, t, context: render_stage.valid(w, t, verify_media=False))
    control = CreationExecutionControl(repo, execution, render_stage, lambda _: 'ready')
    runtime = SimpleNamespace(scheduler=scheduler,
        registrations={'creation_job': SimpleNamespace(owner=owner, executor=executor)}, wake=lambda: None)
    try:
        job = execution.start('local', task_id, script_id, revision_digest(script, plan), actor=actor)
        original_id = submit_creation_stage(execution, scheduler, job, actor)
        original = scheduler.pick_and_grant()
        assert original is not None and original.request_id == original_id
        executor.execute(original, scheduler)
        scheduler.assert_released(original)
        before = control.status('local', task_id, verify_media=False)
        assert before['job']['status'] == 'failed' and before['current_stage'] == 'render'
        assert failure_reason in before['job']['failure_reason'] and not before['video_ready']
        inner, = [row for row in repo.job_records.list_job_runs('local', task_id)
                  if row.kind == 'creation_task_montage_render']
        assert inner.status == 'failed' and inner.output_refs == []
        yield SimpleNamespace(repo=repo, task_id=task_id, actor=actor, job=job, inner=inner,
            version=version, scheduler=scheduler, original=original, executor=executor, control=control,
            runtime=runtime, owner=owner, mix_policy=mix_policy, calls=calls, source_path=source_path)
    finally:
        scheduler.close()


@pytest.mark.parametrize('new_policy', [False, True])
def test_presenter_resume_preserves_original_and_renders_new_inner_attempt(failed_presenter, new_policy):
    case = failed_presenter
    if new_policy:
        case.mix_policy.update(timeout_seconds=30, source_duration_limit_seconds=2)
    prior = case.scheduler._get(case.original.request_id)
    narration_before = case.repo.narration_units.creation_narration_usage('local', case.job.id)
    queued = case.control.resume('local', case.task_id, case.actor, case.runtime, verify_media=False)
    assert queued['job']['id'] == case.job.id and queued['job']['status'] == 'queued'
    assert case.control.resume('local', case.task_id, case.actor, case.runtime, verify_media=False)['job']['id'] == case.job.id
    attempt = case.scheduler.pick_and_grant()
    assert attempt is not None and attempt.owner_id == case.job.id
    assert attempt.request_id != prior.id and attempt.unit_key.endswith(':retry:' + prior.id)
    assert (attempt.unit_key.partition(':retry:')[0] != prior.unit_key) is new_policy
    assert case.scheduler._get(prior.id) == prior
    current = case.repo.task_records.get_creation_task('local', case.task_id)
    assert current.current_timeline_version_id == case.version.id
    case.executor.execute(attempt, case.scheduler)
    after = case.control.status('local', case.task_id, verify_media=False)
    assert after['job']['id'] == case.job.id and after['job']['status'] == 'succeeded' and after['video_ready']
    output = case.repo.task_records.get_creation_task('local', case.task_id)
    version = case.repo.timeline_records.get_timeline_version('local', output.current_timeline_version_id)
    assert version.parent_version_id == case.version.id
    assert version.snapshot['creation_render_request_id'] == attempt.request_id
    assert case.calls == [prior.id, attempt.request_id]
    old, new = [row for row in case.repo.job_records.list_job_runs('local', case.task_id)
                if row.kind == 'creation_task_montage_render']
    assert old == case.inner and new.status == 'succeeded'
    assert new.input_digest == old.input_digest and new.idempotency_key == old.idempotency_key + ':retry:1'
    assert case.repo.narration_units.creation_narration_usage('local', case.job.id) == narration_before


@pytest.mark.parametrize('invalid', ['unknown', 'inner_succeeded', 'changed_input', 'changed_timeline'])
def test_presenter_resume_refuses_unsettled_or_changed_attempt(failed_presenter, invalid):
    case = failed_presenter
    if invalid == 'changed_input':
        with case.source_path.open('ab') as source:
            source.write(b'changed')
    elif invalid == 'changed_timeline':
        case.repo.timeline_records.create_timeline_version(replace(case.version, id='another-storyboard',
            parent_version_id=case.version.id, sequence=case.version.sequence + 1))
    else:
        with case.repo._connect() as conn:
            if invalid == 'unknown':
                case.repo.execution_records.append_task_event(TaskEvent('unknown-render', 'local', case.task_id,
                    case.job.id, 'task_execution_unit_started', {'usage_id': 'unsettled-render'}), connection=conn)
            else:
                conn.execute("UPDATE job_runs SET status='succeeded' WHERE id=?", (case.inner.id,))
    with pytest.raises(ExecutionControlRejected) as reject:
        case.control.resume('local', case.task_id, case.actor, case.runtime, verify_media=False)
    assert reject.value.status_code == 409
    assert case.repo.job_records.get_job_run('local', case.job.id).status == 'failed'
    assert case.scheduler._get(case.original.request_id).state == 'failed'
    assert case.calls == [case.original.request_id]


@pytest.mark.parametrize('failed_presenter', ['Command exceeded its unit deadline'], indirect=True)
def test_presenter_timeout_retries_only_with_a_new_bound(failed_presenter):
    from app.host_resource_probes import Y4M_SERIAL_PICTURE_FORMAT

    case = failed_presenter
    prior = case.scheduler._get(case.original.request_id)
    with pytest.raises(ExecutionControlRejected, match='重新准备') as rejection:
        case.control.resume('local', case.task_id, case.actor, case.runtime, verify_media=False)
    assert rejection.value.status_code == 409
    assert case.scheduler._get(prior.id) == prior
    assert case.calls == [prior.id]

    # Increasing an audio cap already above the old outer cap buys no usable time.
    case.mix_policy['timeout_seconds'] += 1
    with pytest.raises(ExecutionControlRejected, match='重新准备'):
        case.control.resume('local', case.task_id, case.actor, case.runtime, verify_media=False)
    case.mix_policy['timeout_seconds'] -= 1
    limits = case.owner.presenter_cpu_render_limits
    case.owner.presenter_cpu_render_limits = replace(limits, serial_picture_format=Y4M_SERIAL_PICTURE_FORMAT)
    queued = case.control.resume('local', case.task_id, case.actor, case.runtime, verify_media=False)
    assert queued['job']['id'] == case.job.id and queued['job']['status'] == 'queued'
    attempt = case.scheduler.pick_and_grant()
    assert attempt is not None and attempt.unit_key.endswith(':retry:' + prior.id)
    actual = case.scheduler._get(attempt.request_id)
    assert 'picture_timeout_seconds' not in prior.resources
    assert actual.resources['picture_timeout_seconds'] == prior.resources['timeout_seconds'] == limits.timeout_seconds
    assert actual.resources['timeout_seconds'] == limits.timeout_seconds + case.mix_policy['timeout_seconds']
    assert actual.resources['audio_mix'] == prior.resources['audio_mix']
    assert case.scheduler._get(prior.id) == prior

    case.executor.execute(attempt, case.scheduler)
    assert case.control.status('local', case.task_id, verify_media=False)['video_ready']
    assert case.calls == [prior.id, attempt.request_id]
