from uuid import uuid4

import pytest

from app.assistant import script_generation_runner as runner
from app.assistant.turn_control import AssistantProcessReleaseError
from app.creation.script_generation_intent import (
    ScriptGenerationAgentFailed, ScriptGenerationAgentStopped, ScriptGenerationAgentUnknown,
)


@pytest.mark.parametrize("kind", ["early_stop", "sdk_stop", "unknown_release", "unknown_write", "known_failure", "success"])
def test_generation_runner_keeps_original_action_and_releases_control_without_granting_more(kind, monkeypatch):
    record = {"turn_id": str(uuid4()), "conversation_id": str(uuid4()), "task_id": "task",
              "request_id": str(uuid4()), "frozen_input": {"goal": "original requirement"}}
    controls, calls = [], []
    monkeypatch.setattr(runner, "register_turn_control", lambda identity: controls.append(("register", identity)))
    monkeypatch.setattr(runner, "forget_turn_control", lambda identity: controls.append(("forget", identity)))
    def execute(settings, **values):
        calls.append(values)
        assert values["source_binding"] == {
            "workspace_id": "ws", "member_id": "member", "task_id": "task",
            "conversation_id": record["conversation_id"], "turn_id": record["turn_id"],
            "intent_kind": "script_generation", "generation_request_id": record["request_id"]}
        assert not any(key in values for key in ("native_handler", "input_handler", "authorization", "cookie"))
        if kind == "sdk_stop":
            raise runner.AssistantStopped()
        if kind == "unknown_release":
            raise AssistantProcessReleaseError("private process detail")
        if kind in {"unknown_write", "known_failure"}:
            error = runner.AssistantUnavailable("private provider detail", service_contacted=True)
            if kind == "unknown_write":
                error.business_failure = {"submission_effect": "unknown"}
            raise error
        return "reply", {"not_completion_proof": True}
    monkeypatch.setattr(runner, "run_task_turn", execute)
    def invoke():
        return runner.run_generation_agent(object(), workspace_id="ws", member_id="member", record=record,
            business=object(), read_request=lambda: {"stop_requested": kind == "early_stop"}, on_event=lambda event: None)
    if kind == "success":
        assert invoke() == ("reply", {"not_completion_proof": True})
    else:
        expected = (ScriptGenerationAgentStopped if kind in {"early_stop", "sdk_stop"} else
                    ScriptGenerationAgentFailed if kind == "known_failure" else ScriptGenerationAgentUnknown)
        with pytest.raises(expected) as result:
            invoke()
        assert "private" not in str(result.value)
    assert len(calls) == (0 if kind == "early_stop" else 1)
    assert controls == [("register", record["turn_id"]), ("forget", record["turn_id"])]
