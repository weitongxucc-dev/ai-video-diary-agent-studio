from __future__ import annotations

import queue
import subprocess
import threading
import time
import json
from collections import deque
from pathlib import Path
from app.assistant.turn_control import AssistantStopped, TurnControl, _capture_process_group, _process_group_released, _require_process_group_platform

def run_pi_streaming(
    cmd: list[str], *, message: str, env: dict[str, str], cwd: Path,
    timeout_seconds: int, on_event, control: TurnControl, terminate, projector_factory, business_event_lines, turn_id: str | None = None, keep_turn_control: bool = False, input_handler=None,
) -> subprocess.CompletedProcess[str]:
    stop_event = control.begin(turn_id) if turn_id else None
    _require_process_group_platform()
    def launch_process():
        return subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", bufsize=1, env=env, cwd=cwd, start_new_session=True,
        )
    if turn_id:
        process = control.launch(turn_id, stop_event, launch_process)
    else:
        process = launch_process()
        _capture_process_group(process)
    output_lines: list[str] = []
    error_lines: deque[str] = deque(maxlen=10000)
    line_queue: queue.Queue[str | None] = queue.Queue()
    projector = projector_factory()
    seen_business_calls: set[str] = set()

    def read_stdout() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            line_queue.put(line)
        line_queue.put(None)

    def read_stderr() -> None:
        assert process.stderr is not None
        for line in process.stderr:
            error_lines.append(line)

    stdout_thread = threading.Thread(target=read_stdout, name="pi-assistant-stdout", daemon=True)
    stderr_thread = threading.Thread(target=read_stderr, name="pi-assistant-stderr", daemon=True)
    stdout_thread.start()
    stderr_thread.start()
    try:
        assert process.stdin is not None
        try:
            process.stdin.write(message)
            process.stdin.flush()
        except BrokenPipeError:
            pass
        finally:
            try:
                if input_handler is None:
                    process.stdin.close()
            except BrokenPipeError:
                pass
        deadline = time.monotonic() + timeout_seconds
        while True:
            if stop_event is not None and stop_event.is_set():
                terminate(process)
                stdout_thread.join(timeout=1)
                stderr_thread.join(timeout=1)
                raise AssistantStopped("assistant turn stopped")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(cmd, timeout_seconds, "".join(output_lines), "".join(error_lines))
            try:
                line = line_queue.get(timeout=min(0.25, remaining))
            except queue.Empty:
                continue
            if line is None:
                if stop_event is not None and stop_event.is_set():
                    raise AssistantStopped("assistant turn stopped")
                break
            output_lines.append(line)
            if input_handler is not None:
                event = json.loads(line)
                if event.get("type") == "diary_input_checkpoint":
                    result = input_handler(event["phase"], event.get("input_id"), event.get("mode"))
                    process.stdin.write(json.dumps({"type": "diary_input_response", "checkpoint_id": event["checkpoint_id"], "result": result}, ensure_ascii=False) + "\n")
                    process.stdin.flush()
                    continue
            for business_line in business_event_lines(line, seen_business_calls):
                event = projector.project(business_line)
                if event is not None:
                    on_event(event)
        return_code = process.wait(timeout=max(0.05, deadline - time.monotonic()))
        stderr_thread.join(timeout=1)
        if stop_event is not None and stop_event.is_set():
            raise AssistantStopped("assistant turn stopped")
        return subprocess.CompletedProcess(cmd, return_code, "".join(output_lines), "".join(error_lines))
    except OSError as exc:
        # Keep the acknowledged stop semantics before finally releases its marker.
        # An unrelated launch/I/O error without this turn's stop still propagates.
        if stop_event is not None and stop_event.is_set():
            raise AssistantStopped("assistant turn stopped") from exc
        raise
    except subprocess.TimeoutExpired:
        terminate(process)
        stdout_thread.join(timeout=1)
        stderr_thread.join(timeout=1)
        raise
    finally:
        if turn_id:
            control.release(turn_id, process, keep_turn_control)
        elif not _process_group_released(process):
            terminate(process)
