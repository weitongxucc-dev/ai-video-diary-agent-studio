"""Adapt the draft-generation application to the existing Pi task runtime."""
import json

from app.assistant.business_failure import _safe_business_failure
from app.assistant.task_business_adapter import TaskBusinessAdapter
from app.assistant.turn_control import AssistantProcessReleaseError
from app.assistant_pi import (
    AssistantStopped, AssistantUnavailable, forget_turn_control, register_turn_control, run_task_turn,
)
from app.creation.script_generation_intent import (
    ScriptGenerationAgentFailed, ScriptGenerationAgentStopped, ScriptGenerationAgentUnknown,
)


def run_generation_agent(settings, *, workspace_id, member_id, record, business, read_request, on_event):
    turn_id = record["turn_id"]
    binding = {"workspace_id": workspace_id, "member_id": member_id, "task_id": record["task_id"],
               "conversation_id": record["conversation_id"], "turn_id": turn_id,
               "intent_kind": "script_generation", "generation_request_id": record["request_id"]}
    register_turn_control(turn_id)
    try:
        # Stop may have been requested before the SDK registered its control.
        if read_request()["stop_requested"]:
            raise ScriptGenerationAgentStopped()
        return run_task_turn(settings, workspace_id=workspace_id, task_id=record["task_id"],
            conversation_id=record["conversation_id"], caller_user_id=member_id, turn_id=turn_id,
            message=json.dumps({"action": "generate_script_draft", "input": record["frozen_input"]},
                               ensure_ascii=False),
            stage="script", selected_refs=[], playhead_ms=None, selected_references=[],
            business_handler=TaskBusinessAdapter(business), source_binding=binding, on_event=on_event)
    except AssistantStopped as exc:
        raise ScriptGenerationAgentStopped() from exc
    except AssistantProcessReleaseError as exc:
        raise ScriptGenerationAgentUnknown() from exc
    except AssistantUnavailable as exc:
        failure = _safe_business_failure(getattr(exc, "business_failure", None))
        if failure.get("submission_effect") == "unknown":
            raise ScriptGenerationAgentUnknown() from exc
        # Raw SDK errors, prompts and transport addresses never enter the request record.
        raise ScriptGenerationAgentFailed("agent_unavailable",
            provider_attempted=exc.service_contacted, provider_failed=exc.service_contacted) from exc
    finally:
        forget_turn_control(turn_id)
