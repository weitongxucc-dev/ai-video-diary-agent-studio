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
from app.production import CreationPlanVoiceReference, NarrationArtifactRecord, NarrationSegment
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
    plan = repo.get_creation_plan('local', 'plan_local')
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
    original_build, original_save, original_base = MontagePlanner.build_for_creation_task, repo.save_creation_task_montage_plan, case.metadata.ensure_montage_base

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

    monkeypatch.setattr(MontagePlanner, 'build_for_creation_task', observe_build)
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
        event = repo.get_task_event('local', f'event_{job_id}_montage_plan_queued')
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
        assert not repo.list_person_renders('local', task_id) and not repo.list_transform_runs('local', task_id)
        assert not repo.list_artifact_records('local', task_id)
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
