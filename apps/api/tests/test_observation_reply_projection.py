"""A status query must not erase a later answer grounded in actual observations."""
import pytest

from app.assistant.result_projection import project_business_result


def session(*, status='succeeded', stale=False, observed_job='observation', samples=True):
    execution={'task_id':'task','job':{'id':'render','task_id':'task','status':'succeeded'},'video_ready':True}
    observed={'observation_id':observed_job,'task_id':'task','timeline_version_id':'version',
        'samples':[{'time_seconds':1},{'time_seconds':4},{'time_seconds':7}] if samples else [],
        'unavailable':[{'scope':'human_listening','reason':'not available'}]}
    def tool(name,details):
        return {'type':'tool_execution_end','toolName':name,'isError':False,'result':{'details':details}}
    result={'status':'settled','answer':'1、4、7秒已有真实采样；技术音频检查已返回，人耳听感尚未验证。',
        'tool_results':[
            tool('read_creation_execution',{'kind':'execution_result','status':execution}),
            tool('observe_task_output',{'kind':'task_media_job','media_job':{
                'job_id':'observation','result_kind':'observation','status':'queued'}}),
            tool('read_task_observations',{'task_id':'task','timeline_version_id':'version','observations':[
                {'job_id':observed_job,'status':status,'stale':stale,'result':observed}]}),
        ]}
    return result,execution


def test_actual_successful_observation_readback_preserves_final_review_and_execution_identity():
    result,execution=session()
    answer,preview=project_business_result(result)
    assert answer==result['answer']
    assert '听感尚未验证' in answer
    assert preview=={'kind':'execution_result','action':None,'status':execution}


def test_successful_direct_job_without_current_version_readback_cannot_certify_current_review():
    result,_=session()
    page=result['tool_results'].pop()['result']['details']
    job=result['tool_results'][-1]['result']['details']['media_job']
    job.update(status='succeeded',result=page['observations'][0]['result'])
    answer,_=project_business_result(result)
    assert '已查询原制作作业' in answer and '已有真实采样' not in answer


def test_stale_page_cannot_be_overridden_by_a_successful_historical_job_receipt():
    result,_=session(stale=True)
    job=result['tool_results'][1]['result']['details']['media_job']
    job.update(status='succeeded',result=result['tool_results'][-1]['result']['details']['observations'][0]['result'])
    answer,_=project_business_result(result)
    assert '已查询原制作作业' in answer and '已有真实采样' not in answer


@pytest.mark.parametrize('failed', [False, True])
def test_newer_stale_or_failed_read_supersedes_an_earlier_current_observation_read(failed):
    from copy import deepcopy
    result,_=session()
    newer=deepcopy(result['tool_results'][-1])
    newer['result']['details']['observations'][0]['stale']=True
    newer['isError']=failed
    result['tool_results'].append(newer)
    answer,_=project_business_result(result)
    assert '已查询原制作作业' in answer and '已有真实采样' not in answer


@pytest.mark.parametrize('changes',[
    {'status':'queued'},{'status':'failed'},{'stale':True},{'observed_job':'foreign'},{'samples':False},
])
def test_pending_failed_stale_or_unrelated_observations_do_not_preserve_claimed_review(changes):
    result,_=session(**changes)
    answer,_=project_business_result(result)
    assert '已查询原制作作业' in answer
    assert '已有真实采样' not in answer
