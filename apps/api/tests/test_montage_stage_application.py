"""One metadata-only storyboard normal through the public device queue."""
from dataclasses import asdict, replace
import hashlib
import json
import os
from pathlib import Path
import shutil
import time
from types import SimpleNamespace

import psutil
import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.artifact_store import ArtifactStore
from app.creation_execution import revision_digest
from app.device_runtime import build_device_runtime
from app.host_resource_probes import HostBudget, HostProcessProbe, HostResourceProbe, MontageBudget
from app.local_members import LocalMemberService
from app.member_permits import MemberDeviceAccess, creation_job_registration
from app.montage import MontagePlanner
from app.creation.contracts import CreationPlanVoiceReference, NarrationSegment
from app.production import NarrationArtifactRecord
from app.visual_effects import reusable_color_settings
from _media_preview_test_fixtures import register_synthetic_profiles, register_synthetic_video_asset, synthetic_wav_bytes
from test_creation_execution import _seed


@pytest.mark.skipif(shutil.which('ffmpeg') is None, reason='synthetic B-roll fixture requires ffmpeg')
def test_public_montage_stage_keeps_low_confidence_wait_and_single_publication(tmp_path, monkeypatch):
    repo, task_id, script_id, _ = _seed(tmp_path, workspace_id='local')
    members = LocalMemberService(repo.database_path)
    member, token = members.join(members.create_invite_code(), '故事板 normal 合成成员')
    person, voice, *_ = register_synthetic_profiles(repo, workspace_id='local', member_id=member.member_id,
        person_id='person_1', voice_id='voice_1', person_version='v1', voice_version='v1', language='zh',
        model_id='declared-synthetic-narration', person_authorization_id='person_auth_1', voice_authorization_id='voice_auth_1',
        voice_usage_scopes=('narration',))
    plan = repo.plan_records.get_creation_plan('local', 'plan_local')
    plan.presenter_share_target = 0
    plan.selected_voice = CreationPlanVoiceReference(voice.id, voice.version, person.id, person.version,
        person.authorization.id, person.authorization.version, voice.authorization.id, voice.authorization.version, 'zh-CN', voice.model_id)
    plan.owner_user_id = member.member_id
    plan.aspect_ratio = '16:9'
    plan.template_settings = {'fit_mode': 'fit', 'color_style': {'exposure': 0.1}, 'subtitle_style': {'font_size': 42}}
    task = repo.get_creation_task('local', task_id)
    task.owner_user_id = member.member_id
    with repo._connect() as conn:
        conn.execute('UPDATE creation_plans SET payload=? WHERE id=?', (json.dumps(asdict(plan), default=str), plan.id))
        conn.execute('UPDATE creation_tasks SET payload=? WHERE id=?', (json.dumps(asdict(task), default=str), task.id))
    script = repo.get_creation_task_script('local', script_id)
    repo.start_creation_task_narration('local', task_id)
    audio = tmp_path / 'declared-narration.wav'
    audio.write_bytes(synthetic_wav_bytes(2))
    cache = hashlib.sha256(b'declared-montage-narration').hexdigest()
    record = NarrationArtifactRecord(id='declared-montage-narration', workspace_id='local', task_id=task_id,
        script_id=script_id, voice_profile_id=voice.id, cache_key=cache, audio_path=str(audio), duration_seconds=2,
        sample_rate=8000, model_id=voice.model_id, language='zh', pronunciation_version='fixture-rules')
    segment = NarrationSegment(id='declared-montage-section', workspace_id='local', task_id=task_id,
        script_id=script_id, source_section_id=script.sections[0].id, text=script.sections[0].content,
        audio_reference=repo.narration_artifact_reference(cache), duration_seconds=2, sample_rate=8000,
        model_id=voice.model_id, voice_profile_id=voice.id, voice_version=voice.version, language='zh',
        pronunciation_version='fixture-rules', status='ready')
    repo.complete_creation_task_narration(record, segments=[segment])
    # This actual ffmpeg command creates input data, before the metadata execution window.
    asset, material_ref = register_synthetic_video_asset(repo, workspace_id='local', member_id=member.member_id,
        path=tmp_path / 'library/source.mp4', asset_id='declared-montage-broll', seconds=6)
    monkeypatch.setattr(main, 'auth_context', SimpleNamespace(enabled=False))
    monkeypatch.setattr(main, 'local_members_service', members)
    monkeypatch.setattr(main, 'production_repo', repo)
    monkeypatch.setattr(main, 'artifact_store', ArtifactStore(tmp_path / 'artifacts'))
    monkeypatch.setattr(main, 'settings', replace(main.settings, video_output_path=str(tmp_path / 'outputs'),
        video_inbox_path=str(tmp_path / 'library'), temporary_root_path=str(tmp_path / 'temporary'),
        native_portrait_worker_path=None, hermes_radar_path=None, obsidian_vault_path=None))
    monkeypatch.setattr(main, 'creation_execution', main._new_creation_execution(repo))
    budget = MontageBudget(10, 20, 1_000_000, 1_000_000, 30, 1_000_000)
    # Exact pre-existing finite narration application's host/OS fixture values.
    host_budget = HostBudget(100, 1, 100 * 1024**3, 1, 10**12, 10_000_000, montage=budget)
    swap = psutil.swap_memory()
    disk = psutil.disk_io_counters()
    devices = psutil.disk_io_counters(perdisk=True)
    monkeypatch.setattr(psutil, 'cpu_percent', lambda *args, **kwargs: 0.0)
    monkeypatch.setattr(psutil, 'swap_memory', lambda: swap._replace(used=0))
    monkeypatch.setattr(psutil, 'disk_io_counters', lambda **kwargs: devices if kwargs.get('perdisk') else disk)
    resources = HostResourceProbe(repo.database_path, disk_paths=(tmp_path,), budget=host_budget)
    case = main._montage_stage_use_case(repo)
    assert case.repository is case.metadata.repository is case.person_stage.repository is repo
    assert case.metadata is case.person_stage.metadata and case.montage_planner is main.montage_planner
    calls = []
    original_build, original_save, original_base = MontagePlanner.build_for_creation_inputs, repo.save_creation_task_montage_plan, case.metadata.ensure_montage_base

    def observe_build(planner, *args, **kwargs):
        result = original_build(planner, *args, **kwargs)
        calls.append({'port': 'planner', 'configured_global': planner is main.montage_planner,
            'logical_refs': [clip.source_path for clip in result.clips], 'status': result.status})
        return result

    def observe_save(*args, **kwargs):
        calls.append({'port': 'save_montage', 'connection_supplied': kwargs.get('connection') is not None})
        return original_save(*args, **kwargs)

    def observe_base(*args, **kwargs):
        result = original_base(*args, **kwargs)
        calls.append({'port': 'ensure_base', 'version_id': result.id})
        return result

    monkeypatch.setattr(MontagePlanner, 'build_for_creation_inputs', observe_build)
    monkeypatch.setattr(repo, 'save_creation_task_montage_plan', observe_save)
    monkeypatch.setattr(case.metadata, 'ensure_montage_base', observe_base)
    registration = creation_job_registration(repo, render_action=main._render_creation_stage_with_grant,
        render_is_valid=main._creation_stage_valid_with_grant, verify_identity=main._verify_creation_identity,
        montage_budget=budget, montage_stage=case)
    assert registration.executor.montage_stage is case and not hasattr(registration.executor, 'stage_actions')
    runtime = build_device_runtime(database_path=repo.database_path, device_lock_path=tmp_path / 'device.lock',
        device_access=MemberDeviceAccess(members, identity_verifier=main._verify_creation_identity),
        registrations={'creation_job': registration}, resources=resources, processes=HostProcessProbe())
    monkeypatch.setattr(main, 'device_runtime', runtime)
    client = TestClient(main.app, headers={'X-Local-Member-Token': token})
    url = '/api/creation-tasks/' + task_id
    request = {'script_id': script_id, 'input_digest': revision_digest(script, plan), 'requirements_confirmed': True}
    evidence_dir = os.environ.get('MONTAGE_STAGE_EVIDENCE_DIR')
    try:
        if evidence_dir:
            Path(evidence_dir, 'METADATA_WINDOW_START').write_text(str(time.monotonic()))
        first = client.post(url + '/execution/start', json=request)
        assert first.status_code == 202, first.text
        job_id = first.json()['job']['id']
        second = client.post(url + '/execution/start', json=request)
        assert second.status_code == 202 and second.json()['job']['id'] == job_id, second.text
        assert calls == []
        deadline = time.monotonic() + 4
        while not runtime.run_once():
            assert time.monotonic() < deadline, runtime.last_error
            time.sleep(.1)
        task = repo.get_creation_task('local', task_id)
        montage = repo.get_creation_task_montage_plan('local', task_id)
        versions = repo.list_timeline_versions('local', task_id)
        assert montage['status'] == task.stage_status == task.status == 'waiting_for_user'
        assert task.stage == 'montage' and not task.output_ready
        assert len(montage['clips']) == len(montage['cues']) == montage['low_confidence_clip_count'] == 1
        clip, cue = montage['clips'][0], montage['cues'][0]
        assert clip['needs_confirmation'] and clip['person_render_id'] is None and clip['source_path'] == material_ref
        assert clip['asset_id'] == asset.id and clip['segment_id'] in {item.id for item in asset.segments}
        assert cue['narration_segment_id'] == segment.id and cue['source_section_id'] == script.sections[0].id
        assert montage['narration_audio_path'] == segment.audio_reference and montage['duration_seconds'] == 2
        assert montage['presenter_share_target'] == 0 and montage['aspect_ratio'] == '16:9'
        assert clip['fit_mode'] == 'fit' and clip['visual_effects'] == reusable_color_settings(plan.template_settings['color_style'])
        assert cue['subtitle_style'] == plan.template_settings['subtitle_style']
        assert '未运行视觉语义模型' in montage['message'] and any('缺少本地多帧' in reason for reason in clip['match_reasons'])
        assert len(versions) == 1 and versions[0].id == task.current_timeline_version_id
        assert versions[0].snapshot['montage']['id'] == montage['id'] == task.montage_plan_id
        assert versions[0].snapshot['narration_segments'][0]['id'] == segment.id
        event = repo.execution_records.get_task_event('local', f'event_{job_id}_montage_plan_queued')
        assert event.payload == {'stage': 'montage_plan', 'status': 'completed', 'output': montage['id'], 'reason': 'waiting_for_user'}
        assert repo.get_job_run('local', job_id).status == 'queued'
        assert [call['port'] for call in calls] == ['planner', 'save_montage', 'save_montage', 'ensure_base']
        assert calls[0]['configured_global'] is False and calls[1]['connection_supplied'] is False and calls[2]['connection_supplied'] is True
        before_queries = list(calls)
        public = client.get(url + '/execution', params={'job_id': job_id})
        montage_public = client.get(url)
        assert public.status_code == montage_public.status_code == 200, (public.text, montage_public.text)
        assert montage_public.json()['montage']['id'] == montage['id'] and montage_public.json()['montage']['status'] == 'waiting_for_user'
        assert not runtime.run_once()
        assert calls == before_queries and len(repo.list_timeline_versions('local', task_id)) == 1
        assert not repo.list_person_renders('local', task_id) and not repo.lineage_records.list_transform_runs('local', task_id)
        assert not repo.execution_records.list_artifact_records('local', task_id)
        stopped = client.post(url + '/execution/cancel')
        assert stopped.status_code == 200, stopped.text
        assert calls == before_queries and repo.get_creation_task_montage_plan('local', task_id)['id'] == montage['id']
        if evidence_dir:
            Path(evidence_dir, 'NORMAL_SUMMARY.json').write_text(json.dumps({
                'job_id': job_id, 'task': asdict(task), 'montage': montage, 'versions': [asdict(v) for v in versions],
                'event': asdict(event), 'calls': calls, 'execution_query': public.json(), 'montage_query': montage_public.json(),
                'public_stop_status': stopped.status_code, 'montage_budget': asdict(budget), 'host_budget': asdict(host_budget),
                'OS_fixture': {'cpu_percent': 0, 'swap_used_bytes': 0, 'actual_swap_before': swap.used,
                    'disk_io_baseline': disk._asdict(), 'per_device_disk_io_baseline': {name: value._asdict() for name, value in devices.items()}},
                'fixture_media': 'one synthetic B-roll ffmpeg setup command; declared 2s WAV; no model/person/narration/render execution',
                'business_rules_replaced': 0, 'query_replay_added_planning_save_version': 0,
            }, ensure_ascii=False, indent=2, default=str) + '\n')
    finally:
        if evidence_dir:
            Path(evidence_dir, 'METADATA_WINDOW_END').write_text(str(time.monotonic()))
        assert runtime.stop(1)


def _montage_stream_case(tmp_path, monkeypatch):
    """Paid metadata-only inputs for the real initial and replacement owners."""
    from datetime import datetime, timedelta, timezone
    from app.materials.contracts import MaterialAsset, MaterialSegment
    from app.creation.script_contracts import CreationTaskScriptSection
    from app.creation.montage_stage_use_case import MontageStageUseCase
    from app.creation.editing.metadata_use_case import MetadataEditUseCase
    from app.creation.execution_control import CreationExecutionControl
    from app.creation_execution import CreationExecution, CreationExecutionActor, CreationJobOwnerVerifier, CreationJobUnitExecutor
    from app.device_scheduler import DeviceScheduler
    from app.artifact_store import ArtifactStore
    from test_device_scheduler import Resources, Processes
    repo, task_id, script_id, _ = _seed(tmp_path, workspace_id='local', duration_seconds=4)
    task = repo.get_creation_task('local', task_id)
    script = repo.get_creation_task_script('local', script_id)
    script.sections = [replace(script.sections[0], content='中文 "quoted" café 😺。'),
        CreationTaskScriptSection('second', '尾段', '实际尾段。', '素材画面', [])]
    with repo._connect() as conn:
        conn.execute('BEGIN')
        conn.execute('UPDATE creation_task_scripts SET payload=? WHERE id=?', (repo._script_payload(conn, script), script.id))
    actor = CreationExecutionActor('local', 'stream-member')
    path = tmp_path/'paid.wav'; path.write_bytes(b'paid source metadata; no decode')
    record = NarrationArtifactRecord(id='paid', workspace_id='local', task_id=task.id, script_id=script.id,
        voice_profile_id='voice_1', cache_key='paid', audio_path=str(path), duration_seconds=4,
        sample_rate=24000, model_id='fixture', language='zh', pronunciation_version='v1')
    repo.save_narration_artifact(record)
    rows = [NarrationSegment('audio-'+section.id, 'local', task.id, script.id, section.id, section.content,
        repo.narration_artifact_reference('paid'), 2, 24000, 'fixture', 'voice_1', 'v1', 'zh', 'v1', 'ready',
        created_at=script.created_at-timedelta(seconds=i)) for i, section in enumerate(script.sections)]
    repo.save_narration_segments('local', task.id, script.id, rows)
    task.narration_cache_key = 'paid'; task.stage = 'montage'
    with repo._connect() as conn:
        conn.execute('UPDATE creation_tasks SET payload=? WHERE id=?', (json.dumps(asdict(task), default=str), task.id))
    source = tmp_path/'library'/'metadata.mp4'; source.write_bytes(b'fixed metadata-only asset')
    stat = source.stat()
    asset = MaterialAsset('stream-asset', 'local', source.name, str(source), stat.st_size,
        datetime.fromtimestamp(stat.st_mtime, timezone.utc), duration_seconds=8, analysis_status='ready', review_status='approved',
        usage_role='b_roll', owner_user_id=actor.member_id,
        segments=[MaterialSegment('stream-shot', 'stream-asset', 0, 8, usage_role='b_roll', review_status='approved')])
    repo._save_asset(asset)
    with repo._connect() as conn:
        repo._claim_workspace_media_reference(conn, workspace_id='local',
            reference=repo.workspace_media_reference_for_asset('local', asset.id))
    from app.task_audio import TaskAudio
    from app.media.picture_preview import PicturePreviewRenderer
    store = ArtifactStore(tmp_path/'outputs')
    audio = TaskAudio(repo, store, tmp_path/'temporary')
    metadata = MetadataEditUseCase(repo, store, output_root=tmp_path/'outputs', audio_service=audio)
    metadata.picture_renderer = PicturePreviewRenderer(repo, store, audio, metadata, None,
        verify_identity=lambda _: 'ready', presenter_model_ids=lambda: {}, output_root=tmp_path/'outputs',
        portrait_worker_path=None, temporary_root=tmp_path/'temporary')
    stage = MontageStageUseCase(repo, metadata, SimpleNamespace(presenter_model_ids=lambda: {}), MontagePlanner())
    owner = CreationJobOwnerVerifier(repo, montage_budget=MontageBudget(20, 20, 1_000_000, 1_000_000, 30, 1_000_000),
        verify_identity=lambda _: 'ready')
    def forbidden(*args, **kwargs):
        pytest.fail('whole montage stream must not hydrate input DTOs or execute media/models')
    execution = CreationExecution(repo, run_montage_plan=forbidden, run_render=forbidden, render_is_valid=lambda *_: False)
    scheduler = DeviceScheduler(repo.database_path, tmp_path/'permit.lock', owners=owner, resources=Resources(), processes=Processes())
    executor = CreationJobUnitExecutor(repo, render_action=forbidden, render_is_valid=lambda *_: False, montage_stage=stage)
    runtime = SimpleNamespace(scheduler=scheduler, wake=lambda: None,
        registrations={'creation_job': SimpleNamespace(owner=owner, executor=executor)})
    control = CreationExecutionControl(repo, execution, SimpleNamespace(valid=lambda *args, **kwargs: False), lambda _: 'ready')
    plan = repo.plan_records.get_creation_plan('local', task.plan_id)
    from app.creation_execution import revision_digest
    digest = revision_digest(script, plan)
    return SimpleNamespace(repo=repo, task=task, script=script, plan=plan, rows=rows, record=record, asset=asset,
        actor=actor, stage=stage, owner=owner, scheduler=scheduler, executor=executor, runtime=runtime,
        control=control, digest=digest, forbidden=forbidden)


@pytest.mark.parametrize('outcome', ['normal', 'save_base_gap', 'tail', 'revoked', 'checkpoint_revoked', 'save_base_gap_changed_audio'])
def test_whole_montage_stream_initial_owner_and_save_gap(tmp_path, monkeypatch, outcome):
    from app.montage import creation_task_montage_plan_from_payload
    case = _montage_stream_case(tmp_path, monkeypatch)
    builds = []
    build = MontagePlanner._build_creation_plan
    def observed(*args, **kwargs):
        builds.append(True)
        return build(*args, **kwargs)
    monkeypatch.setattr(MontagePlanner, '_build_creation_plan', observed)
    for name in ('get_creation_task_script', '_creation_task_script_from_payload',
                 'effective_creation_script', 'list_narration_segments'):
        monkeypatch.setattr(case.repo, name, case.forbidden)
    monkeypatch.setattr('app.granted_subprocess.run_granted_command', case.forbidden)
    try:
        result = case.control.start('local', case.task.id, case.script.id, case.digest, case.actor,
            case.runtime, verify_media=False)
        grant = case.scheduler.pick_and_grant(); assert grant is not None
        if outcome in {'save_base_gap', 'save_base_gap_changed_audio'}:
            original = case.stage.metadata.ensure_montage_base
            def interrupted(*args, **kwargs):
                raise KeyboardInterrupt('process exits after saved montage before base')
            monkeypatch.setattr(case.stage.metadata, 'ensure_montage_base', interrupted)
            with pytest.raises(KeyboardInterrupt):
                case.executor.execute(grant, case.scheduler)
            assert case.repo.get_creation_task('local', case.task.id).montage_plan_id
            assert not case.repo.list_timeline_versions('local', case.task.id)
            monkeypatch.setattr(case.stage.metadata, 'ensure_montage_base', original)
            if outcome == 'save_base_gap_changed_audio':
                with case.repo._connect() as conn:
                    conn.execute('UPDATE narration_segments SET duration_seconds=3 WHERE id=?', (case.rows[-1].id,))
                # The original raw stage key/aggregate permit cannot detect this change.
                with case.repo.creation_execution_read() as conn:
                    assert case.owner.check_current(case.scheduler._get(grant.request_id), conn) == 'ready'
        elif outcome in {'tail', 'revoked'}:
            injected = []
            original = case.repo.save_creation_task_montage_plan
            def changed(*args, **kwargs):
                verify = kwargs['verify_inputs']
                def inject(conn, task):
                    injected.append(True)
                    if outcome == 'tail':
                        conn.execute("UPDATE creation_task_script_sections SET section_json=json_set(section_json,'$.content','changed tail') WHERE script_id=? AND section_id='second'", (case.script.id,))
                    else:
                        monkeypatch.setattr(case.owner, 'verify_identity', lambda _: 'unauthorized')
                    verify(conn, task)
                kwargs['verify_inputs'] = inject
                return original(*args, **kwargs)
            monkeypatch.setattr(case.repo, 'save_creation_task_montage_plan', changed)
        if outcome == 'checkpoint_revoked':
            released = case.scheduler._released_for_commit
            def revoked(conn, current):
                result = released(conn, current)
                monkeypatch.setattr(case.owner, 'verify_identity', lambda _: 'unauthorized')
                return result
            monkeypatch.setattr(case.scheduler, '_released_for_commit', revoked)
        case.executor.execute(grant, case.scheduler)
        current = case.repo.get_creation_task('local', case.task.id)
        assert len(builds) == 1
        if outcome in {'tail', 'revoked'}:
            assert injected
            assert current.montage_plan_id is None
            assert not case.repo.list_timeline_versions('local', case.task.id)
        elif outcome == 'save_base_gap_changed_audio':
            assert current.montage_plan_id
            assert not case.repo.list_timeline_versions('local', case.task.id)
            assert case.scheduler._get(grant.request_id).state != 'done'
        elif outcome == 'checkpoint_revoked':
            assert current.montage_plan_id
            assert len(case.repo.list_timeline_versions('local', case.task.id)) == 1
            assert case.repo.get_job_run('local', grant.owner_id).status == 'interrupted'
            assert case.scheduler._get(grant.request_id).state != 'done'
        else:
            assert current.stage_status == 'waiting_for_user', (current.stage_repair_action, case.repo.get_job_run('local', grant.owner_id).failure_reason)
            versions = case.repo.list_timeline_versions('local', case.task.id)
            assert len(versions) == 1
            assert [row['id'] for row in versions[0].snapshot['narration_segments']] == [row.id for row in reversed(case.rows)]
            assert case.repo.get_job_run('local', grant.owner_id).status == 'queued'
            same = case.control.start('local', case.task.id, case.script.id, case.digest, case.actor,
                case.runtime, verify_media=False)
            assert len(builds) == 1 and len(case.repo.list_timeline_versions('local', case.task.id)) == 1
    finally:
        case.scheduler.close()
