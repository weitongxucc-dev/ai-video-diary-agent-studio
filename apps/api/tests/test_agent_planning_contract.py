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
