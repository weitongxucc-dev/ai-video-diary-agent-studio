"""Packaged music through the real catalog, task import, versioned audio edit and undo.

All task/permit/artifact state is temporary; no provider, service or production task.
"""
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import wave
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app
from app.creation.source_task_use_case import SourceTaskCommand, SourceTaskUseCase
from app.creation.task_authorization import TaskAuthorizationUseCase
from app.materials.contracts import MaterialAsset
from app.production import ProductionRepository
from test_audio_edit_application import edit_state
from test_audio_ingress_application import audio_state, settled
from test_cpu_audio_budget import audio_budget

ROOT = Path(__file__).resolve().parents[1] / 'app/resource_assets/openfilm/music/minimal-ambient-piano'
RESOURCE_ID = 'openfilm:music-minimal-ambient-piano'


def test_imported_music_inherits_only_its_original_task_authority(tmp_path, monkeypatch):
    import test_audio_ingress_application as fixture
    monkeypatch.setattr(fixture, 'audio_budget', lambda: replace(audio_budget(),
        max_input_bytes=10 * 1024 * 1024, max_output_bytes=20 * 1024 * 1024,
        max_process_rss_bytes=256 * 1024 * 1024,
        additional_memory_bytes=256 * 1024 * 1024, additional_disk_bytes=64 * 1024 * 1024))
    repo, actor, media, runtime, client, _, _ = audio_state(tmp_path, monkeypatch)
    # Declared source metadata suffices here; only the imported music is decoded.
    source = repo.video_library_path / 'source.mp4'
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b'isolated source metadata fixture')
    asset = repo._save_asset(MaterialAsset('music-scope-source', 'local', source.name, str(source),
        source.stat().st_size, datetime.fromtimestamp(source.stat().st_mtime, timezone.utc), duration_seconds=6, analysis_status='ready',
        owner_user_id=actor.member_id))
    source_ref = repo.workspace_media_reference_for_asset('local', asset.id)
    with repo._connect() as conn:
        repo._claim_workspace_media_reference(conn, workspace_id='local', reference=source_ref)
    created = SourceTaskUseCase(repo, lambda current: 'ready' if current == actor else 'unauthorized').create(
        SourceTaskCommand(str(uuid4()), asset.id, asset.updated_at, 0, 6, '资源授权测试', '16:9', False), actor)
    task_id = created['task_id']
    task = repo.task_records.get_creation_task('local', task_id)
    authorization = TaskAuthorizationUseCase(repo, lambda identity: 'ready' if identity ==
        TaskAuthorizationUseCase.identity(actor) else 'unauthorized')
    authorization.ensure(task, actor)
    assert authorization.status('local', task_id, operation='render') == 'valid'
    try:
        receipt = client.post(f'/api/creation-tasks/{task_id}/audio/resources',
                              json={'resource_id': RESOURCE_ID, 'version': 1})
        assert receipt.status_code == 202, receipt.text
        imported = settled(client, runtime, receipt.json())
        assert imported['status'] == 'succeeded', imported
        reference = imported['result']['source_ref']
        base = repo.timeline_records.get_timeline_version('local', task.current_timeline_version_id)
        applied = client.post(f'/api/creation-tasks/{task_id}/edits/exact', json={
            'operation_type': 'EditAudioTimeline', 'request_id': str(uuid4()),
            'expected_timeline_version_id': base.id, 'expected_timeline_sequence': base.sequence,
            'audio_edit': {'action': 'add', 'target_id': 'audio-music', 'values': {
                'name': '极简环境钢琴', 'source_ref': reference, 'source_start_ms': 0, 'source_end_ms': 6000,
                'timeline_start_ms': 0, 'gain_db': -12}}})
        assert applied.status_code == 200, applied.text
        assert authorization.status('local', task_id, operation='render') == 'valid'
        assert authorization.verdict(actor, task_id, 'render', references=[reference]) == 'ready'
        other = repo.task_records.get_creation_task('local', 'task')
        authorization.ensure(other, actor)
        assert authorization.status('local', other.id, operation='render') == 'valid'
        assert authorization.verdict(actor, other.id, 'render', references=[reference]) == 'unauthorized'
    finally:
        runtime.stop(1)
        client.close()


def test_music_catalog_preview_hash_and_adoption_boundary():
    manifest = json.loads((ROOT / 'manifest.json').read_text())
    source = manifest['provenance']
    assert hashlib.sha256((ROOT / manifest['source_file']).read_bytes()).hexdigest() == source['source_sha256']
    assert source['revision'] == 'openfilm@0.3.2'
    client = TestClient(app)
    try:
        listing = client.get('/api/editing-resource-catalog', params={'category': 'music'}).json()
        item, = listing['resources']
        assert item['id'] == RESOURCE_ID and item['status'] == 'connected'
        assert item['provenance'][0]['source_sha256'] == source['source_sha256']
        preview = client.get(item['preview']['thumbnail_ref'])
        assert preview.status_code == 200 and preview.headers['content-type'].startswith('audio/wav')
        assert preview.headers['content-disposition'].startswith('inline')
        assert hashlib.sha256(preview.content).hexdigest() == manifest['preview']['sha256']
        with wave.open(io.BytesIO(preview.content)) as audio:
            assert audio.getframerate() == 44100 and audio.getnchannels() == 2
            assert abs(audio.getnframes() / audio.getframerate() - manifest['duration_seconds']) < .05
        preflight = client.get(f'/api/editing-resources/{RESOURCE_ID}/preflight').json()
        assert preflight['ready'] and preflight['runnable']
        assert preflight['adoption']['status'] == 'requires_task_import'
        assert preflight['adoption']['requires_explicit_edit_authorization'] is True
        assert preflight['rollback']['deletes_user_data'] is False
        assert client.get(f'/api/editing-resources/{RESOURCE_ID}/preview?version=2').status_code == 404
        glitch = client.get('/api/editing-resources/openfilm:transition-glitch/preflight?version=2').json()
        assert glitch['status'] == 'catalog_only' and not glitch['runnable']
    finally:
        client.close()


def test_music_import_adopt_replay_reopen_and_undo(tmp_path, monkeypatch):
    # Explicit test-only caps for this 45-second source. They do not calibrate a device.
    import test_audio_ingress_application as fixture
    monkeypatch.setattr(fixture, 'audio_budget', lambda: replace(audio_budget(),
        max_input_bytes=10 * 1024 * 1024, max_output_bytes=20 * 1024 * 1024,
        max_process_rss_bytes=256 * 1024 * 1024,
        additional_memory_bytes=256 * 1024 * 1024, additional_disk_bytes=64 * 1024 * 1024))
    repo, actor, media, runtime, client, base, graph, path, calls = edit_state(tmp_path, monkeypatch)
    url = '/api/creation-tasks/task'
    try:
        manifest = json.loads((ROOT / 'manifest.json').read_text())
        before = repo.get_creation_task_montage_plan('local', 'task')
        preview = client.get(f'/api/editing-resources/{RESOURCE_ID}/preview?version=1')
        assert preview.status_code == 200
        jobs_before_import = repo.job_records.list_job_runs('local', 'task')
        denied = client.post(url + '/audio/resources', json={'resource_id': RESOURCE_ID, 'version': 1},
                             headers={'X-Local-Member-Token': 'invalid-member-token'})
        assert denied.status_code == 401, denied.text
        assert repo.job_records.list_job_runs('local', 'task') == jobs_before_import
        receipt = client.post(url + '/audio/resources', json={'resource_id': RESOURCE_ID, 'version': 1})
        assert receipt.status_code == 202, receipt.text
        duplicate = client.post(url + '/audio/resources', json={'resource_id': RESOURCE_ID, 'version': 1})
        assert duplicate.status_code == 202 and duplicate.json()['job_id'] == receipt.json()['job_id']
        imported = settled(client, runtime, receipt.json())
        assert imported['status'] == 'succeeded', imported
        fact = imported['result']['catalog_resource']
        assert fact['id'] == RESOURCE_ID and fact['version'] == 1
        assert fact['provenance'][0]['source_sha256'] == manifest['provenance']['source_sha256']
        transform = next(row for row in repo.lineage_records.list_transform_runs('local', 'task')
                         if row.job_id == receipt.json()['job_id'])
        assert transform.parameters['catalog_resource'] == fact
        assert transform.parameters['source_sha256'] == manifest['preview']['sha256']
        assert imported['result']['source_sha256'] == manifest['preview']['sha256']
        assert transform.input_refs == []
        start = repo.execution_records.get_task_event('local', 'event_' + receipt.json()['job_id'] + '_start')
        assert start.payload['arguments']['catalog_resource'] == fact
        assert start.payload['arguments']['audio']['sha256'] == imported['result']['source_sha256']
        assert abs(imported['result']['duration_ms'] - manifest['duration_seconds'] * 1000) < 50
        reference = imported['result']['source_ref']
        assert reference.startswith('artifacts/')
        assert client.get(receipt.json()['result_url']).json()['result'] == imported['result']
        jobs = repo.job_records.list_job_runs('local', 'task')
        music = next(track for track in graph.tracks if track.role == 'music')
        values = {'source_ref': reference, 'name': manifest['name'], 'source_start_ms': 0,
            'source_end_ms': 200, 'timeline_start_ms': 0, 'gain_db': -12}
        command = {'operation_type': 'EditAudioTimeline', 'audio_edit': {'action': 'add', 'target_id': music.id, 'values': values},
            'request_id': str(uuid4()), 'expected_timeline_version_id': base.id, 'expected_timeline_sequence': base.sequence}
        # A preview URL alone grants no task source capability.
        rejected = client.post(url + '/edits/exact', json={**command, 'request_id': str(uuid4()),
            'audio_edit': {**command['audio_edit'], 'values': {**values, 'source_ref': f'/api/editing-resources/{RESOURCE_ID}/preview'}}})
        assert rejected.status_code == 422
        applied = client.post(url + '/edits/exact', json=command)
        assert applied.status_code == 200, applied.text
        assert applied.json()['operation']['operation_type'] == 'EditAudioTimeline'
        assert client.post(url + '/edits/exact', json=command).json() == applied.json()
        reopened = ProductionRepository(repo.database_path, repo.video_library_path)
        artifact = next(row for row in reopened.execution_records.list_artifact_records('local', 'task')
                        if row.logical_ref == reference)
        assert artifact.input_refs == transform.input_refs
        assert artifact.sha256 == manifest['preview']['sha256']
        assert reopened.lineage_records.get_transform_run('local', transform.id).parameters == transform.parameters
        edges = [edge for edge in reopened.lineage_records.list_dependency_edges('local', 'task')
                 if edge.downstream_ref == reference]
        assert edges == []
        saved = reopened.get_creation_task_montage_plan('local', 'task')
        saved_music = next(track for track in saved['audio_timeline']['tracks'] if track['role'] == 'music')
        assert len(saved_music['clips']) == 1 and saved_music['clips'][0]['source_ref'] == reference
        assert saved['clips'] == before['clips'] and saved['cues'] == before['cues']
        task = reopened.task_records.get_creation_task('local', 'task')
        head = reopened.timeline_records.get_timeline_version('local', task.current_timeline_version_id)
        undo = {'request_id': str(uuid4()), 'expected_timeline_version_id': head.id, 'expected_timeline_sequence': head.sequence}
        stale = client.post(url + '/timeline/undo', json={**undo, 'request_id': str(uuid4()), 'expected_timeline_version_id': base.id})
        assert stale.status_code == 409
        undone = client.post(url + '/timeline/undo', json=undo)
        assert undone.status_code == 200, undone.text
        versions = reopened.timeline_records.list_timeline_versions('local', 'task')
        operations = reopened.edit_records.list_edit_operations('local', 'task')
        replay = client.post(url + '/timeline/undo', json=undo)
        assert replay.status_code == 200, replay.text
        assert replay.json()['montage']['audio_timeline'] == undone.json()['montage']['audio_timeline']
        assert reopened.timeline_records.list_timeline_versions('local', 'task') == versions
        assert reopened.edit_records.list_edit_operations('local', 'task') == operations
        restored = reopened.get_creation_task_montage_plan('local', 'task')
        assert restored['audio_timeline'] == before['audio_timeline']
        assert restored['clips'] == before['clips'] and restored['cues'] == before['cues']
        assert reopened.job_records.list_job_runs('local', 'task') == jobs
        assert len([op for op in operations if op.operation_type == 'EditAudioTimeline']) == 1
        assert any(row.logical_ref == reference for row in reopened.execution_records.list_artifact_records('local', 'task'))
    finally:
        runtime.stop(1)
        client.close()
