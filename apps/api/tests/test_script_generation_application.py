"""Generation preflight keeps a durable failure without invoking a provider."""
import pytest

import app.main as main
from test_creation_task_script_grounding import api_state  # noqa: F401


def test_generation_source_preflight_records_original_run_without_model(api_state, monkeypatch):
    state = api_state
    state.sources.set_knowledge_item_use_state('local', 'fixture', 'excluded')
    monkeypatch.setattr(main, '_creation_task_script_generator_for',
        lambda _: pytest.fail('preflight failure must not construct or invoke a provider'))
    response = state.client.post('/api/creation-tasks/task/scripts/generate', json={})
    assert response.status_code == 503, response.text
    run = state.repository.script_records.latest_script_generation_run('local', 'task')
    assert run is not None and run.status == 'failed'
    assert run.reason == response.json()['detail']['reason']
    assert run.finished_at is not None
    assert state.repository.script_records.list_creation_task_scripts('local', 'task') == []
    assert state.verifications == []
