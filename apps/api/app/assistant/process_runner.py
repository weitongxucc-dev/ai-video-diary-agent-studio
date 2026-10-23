from __future__ import annotations

import queue
import subprocess
import threading
import time
import json
import copy
from collections import deque
from pathlib import Path
from app.assistant.turn_control import AssistantStopped, AssistantProcessReleaseError, TurnControl, _capture_process_group, _process_group_released, _require_process_group_platform


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
    business_handler=None, native_handler=None, source_binding=None,
) -> subprocess.CompletedProcess[str]:
    business_bridge=business_handler is not None
    if native_handler is not None:
        if not callable(getattr(native_handler,'cancel',None)):
            raise ValueError('原生执行缺少宿主取消与释放端口，未启动助手')
        original_business=business_handler
        native_operations={'open_task_working_context','native_task_file','submit_task_command',
            'read_task_command','cancel_task_command','register_task_artifacts'}
        def dispatch(operation,arguments,**context):
            if operation in native_operations: return native_handler(operation,arguments,**context)
            if original_business is None:
                return {'ok':False,'failure':{'status_code':403,'submission_effect':'not_submitted',
                    'reason':'此管道只提供任务原生操作；原业务工具沿原入口执行。'}}
            return original_business(operation,arguments,**context)
        business_handler=dispatch
    if business_handler is not None and not isinstance(source_binding, dict):
        raise ValueError("后台业务管道缺少宿主绑定的可信来源。")
    if business_handler is not None and turn_id is None:
        turn_id = source_binding["turn_id"]
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
    current_binding = copy.deepcopy(source_binding)
    tool_bindings = {}
    # Code Mode already admits at most eight calls. One worker preserves the
    # business writer order while its bounded queue leaves stdout/cancel free.
    business_queue = queue.Queue(maxsize=8)
    business_results = queue.SimpleQueue()
    business_calls = {}
    business_closed = threading.Event()
    native_cancel_requested=threading.Event()

    def verify_native_release(deadline):
        if not native_cancel_requested.is_set() and not (stop_event is not None and stop_event.is_set()): return
        # Called only after the one admitted host worker drained, so no late
        # submit can commit after the native owner enumerates these jobs.
        try:
            receipt=native_handler.cancel(source_binding=current_binding,deadline_monotonic=deadline)
            if not receipt['cancellation_complete'] or receipt['truncated'] or any((job['process_state'],job['release_state']) not in {
                    ('exited','released'),('not_started','not_acquired')} for job in receipt['jobs']):
                raise AssistantProcessReleaseError('原生执行仍未证实退出和释放；停止登记保留，请回读原作业')
        except AssistantProcessReleaseError: raise
        except Exception as exc:
            raise AssistantProcessReleaseError('原生执行取消回执不可核实；停止登记保留，不能确认资源已释放') from exc

    def failure(error, reason, effect="not_submitted", status=409):
        return {"ok": False, "failure": {"status_code": status, "error": error,
            "reason": reason, "stage": "host_business", "submission_effect": effect,
            "repair_action": "读取原请求与当前对象，核实结果后再决定后续动作；不要盲重试。"}}

    def run_business():
        while not business_closed.is_set():
            try:
                item = business_queue.get(timeout=.25)
            except queue.Empty:
                continue
            if item is None:
                break
            event, binding, cancelled, deadline = item
            if cancelled.is_set() or time.monotonic() >= deadline:
                result = failure("request_aborted_before_submit", "业务调用在执行前已停止，请求未提交。")
            else:
                try:
                    result = business_handler(event["operation"], event["arguments"],
                        call_id=event["call_id"], tool_call_id=event.get("tool_call_id"),
                        source_binding=binding, cancel_event=cancelled, deadline_monotonic=deadline)
                except Exception:
                    # A callback exception cannot prove that an admitted write
                    # did not happen. Never expose its raw exception to Pi.
                    result = failure("business_host_failed", "宿主业务调用未取得完整回执。", "unknown", 500)
            business_results.put({"type": "diary_business_result", "call_id": event["call_id"], **result})

    def close_business():
        business_closed.set()
        for cancelled in tuple(business_calls.values()):
            cancelled.set()
        try:
            business_queue.put_nowait(None)
        except queue.Full:
            pass  # A busy worker observes the close flag before taking more work.

    def write_input(value):
        # This invocation's consume/drain loop is the sole stdin writer.
        process.stdin.write(json.dumps(value, ensure_ascii=False) + "\n")
        process.stdin.flush()

    def drain_business_results():
        while not business_results.empty():
            result = business_results.get_nowait()
            business_calls.pop(result["call_id"], None)
            if process.poll() is None:
                try:
                    write_input(result)
                except BrokenPipeError:
                    return

    def decode(line):
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            return None  # The SDK result reader classifies malformed stdout.
        return event if isinstance(event, dict) else None

    def consume(line: str, *, cancelling: bool = False) -> None:
        nonlocal current_binding
        output_lines.append(line)
        event = decode(line)
        if event is None:
            return
        kind = event.get("type")
        if business_handler is not None:
            if native_handler is not None and kind=='turn_start':
                current_binding={**current_binding,'sdk_round':current_binding.get('sdk_round',0)+1}
            if kind == "tool_execution_start":
                tool_bindings[event["toolCallId"]] = copy.deepcopy(current_binding)
            elif kind == "diary_business_cancel":
                cancelled = business_calls.get(event.get("call_id"))
                if cancelled is not None:
                    cancelled.set()
                return
            elif kind == "diary_business_call":
                call_id = event.get("call_id")
                if not isinstance(call_id, str) or call_id in business_calls or not isinstance(event.get("operation"), str) or not isinstance(event.get("arguments"), dict):
                    raise ValueError("后台业务调用身份或参数不可读。")
                binding = tool_bindings.get(event.get("tool_call_id"))
                if event.get("tool_call_id") is None and isinstance(initial, dict) and initial.get("factMode") in {"task", "memory"}:
                    binding = current_binding
                if cancelling or business_closed.is_set() or binding is None:
                    write_input({"type": "diary_business_result", "call_id": call_id,
                        **failure("request_not_submitted", "调用已停止或没有可信工具来源，请求未提交。")})
                    return
                cancelled = threading.Event()
                try:
                    business_queue.put_nowait((event, copy.deepcopy(binding), cancelled,
                        time.monotonic() + min(120, max(0, phases.remaining(time.monotonic())))))
                except queue.Full:
                    write_input({"type": "diary_business_result", "call_id": call_id,
                        **failure("business_queue_full", "本轮业务调用已达到现有编排容量，请求未提交。")})
                else:
                    business_calls[call_id] = cancelled
                return
        if event.get("type") == "diary_input_checkpoint":
            if input_handler is not None and not cancelling:
                input_options=({"cancel_event":stop_event,"deadline_monotonic":time.monotonic()+min(120,max(0,phases.remaining(time.monotonic())))}
                    if business_bridge else {})
                result = input_handler(event["phase"], event.get("input_id"), event.get("mode"),**input_options)
                if business_bridge and event["phase"] == "consume" and result:
                    # Only the repository-backed host callback can advance a
                    # goal binding. This private field is never sent to JS.
                    trusted=result.pop('_business_binding')
                    current_binding = {**current_binding,
                        "goal_revision":trusted['goal_revision'],"input_id":trusted['input_id'],
                        "input_turn_id":trusted['input_turn_id'],"task_version":trusted['task_version']}
                elif native_handler is not None and event['phase']=='consume' and result:
                    snapshot=result['frozen_context']['target_snapshot']
                    current_binding={**current_binding,'input_id':event['input_id'],'input_turn_id':current_binding['turn_id'],
                        'task_version':{key:snapshot[key] for key in ('timeline_version_id','timeline_sequence')}}
                write_input({"type": "diary_input_response", "checkpoint_id": event["checkpoint_id"], "result": result})
            return
        for business_line in business_event_lines(line, seen_business_calls):
            projected = projector.project(business_line)
            if projected is not None and (not cancelling or projected.get("type") == "tool_end"):
                on_event(projected)

    def cancel_and_collect() -> None:
        native_cancel_requested.set()
        for cancelled in business_calls.values():
            cancelled.set()
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
                drain_business_results()
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
        if business_handler is not None:
            control.launch_callback(turn_id, threading.Thread(target=run_business, name="pi-assistant-business", daemon=True), close_business,
                **({'verify_release':verify_native_release} if native_handler is not None else {}))
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
            drain_business_results()
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
        close_business()
        if process.stdin is not None and not process.stdin.closed:
            try:
                process.stdin.close()
            except BrokenPipeError:
                pass
        if turn_id:
            control.release(turn_id, process, keep_turn_control)
        elif not _process_group_released(process):
            terminate(process)
