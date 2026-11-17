"""Resource imports select server-owned bytes and use the existing authorized ingress."""
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest

from app.editing_resource_catalog import editing_resource_catalog
from app.task_audio_routes import build_task_audio_router


@pytest.fixture
def resource_router():
    catalog = editing_resource_catalog()
    calls = []
    def submit(task, request, operation, kind, *, audio, arguments):
        calls.append((task, request, operation, kind, audio, arguments, audio.file.read()))
        raise HTTPException(status_code=403, detail='original authorization rejected')
    unused = dict.fromkeys(('workspace_for', 'task_for', 'ingress_for', 'outputs_for',
        'ingress_error_type_for', 'narration_for', 'records_for', 'media_kind_for',
        'actor_for', 'runtime_for', 'actor_type_for', 'audio_for', 'file_response_for', 'preview_for'), lambda: None)
    app = FastAPI()
    app.include_router(build_task_audio_router(catalog_for=lambda: catalog, submit_for=lambda: submit,
        http_error_type_for=lambda: HTTPException, route_names=('import_creation_task_audio_resource',), **unused))
    client = TestClient(app)
    yield client, catalog, calls
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
    result = client.post('/api/creation-tasks/current-task/audio/resources', json={'resource_id': resource.id, 'version': 1})
    assert result.status_code == 403 and result.json()['detail'] == 'original authorization rejected'
    task, request, operation, kind, audio, arguments, data = calls[0]
    assert task == 'current-task' and request.url.path.endswith('/audio/resources')
    assert (operation, kind) == ('audio_import', 'audio_source')
    assert audio.filename.endswith('.wav') and data[:4] == b'RIFF'
    assert audio.file.closed
    assert arguments == {'catalog_resource': {'id': resource.id, 'version': 1,
        'provenance': [entry.model_dump(mode='json') for entry in resource.provenance]}}


def test_missing_packaged_audio_is_not_submitted(resource_router, monkeypatch):
    client, _, calls = resource_router
    monkeypatch.setattr('app.task_audio_routes.resource_preview_path', lambda resource_id: None)
    result = client.post('/api/creation-tasks/task/audio/resources', json={'resource_id': 'openfilm:sound-glass-pulse', 'version': 1})
    assert result.status_code == 404 and calls == []
