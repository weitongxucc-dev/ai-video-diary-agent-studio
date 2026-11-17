"""Current media submissions stay distinct from historical read-only receipts."""
from types import SimpleNamespace

import pytest

from app.assistant.result_projection import project_business_result
from app.assistant.media_result_binding import AssistantMediaResultBinding
from app.creation.media_result_use_case import MediaResultRejected


def tool(name, details, *, error=False):
    return {'type':'tool_execution_end','toolName':name,'isError':error,'result':{'details':details}}


def receipt(job_id, *, status='succeeded', result_kind='observation'):
    return {'job_id':job_id,'operation':'output_observation','result_kind':result_kind,'status':status,
        'input_digest':'actual-digest','created_at':'2026-11-17T08:00:00Z',
        'result_url':f'/api/creation-tasks/task/media-jobs/{job_id}',
        'result':{'observation_id':job_id,'task_id':'task','samples':[{'time_seconds':1}]}}


def rejected():
    return tool('observe_task_output',{'kind':'edit_request_status','task_id':'task','request_id':'rejected-request',
        'phase':'observation','status':'rejected','failure':{'reason':'media_job target cannot be a final output',
            'submission_effect':'rejected'}},error=True)


def test_historical_reads_cannot_replace_current_observation_or_attach_to_its_later_refusal():
    current=receipt('current-observation')
    events=[tool('observe_task_output',{'kind':'task_media_job','media_job':receipt('current-observation',status='queued')}),
        tool('read_task_media_job',{'kind':'task_media_job','media_job':current}),
        tool('read_task_media_jobs',{'items':[receipt('historical-one'),receipt('historical-two')]}),
        tool('read_task_media_job',{'kind':'task_media_job','media_job':receipt('historical-one',result_kind='audio_source')}),
        tool('read_task_media_job',{'kind':'task_media_job','media_job':receipt('historical-two')}),rejected()]
    answer,preview=project_business_result({'status':'settled','answer':'sampling analysis','tool_results':events})
    assert preview['kind']=='edit_request_status' and preview['status']=='rejected'
    assert preview['failure']['submission_effect']=='rejected'
    assert preview['related_media_job']==current
    assert '已被拒绝' in answer
    assert 'sampling analysis' in answer and 'media_job target cannot be a final output' in answer
    binding=AssistantMediaResultBinding(None,None,None,None,None)
    def read(workspace,job_id):
        source={'assistant_turn_id':'this-turn' if job_id=='current-observation' else 'old-turn',
                'conversation_id':'this-conversation' if job_id=='current-observation' else 'old-conversation'}
        return SimpleNamespace(id=job_id,task_id='task'),{'arguments':{'_typed_source':source}}
    media=SimpleNamespace(read=read)
    assert binding.related_job(media,'workspace','task','this-turn','this-conversation',preview['related_media_job'])=='current-observation'
    with pytest.raises(MediaResultRejected,match='不属于本轮真实目标'):
        binding.related_job(media,'workspace','task','this-turn','this-conversation',receipt('historical-two'))


def test_only_reading_historical_jobs_never_attaches_them_as_related_writes():
    events=[tool('read_task_media_job',{'kind':'task_media_job','media_job':receipt('old')}),rejected()]
    answer,preview=project_business_result({'status':'settled','answer':'history read','tool_results':events})
    assert preview['status']=='rejected' and 'related_media_job' not in preview
    assert '先前已经返回的后台作业' not in answer


def test_historical_reads_cannot_replace_the_observation_used_by_final_review():
    current=receipt('current')
    result={'status':'settled','answer':'1秒采样已返回，听感仍未验证。','tool_results':[
        tool('read_creation_execution',{'kind':'execution_result','status':{'task_id':'task',
            'job':{'id':'render','task_id':'task','status':'succeeded'},'video_ready':True}}),
        tool('observe_task_output',{'kind':'task_media_job','media_job':current}),
        tool('read_task_media_job',{'kind':'task_media_job','media_job':receipt('old')}),
        tool('read_task_observations',{'task_id':'task','timeline_version_id':'version','observations':[
            {'job_id':'current','status':'succeeded','stale':False,'result':{
                **current['result'],'timeline_version_id':'version'}}]}),
    ]}
    answer,preview=project_business_result(result)
    assert answer==result['answer'] and preview['kind']=='execution_result'


@pytest.mark.parametrize('current_status', ['succeeded','stale'])
def test_final_binding_keeps_review_only_for_current_job_and_always_preserves_definite_refusal(current_status):
    from app.assistant import task_turn_use_case as turn
    current=receipt('current')
    answer,preview=project_business_result({'status':'settled','answer':'actual sampling analysis','tool_results':[
        tool('observe_task_output',{'kind':'task_media_job','media_job':current}),rejected()]})
    case=turn.TaskAssistantTurnUseCase(conversations_for=lambda:None,window_case_for=None,
        read_event=None,read_intent=None,list_events=None)
    operation=case.finish('task',SimpleNamespace(conversation_id='conversation',expected_draft_version=1),
        'turn',answer,preview,'montage')
    checkpoint=next(operation)
    while not isinstance(checkpoint,turn.BuildTaskResponse):
        if isinstance(checkpoint,turn.ReadWorkspace):
            value='workspace'
        elif isinstance(checkpoint,(turn.CleanText,turn.SafeText)):
            value=checkpoint.value
        elif isinstance(checkpoint,turn.ResolveEditRequest):
            value=SimpleNamespace(phase='observation',status='recorded',result_status_code=422,
                result={'detail':'media_job target cannot be a final output'})
        elif isinstance(checkpoint,turn.RelatedMedia):
            value=SimpleNamespace(result_kind='observation',status=current_status)
        elif isinstance(checkpoint,turn.ReadSettings):
            value=SimpleNamespace(effective_llm_provider='provider',effective_llm_model='model')
        else:
            pytest.fail(f'unexpected checkpoint {checkpoint}')
        checkpoint=operation.send(value)
    reply=checkpoint.fields['reply']
    assert 'media_job target cannot be a final output' in reply
    assert ('actual sampling analysis' in reply)==(current_status=='succeeded')
    operation.close()
