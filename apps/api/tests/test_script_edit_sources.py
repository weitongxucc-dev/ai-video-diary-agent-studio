"""Saved library drafts read source choices without a generated-topic context."""
from app import main
from test_script_library import library, reuse, save  # noqa: F401


def test_library_draft_sources_use_current_permissions_without_topic_or_body(library, monkeypatch):
    client, repo, sources = library
    created = reuse(client, save(client, repo)['id']).json()
    task_id, script_id = created['task_id'], created['script_id']
    task = repo.get_creation_task('local', task_id)
    assert task.selected_topic_id is None
    before = repo.script_records.read_creation_task_script_header('local', script_id)
    before_events = repo.execution_records.list_task_events('local', task_id)

    def forbidden(*args, **kwargs):
        raise AssertionError('source choices must not load script bodies or call a model')

    monkeypatch.setattr(repo.script_records, 'get_creation_task_script', forbidden)
    monkeypatch.setattr(repo.script_records, 'get_script_library_entry', forbidden)
    monkeypatch.setattr(main, '_creation_task_script_generator_for', forbidden)
    url = f'/api/creation-tasks/{task_id}/scripts/{script_id}/sources'
    response = client.get(url)
    assert response.status_code == 200, response.text
    assert response.json() == {
        'task_id': task_id, 'script_id': script_id,
        'sources': [{'reference_id': 'knowledge:knowledge_own:1', 'title': '本人产品资料'}],
    }
    assert client.get(f'/api/creation-tasks/task_original/scripts/{script_id}/sources').status_code == 404
    assert client.get(f'/api/creation-tasks/{task_id}/scripts/missing/sources').status_code == 404
    sources.set_knowledge_item_use_state('local', 'knowledge_own', 'excluded')
    refused = client.get(url)
    assert refused.status_code == 409, refused.text
    assert repo.script_records.read_creation_task_script_header('local', script_id) == before
    assert repo.execution_records.list_task_events('local', task_id) == before_events
