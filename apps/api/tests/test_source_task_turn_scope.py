"""Source chat scopes use adopted metadata, never invented Script/Narration."""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from app.assistant.task_turn_scope_use_case import TaskTurnScopeUseCase, TaskTurnSelection, TaskTurnScopeRejected
from test_source_task_use_case import source_case as source_case  # noqa: F401


@pytest.fixture
def scope_case(source_case):  # noqa: F811 (shared metadata-only source fixture)
    case = source_case
    receipt = case.use_case.create(case.command, case.actor)
    case.task = case.repo.task_records.get_creation_task('local', receipt['task_id'])
    case.plan = case.repo.plan_records.get_creation_plan('local', case.task.plan_id)
    def forbidden(*args, **kwargs):
        pytest.fail('source turn scope must not require Script or Narration')
    case.scope = TaskTurnScopeUseCase(read_plan=case.repo.plan_records.get_creation_plan,
        read_script_inputs=forbidden, read_narration=forbidden, read_segments=forbidden,
        read_montage=case.repo.get_creation_task_montage_plan,
        read_version=case.repo.timeline_records.get_timeline_version,
        read_subtitles=lambda *args, **kwargs: [], read_covers=lambda *args, **kwargs: [])
    return case


@pytest.mark.parametrize('stage', ['montage', 'review', 'subtitle_cover'])
def test_source_task_can_read_and_continue_its_actual_editing_scope(scope_case, stage):
    case = scope_case
    assert case.scope.resolve('local', case.task, TaskTurnSelection(stage, [], None)) == (stage, [])


def test_source_selection_uses_real_clip_audio_cover_and_playhead_boundaries(scope_case):
    case = scope_case
    refs = ['clip:clip-source', 'audio:audio-source']
    assert case.scope.resolve('local', case.task, TaskTurnSelection('montage', refs, 1000)) == ('montage', refs)
    with pytest.raises(TaskTurnScopeRejected) as rejected:
        case.scope.resolve('local', case.task, TaskTurnSelection('montage', ['clip:other-task'], 1000))
    assert rejected.value.status_code == 422
    with pytest.raises(TaskTurnScopeRejected) as rejected:
        case.scope.resolve('local', case.task, TaskTurnSelection('montage', [], 10000))
    assert rejected.value.status_code == 422
    # Covers are independent editorial objects. A source task need not invent
    # subtitle tracks merely to select an existing cover in this stage.
    scope = replace(case.scope, read_covers=lambda *args, **kwargs: [SimpleNamespace(id='cover')])
    assert scope.resolve('local', case.task, TaskTurnSelection('subtitle_cover', ['cover:cover'], None)) == (
        'subtitle_cover', ['cover:cover'])


@pytest.mark.parametrize('mutation', ['plan_owner', 'plan_task', 'unconfirmed', 'missing_timeline', 'wrong_version_task', 'mixed_script'])
def test_source_task_requires_its_confirmed_owner_plan_and_current_timeline(scope_case, mutation):
    case = scope_case
    scope, task, plan = case.scope, case.task, case.plan
    if mutation == 'plan_owner':
        scope = replace(scope, read_plan=lambda *args: replace(plan, owner_user_id='someone-else'))
    elif mutation == 'plan_task':
        scope = replace(scope, read_plan=lambda *args: replace(plan, creation_task_id='other-task'))
    elif mutation == 'unconfirmed':
        scope = replace(scope, read_plan=lambda *args: replace(plan, status='draft'))
    elif mutation == 'missing_timeline':
        task = replace(task, current_timeline_version_id=None)
    elif mutation == 'wrong_version_task':
        version = case.repo.timeline_records.get_timeline_version('local', task.current_timeline_version_id)
        scope = replace(scope, read_version=lambda *args: replace(version, task_id='other-task'))
    else:
        task = replace(task, confirmed_script_id='invented-script')
    with pytest.raises(TaskTurnScopeRejected) as rejected:
        scope.resolve('local', task, TaskTurnSelection('montage', [], None))
    assert rejected.value.status_code == 409


def test_ordinary_missing_script_is_not_implicitly_promoted_to_source_mode(scope_case):
    case = scope_case
    ordinary = replace(case.plan, source_media=None)
    scope = replace(case.scope, read_plan=lambda *args: ordinary)
    with pytest.raises(TaskTurnScopeRejected, match='文案确认') as rejected:
        scope.resolve('local', case.task, TaskTurnSelection('montage', [], None))
    assert rejected.value.status_code == 409
    with pytest.raises(TaskTurnScopeRejected):
        case.scope.resolve('local', case.task, TaskTurnSelection('narration', [], None))
