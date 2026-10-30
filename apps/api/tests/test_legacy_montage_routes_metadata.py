"""Declared historical metadata only; no legacy business writer or media execution."""
from dataclasses import asdict
import json
import subprocess
import wave

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.auth import SupabaseAuthContext
from app.config import Settings
from app.device_runtime import DeviceRuntime
from app.device_scheduler import DeviceScheduler
from app.local_members import LocalMemberService
from app.montage import MontageClip, MontagePlan, MontageRepository, NarrationCue
from app.production import ProductionBatch, ProductionBatchItem, ProductionRepository


def test_legacy_montage_metadata_browses_isolated_history_without_writes(tmp_path, monkeypatch):
    forbidden_calls = []

    def forbidden(*args, **kwargs):
        forbidden_calls.append('execution')
        pytest.fail('historical browsing forbids runtime, renderer and media execution')

    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(wave, 'open', forbidden)
    for name in ('start', 'run_once', 'wake'):
        monkeypatch.setattr(DeviceRuntime, name, forbidden)
    monkeypatch.setattr(DeviceScheduler, 'pick_and_grant', forbidden)
    monkeypatch.setattr(main, '_creation_runtime', forbidden)
    monkeypatch.setattr(main, '_picture_preview_renderer', forbidden)
    monkeypatch.setattr(main, '_render_stage_use_case', forbidden)
    database = tmp_path / 'history.sqlite'
    output = tmp_path / 'output'
    output.mkdir()
    outside = tmp_path / 'outside.bin'
    outside.write_bytes(b'opaque historical reference, not media')
    settings = Settings(database_path=str(database), video_output_path=str(output),
        video_inbox_path=str(tmp_path / 'inbox'), model_root_path=str(tmp_path / 'models'),
        temporary_root_path=str(tmp_path / 'temporary'), obsidian_vault_path=None,
        hermes_radar_path=None, local_whisper_python_path=None, local_whisper_cli_path=None,
        local_whisper_model_path=None, supabase_url=None, supabase_publishable_key=None)
    monkeypatch.setattr(main, 'settings', settings)
    monkeypatch.setattr(main, 'auth_context', SupabaseAuthContext(settings))
    production = ProductionRepository(database, tmp_path / 'library')
    MontageRepository(database)
    members = LocalMemberService(database)
    _, token = members.whoami(None)
    monkeypatch.setattr(main, 'local_members_service', members)

    # These rows declare legacy history in an isolated fixture; no retired writer is restored.
    for workspace, suffix in (('local', ''), ('foreign', '-foreign')):
        batch_id, plan_id, episode_id = 'batch' + suffix, 'plan' + suffix, 'episode' + suffix
        batch = ProductionBatch(id=batch_id, workspace_id=workspace, name='Historical batch',
            target_count=1, status='interrupted', episode_ids=[episode_id],
            items=[ProductionBatchItem(episode_id=episode_id, status='interrupted', plan_id=plan_id)])
        cue = NarrationCue(id='cue', source_section_id='section', text='历史字幕', duration_seconds=2)
        clip = MontageClip(id='clip', cue_id='cue', asset_id='missing-asset', segment_id='missing-segment',
            source_path='opaque-reference', source_start_seconds=0, source_end_seconds=2,
            timeline_start_seconds=0, timeline_end_seconds=2, match_score=0, match_reasons=[],
            needs_confirmation=False)
        plan = MontagePlan(id=plan_id, workspace_id=workspace, batch_id=batch_id, episode_id=episode_id,
            script_title='Historical plan', status='rendered', cues=[cue], clips=[clip],
            duration_seconds=2, low_confidence_clip_count=0, output_path=str(outside), subtitle_path=str(outside))
        with production._connect() as connection:
            connection.execute('INSERT INTO production_batches (id,workspace_id,payload,updated_at) VALUES (?,?,?,?)',
                (batch.id, workspace, json.dumps(asdict(batch), default=str), str(batch.updated_at)))
            connection.execute('INSERT INTO production_montage_plans (id,workspace_id,batch_id,episode_id,payload,updated_at) VALUES (?,?,?,?,?,?)',
                (plan.id, workspace, batch.id, episode_id, json.dumps(asdict(plan), default=str), str(plan.updated_at)))
    # Reopen the actual readers after seeding the original tables.
    production = ProductionRepository(database, tmp_path / 'library')
    montage = MontageRepository(database)
    monkeypatch.setattr(main, 'production_repo', production)
    monkeypatch.setattr(main, 'montage_repo', montage)
    assert production.get_batch('local', 'batch').status == 'interrupted'
    assert montage.get('local', 'plan').script_title == 'Historical plan'

    def facts():
        with production._connect() as connection:
            tables = [row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
            return {name: sorted((tuple(row) for row in connection.execute(
                'SELECT * FROM "' + name.replace('"', '""') + '"')), key=repr) for name in tables}

    def tree():
        return {str(path.relative_to(tmp_path)): path.read_bytes() if path.is_file() else None
                for path in tmp_path.rglob('*')}

    before_facts, before_tree = facts(), tree()
    client = TestClient(main.app)  # No lifespan or recovery.
    headers = {'X-Local-Member-Token': token}
    root = '/api/production'
    listed = client.get(root + '/batches', headers=headers)
    assert listed.status_code == 200, listed.text
    assert listed.json()['total_count'] == 1
    assert [batch['id'] for batch in listed.json()['batches']] == ['batch']
    detail = client.get(root + '/batches/batch', headers=headers)
    assert detail.status_code == 200, detail.text
    assert detail.json() == listed.json()['batches'][0]
    assert detail.json()['status'] == 'interrupted'
    assert detail.json()['items'][0]['plan_id'] == 'plan'
    plans = client.get(root + '/batches/batch/montage-plans', headers=headers)
    assert plans.status_code == 200, plans.text
    assert plans.json()['total_count'] == 1
    plan = client.get(root + '/montage-plans/plan', headers=headers)
    assert plan.status_code == 200, plan.text
    assert plan.json() == plans.json()['plans'][0]
    assert plan.json()['id'] == 'plan' and plan.json()['cues'][0]['text'] == '历史字幕'
    assert plan.json()['output_url'] is None and plan.json()['subtitle_url'] is None
    assert plan.json()['clips'][0]['source_path'] is None
    candidates = client.get(root + '/montage-plans/plan/clips/clip/candidates', headers=headers)
    assert candidates.status_code == 200 and candidates.json() == []
    for path in ('/batches/batch-foreign', '/batches/batch-foreign/montage-plans',
                 '/montage-plans/plan-foreign', '/montage-plans/plan/clips/missing/candidates',
                 '/montage-plans/plan/output', '/montage-plans/plan/subtitle'):
        response = client.get(root + path, headers=headers)
        assert response.status_code == 404, (path, response.text)
    assert facts() == before_facts
    assert tree() == before_tree
    assert forbidden_calls == []
    client.close()
