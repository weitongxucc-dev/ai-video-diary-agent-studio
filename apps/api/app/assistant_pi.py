from __future__ import annotations

# Stable public host boundary. Concrete runtime modules own their private state;
# this module assembles dependencies and preserves existing caller entry points.
import os
import shutil
import subprocess
from pathlib import Path
from typing import Callable
from app.config import Settings
from app.assistant.catalogue import (MEMORY_TOOLS, SCRIPT_TOOL_READS, ASSISTANT_BUSINESS_TOOLS,
    TASK_READ_PREVIEW_TOOLS, SYSTEM_PROMPT, PLAN_PROMPT, MATERIAL_PROMPT, SCRIPT_PROMPT, TOPIC_PROMPT, _PI_TOOL_LABELS)
from app.assistant.failures import (AssistantUnavailable, _fail, _classify_output, _tool_error_seen,
    _FAILURES, FAILURE_KEY_REJECTED, FAILURE_ADDRESS_UNREACHABLE, FAILURE_TIMEOUT, FAILURE_RUNTIME_MISSING,
    FAILURE_TOOL_FAILED, FAILURE_UNKNOWN)
from app.assistant.projection import _safe_tool_result_summary, _business_event_lines, _PiStreamProjector
from app.assistant.result_projection import read_sdk_result
from app.assistant.service_observations import _record_service
from app.assistant.turn_control import AssistantStopped, TurnControl, _terminate_process_group
from app.assistant.sessions import SessionLocks
from app.assistant.runtime_configuration import _agent_root, _configured_model
from app.assistant.runtime_checks import _pi_executable, runtime_checks
from app.assistant.preflight import read_task_before_pi, read_memory_before_pi
from app.assistant.process_runner import run_pi_streaming
from app.assistant.turn import run_turn

_turn_control = TurnControl(terminate=lambda process, **kwargs: _terminate_process_group(process, **kwargs))
_session_control = SessionLocks()
# Existing diagnostic readers can inspect markers; all writes stay with owner.
_turn_stop_events = _turn_control.stop_events

def request_stop_turn(turn_id: str) -> bool:
    return _turn_control.request_stop(turn_id)

def register_turn_control(turn_id: str) -> None:
    _turn_control.register(turn_id)

def forget_turn_control(turn_id: str) -> None:
    _turn_control.forget(turn_id)

def stop_all_assistant_turns(*, deadline_monotonic: float | None = None) -> list[str]:
    return _turn_control.stop_all(deadline_monotonic=deadline_monotonic)

def _extension_path() -> Path:
    return Path(os.getenv("AI_VIDEO_DIARY_PI_EXTENSION") or Path(__file__).parents[1] / "agent_tools" / "diary_tools.ts")

def pi_runtime_checks() -> list[dict]:
    return runtime_checks(_extension_path(), _pi_executable)

def _run_pi_streaming(cmd: list[str], *, message: str, env: dict[str, str], cwd: Path,
    timeout_seconds: int, on_event, turn_id: str | None = None, keep_turn_control: bool = False, input_handler=None, business_handler=None, native_handler=None, source_binding=None,
) -> subprocess.CompletedProcess[str]:
    return run_pi_streaming(cmd, message=message, env=env, cwd=cwd, timeout_seconds=timeout_seconds,
        on_event=on_event, turn_id=turn_id, keep_turn_control=keep_turn_control, control=_turn_control,
        terminate=_terminate_process_group, projector_factory=_PiStreamProjector, business_event_lines=_business_event_lines, input_handler=input_handler, business_handler=business_handler,native_handler=native_handler,source_binding=source_binding)

def _read_task_before_pi(extension: Path, env: dict[str, str], cwd: Path, *, business_handler=None, source_binding=None) -> dict:
    return read_task_before_pi(extension, env, cwd, run_process=_run_pi_streaming,
        business_handler=business_handler,source_binding=source_binding)

def _read_memory_before_pi(extension: Path, env: dict[str, str], cwd: Path, *, business_handler=None, source_binding=None) -> dict:
    return read_memory_before_pi(extension, env, cwd, run_process=_run_pi_streaming,
        business_handler=business_handler,source_binding=source_binding)

def _material_tool_selection(selection: dict[str, str] | None) -> dict[str, str] | None:
    if selection is None:
        return None
    return {
        "scope": selection["scope"],
        "assetId": selection["asset_id"],
        **({"segmentId": selection["segment_id"]} if selection["scope"] == "segment" else {}),
        **({"expectedUpdatedAt": selection["expected_updated_at"]} if "expected_updated_at" in selection else {}),
        **({"expectedSegmentUpdatedAt": selection["expected_segment_updated_at"]} if "expected_segment_updated_at" in selection else {}),
    }


def run_task_turn(
    settings: Settings,
    *,
    workspace_id: str,
    task_id: str,
    conversation_id: str | None = None,
    message: str,
    stage: str,
    selected_refs: list[str],
    playhead_ms: int | None,
    authorization: str | None = None,
    cookie: str | None = None,
    local_member_token: str | None = None,
    caller_user_id: str | None = None,
    on_event: Callable[[dict[str, object]], None] | None = None,
    turn_id: str | None = None,
    profile_reference: dict[str, object] | None = None,
    knowledge_reference: dict[str, object] | None = None,
    task_version: dict[str, object] | None = None,
    material_selection: dict[str, str] | None = None,
    continuation_context: dict[str, object] | None = None,
    selected_references: list[dict] | None = None,
    input_handler=None,
    business_handler=None, native_handler=None, source_binding=None,
) -> tuple[str, dict | None]:
    script_turn = stage == "script"
    topic_turn = stage == "topic"
    tools = ",".join(ASSISTANT_BUSINESS_TOOLS[
        "task_edit" if continuation_context else "topic" if topic_turn else "script" if script_turn else "task_edit"
    ])
    return _run_turn(
        settings, workspace_id=workspace_id, task_id=task_id, message=message,
        stage=stage, selected_refs=selected_refs, playhead_ms=playhead_ms,
        authorization=authorization, cookie=cookie,
        local_member_token=local_member_token, caller_user_id=caller_user_id,
        on_event=on_event,
        prompt=SYSTEM_PROMPT if continuation_context else TOPIC_PROMPT if topic_turn else SCRIPT_PROMPT if script_turn else SYSTEM_PROMPT,
        tool_names=tools + (",read_selected_profile" if profile_reference else "") + (",read_selected_knowledge" if knowledge_reference else ""),
        preview_kind="edit_preview" if continuation_context else "topic_generation" if topic_turn else "script_draft" if script_turn else "edit_preview",
        session_key=(f"conversation:{conversation_id}"
                     if conversation_id else f"task:{task_id}:{'topic' if topic_turn else 'script' if script_turn else 'edit'}"),
        turn_id=turn_id, profile_reference=profile_reference, knowledge_reference=knowledge_reference, task_version=task_version,
        material_selection=_material_tool_selection(material_selection),
        continuation_context=continuation_context, selected_references=selected_references, input_handler=input_handler, business_handler=business_handler,native_handler=native_handler,source_binding=source_binding,
    )


def run_plan_turn(
    settings: Settings,
    *,
    workspace_id: str,
    conversation_id: str,
    message: str,
    authorization: str | None = None,
    cookie: str | None = None,
    local_member_token: str | None = None,
    caller_user_id: str | None = None,
    on_event: Callable[[dict[str, object]], None] | None = None,
    turn_id: str | None = None,
    profile_reference: dict[str, object] | None = None,
    knowledge_reference: dict[str, object] | None = None,
    creation_request: dict[str, object] | None = None,
    keep_turn_control: bool = False,
    selected_references: list[dict] | None = None,
    input_handler=None,
    material_selection: dict | None = None,
) -> tuple[str, dict | None]:
    materials = [item["selection"] for item in (selected_references or []) if item["kind"] == "material"]
    selection = materials[0] if len(materials) == 1 else material_selection if not materials else None
    tools = ASSISTANT_BUSINESS_TOOLS["home"]
    if selection is not None:
        tools += ("read_selected_material", "suggest_selected_material_tags", "update_selected_material_tags")
    if creation_request is not None:
        tools = tuple(name for name in tools if name != "preview_creation_plan")
    return _run_turn(
        settings, workspace_id=workspace_id, task_id="", message=message,
        stage="", selected_refs=[], playhead_ms=None,
        authorization=authorization, cookie=cookie,
        local_member_token=local_member_token, caller_user_id=caller_user_id,
        on_event=on_event,
        prompt=PLAN_PROMPT, tool_names=",".join(tools) + (",read_selected_profile" if profile_reference else "") + (",read_selected_knowledge" if knowledge_reference else ""),
        preview_kind="plan_preview", session_key=f"conversation:{conversation_id}", turn_id=turn_id, profile_reference=profile_reference, knowledge_reference=knowledge_reference,
        creation_request=creation_request, material_selection=_material_tool_selection(selection),
        keep_turn_control=keep_turn_control, selected_references=selected_references, input_handler=input_handler,
    )


def run_material_turn(
    settings: Settings,
    *,
    workspace_id: str,
    conversation_id: str,
    message: str,
    selection: dict[str, str] | None = None,
    authorization: str | None = None,
    cookie: str | None = None,
    local_member_token: str | None = None,
    caller_user_id: str | None = None,
    on_event: Callable[[dict[str, object]], None] | None = None,
    turn_id: str | None = None,
    selected_references: list[dict] | None = None,
    input_handler=None,
) -> tuple[str, dict | None]:
    return run_plan_turn(
        settings, workspace_id=workspace_id, conversation_id=conversation_id, message=message,
        authorization=authorization, cookie=cookie, local_member_token=local_member_token,
        caller_user_id=caller_user_id, on_event=on_event, turn_id=turn_id,
        selected_references=selected_references, input_handler=input_handler, material_selection=selection,
    )


def _run_turn(
    settings: Settings,
    *,
    workspace_id: str,
    task_id: str,
    message: str,
    stage: str,
    selected_refs: list[str],
    playhead_ms: int | None,
    authorization: str | None,
    cookie: str | None,
    local_member_token: str | None,
    caller_user_id: str | None,
    prompt: str,
    tool_names: str,
    preview_kind: str,
    session_key: str | None = None,
    on_event: Callable[[dict[str, object]], None] | None = None,
    material_selection: dict[str, str] | None = None,
    profile_reference: dict[str, object] | None = None,
    knowledge_reference: dict[str, object] | None = None,
    task_version: dict[str, object] | None = None,
    creation_request: dict[str, object] | None = None,
    keep_turn_control: bool = False,
    turn_id: str | None = None,
    continuation_context: dict[str, object] | None = None,
    selected_references: list[dict] | None = None,
    input_handler=None,
    business_handler=None, native_handler=None, source_binding=None,
) -> tuple[str, dict | None]:
    return run_turn(settings, check_runtime=pi_runtime_checks, find_pi=_pi_executable,
        extension=_extension_path(), configure_model=_configured_model, agent_root=_agent_root,
        read_task=_read_task_before_pi, read_memory=_read_memory_before_pi, run_process=_run_pi_streaming,
        record_service=_record_service, parse_result=read_sdk_result, classify_output=_classify_output,
        sessions=_session_control, control=_turn_control,
        workspace_id=workspace_id, task_id=task_id, message=message, stage=stage, selected_refs=selected_refs,
        playhead_ms=playhead_ms, authorization=authorization, cookie=cookie, local_member_token=local_member_token,
        caller_user_id=caller_user_id, prompt=prompt, tool_names=tool_names, preview_kind=preview_kind,
        session_key=session_key, on_event=on_event, material_selection=material_selection,
        profile_reference=profile_reference, knowledge_reference=knowledge_reference, task_version=task_version,
        creation_request=creation_request, keep_turn_control=keep_turn_control, turn_id=turn_id,
        continuation_context=continuation_context, selected_references=selected_references, input_handler=input_handler,business_handler=business_handler,native_handler=native_handler,source_binding=source_binding)
