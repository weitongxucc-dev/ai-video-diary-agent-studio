from dataclasses import replace

import pytest

from app.creation.contracts import EditCommand
from app.schemas import CreationTaskExactEditRequest
from app.montage import creation_task_montage_plan_to_payload
from test_linked_speed_application import speed_state
from test_linked_edit_use_cases import linked_state as linked_state


def command(state, ranges):
    return EditCommand('ws', state.task.id, state.identity, state.base.id, 'RemoveTimelineRanges',
        {'base_version_id': state.base.id, 'range_edit': {'removed_ranges_ms': ranges,
            'reason': '删除已定位重说，保留完整限定与其余人工修改'}})


def test_multiple_ranges_use_one_linked_transaction_and_preserve_source_clock(tmp_path):
    state = speed_state(tmp_path)
    before = state.plan.duration_seconds
    outcome = state.application.apply(command(state, [[250, 500], [1250, 1500]]))
    assert outcome.operation.operation_type == 'RemoveTimelineRanges'
    assert outcome.montage.duration_seconds == before - .5
    assert outcome.task.current_timeline_version_id == outcome.operation.result_version_id
    graph = outcome.montage.audio_timeline
    assert graph.tracks[0].clips[0].source_ref == state.plan.audio_timeline.tracks[0].clips[0].source_ref
    assert graph.tracks[0].clips[1].timeline_start_ms == 250
    assert graph.tracks[0].clips[1].source_start_ms == 500
    saved = state.repo.subtitle_records.list_subtitle_tracks('ws', state.task.id,
        timeline_version_id=outcome.operation.result_version_id)
    assert saved  # Captions are committed in the same result version, not left on the old clock.


def test_range_edit_rejects_locked_sources_and_all_content_removal(tmp_path):
    state = speed_state(tmp_path)
    for ranges in [[[0, round(state.plan.duration_seconds * 1000)]], [[0, 999999]]]:
        with pytest.raises(ValueError):
            state.application.apply(command(state, ranges))
    plan = replace(state.plan)
    plan.clips[0].locked = True
    state.repo.montage_plan_adoption.save_creation_task_montage_plan('ws', state.task.id, plan.id,
        creation_task_montage_plan_to_payload(plan))
    with pytest.raises(ValueError, match='锁定'):
        state.application.apply(command(state, [[250, 500]]))
    assert state.repo.task_records.get_creation_task('ws', state.task.id).current_timeline_version_id == state.base.id


@pytest.mark.parametrize('ranges', [[[10, 20], [15, 25]], [[True, 100]], [[0, 0]], [[20, 30], [0, 10]]])
def test_exact_contract_rejects_ambiguous_or_non_integer_clock(ranges):
    with pytest.raises(ValueError):
        CreationTaskExactEditRequest(operation_type='RemoveTimelineRanges',
            range_edit={'removed_ranges_ms': ranges, 'reason': '语义精剪'})


def test_exact_http_range_edit_saves_one_version_and_replays_original_request(linked_state):
    import app.main as main
    from fastapi.testclient import TestClient
    from uuid import uuid4
    repo, task, plan, track, identity, verifier = linked_state
    client = TestClient(main.app)
    evidence = client.get('/api/production/materials/asset/evidence?limit=1')
    assert evidence.status_code == 200, evidence.text
    assert evidence.json()['asset_id'] == 'asset' and 'file_path' not in evidence.text
    body = {'operation_type': 'RemoveTimelineRanges', 'range_edit': {
        'removed_ranges_ms': [[3250, 3500], [4250, 4500]], 'reason': '保留锁定片头和限定，按已读时间范围删除重说'},
        'request_id': str(uuid4()), 'expected_timeline_version_id': task.current_timeline_version_id,
        'expected_timeline_sequence': 2}
    response = client.post('/api/creation-tasks/task/edits/exact', json=body)
    assert response.status_code == 200, response.text
    actual = repo.get_creation_task_montage_plan('local', 'task')
    assert actual['duration_seconds'] == plan.duration_seconds - .5
    head = repo.task_records.get_creation_task('local', 'task').current_timeline_version_id
    replay = client.post('/api/creation-tasks/task/edits/exact', json=body)
    assert replay.status_code == 200 and replay.json() == response.json()
    assert repo.task_records.get_creation_task('local', 'task').current_timeline_version_id == head
