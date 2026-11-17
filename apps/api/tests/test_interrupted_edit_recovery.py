"""Real owned-child interruption after a real temporary-store metadata commit."""
import json
import os
import sys
import threading
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from app import main
from app.audio_timeline import AudioEditRequest
from app.assistant.process_runner import run_pi_streaming
from app.assistant.projection import _PiStreamProjector, _business_event_lines
from app.assistant.turn_control import TurnControl, _terminate_process_group
from app.assistant_goal_continuation import GoalResultProjection
from app.conversation_repository import ConversationRepository
from app.creation.editing.metadata_use_case import EditPreviewRejected
from app.schemas import CreationTaskExactEditRequest
from test_audio_edit_application import edit_state


def prepare(tmp_path, monkeypatch):
    repo,actor,media,runtime,client,base,graph,path,calls=edit_state(tmp_path,monkeypatch)
    audio=media.audio_edit
    task=repo.task_records.get_creation_task('local','task')
    preview=audio.preview('local','task',AudioEditRequest(base_version_id=task.current_timeline_version_id,
        action='add',target_id='audio-music',values={'source_ref':graph.tracks[0].clips[0].source_ref,
            'name':'钢琴','source_start_ms':0,'source_end_ms':200,'timeline_start_ms':0,'gain_db':-18}),actor,draft_only=True)
    adopted=audio.confirm(task,preview.proposal,actor)
    music=next(track for track in adopted.montage.audio_timeline.tracks if track.role=='music')
    task=repo.task_records.get_creation_task('local','task')
    version=repo.timeline_records.get_timeline_version('local',task.current_timeline_version_id)
    conversations=ConversationRepository(repo.database_path)
    monkeypatch.setattr(main,'conversation_repo',conversations)
    chat=conversations.create('local',actor.member_id,'task','task')
    message='把背景钢琴音乐调大1分贝，原声保持不变。'
    conversations.save_draft(chat['id'],'local',actor.member_id,message,0)
    turn,_=conversations.create_turn_idempotent(chat['id'],'local',actor.member_id,message,str(uuid4()),1,
        request_context={'kind':'task','task_id':'task','stage':'montage','target_snapshot':{
            'timeline_version_id':version.id,'timeline_sequence':version.sequence}})
    payload=CreationTaskExactEditRequest(operation_type='EditAudioTimeline',request_id=uuid4(),
        assistant_turn_id=UUID(turn['id']),expected_timeline_version_id=version.id,expected_timeline_sequence=version.sequence,
        audio_edit={'action':'update','target_id':music.clips[0].id,'values':{'gain_db':-17}})
    return SimpleNamespace(repo=repo,actor=actor,audio=audio,conversations=conversations,turn=turn,task=task,
        case=main._exact_preview_use_case(),payload=payload,path=path,base=version)


class OwnedControl(TurnControl):
    def launch(self,*args,**kwargs):
        self.child=super().launch(*args,**kwargs)
        return self.child


def interrupt_after_commit(state,tmp_path):
    """Pi bridge protocol child; the real host remains the sole DB writer."""
    committed=threading.Event()
    result=[]
    errors=[]
    control=OwnedControl(_terminate_process_group)
    saved=[]
    def business(operation,arguments,**options):
        try:
            assert operation=='apply_task_edit'
            assert options['source_binding']['turn_id']==state.turn['id']
            payload=CreationTaskExactEditRequest.model_validate(arguments['payload'])
            outcome=state.case.apply(state.task,state.actor,payload,identity=state.audio.identity(state.actor))
            assert outcome.status_code==200
            saved.append(outcome.value)
            return {'ok':True,'value':outcome.value}
        except Exception as error:
            errors.append(error)
            raise
        finally:
            committed.set()
    script='''import json,sys,signal
args=json.loads(sys.stdin.readline())
print(json.dumps({'type':'tool_execution_start','toolName':'apply_typed_edit','toolCallId':'edit'}),flush=True)
print(json.dumps({'type':'diary_business_call','call_id':'save','tool_call_id':'edit',
 'operation':'apply_task_edit','arguments':{'payload':args['payload']}}),flush=True)
sys.stdin.readline()
while True: signal.pause()
'''
    def run():
        try:
            result.append(run_pi_streaming([sys.executable,'-u','-c',script],
                message=json.dumps({'controlEnabled':True,'payload':state.payload.model_dump(mode='json',exclude_unset=True)})+'\n',
                env=os.environ.copy(),cwd=tmp_path,timeout_seconds=8,on_event=lambda event:None,
                control=control,terminate=_terminate_process_group,projector_factory=_PiStreamProjector,
                business_event_lines=_business_event_lines,turn_id=state.turn['id'],business_handler=business,
                source_binding={'turn_id':state.turn['id'],'workspace_id':'local','member_id':state.actor.member_id,'task_id':'task'}))
        except Exception as error:
            errors.append(error)
    worker=threading.Thread(target=run)
    worker.start()
    try:
        assert committed.wait(5),errors
        assert not errors,repr(errors)
        receipt=state.case.read_request_receipt(state.task,state.actor.member_id,state.payload.request_id,'apply')
        assert receipt['result_status_code']==200
        assert control.child.pid!=os.getpid() and control.child.poll() is None
        _terminate_process_group(control.child)  # Captured identity, only this test's child/group.
        worker.join(5)
        assert not worker.is_alive() and not errors,errors
        assert len(result)==1 and result[0].returncode!=0
        return saved[0]
    finally:
        control.stop_all()
        worker.join(5)
        assert not worker.is_alive()


def recover(state, projection):
    candidates=projection.interrupted_edit_candidates()
    assert len(candidates)==1
    def no_execution(*args):
        pytest.fail('recovery must not submit, render, encode previews or wake a model')
    assert projection.project_request_candidate(candidates[0],runtime_available=lambda:False,
        verify_actor=lambda actor:'ready' if actor==state.actor else 'forbidden',
        read_request=state.case.read_request_result,encode_preview=no_execution,encode_request_job=no_execution,
        observe=lambda *args:False,wake=no_execution)
    assert projection.interrupted_edit_candidates()==[]


@pytest.mark.parametrize('terminal',['pi_failure','api_restart','stopped','prior_proposal_event'])
def test_saved_edit_survives_owned_child_death_and_recovers_exactly_once(tmp_path,monkeypatch,terminal):
    state=prepare(tmp_path,monkeypatch)
    before_operations=state.repo.edit_records.list_edit_operations('local','task')
    original=state.repo.get_creation_task_montage_plan('local','task')['audio_timeline']['tracks'][0]
    source_bytes=state.path.read_bytes()
    saved=interrupt_after_commit(state,tmp_path)
    operation=saved['operation']
    if terminal=='api_restart':
        assert ConversationRepository(state.repo.database_path).fail_interrupted_turns()==1
    else:
        state.conversations.finish_turn(state.turn['id'],'stopped' if terminal=='stopped' else 'failed',
            result={'kind':'failure','message':'owned Pi child interrupted'})
    restarted=ConversationRepository(state.repo.database_path)
    projection=GoalResultProjection(state.repo,restarted)
    if terminal=='prior_proposal_event':
        restarted.project_business_result(state.turn['id'],'local',state.actor.member_id,
            {'type':'business_result','event_id':'earlier-proposal','phase':'apply','status':'confirmed','task_id':'task',
             'operation_id':operation['id'],'base_version_id':operation['base_version_id'],
             'result_version_id':operation['result_version_id']},response_patch={'timeline_action':'apply'},message='saved')
    recover(state,projection)
    recovered=restarted.get_turn(state.turn['id'])
    assert recovered['status']==('stopped' if terminal=='stopped' else 'failed')
    assert recovered['result'].get('kind')!='task_turn'
    receipt=recovered['result']['response']['edit_request']
    assert receipt['request_id']==str(state.payload.request_id)
    assert receipt['result']['operation']['id']==operation['id']
    assert restarted.get_task_goal(state.turn['id']) is None
    assert restarted.register_task_goal(state.turn['id']) is None
    current=state.repo.task_records.get_creation_task('local','task')
    # Same original UUID/identity reads its original result even after the head moved.
    replay=main._exact_preview_use_case().apply(current,state.actor,state.payload,identity=state.audio.identity(state.actor))
    assert replay.value['operation']['id']==operation['id']
    assert len(state.repo.edit_records.list_edit_operations('local','task'))==len(before_operations)+1
    assert state.repo.task_records.get_creation_task('local','task').current_timeline_version_id==operation['result_version_id']
    graph=state.repo.get_creation_task_montage_plan('local','task')['audio_timeline']
    assert graph['tracks'][0]==original and state.path.read_bytes()==source_bytes
    assert next(track for track in graph['tracks'] if track['id']=='audio-music')['clips'][0]['gain_db']==-17
    with pytest.raises(EditPreviewRejected) as foreign:
        state.case.read_request_receipt(current,'another-member',state.payload.request_id,'apply')
    assert foreign.value.status_code in {403,404}


def test_unknown_original_claim_is_not_promoted_to_a_saved_edit(tmp_path,monkeypatch):
    state=prepare(tmp_path,monkeypatch)
    inputs=state.payload.model_dump(mode='json',exclude_unset=True)
    event_id,_=state.case.metadata.begin(state.task,state.actor.member_id,state.payload.request_id,inputs,phase='apply')
    source=state.case._request_origin(state.task,state.payload,state.audio.identity(state.actor),state.actor,None,None)
    state.case._seal_request_origin(state.task,event_id,source,state.actor)
    state.conversations.finish_turn(state.turn['id'],'failed',result={'kind':'failure','message':'interrupted before commit'})
    assert GoalResultProjection(state.repo,ConversationRepository(state.repo.database_path)).interrupted_edit_candidates()==[]
    receipt=state.case.read_request_receipt(state.task,state.actor.member_id,state.payload.request_id,'apply')
    assert receipt['status']=='unknown' and receipt['result'] is None
