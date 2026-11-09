from types import SimpleNamespace
from uuid import uuid4

from app.assistant.task_business import TaskBusiness
from app.creation_execution import CreationExecutionActor


def test_task_preparation_forwards_real_kind_and_binds_narration_references_to_current_task():
    actor=CreationExecutionActor.account(str(uuid4()),str(uuid4()),str(uuid4()))
    task=SimpleNamespace(workspace_id=actor.workspace_id,id='actual_task')
    business=object.__new__(TaskBusiness)
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
