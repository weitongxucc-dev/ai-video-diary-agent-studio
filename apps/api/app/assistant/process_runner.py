from __future__ import annotations

import queue
import subprocess
import threading
import time
import json
from collections import deque
from pathlib import Path
from app.assistant.turn_control import AssistantStopped, TurnControl, _capture_process_group, _process_group_released, _require_process_group_platform


class AssistantPhaseTimeout(subprocess.TimeoutExpired):
    def __init__(self, cmd, phase, seconds, output, stderr):
        super().__init__(cmd, seconds, output, stderr)
        self.phase = phase


class _PhaseDeadlines:
    """One invocation's SDK phases; HTTP waiting never spends model/tool CPU time."""
    def __init__(self, seconds: float, now: float):
        self.seconds = seconds
        self.phase = "startup"
        self.deadline = now + seconds
        self.turn_open = False
        self.model_open = False
        self.compacting = False
        self.tools: dict[str, dict] = {}
        self.requests: dict[str, tuple[str, float]] = {}
        self.seen_requests: set[str] = set()
        self.seen_tools: set[str] = set()

    def _deadlines(self):
        deadlines = [("business_request", deadline, 120) for _, deadline in self.requests.values()]
        deadlines.extend(("tool", tool["since"] + tool["remaining"], self.seconds)
                         for tool in self.tools.values() if tool["since"] is not None)
        if not self.tools:
            deadlines.append((self.phase, self.deadline, self.seconds))
        return deadlines

    def remaining(self, now: float) -> float:
        return min(deadline - now for _, deadline, _ in self._deadlines())

    def expired(self, now: float):
        phase, deadline, seconds = min(self._deadlines(), key=lambda value: value[1])
        return (phase, seconds) if now >= deadline else None

    def observe(self, event: dict, now: float) -> None:
        # A late end/start must not rescue an already expired phase.
        if self.expired(now):
            return
        kind = event.get("type")
        if kind == "turn_start" and not self.turn_open and not self.tools:
            self.seen_tools.clear()  # Provider call IDs may repeat in a later SDK turn.
            self.turn_open = self.model_open = True
            self.phase, self.deadline = "model", now + self.seconds
        elif kind == "message_end" and (event.get("message") or {}).get("role") == "assistant" and self.model_open:
            self.model_open = False
            self.phase, self.deadline = "handoff", now + self.seconds
        elif kind == "turn_end" and not self.tools:
            self.turn_open = self.model_open = False
        elif kind == "compaction_start" and not self.compacting and not self.tools:
            self.compacting = True
            self.phase, self.deadline = "compaction", now + self.seconds
        elif kind == "compaction_end" and self.compacting:
            self.compacting = False
            self.phase, self.deadline = "handoff", now + self.seconds
        elif kind == "tool_execution_start":
            call = event.get("toolCallId")
            if isinstance(call, str) and call and call not in self.seen_tools:
                self.seen_tools.add(call)
                self.tools[call] = {"remaining": self.seconds, "since": now, "requests": set()}
        elif kind == "diary_business_request":
            call, request, phase = event.get("tool_call_id"), event.get("request_id"), event.get("phase")
            tool = self.tools.get(call) if isinstance(call, str) else None
            if tool is None or not isinstance(request, str) or not request or event.get("timeout_ms") != 120000:
                return
            if phase == "start" and request not in self.seen_requests:
                self.seen_requests.add(request)
                if not tool["requests"]:
                    tool["remaining"] -= now - tool["since"]
                    tool["since"] = None
                tool["requests"].add(request)
                self.requests[request] = (call, now + 120)
            elif phase == "end" and request in self.requests and self.requests[request][0] == call:
                self.requests.pop(request)
                tool["requests"].remove(request)
                if not tool["requests"]:
                    tool["since"] = now
        elif kind == "tool_execution_end":
            call = event.get("toolCallId")
            tool = self.tools.get(call) if isinstance(call, str) else None
            if tool is not None and not tool["requests"]:
                self.tools.pop(call)
                if not self.tools:
                    self.phase, self.deadline = "handoff", now + self.seconds
        elif kind == "agent_settled" and not self.tools and self.phase != "settled":
            self.phase, self.deadline = "settled", now + self.seconds


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
    line_queue: queue.Queue[tuple[float, str | None]] = queue.Queue()
    projector = projector_factory()
    seen_business_calls: set[str] = set()

    def read_stdout() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            line_queue.put((time.monotonic(), line))
        line_queue.put((time.monotonic(), None))

    def read_stderr() -> None:
        assert process.stderr is not None
        for line in process.stderr:
            error_lines.append(line)

    stdout_thread = threading.Thread(target=read_stdout, name="pi-assistant-stdout", daemon=True)
    stderr_thread = threading.Thread(target=read_stderr, name="pi-assistant-stderr", daemon=True)
    stdout_thread.start()
    stderr_thread.start()
    phases = _PhaseDeadlines(timeout_seconds, time.monotonic())
    try:
        initial = json.loads(message)
    except json.JSONDecodeError:
        initial = None
    control_enabled = isinstance(initial, dict) and initial.get("controlEnabled") is True

    def decode(line):
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            return None  # The SDK result reader classifies malformed stdout.
        return event if isinstance(event, dict) else None

    def consume(line: str, *, cancelling: bool = False) -> None:
        output_lines.append(line)
        event = decode(line)
        if event is None:
            return
        if event.get("type") == "diary_input_checkpoint":
            if input_handler is not None and not cancelling:
                result = input_handler(event["phase"], event.get("input_id"), event.get("mode"))
                process.stdin.write(json.dumps({"type": "diary_input_response", "checkpoint_id": event["checkpoint_id"], "result": result}, ensure_ascii=False) + "\n")
                process.stdin.flush()
            return
        for business_line in business_event_lines(line, seen_business_calls):
            projected = projector.project(business_line)
            if projected is not None and (not cancelling or projected.get("type") == "tool_end"):
                on_event(projected)

    def cancel_and_collect() -> None:
        if control_enabled and process.poll() is None:
            try:
                process.stdin.write('{"type":"diary_abort"}\n')
                process.stdin.flush()
            except BrokenPipeError:
                pass
            # Reuse the previous one-second stream-drain allowance. This is
            # cooperative shutdown, never extra model or HTTP execution budget.
            until = time.monotonic() + 1
            while time.monotonic() < until:
                try:
                    _, line = line_queue.get(timeout=max(0, min(.25, until - time.monotonic())))
                except queue.Empty:
                    continue
                if line is None:
                    break
                consume(line, cancelling=True)
            try:
                process.wait(timeout=max(0, until - time.monotonic()))
            except subprocess.TimeoutExpired:
                pass
        process.poll()  # Reap a cooperating leader before the existing PGID proof.
        if not _process_group_released(process):
            terminate(process)
        stdout_thread.join(timeout=1)
        stderr_thread.join(timeout=1)
    try:
        assert process.stdin is not None
        try:
            process.stdin.write(message)
            process.stdin.flush()
        except BrokenPipeError:
            pass
        finally:
            try:
                if input_handler is None and not control_enabled:
                    process.stdin.close()
            except BrokenPipeError:
                pass
        while True:
            if stop_event is not None and stop_event.is_set():
                cancel_and_collect()
                raise AssistantStopped("assistant turn stopped")
            try:
                arrived, line = line_queue.get(timeout=max(0, min(.25, phases.remaining(time.monotonic()))))
            except queue.Empty:
                expired = phases.expired(time.monotonic())
                if expired:
                    raise AssistantPhaseTimeout(cmd, *expired, "".join(output_lines), "".join(error_lines))
                continue
            expired = phases.expired(arrived)
            if expired:
                # Keep the actual late receipt as evidence during cancellation.
                if line is not None:
                    consume(line, cancelling=True)
                raise AssistantPhaseTimeout(cmd, *expired, "".join(output_lines), "".join(error_lines))
            if line is None:
                if stop_event is not None and stop_event.is_set():
                    raise AssistantStopped("assistant turn stopped")
                break
            event = decode(line)
            if event is not None:
                phases.observe(event, arrived)
            consume(line)
        return_code = process.wait(timeout=max(.05, phases.remaining(time.monotonic())))
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
    except subprocess.TimeoutExpired as exc:
        cancel_and_collect()
        exc.output, exc.stderr = "".join(output_lines), "".join(error_lines)
        raise
    finally:
        if process.stdin is not None and not process.stdin.closed:
            try:
                process.stdin.close()
            except BrokenPipeError:
                pass
        if turn_id:
            control.release(turn_id, process, keep_turn_control)
        elif not _process_group_released(process):
            terminate(process)
