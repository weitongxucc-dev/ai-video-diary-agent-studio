"""Exercise the existing settings HTTP command against an isolated host ledger."""
from dataclasses import asdict, replace
import hashlib
import json
import shutil
import sys
import time
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from app import device_paths, main
from app import device_resource_preparation as preparation
from app.device_resource_configuration import current_device_identity
from app.device_scheduler import ProcessIdentity, RecoveryBlocked
from app.local_members import LocalMemberService
from app.production import ProductionRepository


@pytest.fixture
def cut_preparation_http(tmp_path, monkeypatch):
    repo = ProductionRepository(tmp_path / 'db' / 'production.sqlite', tmp_path / 'library')
    members = LocalMemberService(repo.database_path)
    _, token = members.whoami(None)
    profile = tmp_path / 'profile' / 'resource.json'
    monkeypatch.setattr(device_paths, 'application_data_root', lambda: tmp_path / 'isolated-ledger')
    monkeypatch.setenv('DEVICE_ID', 'isolated-cut-preparation')
    monkeypatch.setattr(main, 'device_runtime', None)
    monkeypatch.setattr(main, 'production_repo', repo)
    monkeypatch.setattr(main, 'local_members_service', members)
    monkeypatch.setattr(main, 'settings', replace(main.settings, device_resource_mode='normal',
        device_resource_profile_path=str(profile), video_output_path=str(tmp_path / 'output'),
        temporary_root_path=str(tmp_path / 'temporary')))
    try:
        yield TestClient(main.app, headers={'X-Local-Member-Token': token}), repo, profile
    finally:
        main._stop_device_runtime()


@pytest.mark.skipif(sys.platform != 'darwin' or not shutil.which('ffmpeg') or not shutil.which('ffprobe'),
                    reason='requires the existing macOS CPU preparation backend')
@pytest.mark.parametrize('outcome', ['done', 'timeout', 'unknown_release'])
def test_http_cpu_preparation_keeps_cut_measurement_in_original_receipt(cut_preparation_http, monkeypatch, outcome):
    client, repo, profile = cut_preparation_http
    url = '/api/device/resource-preparations'
    identity = current_device_identity({'DEVICE_ID': 'isolated-cut-preparation'})
    calls = []
    real_run = preparation.run_granted_command
    release_probe = None

    def record(scheduler, grant, argv, **kwargs):
        nonlocal release_probe
        paths = kwargs['output_paths']
        if not any('cpu-preparation-cut-' in str(path) for path in paths):
            return real_run(scheduler, grant, argv, **kwargs)
        phase = 'fixture' if len(paths) == 1 else ('source_probe', 'encode', 'output_probe')[len(calls) - 1]
        call = {'phase': phase, 'grant': grant, 'deadline': time.monotonic() + kwargs['timeout_seconds'],
                'disk_limit': kwargs['max_output_file_bytes'], 'stdout_limit': kwargs['max_stdout_bytes']}
        calls.append(call)
        if outcome == 'timeout' and phase == 'source_probe':
            kwargs['timeout_seconds'] = 0.0001
        if outcome == 'unknown_release' and phase == 'source_probe':
            release_probe = scheduler.processes.children_released
            monkeypatch.setattr(scheduler.processes, 'children_released', lambda children: None)
        result = real_run(scheduler, grant, argv, **kwargs)
        with repo._connect() as connection:
            children = json.loads(connection.execute('SELECT children_json FROM device_permit WHERE id=1').fetchone()[0])
        call['child'] = children[-1]
        return result

    monkeypatch.setattr(preparation, 'run_granted_command', record)
    payload = {'request_id': str(uuid4()), 'kind': 'cpu', 'shape': {'width_px': 1280, 'height_px': 720,
        'frame_rate': 30, 'duration_seconds': 2.0, 'clip_count': 2,
        'audio_input_count': 2, 'sample_rate': 48000, 'channels': 2}}
    submitted = client.post(url, json=payload)
    assert submitted.status_code == 200, submitted.text
    request = submitted.json()
    runtime = main.device_runtime
    request_url = f"{url}/{request['id']}"
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        status = client.get(request_url)
        assert status.status_code == 200, status.text
        state = status.json()['state']
        if ((state in {'done', 'failed'} and runtime.scheduler.shutdown_released())
                or runtime.last_error is not None):
            break
        time.sleep(.1)
    root = profile.parent / 'resource-preparations'
    plan_path = root / 'plans' / f"{request['plan_id']}.json"
    plan = json.loads(plan_path.read_bytes())
    bound = plan['mp4_cut_probe']
    assert len(calls) >= 2 and calls[0]['grant'] == calls[1]['grant']
    grant = calls[0]['grant']
    receipt_path = root / 'receipts' / f'{grant.request_id}-{grant.generation}.json'
    with repo._connect() as connection:
        row = connection.execute('SELECT checkpoint_ref FROM device_work_requests WHERE id=?', (request['id'],)).fetchone()
        permit_state = connection.execute('SELECT state FROM device_permit WHERE id=1').fetchone()[0]
    ledger = device_paths.device_lock_path().with_suffix('.lock.state')
    ledger_state = json.loads(ledger.read_bytes())['state']
    if outcome != 'done':
        assert [call['phase'] for call in calls] == ['fixture', 'source_probe']
        assert row['checkpoint_ref'] is None and not receipt_path.exists()
        assert not profile.exists()
        if outcome == 'unknown_release':
            assert isinstance(runtime.last_error, RecoveryBlocked) and '释放状态未知' in str(runtime.last_error)
            assert state == 'granted' and permit_state == ledger_state == 'held'
            assert client.post(request_url + '/finalize').status_code == 409
            # The test port hid the real exit confirmation. Restore the port,
            # then let teardown close only this isolated runtime.
            monkeypatch.setattr(runtime.scheduler.processes, 'children_released', release_probe)
        else:
            assert state == 'failed' and runtime.last_error is None and permit_state == ledger_state == 'free'
            finalized = client.post(request_url + '/finalize')
            assert finalized.status_code == 200 and finalized.json()['profile_published'] is False
        assert not profile.exists()
        return
    assert state == 'done' and runtime.last_error is None and permit_state == ledger_state == 'free'
    assert [call['phase'] for call in calls] == ['fixture', 'source_probe', 'encode', 'output_probe']
    assert all(call['grant'] == grant and abs(call['deadline'] - calls[0]['deadline']) < .025 for call in calls)
    assert all(call['disk_limit'] == bound['limits']['max_work_bytes'] and call['stdout_limit'] == 65536 for call in calls)
    raw = receipt_path.read_bytes()
    assert row['checkpoint_ref'] == 'device-resource-preparation:' + hashlib.sha256(raw).hexdigest()
    receipt = json.loads(raw)
    cut = receipt['mp4_cut_probe']
    assert receipt['device'] == asdict(identity) and receipt['input_digest'] == plan['input_digest']
    assert cut['tools'] == bound['tools'] and cut['plan_digest'] == preparation._digest(bound)
    for tool in cut['tools'].values():
        assert tool['sha256'] == preparation._hash_file(tool['path'])
    assert cut['contract'] == preparation.MP4_CUT_PROBE and cut['cpu_policy'] == bound['cpu_policy']
    assert cut['sample_interval_seconds'] == plan['bootstrap_budget']['sample_seconds']
    assert cut['source']['video_seconds'] == cut['source']['audio_seconds'] == 2
    assert cut['source']['packets'] == 155 and cut['source']['video_frames'] == 60
    assert (cut['source']['width'], cut['source']['height']) == (1280, 720)
    assert cut['selection'] == [0.25, 1.25] and cut['output']['video_frames'] == 31
    assert cut['output']['duration_seconds'] == cut['output']['audio_seconds'] == 1
    assert cut['source']['bytes'] * 2 + cut['output']['bytes'] <= bound['limits']['max_work_bytes']
    assert all(len(item['sha256']) == 64 for item in (cut['source'], cut['output']))
    assert set(cut['stages']) == {'source_probe', 'encode', 'output_probe'}
    for phase, stage in cut['stages'].items():
        assert stage['group_released'] is True and stage['stdout_bytes'] < cut['pipe_limit_bytes']
        resources = stage['resources']
        assert resources['rss_sample_complete'] and resources['peak_rss_bytes'] > 0
        assert resources['threads_sample_complete'] and resources['peak_threads'] > 0
        child = ProcessIdentity(**next(call['child'] for call in calls if call['phase'] == phase))
        assert runtime.scheduler.processes.children_released((child,)) is True
    assert receipt['elapsed_seconds'] + cut['elapsed_seconds'] < plan['bootstrap_budget']['cpu_render']['max_timeout_seconds']
    final = client.post(request_url + '/finalize')
    assert final.status_code == 200 and final.json()['profile_published'] and not final.json()['normal_work_started']
    published = json.loads(profile.read_bytes())['budget']
    assert 'mp4_cut_probe' not in published
    assert published['cpu_render']['max_timeout_seconds'] == receipt['elapsed_seconds'] * plan['policy']['measured_margin']
    assert client.post(url, json=payload).json()['id'] == request['id'] and len(calls) == 4


def test_cpu_preparation_keeps_existing_non_mac_capability_boundary(cut_preparation_http, monkeypatch):
    from app import device_resource_configuration
    client, repo, profile = cut_preparation_http
    identity = current_device_identity({'DEVICE_ID': 'isolated-cut-preparation'})
    monkeypatch.setattr(device_resource_configuration, 'current_device_identity', lambda _: replace(identity, platform='win32'))
    response = client.post('/api/device/resource-preparations', json={
        'request_id': str(uuid4()), 'kind': 'cpu', 'shape': {'width_px': 1280, 'height_px': 720,
            'frame_rate': 30, 'duration_seconds': 2.0, 'clip_count': 2,
            'audio_input_count': 2, 'sample_rate': 48000, 'channels': 2}})
    assert response.status_code == 409 and '其他平台需要对应系统压力适配' in response.text
    assert main.device_runtime is None and not profile.exists() and not device_paths.device_lock_path().exists()
    with repo._connect() as connection:
        assert connection.execute('SELECT count(*) FROM device_work_requests').fetchone()[0] == 0
