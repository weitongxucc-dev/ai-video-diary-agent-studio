import logging
from types import SimpleNamespace

from app.creation_execution import CreationJobUnitExecutor


def test_render_completion_notification_preserves_failure_diagnostic(caplog):
    committed = SimpleNamespace(workspace_id='local', task_id='original-task', id='original-job', status='succeeded')
    calls = []
    def unavailable(workspace_id, task_id, job_id):
        assert committed.status == 'succeeded'
        calls.append((workspace_id, task_id, job_id))
        raise ValueError('controlled independent QC scheduling failure')
    executor = CreationJobUnitExecutor(SimpleNamespace(), render_action=lambda *_: None,
        render_is_valid=lambda *_: True, on_render_completed=unavailable)
    with caplog.at_level(logging.ERROR):
        executor._notify_render_completed(committed)
    assert calls == [('local', 'original-task', 'original-job')]
    assert committed.status == 'succeeded'
    assert 'Rendered output committed; independent quality scheduling remains pending' in caplog.text
    assert 'controlled independent QC scheduling failure' in caplog.text
    executor.request_stop()
    executor._notify_render_completed(committed)
    assert len(calls) == 1
