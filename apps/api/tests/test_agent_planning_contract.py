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
