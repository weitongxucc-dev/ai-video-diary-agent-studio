from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from app.assistant.failures import _fail, FAILURE_RUNTIME_MISSING, FAILURE_TOOL_FAILED
from app.assistant.runtime_checks import runtime_bundle
from app.assistant.projection import _safe_business_failure

def _read_host_facts(kind, extension, env, cwd, run_process, business_handler, source_binding):
    node = shutil.which("node", path=env["PATH"])
    if node is None:
        raise _fail(FAILURE_RUNTIME_MISSING)
    message = json.dumps({"factMode": kind, "extensionPath": str(extension.resolve()),
                          "businessEnabled": True, "controlEnabled": True}) + "\n"
    try:
        result = run_process([node, "--experimental-strip-types", str(runtime_bundle(extension) / "host.mjs")],
            message=message, env=env, cwd=cwd, timeout_seconds=35, on_event=lambda _event: None,
            turn_id=env.get("AI_VIDEO_DIARY_AGENT_TURN_ID") or None, keep_turn_control=True,
            business_handler=business_handler, source_binding=source_binding)
    except subprocess.TimeoutExpired as exc:
        raise _fail(FAILURE_TOOL_FAILED, explanation="发送前宿主事实读取超时，本轮助手尚未启动。") from exc
    for line in reversed(result.stdout.splitlines()):
        event = json.loads(line)
        if event.get("type") == "diary_preflight_result" and event.get("kind") == kind:
            if event.get("ok") is False:
                failure = _safe_business_failure(event.get("failure"))
                error = _fail(FAILURE_TOOL_FAILED,
                    explanation=f"发送前业务回执（{failure.get('error', 'preflight_failed')}）：{failure.get('reason', '宿主事实读取未完成。')}",
                    repair_action=failure.get("repair_action"))
                error.business_failure = {**failure, "status_code": event["failure"]["status_code"]}
                raise error
            if result.returncode:
                break
            facts = event.get("value")
            if isinstance(facts, dict) and (facts.get("task_id") == env["AI_VIDEO_DIARY_AGENT_TASK_ID"]
                    if kind == "task" else isinstance(facts.get("revision"), int)):
                return facts
            break
    raise _fail(FAILURE_TOOL_FAILED, explanation="发送前宿主事实身份或修订不可读，本轮助手尚未启动。")


def read_task_before_pi(extension: Path, env: dict[str, str], cwd: Path, *, run_process,
                        business_handler=None, source_binding=None) -> dict:
    if business_handler is not None:
        return _read_host_facts("task", extension, env, cwd, run_process, business_handler, source_binding)
    node = shutil.which("node", path=env["PATH"])
    if node is None:
        raise _fail(FAILURE_RUNTIME_MISSING)
    # Use the exact packaged extension and its existing HTTP whitelist. No Pi hook
    # is used here: extension hook exceptions are caught by Pi and cannot fail closed.
    script = "const {readCurrentTaskFacts,readTaskEditCapabilities,readMediaScriptCapabilities,readMediaScriptToolCapabilities}=await import(process.argv[1]); const facts=await readCurrentTaskFacts(); facts._edit_capabilities=await readTaskEditCapabilities(); facts._media_script_capabilities=await readMediaScriptCapabilities(); facts._script_tool_capabilities=await readMediaScriptToolCapabilities(); console.log(JSON.stringify(facts));"
    try:
        result = run_process([node, "--experimental-strip-types", "--input-type=module", "-e", script, extension.resolve().as_uri()],
                                   message="", env=env, cwd=cwd, timeout_seconds=35, on_event=lambda _event: None,
                                   turn_id=env.get("AI_VIDEO_DIARY_AGENT_TURN_ID") or None, keep_turn_control=True)
    except subprocess.TimeoutExpired as exc:
        raise _fail(FAILURE_TOOL_FAILED, explanation="发送前读取当前任务超时，本轮助手尚未启动。",
                    repair_action="确认当前任务可正常打开后重新发送。") from exc
    if result.returncode:
        raise _fail(FAILURE_TOOL_FAILED, explanation="发送前读取当前任务失败，本轮助手尚未启动，也没有提交操作。",
                    repair_action="重新打开当前任务，核对选中对象和版本后发送。")
    try:
        facts = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise _fail(FAILURE_TOOL_FAILED, explanation="发送前任务读取返回不可读内容，本轮助手尚未启动。") from exc
    if not isinstance(facts, dict) or facts.get("task_id") != env["AI_VIDEO_DIARY_AGENT_TASK_ID"]:
        raise _fail(FAILURE_TOOL_FAILED, explanation="发送前任务读取身份不一致，本轮助手尚未启动。")
    return facts


def read_memory_before_pi(extension: Path, env: dict[str, str], cwd: Path, *, run_process,
                          business_handler=None, source_binding=None) -> dict:
    if business_handler is not None:
        return _read_host_facts("memory", extension, env, cwd, run_process, business_handler, source_binding)
    node = shutil.which("node", path=env["PATH"])
    if node is None:
        raise _fail(FAILURE_RUNTIME_MISSING)
    script = "const {readCreatorMemoryFacts}=await import(process.argv[1]); console.log(JSON.stringify(await readCreatorMemoryFacts()));"
    try:
        result = run_process([node, "--experimental-strip-types", "--input-type=module", "-e", script, extension.resolve().as_uri()],
                                  message="", env=env, cwd=cwd, timeout_seconds=35, on_event=lambda _event: None,
                                  turn_id=env.get("AI_VIDEO_DIARY_AGENT_TURN_ID") or None, keep_turn_control=True)
    except subprocess.TimeoutExpired as exc:
        raise _fail(FAILURE_TOOL_FAILED, explanation="读取创作偏好超时，本轮助手尚未启动。") from exc
    if result.returncode:
        raise _fail(FAILURE_TOOL_FAILED, explanation="无法读取当前成员的创作偏好，本轮助手尚未启动。",
                    repair_action="确认创作偏好页面可正常打开后重新发送。")
    try:
        memory = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise _fail(FAILURE_TOOL_FAILED, explanation="创作偏好返回不可读内容，本轮助手尚未启动。") from exc
    if not isinstance(memory, dict) or not isinstance(memory.get("revision"), int):
        raise _fail(FAILURE_TOOL_FAILED, explanation="创作偏好返回缺少有效修订，本轮助手尚未启动。")
    return memory
