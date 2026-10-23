"""Build a typed SDK result from event fixtures used by business projection tests."""
import json
from subprocess import CompletedProcess
from app.assistant.failures import _fail, FAILURE_UNKNOWN
from app.assistant.result_projection import project_business_result


def sdk_fixture_stdout(output):
    try:
        events = [json.loads(line) for line in output.splitlines()]
    except json.JSONDecodeError as exc:
        raise _fail(FAILURE_UNKNOWN) from exc
    messages = [event["message"] for event in events if event.get("type") == "message_end"]
    final = next((message for message in reversed(messages)
                  if message.get("role") == "assistant" and message.get("stopReason") == "stop"), {})
    return json.dumps({"type": "creation_session_result", "protocol": 1, "owner": "pi-coding-agent-sdk",
                       "status": "settled" if any(e.get("type") == "agent_settled" for e in events) else "failed",
                       "answer": "".join(block.get("text", "") for block in final.get("content", []) if block.get("type") == "text"),
                       "tool_results": [event for event in events if event.get("type") == "tool_execution_end"],
                       "failure": [*messages, *(event for event in events if event.get("type") == "error")]})


def project_event_fixture(output, kind="edit_preview"):
    return project_business_result(json.loads(sdk_fixture_stdout(output)), kind)


def sdk_completed(args, returncode, stdout=None, stderr=None):
    if returncode == 0 and args and str(args[-1]).endswith("host.mjs") and stdout:
        stdout = sdk_fixture_stdout(stdout)
    return CompletedProcess(args, returncode, stdout, stderr)


def install_sdk_fixture(root):
    """Layout only; tests replace the host process instead of loading this module."""
    package = root / "node_modules/@earendil-works/pi-coding-agent"
    (package / "dist").mkdir(parents=True)
    (package / "package.json").write_text('{"version":"1.4.0"}')
    (package / "dist/index.js").write_text("// synthetic SDK installation fixture\n")
    cli = package / "dist/cli.js"
    cli.write_text("#!/bin/sh\necho 1.4.0\n")
    cli.chmod(0o700)
    executable = root / "pi"
    executable.symlink_to(cli)
    return executable
