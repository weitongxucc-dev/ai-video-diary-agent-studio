from app.assistant.catalogue import ASSISTANT_BUSINESS_TOOLS


def test_task_context_stages_do_not_hide_existing_cross_stage_recovery_tools():
    stages = ("topic", "script", "task_edit")
    tools = [set(ASSISTANT_BUSINESS_TOOLS[stage]) for stage in stages]
    assert tools[0] == tools[1] == tools[2]
    assert {"read_narration_segment_context", "read_narration_segment_sources",
            "read_narration_revision", "resume_narration_revision",
            "read_execution_capabilities"} <= tools[0]
    assert ASSISTANT_BUSINESS_TOOLS["topic"][0] == "read_current_task"
    assert ASSISTANT_BUSINESS_TOOLS["task_edit"][0] == "record_input_understanding"
    assert "resume_narration_revision" not in ASSISTANT_BUSINESS_TOOLS["home"]
    assert "read_execution_capabilities" not in ASSISTANT_BUSINESS_TOOLS["material"]


def test_script_window_business_dispatch_uses_only_the_window_callback():
    from types import SimpleNamespace
    from app.assistant.task_business import TaskBusiness
    from app.creation.script_windows import SCRIPT_SECTION_WINDOW_LIMIT
    business = object.__new__(TaskBusiness)
    task = SimpleNamespace(workspace_id='workspace', id='task')
    calls = []
    page = {'task_id':'task','script_id':'script','updated_at':'v1','items':[],
            'total_count':45,'next_after_ordinal':20,'truncated':True}
    def read(current, script_id, **options):
        calls.append((current,script_id,options))
        return page
    business.read_script_sections = read
    assert business.execute('read_script_sections',{'script_id':'script'},None,task,None,None,None,None) is page
    assert calls == [(task,'script',{'after_ordinal':0,'limit':SCRIPT_SECTION_WINDOW_LIMIT,'expected_updated_at':None})]


def test_script_window_business_preserves_conflict_missing_and_invalid_errors():
    import pytest
    from types import SimpleNamespace
    from fastapi import HTTPException
    from app.assistant.task_business import TaskBusiness, TaskBusinessRejected
    from app.creation.script_windows import ScriptWindowConflict
    business = object.__new__(TaskBusiness)
    task = SimpleNamespace(workspace_id='workspace',id='task')
    for error, status in [(ScriptWindowConflict('stale'),409),(KeyError('missing'),404),(ValueError('cursor'),422)]:
        def read(*args, **kwargs):
            raise error
        business.read_script_sections = read
        with pytest.raises(HTTPException) as failed:
            business.execute('read_script_sections',{'script_id':'script','after_ordinal':20,'expected_updated_at':'v1'},None,task,None,None,None,None)
        assert failed.value.status_code == status
    business.read_script_sections = None
    with pytest.raises(TaskBusinessRejected) as failed:
        business.execute('read_script_sections',{'script_id':'script'},None,task,None,None,None,None)
    assert failed.value.status_code == 503


def test_device_preparation_uses_original_account_actor_and_only_public_facts():
    from types import SimpleNamespace
    from uuid import uuid4
    from app.assistant.task_business import TaskBusiness
    from app.creation_execution import CreationExecutionActor
    actor = CreationExecutionActor.account(str(uuid4()),str(uuid4()),str(uuid4()))
    task = SimpleNamespace(workspace_id=actor.workspace_id,id='task')
    business = object.__new__(TaskBusiness)
    calls = []
    def dispatch(action, **values):
        calls.append((action,values))
        return {'id':'work','plan_id':'plan','state':'done','wait_reason':None,
                'profile_published':True,'profile_id':'profile','revision':'v1','normal_work_started':False,
                'write_paths':['/private/path'],'bootstrap_budget':{'secret':'not-public'}}
    business.device_preparation = dispatch
    shape = dict(width_px=16,height_px=16,frame_rate=1,duration_seconds=1.0,clip_count=1,
                 audio_input_count=1,sample_rate=8000,channels=1)
    key = str(uuid4())
    for operation,args in [('prepare_device_resources',{'payload':{'request_id':key,'shape':shape},'actor':'forged'}),
                           ('read_device_preparation',{'request_id':'work','actor':'forged'}),
                           ('finalize_device_preparation',{'request_id':'work','actor':'forged'})]:
        result = business.execute(operation,args,actor,task,None,None,None,None)
        assert result['id']=='work' and result['normal_work_started'] is False
        assert 'write_paths' not in result and 'bootstrap_budget' not in result
    assert [action for action,_ in calls] == ['prepare','status','finalize']
    assert all(values['actor'] is actor for _,values in calls)
    assert calls[0][1]['request_id']==key and calls[0][1]['shape']['width_px']==16
    assert 'shape' not in calls[1][1] and 'shape' not in calls[2][1]


def test_device_preparation_status_reuses_pure_application_without_starting_runtime():
    from types import SimpleNamespace
    from threading import RLock
    from app.assistant.task_business import TaskBusiness
    from app.creation_execution import CreationExecutionActor
    from app.device_preparation_application import DevicePreparationApplication
    actor = CreationExecutionActor('workspace','member')
    calls = []
    def status(**values):
        calls.append(values)
        assert values['actor'] is actor
        return {'id':'original','plan_id':'plan','state':'waiting','wait_reason':'device_busy'}
    def forbidden(*args,**kwargs):
        raise AssertionError('status cannot inspect/start/stop runtime or prepare media')
    application = DevicePreparationApplication(service=SimpleNamespace(status=status),database_path='isolated',
        lifecycle_lock=RLock(),current_runtime=forbidden,replace_runtime=forbidden,
        stop_runtime=forbidden,bind_runtime=forbidden)
    business = object.__new__(TaskBusiness)
    business.device_preparation = application.dispatch
    result = business.execute('read_device_preparation',{'request_id':'original'},actor,
        SimpleNamespace(workspace_id='workspace',id='task'),None,None,None,None)
    assert result['state']=='waiting' and len(calls)==1
    assert calls[0]['database_path']=='isolated'


def test_device_preparation_preserves_parameter_conflict_missing_and_unassembled_errors():
    import pytest
    from types import SimpleNamespace
    from fastapi import HTTPException
    from app.assistant.task_business import TaskBusiness,TaskBusinessRejected
    from app.device_preparation_application import DevicePreparationConflict
    business = object.__new__(TaskBusiness)
    task = SimpleNamespace(workspace_id='workspace',id='task')
    for error,status in [(DevicePreparationConflict('busy'),409),(KeyError('missing'),404),(RuntimeError('unavailable'),503)]:
        def dispatch(*args,**kwargs):
            raise error
        business.device_preparation = dispatch
        with pytest.raises(HTTPException) as failure:
            business.execute('read_device_preparation',{'request_id':'work'},None,task,None,None,None,None)
        assert failure.value.status_code==status
    with pytest.raises(HTTPException) as failure:
        business.execute('prepare_device_resources',{'payload':{'request_id':'invalid','shape':{}}},None,task,None,None,None,None)
    assert failure.value.status_code==422
    business.device_preparation = None
    with pytest.raises(TaskBusinessRejected) as failure:
        business.execute('read_device_preparation',{'request_id':'work'},None,task,None,None,None,None)
    assert failure.value.status_code==503
