"""A completed one-step gain save/readback must not create a redundant goal."""
from types import SimpleNamespace
from uuid import UUID, uuid4

from fastapi import Request
import pytest

from app import main
from app.audio_timeline import AudioEditRequest
from app.conversation_repository import ConversationRepository
from app.schemas import CreationTaskExactEditRequest
from test_audio_edit_application import edit_state


MESSAGE = '把背景钢琴音乐调大3分贝，原声保持不变。保存修改后告诉我调整前后的音乐音量。'


def prepare(tmp_path, monkeypatch, message=MESSAGE, delta=3, *, role='music', cite_clip=False):
    repo, actor, media, runtime, client, base, graph, path, calls = edit_state(tmp_path, monkeypatch)
    audio = media.audio_edit
    task = repo.task_records.get_creation_task('local', 'task')
    reference = graph.tracks[0].clips[0].source_ref
    preview = audio.preview('local', 'task', AudioEditRequest(base_version_id=task.current_timeline_version_id,
        action='add', target_id='audio-music', values={'source_ref':reference,'name':'钢琴',
            'source_start_ms':0,'source_end_ms':200,'timeline_start_ms':0,'gain_db':-18}), actor, draft_only=True)
    adopted = audio.confirm(task, preview.proposal, actor)
    clip = next(track for track in adopted.montage.audio_timeline.tracks if track.role == role).clips[0]
    task = repo.task_records.get_creation_task('local', 'task')
    version = repo.timeline_records.get_timeline_version('local', task.current_timeline_version_id)
    conversations = ConversationRepository(repo.database_path)
    monkeypatch.setattr(main, 'conversation_repo', conversations)
    chat = conversations.create('local', actor.member_id, 'task', 'task')
    conversations.save_draft(chat['id'], 'local', actor.member_id, message, 0)
    turn, _ = conversations.create_turn_idempotent(chat['id'], 'local', actor.member_id, message, str(uuid4()), 1,
        request_context={'kind':'task','task_id':'task','stage':'montage','target_snapshot':{
            'timeline_version_id':version.id,'timeline_sequence':version.sequence,
            'selected_references':[{'kind':'audio','object_id':clip.id,'version':version.id,'task_id':'task'}] if cite_clip else []}})
    case = main._exact_preview_use_case()
    payload = CreationTaskExactEditRequest(operation_type='EditAudioTimeline', request_id=uuid4(),
        assistant_turn_id=UUID(turn['id']), expected_timeline_version_id=version.id,
        expected_timeline_sequence=version.sequence, audio_edit={'action':'update','target_id':clip.id,
            'values':{'gain_db':clip.gain_db+delta}})
    saved = case.apply(task, actor, payload, identity=audio.identity(actor))
    assert saved.status_code == 200
    locator = case.read_request_receipt(task, actor.member_id, payload.request_id, 'apply')
    locator['query_url'] = f'/api/creation-tasks/task/assistant/edit-requests/{payload.request_id}?phase=apply'
    return SimpleNamespace(repo=repo,conversations=conversations,chat=chat,turn=turn,locator=locator,
        actor=actor,client=client,case=case,payload=payload)


def test_saved_music_gain_returns_actual_values_and_does_not_schedule_an_empty_continuation(tmp_path, monkeypatch):
    state = prepare(tmp_path, monkeypatch)
    completion = state.conversations.completed_audio_gain_edit(state.turn['id'], state.locator)
    assert completion['gain_before_db'] == -18 and completion['gain_after_db'] == -15
    monkeypatch.setattr(main, '_goal_service', lambda: SimpleNamespace(
        register_turn=lambda turn_id: state.conversations.register_task_goal(turn_id)))
    monkeypatch.setattr(main, '_wake_assistant_continuations', lambda: None)
    request = Request({'type':'http','method':'POST','path':'/','headers':[
        (b'x-local-member-token',state.client.headers['X-Local-Member-Token'].encode())]})
    result = main._task_turn_http_adapter().finish('task',request,SimpleNamespace(
        conversation_id=UUID(state.chat['id']),expected_draft_version=1),state.turn['id'],
        '修改已保存',{'kind':'edit_request_status',**state.locator},'montage')
    assert '-18 dB' in result.reply and '-15 dB' in result.reply and '原声保持不变' in result.reply
    restarted = ConversationRepository(state.repo.database_path)
    persisted = restarted.get_turn(state.turn['id'])
    assert persisted['status'] == 'completed'
    assert persisted['result']['completed_local_edit'] == completion
    assert restarted.get_task_goal(state.turn['id']) is None
    assert restarted.goal_event_candidates() == []


@pytest.mark.parametrize('message,delta', [
    ('把背景钢琴音乐调大3分贝，然后制作成片。',3),
    ('把背景钢琴音乐调大3分贝，检查1、4、7秒的音频。',3),
    (MESSAGE,2),
])
def test_incomplete_instruction_or_wrong_effect_is_not_completed(tmp_path,monkeypatch,message,delta):
    state = prepare(tmp_path,monkeypatch,message,delta)
    assert state.conversations.completed_audio_gain_edit(state.turn['id'],state.locator) is None
    state.conversations.finish_turn(state.turn['id'],'completed','已完成',result={
        'response':{'edit_request':state.locator},
        'goal_business_actions':[{'kind':'edit_request','id':str(state.payload.request_id)}],
        'completed_local_edit':{'scope':'single_music_gain_edit'}})
    goal=state.conversations.register_task_goal(state.turn['id'])
    assert goal is not None and goal['status']=='waiting_result'


def test_history_read_and_unknown_locator_cannot_certify_a_local_gain_edit(tmp_path,monkeypatch):
    state = prepare(tmp_path,monkeypatch)
    assert state.conversations.completed_audio_gain_edit(state.turn['id'],{**state.locator,'status':'unknown'}) is None
    chat=state.conversations.create('local',state.actor.member_id,'task','task')
    state.conversations.save_draft(chat['id'],'local',state.actor.member_id,MESSAGE,0)
    later,_=state.conversations.create_turn_idempotent(chat['id'],'local',state.actor.member_id,MESSAGE,str(uuid4()),1,
        request_context={'kind':'task','task_id':'task'})
    assert state.conversations.completed_audio_gain_edit(later['id'],state.locator) is None



@pytest.mark.parametrize('extra,cited,expected', [('', True, True), ('再生成成片。', True, False), ('', False, False)])
def test_narration_gain_requires_one_bound_clip_and_no_pending_work(tmp_path, monkeypatch, extra, cited, expected):
    message = '把已引用的这一段配音音量从 0 dB 降低 3 dB 至 −3 dB，直接保存到当前草稿。只改该片段的音量，不生成预览，不重新出片。'
    state = prepare(tmp_path, monkeypatch, message + extra, -3, role='narration', cite_clip=cited)
    receipt = state.conversations.completed_audio_gain_edit(state.turn['id'], state.locator)
    if not expected:
        assert receipt is None
        return
    assert receipt['target_id'] == state.payload.audio_edit.target_id
    assert (receipt['gain_before_db'], receipt['gain_after_db']) == (0, -3)
    assert receipt['scope'] == 'single_narration_gain_edit'
    state.conversations.finish_turn(state.turn['id'], 'completed', receipt['summary'], result={
        'response': {'edit_request': state.locator},
        'goal_business_actions': [{'kind': 'edit_request', 'id': str(state.payload.request_id)}],
        'completed_local_edit': receipt})
    assert state.conversations.register_task_goal(state.turn['id']) is None
