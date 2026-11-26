"""Resource imports select server-owned bytes and use the existing authorized ingress."""
from concurrent.futures import ThreadPoolExecutor
import json
from threading import Barrier, Event
from uuid import NAMESPACE_URL, uuid4, uuid5

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest

from app.editing_resource_catalog import editing_resource_catalog
from app.task_audio_routes import build_task_audio_router


@pytest.fixture
def resource_router(tmp_path,monkeypatch):
    from contextlib import contextmanager
    from test_assistant_audio_resources import setup_case
    from app.creation.audio_ingress_use_case import AudioIngressRejected
    repo,actor,media,runtime,_,factory=setup_case(tmp_path,monkeypatch)
    catalog=editing_resource_catalog()
    calls=[]
    @contextmanager
    def rejected(task,who,operation,**kwargs):
        stream=kwargs['stream']
        calls.append((task.id,operation,kwargs['filename'],kwargs['arguments'],stream,stream.read()))
        raise AudioIngressRejected(403,'original authorization rejected')
        yield
    monkeypatch.setattr(media.audio_ingress,'prepare_submission',rejected)
    case=factory()
    case.catalog_for=lambda:catalog
    case.path_for=lambda resource_id: __import__('app.editing_resource_catalog',fromlist=['resource_preview_path']).resource_preview_path(resource_id)
    unused = dict.fromkeys(('task_for','ingress_for','outputs_for','narration_for','records_for',
        'media_kind_for','actor_type_for','audio_for','file_response_for','preview_for'),lambda:None)
    app=FastAPI()
    app.include_router(build_task_audio_router(catalog_for=lambda:catalog,submit_for=lambda:None,
        workspace_for=lambda:lambda request:actor.workspace_id,actor_for=lambda:lambda request,ws:actor,
        runtime_for=lambda:lambda:runtime,resource_audio_for=lambda:case,
        ingress_error_type_for=lambda:AudioIngressRejected,http_error_type_for=lambda:HTTPException,
        route_names=('import_creation_task_audio_resource',),**unused))
    client=TestClient(app)
    yield client,catalog,calls
    client.close()


@pytest.mark.parametrize('payload,expected', [
    ({'resource_id': 'openfilm:sound-glass-pulse', 'version': 9}, 404),
    ({'resource_id': 'openfilm:transition-glitch', 'version': 2}, 422),
    ({'resource_id': 'skill:editing-videos', 'version': 1}, 422),
    ({'resource_id': '../../local.wav', 'version': 1}, 404),
    ({'resource_id': 'openfilm:sound-glass-pulse', 'version': 1, 'path': '/tmp/private.wav'}, 422),
    ({'resource_id': 'openfilm:sound-glass-pulse', 'version': True}, 422),
])
def test_resource_import_rejects_invalid_selection_before_submission(resource_router, payload, expected):
    client, _, calls = resource_router
    response = client.post('/api/creation-tasks/task/audio/resources', json=payload)
    assert response.status_code == expected, response.text
    assert calls == []


@pytest.mark.parametrize('status', ['catalog_only', 'planned', 'failed'])
def test_resource_import_rejects_unconnected_audio(resource_router, status):
    client, catalog, calls = resource_router
    resource = next(item for item in catalog.resources if item.id == 'openfilm:sound-glass-pulse')
    resource.status = status
    result = client.post('/api/creation-tasks/task/audio/resources', json={'resource_id': resource.id, 'version': resource.version})
    assert result.status_code == 409
    assert calls == []


def test_resource_import_uses_original_submit_and_closes_server_file_on_rejection(resource_router):
    client, catalog, calls = resource_router
    resource = next(item for item in catalog.resources if item.id == 'openfilm:sound-glass-pulse')
    result = client.post('/api/creation-tasks/task/audio/resources', json={'resource_id': resource.id, 'version': 1})
    assert result.status_code == 403 and result.json()['detail'] == 'original authorization rejected'
    task,operation,filename,arguments,stream,data=calls[0]
    assert task=='task' and operation=='audio_import'
    assert filename.endswith('.wav') and data[:4]==b'RIFF'
    assert stream.closed
    assert arguments['catalog_resource']=={'id':resource.id,'version':1,
        'provenance':[entry.model_dump(mode='json') for entry in resource.provenance]}
    assert arguments['_resource_request_id']



def test_missing_packaged_audio_is_not_submitted(resource_router, monkeypatch):
    client, _, calls = resource_router
    monkeypatch.setattr('app.editing_resource_catalog.resource_preview_path', lambda resource_id: None)
    result = client.post('/api/creation-tasks/task/audio/resources', json={'resource_id': 'openfilm:sound-glass-pulse', 'version': 1})
    assert result.status_code == 404 and calls == []


@pytest.mark.parametrize(('retry', 'legacy_window'), [
    pytest.param(False, False, id='first-attempt'),
    pytest.param(False, True, id='legacy-and-keyed-first-attempt'),
    pytest.param(True, False, id='same-rejected-predecessor'),
])
def test_manual_resource_attempt_claim_is_shared_across_windows(tmp_path, monkeypatch, retry, legacy_window):
    from app import main
    from app.creation.resource_audio_use_case import ResourceAudioUseCase
    from test_audio_ingress_application import audio_state, pcm_bytes

    repo, actor, _, runtime, client, *_ = audio_state(tmp_path, monkeypatch)
    url = '/api/creation-tasks/task/audio/resources'
    receipt_url = '/api/creation-tasks/task/audio/resource-requests/'
    resource = {'resource_id': 'openfilm:sound-glass-pulse', 'version': 1}
    legacy_id = str(uuid5(NAMESPACE_URL, str([
        actor.workspace_id, 'task', actor.identity_payload(), resource['resource_id'], resource['version'],
    ])))
    path = tmp_path / 'glass.wav'
    monkeypatch.setattr(main, 'resource_preview_path', lambda _id: path if path.is_file() else None)
    with repo._connect() as conn:
        # The shared journal reader uses actual task ownership.
        task = json.loads(conn.execute("SELECT payload FROM creation_tasks WHERE id='task'").fetchone()[0])
        task['owner_user_id'] = actor.member_id
        conn.execute("UPDATE creation_tasks SET payload=? WHERE id='task'", (json.dumps(task),))

    history_url = url + '/openfilm%3Asound-glass-pulse/history?version=1'
    previous = None
    started = Event()
    release = Event()
    submit_fresh = ResourceAudioUseCase._submit_fresh
    barrier = Barrier(2, timeout=10)

    def pause_winner(self, *args, **kwargs):
        if started.is_set():
            pytest.fail('another request passed the same atomic claim')
        started.set()
        assert release.wait(10), 'the second request did not recover the pending attempt'
        return submit_fresh(self, *args, **kwargs)

    def window(index):
        browser = TestClient(main.app, headers=client.headers)
        try:
            seen = browser.get(history_url)
            assert seen.status_code == 200, seen.text
            assert [item['status'] for item in seen.json()['requests']] == (['rejected'] if retry else [])
            locate = browser.get(url + '/request-id', params=selection)
            assert locate.status_code == 200, locate.text
            request_id = locate.json()['request_id']
            barrier.wait()
            payload = resource if legacy_window and index == 0 else {**selection, 'request_id': request_id}
            response = browser.post(url, json=payload)
            if response.status_code == 409:
                try:
                    assert started.is_set()
                    pending = response.json()['detail']
                    assert pending['request_id'] == request_id and pending['status'] == 'unknown'
                    assert pending['job_id'] is None
                    assert browser.get(receipt_url + request_id).json() == pending
                    rejected = browser.get(url + '/request-id', params={**resource, 'retry_of': request_id})
                    assert rejected.status_code == 409, rejected.text
                finally:
                    release.set()
            return request_id, response
        finally:
            browser.close()

    try:
        if retry:
            rejected = client.post(url, json=resource)  # Ordinary legacy HTTP request.
            assert rejected.status_code == 404, rejected.text
            previous = client.get(history_url).json()['requests'][0]['request_id']
            assert previous == legacy_id
            wrong = client.get(url + '/request-id', params={**resource, 'version': 2, 'retry_of': previous})
            assert wrong.status_code == 409 and '不一致' in wrong.text
            wrong = client.post(url, json={**resource, 'resource_id': 'other', 'retry_of': previous,
                                          'request_id': str(uuid4())})
            assert wrong.status_code == 409 and '不一致' in wrong.text
        selection = {**resource, **({'retry_of': previous} if retry else {})}
        before = repo.execution_records.list_task_events(actor.workspace_id, 'task')

        def forbidden(*args):
            pytest.fail('locating a request or rejecting its UUID must not start runtime or read media')

        with monkeypatch.context() as isolated:
            isolated.setattr(main, '_creation_runtime', forbidden)
            isolated.setattr(main, 'resource_preview_path', forbidden)
            locate = client.get(url + '/request-id', params=selection)
            assert locate.status_code == 200, locate.text
            arbitrary = client.post(url, json={**selection, 'request_id': str(uuid4())})
            assert arbitrary.status_code == 409 and '编号' in arbitrary.text
        assert repo.execution_records.list_task_events(actor.workspace_id, 'task') == before
        path.write_bytes(pcm_bytes())
        monkeypatch.setattr(ResourceAudioUseCase, '_submit_fresh', pause_winner)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(window, range(2)))
        assert results[0][0] == results[1][0] == locate.json()['request_id']
        request_id = results[0][0]
        assert (request_id == legacy_id) == (not retry)
        assert sorted(response.status_code for _, response in results) == [202, 409]
        job_id = next(response.json()['job_id'] for _, response in results if response.status_code == 202)
        completed = client.get(receipt_url + request_id).json()
        assert completed['request_id'] == request_id and completed['job_id'] == job_id
        assert completed['status'] == 'recorded'
        assert completed['submitted_input'] == {**resource, 'source': None, 'actor_identity': actor.identity_payload()}
        invalid_retry = client.get(url + '/request-id', params={**resource, 'retry_of': request_id})
        assert invalid_retry.status_code == 409, invalid_retry.text
        # The old unkeyed entry still points to its historical first request.
        legacy = client.post(url, json=resource)
        if retry:
            assert legacy.status_code == 409 and legacy.json()['detail']['request_id'] == previous
            assert client.get(receipt_url + previous).json()['submission_effect'] == 'not_submitted'
        else:
            assert legacy.status_code == 202 and legacy.json()['job_id'] == job_id
        same_attempt = client.post(url, json={**selection, 'request_id': request_id})
        assert same_attempt.status_code == 202 and same_attempt.json()['job_id'] == job_id
        with repo._connect() as conn:
            assert conn.execute('SELECT count(*) FROM job_runs').fetchone()[0] == 1
            assert conn.execute('SELECT count(*) FROM device_work_requests').fetchone()[0] == 1
        assert len(client.get(history_url).json()['requests']) == (2 if retry else 1)
    finally:
        runtime.stop(0)
        client.close()


def test_resource_retry_rejects_the_same_account_on_another_device(tmp_path, monkeypatch):
    from app import main
    from app.creation_execution import CreationExecutionActor
    from test_audio_ingress_application import audio_state

    repo, local_actor, media, runtime, client, *_ = audio_state(tmp_path, monkeypatch)
    workspace_id, device_id, user_id = (str(uuid4()) for _ in range(3))
    original = CreationExecutionActor.account(workspace_id, device_id, user_id)
    other_device = CreationExecutionActor.account(workspace_id, str(uuid4()), user_id)
    other_workspace = CreationExecutionActor.account(str(uuid4()), device_id, user_id)
    actors = {'original': original, 'other-device': other_device, 'other-workspace': other_workspace}
    monkeypatch.setattr(main, 'current_workspace_id', lambda request: actors[request.headers['X-Test-Actor']].workspace_id)
    monkeypatch.setattr(main, '_require_permit_actor', lambda request, _: actors[request.headers['X-Test-Actor']])
    monkeypatch.setattr(media, 'verify_identity', lambda actor: 'ready')
    monkeypatch.setattr(main, 'resource_preview_path', lambda _: None)
    task = repo.task_records.get_creation_task(local_actor.workspace_id, 'task')
    with repo._connect() as conn:
        payload = json.loads(conn.execute("SELECT payload FROM creation_tasks WHERE id=?", (task.id,)).fetchone()[0])
        payload.update(workspace_id=workspace_id, owner_user_id=user_id)
        conn.execute('UPDATE creation_tasks SET workspace_id=?, payload=? WHERE id=?',
                     (workspace_id, json.dumps(payload), task.id))
    url = '/api/creation-tasks/task/audio/resources'
    resource = {'resource_id': 'openfilm:sound-glass-pulse', 'version': 1}
    original_headers = {'X-Test-Actor': 'original'}
    try:
        previous = client.get(url + '/request-id', params=resource, headers=original_headers).json()['request_id']
        rejected = client.post(url, json=resource, headers=original_headers)
        assert rejected.status_code == 404 and '固定音频文件不可用' in rejected.text
        selection = {**resource, 'retry_of': previous}
        valid = client.get(url + '/request-id', params=selection, headers=original_headers)
        assert valid.status_code == 200, valid.text
        retry_id = valid.json()['request_id']
        assert retry_id != previous
        before = repo.execution_records.list_task_events(workspace_id, task.id)
        for name, status in [('other-device', 409), ('other-workspace', 404)]:
            headers = {'X-Test-Actor': name}
            assert client.get(url + '/request-id', params=selection, headers=headers).status_code == status
            assert client.post(url, json={**selection, 'request_id': retry_id}, headers=headers).status_code == status
        missing_task = client.get(url.replace('/task/', '/missing/') + '/request-id',
                                  params=selection, headers=original_headers)
        assert missing_task.status_code == 404
        assert repo.execution_records.list_task_events(workspace_id, task.id) == before
        with repo._connect() as conn:
            assert conn.execute('SELECT count(*) FROM job_runs').fetchone()[0] == 0
    finally:
        runtime.stop(0)
        client.close()
