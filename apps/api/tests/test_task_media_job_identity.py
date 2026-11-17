"""Task-bound media IDs with existing per-task idempotency and legacy receipts."""
from dataclasses import replace

import pytest

from test_audio_ingress_application import audio_state, pcm_bytes


@pytest.mark.parametrize('legacy_first_job', [False, True])
def test_same_audio_in_two_tasks_has_distinct_jobs_and_original_retries(tmp_path, monkeypatch, legacy_first_job):
    repo, actor, media, runtime, client, _, executions = audio_state(tmp_path, monkeypatch)
    plan = repo.plan_records.get_creation_plan('local', 'plan')
    task = repo.task_records.get_creation_task('local', 'task')
    repo.plan_confirmation.confirm_creation_plan(replace(plan, id='other-plan'),
        replace(task, id='other-task', plan_id='other-plan'))
    data = pcm_bytes()

    def submit(task_id):
        response = client.post(f'/api/creation-tasks/{task_id}/audio/import',
            files={'audio': ('same.wav', data, 'audio/wav')})
        assert response.status_code == 202, response.text
        return response.json()['job_id']

    try:
        with monkeypatch.context() as legacy:
            if legacy_first_job:
                prepare = media.prepare_submission

                def old_prepare(*args, **kwargs):
                    run, payload = prepare(*args, **kwargs)
                    return replace(run, id='job_media_' + run.input_digest), payload

                legacy.setattr(media, 'prepare_submission', old_prepare)
            first = submit('task')
        second = submit('other-task')
        assert first != second
        assert submit('task') == first
        assert submit('other-task') == second
        first_run = repo.job_records.get_job_run('local', first)
        second_run = repo.job_records.get_job_run('local', second)
        assert first_run.input_digest == second_run.input_digest
        assert first_run.idempotency_key == f'task-media:task:{first_run.input_digest}'
        assert second_run.idempotency_key == f'task-media:other-task:{second_run.input_digest}'
        if legacy_first_job:
            assert first == 'job_media_' + first_run.input_digest
        for task_id, job_id in [('task', first), ('other-task', second)]:
            assert [run.id for run in repo.job_records.list_job_runs('local', task_id)] == [job_id]
            event = repo.execution_records.get_task_event('local', f'event_{job_id}_start')
            assert event.task_id == task_id and event.job_id == job_id
        assert executions == []  # Submission/replay must not execute media.
    finally:
        runtime.stop(1)
        client.close()
