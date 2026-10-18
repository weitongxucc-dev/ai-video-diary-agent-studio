import base64
import hashlib

import pytest

import app.main as main
from app.knowledge_file_store import KnowledgeFileStore
from app.source_repository import SourceRepository
from test_creation_request_api import client_state  # noqa: F401
from tests.test_knowledge_office_ingestion import word_file


@pytest.fixture
def original_api(client_state, tmp_path, monkeypatch):
    c = client_state
    store = tmp_path / 'file-store'
    store.mkdir()
    repo = SourceRepository(tmp_path / 'sources.sqlite3', file_store=KnowledgeFileStore(store))
    monkeypatch.setattr(main, 'source_repository', repo)
    for path, handler, methods in [
        ('/api/source-items/file', main.import_knowledge_file, ['POST']),
        ('/api/source-items/{source_item_id}/save-to-knowledge', main.save_source_item_to_knowledge, ['POST']),
        ('/api/knowledge-items/{knowledge_item_id}/versions/{version}/original', main.download_knowledge_original, ['GET']),
    ]:
        c.client.app.add_api_route(path, handler, methods=methods)
    return c, repo, store


def upload_and_save(c, data):
    uploaded = c.client.post('/api/source-items/file', headers=c.alice,
        json={'filename': '工厂参数.docx', 'content_base64': base64.b64encode(data).decode()})
    assert uploaded.status_code == 200, uploaded.text
    source = uploaded.json()
    saved = c.client.post(f"/api/source-items/{source['id']}/save-to-knowledge", headers=c.alice, json={})
    assert saved.status_code == 200, saved.text
    return source, saved.json()


def test_real_upload_fixed_version_download_and_current_identity(original_api):
    c, repo, _store = original_api
    first, second = word_file('1200'), word_file('1500')
    source, knowledge = upload_and_save(c, first)
    url = f"/api/knowledge-items/{knowledge['id']}/versions/{knowledge['version']}/original"
    downloaded = c.client.get(url, headers=c.alice)
    assert downloaded.status_code == 200 and downloaded.content == first
    assert downloaded.headers['etag'] == '"' + hashlib.sha256(first).hexdigest() + '"'
    assert "filename*=UTF-8''" in downloaded.headers['content-disposition']
    assert c.client.get(url, headers=c.bob).status_code == 404
    denied = c.client.post(f"/api/source-items/{source['id']}/save-to-knowledge", headers=c.bob, json={})
    assert denied.status_code == 404
    _source, newer = upload_and_save(c, second)
    assert newer['id'] == knowledge['id'] and newer['version'] != knowledge['version']
    assert c.client.get(url, headers=c.alice).content == first
    assert c.client.get(f"/api/knowledge-items/{newer['id']}/versions/{newer['version']}/original", headers=c.alice).content == second
    assert c.client.get(f"/api/knowledge-items/{newer['id']}/versions/999/original", headers=c.alice).status_code == 404
    restarted = SourceRepository(repo._database_path, file_store=repo._original_files)
    assert restarted.read_knowledge_original('request-test', knowledge['id'], knowledge['version'], user_id=c.member.member_id).content == first


def test_original_corruption_missing_bytes_and_offline_store_have_distinct_http_results(original_api):
    c, _repo, store = original_api
    data = word_file()
    _source, knowledge = upload_and_save(c, data)
    url = f"/api/knowledge-items/{knowledge['id']}/versions/{knowledge['version']}/original"
    blob = next(path for path in store.rglob('*') if path.is_file())
    blob.write_bytes(b'X' + data[1:])
    assert c.client.get(url, headers=c.alice).status_code == 409
    blob.unlink()
    assert c.client.get(url, headers=c.alice).status_code == 404
    store.rename(store.with_name('offline-store'))
    response = c.client.get(url, headers=c.alice)
    assert response.status_code == 503
    assert not store.exists()
