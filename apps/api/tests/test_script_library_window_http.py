"""Library browsing reads metadata and actual indexed body windows."""
import pytest
from uuid import uuid4

from app import main
from app.creation.script_contracts import CreationTaskScriptSection
from test_script_library import library  # noqa: F401


def _bind_library_member(library, monkeypatch):
    from app.local_members import LocalMemberService
    client, repo, sources = library
    members = LocalMemberService(repo.database_path)
    # The request holder must differ from the source owner: making a task
    # private revokes a shared reader, while its owner remains entitled to read.
    member, token = members.join(members.create_invite_code(), '文案库使用者')
    assert member.member_id != repo.get_creation_task('local', 'task_original').owner_user_id
    monkeypatch.setattr(main, 'local_members_service', members)
    client.headers['X-Local-Member-Token'] = token
    repo.set_creation_task_visibility('local', 'task_original', 'shared')
    sources.set_knowledge_item_visibility('local', 'knowledge_own', 'shared')
    return client, repo, sources


def test_library_write_requests_replay_original_receipts_without_full_bodies(library, monkeypatch):
    client, repo, sources = _bind_library_member(library, monkeypatch)
    script = repo.script_records.read_creation_task_script_header('local', 'script_original')
    def no_body(*args, **kwargs):
        pytest.fail('library request must not hydrate a full body')
    monkeypatch.setattr(repo.script_records, 'get_creation_task_script', no_body)
    monkeypatch.setattr(repo.script_records, 'get_script_library_entry', no_body)
    save_input = dict(request_id=str(uuid4()), expected_source_updated_at=script['updated_at'],
                      source_script_id='script_original', name='原请求保存', category='产品')
    saved = client.post('/api/production/script-library', json=save_input)
    assert saved.status_code == 200, saved.text
    receipt = saved.json()
    assert receipt['entry']['sections'] is None and receipt['entry']['sections_total_count'] == 1
    assert client.post('/api/production/script-library', json=save_input).json() == receipt
    query = f"/api/production/script-library-requests/{save_input['request_id']}?operation=save"
    assert client.get(query).json() == receipt
    assert client.post('/api/production/script-library', json={**save_input, 'name': '换输入'}).status_code == 409
    reuse_input = dict(request_id=str(uuid4()), expected_entry_updated_at=receipt['entry']['updated_at'],
                       name='复用的新任务', aspect_ratio='16:9', duration_target_seconds=60,
                       requirements_confirmed=True)
    url = f"/api/production/script-library/{receipt['entry_id']}/reuse"
    reused = client.post(url, json=reuse_input)
    assert reused.status_code == 200, reused.text
    result = reused.json()
    assert 'script' not in result and result['entry_version'] == receipt['version']
    assert client.post(url, json=reuse_input).json() == result
    assert client.get(f"/api/production/script-library-requests/{reuse_input['request_id']}?operation=reuse").json() == result
    assert client.post(url, json={**reuse_input, 'name': '换要求'}).status_code == 409
    task = repo.get_creation_task('local', result['task_id'])
    assert task.confirmed_script_id is None and task.stage == 'script'
    with repo._connect() as conn:
        assert conn.execute('SELECT count(*) FROM creation_tasks').fetchone()[0] == 2
        assert conn.execute('SELECT count(*) FROM script_library_entries').fetchone()[0] == 1
    # Source permission may change after a successful write. Original request
    # replay must recover the saved task, while a fresh request remains blocked.
    sources.set_knowledge_item_use_state('local', 'knowledge_own', 'excluded')
    assert client.post(url, json=reuse_input).json() == result
    assert client.post(url, json={**reuse_input, 'name': '撤权后换要求'}).status_code == 409
    assert client.post(url, json={**reuse_input, 'request_id': str(uuid4())}).status_code == 409
    repo.set_creation_task_visibility('local', 'task_original', 'private')
    assert client.post(url, json=reuse_input).json() == result
    assert client.get(f"/api/production/script-library-requests/{reuse_input['request_id']}?operation=reuse").json() == result
    with repo._connect() as conn:
        assert conn.execute('SELECT count(*) FROM creation_tasks').fetchone()[0] == 2
    monkeypatch.setattr(main, '_content_reader', lambda *a: ('other-member', False))
    assert client.get(query).status_code == 404
    assert client.get(f"/api/production/script-library-requests/{reuse_input['request_id']}?operation=reuse").status_code == 404


def test_library_reuse_rechecks_source_permission_at_commit_without_losing_saved_receipt(library, monkeypatch):
    client, repo, sources = _bind_library_member(library, monkeypatch)
    header = repo.script_records.read_creation_task_script_header('local', 'script_original')
    saved = client.post('/api/production/script-library', json=dict(request_id=str(uuid4()),
        expected_source_updated_at=header['updated_at'], source_script_id=header['id'], name='来源撤权代表'))
    assert saved.status_code == 200, saved.text
    receipt = saved.json()
    original = repo.script_records.reuse_library_rows
    commit_attempted = []
    def revoke_then_commit(*args, **kwargs):
        commit_attempted.append(True)
        sources.set_knowledge_item_use_state('local', 'knowledge_own', 'excluded')
        return original(*args, **kwargs)
    monkeypatch.setattr(repo.script_records, 'reuse_library_rows', revoke_then_commit)
    response = client.post(f"/api/production/script-library/{receipt['entry_id']}/reuse", json=dict(
        request_id=str(uuid4()), expected_entry_updated_at=receipt['entry']['updated_at'],
        name='不得生成的新任务', requirements_confirmed=True))
    assert response.status_code == 409, response.text
    assert commit_attempted == [True]
    with repo._connect() as conn:
        assert conn.execute('SELECT count(*) FROM creation_tasks').fetchone()[0] == 1
    # Read permission and permission to reuse cited sources are separate.
    query = f"/api/production/script-library-requests/{receipt['request_id']}?operation=save"
    assert client.get(query).json() == receipt
    repo.set_creation_task_visibility('local', 'task_original', 'private')
    assert client.get(query).status_code == 404


def test_library_deep_link_reads_header_without_loading_body(library, monkeypatch):
    client, repo, _ = library
    entry = repo.script_records.save_script_library_entry_header('local',
        source_script_id='script_original', name='指定库稿', category='产品')
    monkeypatch.setattr(repo.script_records, 'get_script_library_entry',
        lambda *a: pytest.fail('deep link cannot hydrate the body'))
    url = f"/api/production/script-library/{entry['id']}"
    result = client.get(url)
    assert result.status_code == 200, result.text
    assert result.json()['sections'] is None and result.json()['sections_total_count'] == 1
    assert result.json()['source_script_id'] == 'script_original'
    monkeypatch.setattr(main, 'current_workspace_id', lambda _: 'another-workspace')
    assert client.get(url).status_code == 404


def test_library_http_reads_45_sections_without_full_body_getters(library, monkeypatch):
    client, repo, _ = library
    # Extend the manual fixture before publishing it into the library.
    script = repo.get_creation_task_script('local', 'script_original')
    script.sections = [CreationTaskScriptSection(f'section_{i}', f'特点{i}',
        '模块化结构方便维护。', '产品特写', ['knowledge:knowledge_own:1']) for i in range(45)]
    with repo._connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        conn.execute('UPDATE creation_task_scripts SET payload=? WHERE id=?',
                     (repo._script_payload(conn, script), script.id))
    entry = repo.script_records.save_script_library_entry_header('local',
        source_script_id=script.id, name='长稿', category='产品')
    repo.script_records.save_script_library_entry_header('local',
        source_script_id=script.id, name='长稿第二版', category='产品')
    def no_full_body(*args, **kwargs):
        pytest.fail('library browse must not hydrate a full script or library entry')
    monkeypatch.setattr(repo.script_records, 'get_script_library_entry', no_full_body)
    monkeypatch.setattr(repo.script_records, 'list_script_library_entries', no_full_body)
    monkeypatch.setattr(repo.script_records, 'get_creation_task_script', no_full_body)

    listing = client.get('/api/production/script-library', params={'limit': 1})
    assert listing.status_code == 200, listing.text
    page = listing.json()
    assert page['total_count'] is None and page['next'] is not None
    assert len(page['items']) == 1
    assert page['items'][0]['sections'] is None
    assert page['items'][0]['sections_loaded'] is False
    assert page['items'][0]['sections_total_count'] == 45
    later = client.get('/api/production/script-library', params={'limit': 1, **page['next']})
    assert later.status_code == 200, later.text
    assert later.json()['items'][0]['id'] != page['items'][0]['id']
    assert later.json()['next'] is None

    url = f"/api/production/script-library/{entry['id']}/sections"
    sections, cursor, version = [], 0, None
    for size in (20, 20, 5):
        params = {'after_ordinal': cursor}
        if version: params['expected_updated_at'] = version
        response = client.get(url, params=params)
        assert response.status_code == 200, response.text
        window = response.json()
        assert len(window['items']) == size and window['total_count'] == 45
        sections.extend(item['section']['id'] for item in window['items'])
        cursor, version = window['next_after_ordinal'], window['updated_at']
    assert cursor is None and sections == [f'section_{i}' for i in range(45)]
    assert client.get(url, params={'after_ordinal': 20}).status_code == 422
    assert client.get(url, params={'expected_updated_at': '2000-01-01T00:00:00Z'}).status_code == 409
    monkeypatch.setattr(main, 'current_workspace_id', lambda _: 'another-workspace')
    assert client.get(url).status_code == 404
