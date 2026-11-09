"""Real PNG registrations and resource decisions; no media execution or OS calibration."""
from test_host_resource_probes import render_metadata_host as render_metadata_host
from dataclasses import asdict, replace
from functools import partial
from types import SimpleNamespace
import io
import json

from PIL import Image
import pytest

import app.main as main
import app.member_permits as permits
from app.artifact_store import ArtifactStore
from app.creation_execution import CreationExecutionActor, cpu_render_facts
from app.graphic_overlays import ArrowOverlay, ImageOverlay, TextOverlay
from app.graphic_sources import graphic_source_facts, import_graphic_source
from app.local_members import LocalMemberService
from app.montage import creation_task_montage_plan_from_payload, creation_task_montage_plan_to_payload
from test_creation_resource_contract import _render_ready_state, _render_budget, _probe, _verdict
from test_media_script_api import state as script_state, submit  # noqa: F401
from test_material_edit_qualification import material_edit_state as material_edit_state  # noqa: F401
from test_montage_pipeline import person_render_edit_state as person_render_edit_state  # noqa: F401


@pytest.fixture
def state(tmp_path):
    repo, task, plan, limits = _render_ready_state(tmp_path)
    member, _token = LocalMemberService(repo.database_path).whoami(None)
    task.owner_user_id, task.visibility = member.member_id, 'private'
    with repo._connect() as conn:
        conn.execute('UPDATE creation_tasks SET payload=? WHERE workspace_id=? AND id=?',
            (json.dumps(asdict(task), default=str), task.workspace_id, task.id))
    store = ArtifactStore(tmp_path / 'artifact-store')
    content = io.BytesIO()
    Image.new('RGBA', (16, 12), (255, 0, 0, 128)).save(content, format='PNG')
    source = import_graphic_source(repo, store, tmp_path / 'scratch', task.workspace_id, task.id,
        SimpleNamespace(file=io.BytesIO(content.getvalue()), filename='qa.png'))
    actor = CreationExecutionActor(task.workspace_id, member.member_id)
    resolver = partial(graphic_source_facts, repo, store)
    montage = creation_task_montage_plan_from_payload(repo.get_creation_task_montage_plan(task.workspace_id, task.id))
    montage.graphic_overlays = [ImageOverlay(id=f'image-{index}', name='qa.png', source_ref=source['source_ref'],
        start_seconds=0, end_seconds=1).model_dump() for index in (1, 2)]
    repo.montage_plan_adoption.save_creation_task_montage_plan(task.workspace_id, task.id, montage.id, creation_task_montage_plan_to_payload(montage))
    return SimpleNamespace(repo=repo, task=repo.task_records.get_creation_task(task.workspace_id, task.id), plan=plan, limits=limits,
        store=store, source=source, actor=actor, resolver=resolver, montage=montage)


def facts(state, **changes):
    with state.repo._connect() as conn:
        return cpu_render_facts(state.repo, conn, state.task, state.plan, state.limits,
            **{'actor': state.actor, 'graphic_source_facts': state.resolver, **changes})


def test_normal_image_render_stat_facts_deduplicate_bytes_and_real_probe_counts(state, tmp_path, monkeypatch, render_metadata_host):
    png = state.resolver(state.task.workspace_id, state.task.id, state.source['source_ref'], actor=state.actor)
    assert (png['width'], png['height']) == (16, 12)
    monkeypatch.setattr(state.store, 'describe', lambda *_: pytest.fail('admission must not hash content'))
    monkeypatch.setattr(Image, 'open', lambda *_: pytest.fail('admission must not decode PNG'))
    registration = permits.creation_job_registration(state.repo, cpu_render_budget=_render_budget(),
        render_action=lambda *_: pytest.fail('facts must not render'), render_is_valid=lambda *_: False,
        graphic_source_facts=state.resolver)
    with state.repo._connect() as conn:
        key, resources = registration.owner.render_facts(conn, state.task, state.plan, actor=state.actor)
    assert key.startswith('render:') and resources['overlay_count'] == 2
    # The two image layers share one actual source file.
    assert resources['input_bytes'] == 512 + 4096 + png['size_bytes']
    assert _verdict(_probe(tmp_path, max_overlay_count=2), resources) is True
    assert _verdict(_probe(tmp_path, max_overlay_count=0), resources) is False
    from app.creation.render_stage_use_case import render_input_refs
    refs = render_input_refs(state.task, state.montage)
    assert refs.count(png['source_ref']) == 1
    transform = state.repo.lineage_records.list_transform_runs(state.task.workspace_id, state.task.id)[0]
    with state.repo._connect() as conn:
        conn.execute('UPDATE transform_runs SET parameters_json=? WHERE id=?',
            (json.dumps({**transform.parameters, 'width': 32}), transform.id))
    changed_key, _ = facts(state)
    assert changed_key != key  # Registered dimensions remain part of the sealed render identity.


@pytest.mark.parametrize('invalid', ['no_actor', 'other_member', 'other_workspace', 'other_task', 'revision', 'failed', 'dimensions'])
def test_png_facts_refuse_unknown_or_foreign_provenance(state, invalid):
    actor, task_id = state.actor, state.task.id
    if invalid == 'no_actor':
        actor = None
    elif invalid == 'other_member':
        actor = replace(actor, member_id='another-member')
    elif invalid == 'other_workspace':
        actor = replace(actor, workspace_id='other-workspace')
    elif invalid == 'other_task':
        task_id = 'missing-task'
    else:
        record = state.repo.execution_records.get_artifact_record(state.task.workspace_id, state.source['artifact_id'])
        if invalid == 'revision':
            path = state.store.resolve_file(record.logical_ref)
            path.write_bytes(path.read_bytes())  # Same bytes/length, different actual ctime.
        elif invalid == 'failed':
            with state.repo._connect() as conn:
                conn.execute("UPDATE job_runs SET status='failed' WHERE id=?", (record.producer_job_id,))
        else:
            transform = state.repo.lineage_records.list_transform_runs(state.task.workspace_id, state.task.id)[0]
            with state.repo._connect() as conn:
                conn.execute('UPDATE transform_runs SET parameters_json=? WHERE id=?',
                    (json.dumps({**transform.parameters, 'width': None}), transform.id))
    with pytest.raises((ValueError, PermissionError, KeyError)):
        state.resolver(state.task.workspace_id, task_id, state.source['source_ref'], actor=actor)


def test_text_render_uses_existing_overlay_budget_and_binds_text_values(state, tmp_path, render_metadata_host):
    state.montage.graphic_overlays = [TextOverlay(text='公开素材演示', start_seconds=0, end_seconds=1).model_dump()]
    state.repo.montage_plan_adoption.save_creation_task_montage_plan(state.task.workspace_id, state.task.id, state.montage.id,
        creation_task_montage_plan_to_payload(state.montage))
    before, resources = facts(state, graphic_source_facts=None)
    assert resources['overlay_count'] == 1 and resources['input_bytes'] == 512 + 4096
    assert _verdict(_probe(tmp_path, max_overlay_count=0), resources) is False
    state.montage.graphic_overlays[0]['text'] = '不同的画面文字'
    state.repo.montage_plan_adoption.save_creation_task_montage_plan(state.task.workspace_id, state.task.id, state.montage.id,
        creation_task_montage_plan_to_payload(state.montage))
    after, changed = facts(state, graphic_source_facts=None)
    assert after != before and changed == resources


def test_text_does_not_weaken_mixed_png_source_authority(state):
    state.montage.graphic_overlays[1] = TextOverlay(text='公开素材演示', start_seconds=0, end_seconds=1).model_dump()
    state.repo.montage_plan_adoption.save_creation_task_montage_plan(state.task.workspace_id, state.task.id, state.montage.id,
        creation_task_montage_plan_to_payload(state.montage))
    _, resources = facts(state)
    png = state.resolver(state.task.workspace_id, state.task.id, state.source['source_ref'], actor=state.actor)
    assert resources['overlay_count'] == 2 and resources['input_bytes'] == 512 + 4096 + png['size_bytes']
    with pytest.raises(PermissionError):
        facts(state, actor=None)


@pytest.mark.parametrize('unsupported', ['arrow', 'audio', 'actor'])
def test_normal_render_keeps_unsupported_paths_blocked(state, unsupported):
    if unsupported == 'actor':
        with pytest.raises(PermissionError):
            facts(state, actor=None)
        return
    if unsupported == 'arrow':
        state.montage.graphic_overlays = [ArrowOverlay(name='unsupported', x=.5, y=.5, start_seconds=0, end_seconds=1).model_dump()]
    else:
        state.montage.audio_timeline = main.narration_audio_timeline(state.montage.narration_audio_path, state.montage.duration_seconds)
    state.repo.montage_plan_adoption.save_creation_task_montage_plan(state.task.workspace_id, state.task.id, state.montage.id,
        creation_task_montage_plan_to_payload(state.montage))
    with pytest.raises(PermissionError if unsupported == 'audio' else ValueError,
        match='已许可来源解析器' if unsupported == 'audio' else '仅支持|音轨组合'):
        facts(state)


def test_grant_verifies_actual_png_digest_and_dimensions_before_renderer(tmp_path, monkeypatch):
    import time
    from test_task_media_grants import case
    from app.creation_execution import CpuRenderGrantContext
    repo, actor, _, _, build, media = case(tmp_path)
    members = LocalMemberService(repo.database_path)
    task = repo.task_records.get_creation_task('local', 'task')
    task.owner_user_id = actor.member_id
    with repo._connect() as conn:
        conn.execute('UPDATE creation_tasks SET payload=? WHERE id=?', (json.dumps(asdict(task), default=str), task.id))
    store = ArtifactStore(tmp_path/'png-artifacts')
    content = io.BytesIO()
    Image.new('RGBA', (16, 12), (255, 0, 0, 128)).save(content, format='PNG')
    source = import_graphic_source(repo, store, tmp_path/'scratch', 'local', task.id,
        SimpleNamespace(file=io.BytesIO(content.getvalue()), filename='qa.png'))
    overlays = [ImageOverlay(id='png', name='qa.png', source_ref=source['source_ref'], start_seconds=0, end_seconds=1).model_dump()]
    original_facts = media.facts
    def facts(*args):
        identity, resources = original_facts(*args)
        path = store.resolve_file(source['source_ref'])
        resources.update(kind='creation_render', input_count=1, input_bytes=path.stat().st_size,
            inputs=[{'path': str(path), 'duration_seconds': None, 'sample_rate': None, 'channels': None}])
        return identity, resources
    media.facts = facts
    runtime = build(media)
    access = permits.MemberDeviceAccess(members)
    monkeypatch.setattr(main, 'production_repo', repo)
    monkeypatch.setattr(main, 'artifact_store', store)
    monkeypatch.setattr(main, 'device_runtime', None)
    monkeypatch.setattr(main, 'local_members_service', members)
    monkeypatch.setattr(main, '_verify_creation_identity', access.verify_identity)
    subject = main._render_stage_use_case(repo)
    job = media.submit(runtime, actor, task.id, 'synthetic_media', {}, 'audio_listen')
    grant = runtime.scheduler.pick_and_grant()
    assert grant is not None and grant.owner_id == job.id
    repo.job_records.claim_job_run('local', job.id)
    context = CpuRenderGrantContext(runtime.scheduler, grant, time.monotonic()+30, 65536, 1)
    try:
        with pytest.raises(ValueError, match='设备许可'):
            subject.graphic_source_paths('local', task.id, overlays, None)
        paths = subject.graphic_source_paths('local', task.id, overlays, context)
        assert list(paths) == [source['source_ref']]
        runtime.scheduler.assert_grant(grant)
        original_describe = store.describe
        monkeypatch.setattr(store, 'describe', lambda ref: replace(original_describe(ref), sha256='0'*64))
        with pytest.raises(ValueError, match='内容摘要'):
            subject.graphic_source_paths('local', task.id, overlays, context)
        monkeypatch.setattr(store, 'describe', original_describe)
        before = graphic_source_facts(repo, store, 'local', task.id, source['source_ref'], actor=actor)
        transform = repo.lineage_records.list_transform_runs('local', task.id)[0]
        with repo._connect() as conn:
            conn.execute('UPDATE transform_runs SET parameters_json=? WHERE id=?',
                (json.dumps({**transform.parameters, 'width': 32}), transform.id))
        after = graphic_source_facts(repo, store, 'local', task.id, source['source_ref'], actor=actor)
        assert after['width'] != before['width']
        with pytest.raises(ValueError, match='尺寸'):
            subject.graphic_source_paths('local', task.id, overlays, context)
    finally:
        def finish(conn, current):
            repo.job_records.finish_job_run('local', job.id, status='failed', failure_reason='fixture_complete',
                impact='Synthetic PNG verification has ended.', repair_action='No product work to resume.', connection=conn)
            return 'png-verification-fixture-complete'
        runtime.scheduler.fail_unit(grant, reason='fixture_complete', failure_write=finish)
        runtime.scheduler.assert_released(grant)
        assert runtime.stop(1)


def test_exact_image_preview_declares_union_and_candidate_peak(script_state, monkeypatch, tmp_path):  # noqa: F811
    state = script_state
    from _media_preview_test_fixtures import configure_synthetic_media_budgets
    first = submit(state)
    assert first.status_code == 202, first.text
    assert state.runtime.run_once()
    first_ref = state.client.get(first.json()['result_url']).json()['result']['source_ref']
    second = submit(state, request_id='cbe09c60-16a0-4ee8-9015-74576ba07a88')
    assert second.status_code == 202 and state.runtime.run_once()
    second_ref = state.client.get(second.json()['result_url']).json()['result']['source_ref']
    configure_synthetic_media_budgets(monkeypatch, main)
    # This fixture starts with a deliberately obsolete presenter receipt. Use
    # the existing precise edit to select its registered material candidate.
    material = state.client.post(state.base_url + '/edits/exact', json={
        'operation_type': 'ReplaceClip', 'clip_id': state.montage.clips[0].id,
        'asset_id': state.asset.id, 'segment_id': state.asset.segments[0].id,
        'source_start_seconds': state.asset.segments[0].start_seconds})
    assert material.status_code == 200, material.text
    base_overlay = ImageOverlay(id='base-image', name='base.png', source_ref=first_ref, start_seconds=0, end_seconds=1).model_dump()
    draft = state.client.post(state.base_url + '/edits/exact', json={
        'operation_type': 'EditGraphicOverlay', 'overlay_action': 'add', 'graphic_overlay': base_overlay})
    assert draft.status_code == 200, draft.text
    selected_budget_for = main._device_operation_budget
    monkeypatch.setattr(main, '_device_operation_budget',
        lambda name: _render_budget() if name == 'cpu_render' else selected_budget_for(name))
    payload = {'operation_type': 'EditGraphicOverlay', 'overlay_action': 'add',
        'graphic_overlay': ImageOverlay(id='candidate-image', name='candidate.png', source_ref=second_ref, start_seconds=0, end_seconds=1).model_dump()}
    actor = CreationExecutionActor(state.workspace_id, state.member.member_id)
    actual, resources = main._task_media_facts(state.workspace_id, state.task.id, 'exact_edit_preview', {'payload': payload}, actor)
    assert resources['overlay_count'] == 2
    graphics = actual['graphic_sources']
    assert {item['source_ref'] for item in graphics} == {first_ref, second_ref}
    assert resources['input_bytes'] == sum(item[1] for item in actual['files'])
    with monkeypatch.context() as scoped:
        from test_host_resource_probes import declare_render_metadata_host
        declare_render_metadata_host(scoped)
        assert _verdict(_probe(tmp_path, max_overlay_count=0), resources) is False
    payload['graphic_overlay']['source_ref'] = first_ref
    duplicate, same_resources = main._task_media_facts(state.workspace_id, state.task.id, 'exact_edit_preview', {'payload': payload}, actor)
    assert same_resources['overlay_count'] == 2 and len(duplicate['graphic_sources']) == 1
    assert resources['input_bytes'] - same_resources['input_bytes'] == next(item['size_bytes'] for item in graphics if item['source_ref'] == second_ref)
