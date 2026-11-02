"""Real HTTP/owner/scheduler continuation with an isolated declared worker."""
from dataclasses import replace

import app.main as main
from app.quality_worker_runtime import QUALITY_PHASES
from test_quality_inspection_api import system as system, seed_render  # noqa: F401
from test_quality_resource_owner import metadata_result


def test_continue_http_preserves_original_check_and_uses_registered_service(system, monkeypatch):
    client, repo, _, checks, headers, other_token, _ = system
    seed_render(system)
    runtime, service, actor = client.quality_runtime, client.quality_service, client.quality_actor
    calls = []
    fail = [True]
    def worker(context, request, root):
        calls.append(request['phase'])
        if request['phase'] == 'black_frames' and fail:
            fail.pop()
            raise RuntimeError('declared phase failure')
        return metadata_result(request)
    runtime.registrations['quality_check'].executor.worker = worker
    check_id = service.start(runtime, actor, 'local', 'task', force=True)
    job_id = f'job_quality_{check_id}'
    assert runtime.run_once() and runtime.run_once()
    with repo._connect() as connection:
        original = dict(connection.execute("SELECT * FROM device_work_requests WHERE owner_id=? AND state='failed'",
                                           (job_id,)).fetchone())
    service.budget = replace(service.budget, max_timeout_seconds=12.)
    service.current_policy = lambda: {'budget': service.budget, 'profile': {
        'profile_id': 'isolated-declared-policy', 'revision': 2, 'digest': 'a' * 64,
        'source_kind': 'device_profile'}}
    with monkeypatch.context() as missing_device:
        def unavailable_actor(*args):
            raise main.DeviceAccountError('unavailable', 'trusted device credential is not provisioned')
        missing_device.setattr(main, '_request_permit_actor', unavailable_actor)
        readable = client.get('/api/creation-tasks/task/quality-check', headers=headers)
        assert readable.status_code == 200, readable.text
        assert readable.json()['continuation']['reason_code'] == 'actor_unavailable'
        assert readable.json()['continuation']['can_request'] is False
    read = client.get('/api/creation-tasks/task/quality-check', headers=headers)
    assert read.status_code == 200, read.text
    facts = read.json()['continuation']
    assert facts['can_request'] is True and facts['release_check'] == 'on_submit'
    assert facts['completed_phases'] == ['metadata']
    assert facts['failed_request_id'] == original['id']
    # The write must use the actual registered service, not reconstruct one.
    monkeypatch.setattr(main, '_quality_inspection_use_case', lambda *args, **kwargs:
                        (_ for _ in ()).throw(AssertionError('fresh service on continuation')))
    starts = []
    monkeypatch.setattr(runtime, 'start', lambda: starts.append('start'))  # No background/media work.
    url = '/api/creation-tasks/task/quality-check/continue'
    payload = {'job_id': job_id, 'failed_request_id': original['id']}
    query = '/api/creation-tasks/task/quality-check/continuation'
    unknown = client.get(query, headers=headers, params=payload)
    assert unknown.status_code == 200 and unknown.json() == {**payload, 'status': 'unknown', 'receipt': None}
    assert starts == []
    assert client.get(query, headers={'X-Local-Member-Token': other_token}, params=payload).status_code == 404
    assert client.post(url, headers=headers, json={**payload, 'max_timeout_seconds': 99}).status_code == 422
    assert client.post(url, headers={'X-Local-Member-Token': other_token}, json=payload).status_code == 404
    assert client.post(url.replace('/task/', '/missing/'), headers=headers, json=payload).status_code == 404
    response = client.post(url, headers=headers, json=payload)
    assert response.status_code == 200, response.text
    assert response.json()['check_id'] == check_id and response.json()['job_id'] == job_id
    assert response.json()['request_id'] != original['id']
    assert client.get(query, headers=headers, params=payload).json() == {**payload, 'status': 'recorded', 'receipt': response.json()}
    assert client.post(url, headers=headers, json=payload).json() == response.json()
    for _ in QUALITY_PHASES[1:]:
        assert runtime.run_once()
    assert repo.job_records.get_job_run('local', job_id).status == 'succeeded'
    assert calls.count('metadata') == 1
    count = len(starts)
    assert client.post(url, headers=headers, json=payload).json() == response.json()
    assert len(starts) == count  # Completed replay cannot start a stopped driver.
    with monkeypatch.context() as restarted:
        restarted.setattr(main, 'device_runtime', None)
        def offline_reader(*, budget):
            assert budget is None
            return service
        restarted.setattr(main, '_quality_inspection_use_case', offline_reader)
        restarted.setattr(main, '_start_device_runtime', lambda **kwargs:
                          (_ for _ in ()).throw(AssertionError('completed replay must not bind or recover runtime')))
        restarted.setattr(service, 'current_policy', lambda:
                          (_ for _ in ()).throw(AssertionError('completed replay must not inspect current budget')))
        assert client.post(url, headers=headers, json=payload).json() == response.json()
        assert client.get(query, headers=headers, params=payload).json()['receipt'] == response.json()
    with repo._connect() as connection:
        assert dict(connection.execute('SELECT * FROM device_work_requests WHERE id=?',
                                        (original['id'],)).fetchone()) == original
    assert checks.get('local', 'task')['check_id'] == check_id
