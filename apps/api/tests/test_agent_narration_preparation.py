from types import SimpleNamespace
from uuid import uuid4
import pytest

from app.assistant.task_business import TaskBusiness
from app.creation_execution import CreationExecutionActor
from app.assistant.task_business_errors import TaskBusinessRejected


def test_task_preparation_forwards_real_kind_and_binds_narration_references_to_current_task():
    actor=CreationExecutionActor.account(str(uuid4()),str(uuid4()),str(uuid4()))
    task=SimpleNamespace(workspace_id=actor.workspace_id,id='actual_task',plan_id='plan')
    business=object.__new__(TaskBusiness)
    business.production=SimpleNamespace(plan_records=SimpleNamespace(
        get_creation_plan=lambda workspace_id,plan_id:SimpleNamespace(source_media=None)))
    calls=[]
    def dispatch(action,**values):
        calls.append((action,values))
        return {'id':'work','state':'queued','normal_work_started':False}
    business.device_preparation=dispatch
    shape={'voice_profile_id':'voice','voice_version':'v1','text':'Hello.','language':'en',
        'output_duration_seconds':1.0,'pronunciation_references':[{'id':'entry','version':'v2'}],
        'task_id':'model_supplied_task'}
    request_id=str(uuid4())
    business.execute('prepare_device_resources',{'payload':{'kind':'narration','request_id':request_id,'shape':shape}},
        actor,task,None,{},None,None)
    action,values=calls[0]
    assert action=='prepare' and values['actor'] is actor
    assert values['kind']=='narration' and values['request_id']==request_id
    assert values['shape']=={**shape,'task_id':task.id}
    for operation in ('read_device_preparation','finalize_device_preparation'):
        business.execute(operation,{'request_id':'work','kind':'cpu','actor':'forged'},actor,task,None,{},None,None)
    assert [action for action,_ in calls]==['prepare','status','finalize']
    assert all(values['actor'] is actor and 'kind' not in values for _,values in calls[1:])


def test_storyboard_preparation_cannot_switch_the_bound_task():
    actor = CreationExecutionActor('local', 'member')
    task = SimpleNamespace(workspace_id='local', id='bound-task', plan_id='plan')
    business = object.__new__(TaskBusiness)
    business.production = SimpleNamespace(plan_records=SimpleNamespace(
        get_creation_plan=lambda workspace_id, plan_id: SimpleNamespace(source_media=None)))
    calls = []
    business.device_preparation = lambda action, **values: calls.append((action, values)) or {'id': 'work'}
    payload = {'request_id': str(uuid4()), 'kind': 'montage', 'shape': {'task_id': task.id}}
    business.execute('prepare_device_resources', {'payload': payload}, actor, task, None, {}, None, None)
    assert calls[0][1]['shape'] == {'task_id': task.id} and calls[0][1]['actor'] is actor
    payload['shape'] = {'task_id': 'other-task'}
    with pytest.raises(TaskBusinessRejected, match='不能切换'):
        business.execute('prepare_device_resources', {'payload': payload}, actor, task, None, {}, None, None)
    assert len(calls) == 1
