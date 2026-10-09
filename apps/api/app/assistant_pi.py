from __future__ import annotations

import json
import os
import queue
import re
import signal
import shutil
import subprocess
import threading
import time
from collections import deque
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Callable
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, uuid5

from app.config import Settings, record_llm_service_verification
from app.workspace_settings import workspace_pi_config_dir

# ── 失败分类（0.11 B04）────────────────────────────────────────────────────
# 以前非零退出和不完整回复一律压成一句「请检查模型连接」。这里按日志里**真的**
# 出现过的痕迹分类；查不到就说查不到，不编一个体面的理由。
FAILURE_KEY_REJECTED = "key_rejected"              # 密钥被拒
FAILURE_ADDRESS_UNREACHABLE = "address_unreachable"  # 服务地址不通
FAILURE_TIMEOUT = "timeout"                        # 超时
FAILURE_RUNTIME_MISSING = "runtime_missing"        # 运行环境缺失
FAILURE_TOOL_FAILED = "tool_failed"                # 业务工具失败
FAILURE_UNKNOWN = "unknown"                        # 拿不到更具体的原因

ASSISTANT_BUSINESS_TOOLS = {
    "home": (
        "read_workspace_context", "read_video_templates", "find_knowledge", "find_materials",
        "preview_creation_plan",
    ),
    "material": ("read_selected_material", "update_selected_material_tags"),
    "topic": ("read_current_task", "find_knowledge", "generate_creation_task_topics"),
    "script": ("read_current_task", "find_knowledge", "preview_script_draft"),
    "task_edit": ("read_current_task", "find_knowledge", "find_materials", "preview_edit",
                  "start_creation_execution", "resume_creation_execution", "cancel_creation_execution"),
}

# service_contacted=True 的只有三种：它们都真的向模型服务发过请求，结论可以写进
# 服务状态。业务工具失败和「拿不到原因」不写——本地工具坏掉或日志缺失都不能
# 拿来证明密钥是好的或坏的。
_FAILURES: dict[str, dict[str, object]] = {
    FAILURE_KEY_REJECTED: {
        "service_contacted": True,
        "explanation": "模型服务拒绝了这把密钥——这一次是真的连到了服务，是密钥没通过。",
        "next_step": "在服务商控制台重新复制 API Key 粘贴到设置页，别带上空格和换行，保存后再试一次。",
    },
    FAILURE_ADDRESS_UNREACHABLE: {
        "service_contacted": True,
        "explanation": "连不上设置里的服务地址，还没走到鉴权那一步，所以不是密钥的问题。",
        "next_step": "在设置页核对服务地址是否完整（要含 https:// 和 /v1），并确认当前网络能访问该服务后重试。",
    },
    FAILURE_TIMEOUT: {
        "service_contacted": True,
        "explanation": "等模型回复超时，这一轮没有拿到回复；任务和时间线没有因此改变。",
        "next_step": "先重试一次；仍然超时就核对服务地址是否可达，或换用响应更快的模型。",
    },
    FAILURE_RUNTIME_MISSING: {
        "service_contacted": False,
        "explanation": "对话没能在本机跑起来，所以这一轮根本没碰到密钥和网络。",
        "next_step": "安装 Node.js 22.19 或更高版本，再执行 npm install -g @earendil-works/pi-coding-agent；确认 node --version 和 pi --version 可运行后重新打开应用。桌面安装包尚不自带 Node/Pi。",
    },
    FAILURE_TOOL_FAILED: {
        "service_contacted": False,
        "explanation": "对话本身通了，但业务工具执行失败，助手拿不到任务数据。",
        "next_step": "打开设置页的运行时体检，按标红那一项给的修复提示处理后重试。",
    },
    FAILURE_UNKNOWN: {
        "service_contacted": False,
        "explanation": "这一轮没有完成，日志里没有留下能说明原因的信息，拿不到更具体的原因。",
        "next_step": "重试一次；仍然失败请打开设置页的运行时体检，看服务状态。",
    },
}

# 文本匹配是启发式的，只能说「日志里出现过这句话」，不等于服务端真的这么判。
_KEY_REJECTED_PATTERNS = (
    "invalid api key", "invalid_api_key", "incorrect api key", "incorrect_api_key",
    "unauthorized", "authentication_error", "authentication failed",
    "invalid authentication", "invalid bearer", "no auth credentials",
)
_ADDRESS_PATTERNS = (
    "enotfound", "econnrefused", "ehostunreach", "enetunreach", "eai_again",
    "getaddrinfo", "connection refused", "name resolution", "name or service not known",
    "could not resolve host", "proxyerror",
)
_TIMEOUT_PATTERNS = ("timeout", "timed out", "etimedout", "deadline exceeded", "read timeout")
_TOOL_ERROR_PATTERNS = ("tool_execution_end", "tool call failed", "tool_error", "isError")

_PI_TOOL_LABELS = {
    "read_workspace_context": "读取当前工作区",
    "read_video_templates": "读取可用模板",
    "find_materials": "查找素材",
    "read_preference_memory": "读取制作偏好",
    "find_material_candidates": "查找候选片段",
    "preview_creation_plan": "检查新建方案",
    "read_current_task": "读取当前任务",
    "generate_creation_task_topics": "生成选题候选",
    "find_knowledge": "查找内容资料",
    "preview_script_draft": "准备文案草稿",
    "preview_edit": "准备修改预览",
    "start_creation_execution": "提交连续制作",
    "resume_creation_execution": "继续连续制作",
    "cancel_creation_execution": "取消连续制作",
    "read_selected_material": "读取所选素材",
    "update_selected_material_tags": "修改我的标签",
}


class AssistantStopped(RuntimeError):
    """The current conversation turn's Pi child was stopped by its owner."""


_active_turn_processes: dict[str, subprocess.Popen[str]] = {}
_turn_stop_events: dict[str, threading.Event] = {}
_turn_process_guard = threading.RLock()


def request_stop_turn(turn_id: str) -> bool:
    with _turn_process_guard:
        event = _turn_stop_events.get(turn_id)
        if event is None:
            return False
        process = _active_turn_processes.get(turn_id)
        if process is not None and process.poll() is not None:
            return False
        event.set()
        if process is None:
            return True
    _terminate_process_group(process)
    process.wait(timeout=3)
    return True


def register_turn_control(turn_id: str) -> None:
    with _turn_process_guard:
        _turn_stop_events[turn_id] = threading.Event()


def forget_turn_control(turn_id: str) -> None:
    with _turn_process_guard:
        _turn_stop_events.pop(turn_id, None)
        _active_turn_processes.pop(turn_id, None)


def stop_all_assistant_turns() -> list[str]:
    with _turn_process_guard:
        active_ids = list(_turn_stop_events)
    for turn_id in active_ids:
        request_stop_turn(turn_id)
    return active_ids


def _terminate_process_group(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name == "nt":
            process.terminate()
        else:
            os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        try:
            if os.name == "nt":
                process.kill()
            else:
                os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
    except ProcessLookupError:
        process.wait()


def _safe_tool_result_summary(tool_name: str, result: object, failed: bool) -> str:
    if failed:
        return "这一步没有完成；请以页面显示的任务状态和错误提示为准。"
    payload = result if isinstance(result, dict) else {}
    details = payload.get("details") if isinstance(payload.get("details"), dict) else {}
    if tool_name in {"read_selected_material", "update_selected_material_tags"}:
        scope = "整条素材" if details.get("scope") == "asset" else "当前片段"
        if tool_name == "read_selected_material":
            tags = details.get("user_tags") if isinstance(details.get("user_tags"), list) else []
            return f"已读取{scope}的我的标签，共 {len(tags)} 项。"
        return (f"{scope}的我的标签已提交并回读。" if details.get("changed")
                else f"已回读{scope}的我的标签，内容没有变化。")
    if tool_name in {"start_creation_execution", "resume_creation_execution", "cancel_creation_execution"}:
        action = details.get("action")
        status = details.get("status") if isinstance(details.get("status"), dict) else {}
        job = status.get("job") if isinstance(status.get("job"), dict) else {}
        job_status = job.get("status")
        state = {"queued": "已排队", "running": "正在运行", "succeeded": "已完成",
                 "failed": "失败", "interrupted": "中断", "cancelled": "已取消"}.get(job_status, "状态已返回")
        if action == "cancel":
            return f"取消动作已返回，当前作业{state}。"
        return f"制作请求已返回，当前作业{state}。"
    if tool_name == "preview_creation_plan":
        preview = details.get("preview") if isinstance(details.get("preview"), dict) else {}
        blockers = preview.get("blockers") if isinstance(preview.get("blockers"), list) else []
        return f"新建方案预检已返回，{len(blockers)} 项需处理；尚未创建任务。"
    if tool_name == "preview_script_draft":
        preview = details.get("preview") if isinstance(details.get("preview"), dict) else {}
        sections = preview.get("sections") if isinstance(preview.get("sections"), list) else []
        return f"已准备 {len(sections)} 段文案草稿，尚待你检查和确认。"
    if tool_name == "preview_edit":
        return "修改预览已生成；草稿尚未应用。"
    if tool_name == "generate_creation_task_topics":
        topics = details.get("topics") if isinstance(details.get("topics"), dict) else {}
        candidates = topics.get("candidates") if isinstance(topics.get("candidates"), list) else []
        return f"已生成 {len(candidates)} 个选题候选，尚未选择或确认。"
    count_fields = {
        "find_materials": ("matches", "素材候选"),
        "find_material_candidates": ("candidates", "片段候选"),
        "find_knowledge": ("matches", "资料摘录"),
        "read_video_templates": ("templates", "模板"),
        "read_workspace_context": ("tasks", "任务摘要"),
    }
    if tool_name in count_fields:
        field, label = count_fields[tool_name]
        values = details.get(field) if isinstance(details.get(field), list) else []
        return f"已读取 {len(values)} 项{label}。"
    if tool_name == "read_current_task":
        return "当前任务和所选对象状态已读取。"
    if tool_name == "read_preference_memory":
        return "与本次创作有关的偏好已读取。"
    return "业务步骤已完成。"


class _PiStreamProjector:
    """Project Pi 1.1 JSON events into a small UI-safe event vocabulary."""

    def __init__(self) -> None:
        self._calls: dict[str, tuple[int, str, str]] = {}
        self._next_id = 0

    def project(self, line: str) -> dict[str, object] | None:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            return None  # The final existing event parser reports malformed output.
        event_type = event.get("type")
        if event_type == "message_update":
            update = event.get("assistantMessageEvent")
            if isinstance(update, dict) and update.get("type") == "text_delta":
                delta = update.get("delta")
                return {"type": "text_delta", "delta": delta} if isinstance(delta, str) and delta else None
            return None
        if event_type == "tool_execution_start":
            name = event.get("toolName")
            raw_id = event.get("toolCallId")
            if name not in _PI_TOOL_LABELS:
                return None
            self._next_id += 1
            call_key = raw_id if isinstance(raw_id, str) and raw_id else f"call-{self._next_id}"
            label = _PI_TOOL_LABELS[name]
            self._calls[call_key] = (self._next_id, name, label)
            return {"type": "tool_start", "tool_id": self._next_id, "label": label}
        if event_type == "tool_execution_end":
            raw_id = event.get("toolCallId")
            name = event.get("toolName")
            call_key = raw_id if isinstance(raw_id, str) else ""
            stored = self._calls.pop(call_key, None)
            if stored:
                tool_id, stored_name, label = stored
                name = stored_name
            else:
                if name not in _PI_TOOL_LABELS:
                    return None
                self._next_id += 1
                tool_id, label = self._next_id, _PI_TOOL_LABELS[name]
            failed = bool(event.get("isError"))
            return {
                "type": "tool_end", "tool_id": tool_id, "label": label,
                "ok": not failed,
                "summary": _safe_tool_result_summary(name, event.get("result"), failed),
            }
        return None


def _run_pi_streaming(
    cmd: list[str], *, message: str, env: dict[str, str], cwd: Path,
    timeout_seconds: int, on_event, turn_id: str | None = None,
) -> subprocess.CompletedProcess[str]:
    stop_event = None
    if turn_id:
        with _turn_process_guard:
            stop_event = _turn_stop_events.setdefault(turn_id, threading.Event())
        if stop_event.is_set():
            with _turn_process_guard:
                _turn_stop_events.pop(turn_id, None)
            raise AssistantStopped("assistant turn stopped before Pi started")
    process = subprocess.Popen(
        cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", bufsize=1, env=env, cwd=cwd, start_new_session=True,
    )
    if turn_id:
        with _turn_process_guard:
            _active_turn_processes[turn_id] = process
            if stop_event is not None and stop_event.is_set():
                _terminate_process_group(process)
    output_lines: list[str] = []
    error_lines: deque[str] = deque(maxlen=10000)
    line_queue: queue.Queue[str | None] = queue.Queue()
    projector = _PiStreamProjector()

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
        except BrokenPipeError:
            pass
        finally:
            try:
                process.stdin.close()
            except BrokenPipeError:
                pass
        deadline = time.monotonic() + timeout_seconds
        while True:
            if stop_event is not None and stop_event.is_set():
                _terminate_process_group(process)
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
            event = projector.project(line)
            if event is not None:
                on_event(event)
        return_code = process.wait(timeout=max(0.05, deadline - time.monotonic()))
        stderr_thread.join(timeout=1)
        if stop_event is not None and stop_event.is_set():
            raise AssistantStopped("assistant turn stopped")
        return subprocess.CompletedProcess(cmd, return_code, "".join(output_lines), "".join(error_lines))
    except subprocess.TimeoutExpired:
        _terminate_process_group(process)
        stdout_thread.join(timeout=1)
        stderr_thread.join(timeout=1)
        raise
    finally:
        if turn_id:
            with _turn_process_guard:
                if _active_turn_processes.get(turn_id) is process:
                    _active_turn_processes.pop(turn_id, None)
                _turn_stop_events.pop(turn_id, None)


class AssistantUnavailable(RuntimeError):
    """A conversation failure the user can read.

    `cause` is the class actually observed this round; `service_contacted`
    says whether a request really reached the external model service, which
    is the only thing that may be written back into the service state.
    """

    def __init__(
        self,
        message: str,
        *,
        cause: str = FAILURE_UNKNOWN,
        service_contacted: bool = False,
    ) -> None:
        super().__init__(message)
        self.cause = cause
        self.service_contacted = service_contacted


def _fail(
    cause: str, *, explanation: str | None = None, repair_action: str | None = None,
) -> AssistantUnavailable:
    """Build the user-facing failure text: what happened, then what to do.

    The text is assembled from fixed copy only — never from the process log,
    which can hold the key or the whole session.
    """
    copy = _FAILURES[cause]
    headline = explanation or str(copy["explanation"])
    return AssistantUnavailable(
        f"{headline} 下一步：{repair_action or copy['next_step']}",
        cause=cause,
        service_contacted=bool(copy["service_contacted"]),
    )


def _tool_error_seen(output: str) -> bool:
    for line in output.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "tool_execution_end" and event.get("isError"):
            return True
    return False


def _classify_output(stdout: str, stderr: str = "") -> str:
    """Read one Pi run's logs and name the failure class, or admit we can't."""
    text = f"{stdout}\n{stderr}".lower()
    if any(pattern in text for pattern in _KEY_REJECTED_PATTERNS) or re.search(r"\b401\b", text):
        return FAILURE_KEY_REJECTED
    if any(pattern in text for pattern in _TIMEOUT_PATTERNS):
        return FAILURE_TIMEOUT
    if any(pattern in text for pattern in _ADDRESS_PATTERNS):
        return FAILURE_ADDRESS_UNREACHABLE
    if _tool_error_seen(stdout) or any(pattern in stdout for pattern in _TOOL_ERROR_PATTERNS):
        return FAILURE_TOOL_FAILED
    return FAILURE_UNKNOWN


def _record_service(settings: Settings, *, ok: bool, cause: str, workspace_id: str = "") -> None:
    """Write an observation into the service state — but only a real one.

    A failure that never reached the provider (Pi 没装、业务工具坏、日志读不懂)
    对密钥好坏什么都证明不了，写进去就是把猜测当事实。只有 `ok=True`（真的拿到
    了回复）和 `service_contacted=True` 的失败才写。
    """
    if not ok and not bool(_FAILURES.get(cause, {}).get("service_contacted")):
        return
    record_llm_service_verification(
        settings, ok=ok, cause=cause,
        repair_action=str(_FAILURES[cause]["next_step"]) if not ok else "",
        workspace_id=workspace_id,
    )


_session_locks: dict[str, threading.Lock] = {}
_session_locks_guard = threading.Lock()


SYSTEM_PROMPT = """你是 AI 视频日记的创作助手。你只能处理当前创作任务。
每轮先调用 read_current_task；查资料用 find_knowledge，查可用镜头用 find_materials；修改前调用 preview_edit。
只有当用户明确要求开始制作、修改后重新生成、继续/恢复制作或取消/停止当前制作时，才调用对应的具名制作动作工具一次；问题、建议、假设、单纯提及“生成”或混合的修改要求都不是执行指令。开始只使用当前已确认的文案与制作要求，不能把待确认草稿或普通修改自动应用。
执行开始、继续或取消后，只按工具返回的真实作业状态回复。不得把排队、开始或回复说成已完成。
不得声称已执行、已保存或已出片，除非工具返回相应事实。
preview_edit 仅生成待确认提案；让用户在工作台检查影响并确认。
界面选中对象是默认范围；用户明确说了其他镜头、字幕轨或配音段时，以用户的话为准，从任务摘要定位并向 preview_edit 提供对应引用和阶段。范围不明确就先问。资料不足或工具失败时说明具体阻断，不虚构结果。
对象引用只放 selected_refs，阶段只放 context_stage；command 只包含用户明确且当前工具支持的动作，例如“水平裁剪到40%”，不加入镜头名称、对象范围、保持不变的限制、解释、猜想或副作用。用户的限制必须由实际作用对象和提案影响满足；不能满足就先说明阻断，不丢弃限制。
水平、垂直裁剪百分比表示裁剪窗口的位置，不是画面宽度、面积或保留比例；“向左一点”等缺少明确位置时先问，不猜步长。
每轮最多调用一次 preview_edit。工具失败后准确说明返回的阻断和下一步，结束本轮；不得自行改写指令反复试错。
预览成功后只依据工具返回的 operation_type、before、after 和影响说明实际变化；不得把位置说成宽度收窄或猜测中心对齐。预览不是执行或出片，必须等待用户确认。
不要请求、读取或发送原始媒体文件。只用已提供的业务工具。
回答用日常中文。标题和动作尽量不超过五个字，不说工具名、模型名、预检、提案、版本号等内部词；必要的影响和修复要说清。"""

PLAN_PROMPT = """你是 AI 视频日记的新视频创作助手。只处理当前工作区的新视频目标。
需要事实时调用 find_knowledge；查询可用镜头时调用 find_materials；了解已有任务时调用 read_workspace_context。
用户要求沿用我的模板时，先调用 read_video_templates 查明真实 ID 和版本。
用户指定露脸比例时，将其归到 0%、20%、40%、60%、80%、100% 中最近的一档并说明；未指定时用默认 20%。露脸只用于需要出镜的段落，其余画面共用配音。
用户提出明确制作目标后，调用 preview_creation_plan 做真实预检；信息不足时先问一个具体问题。
不要把旧任务、旧文案或旧成片说成新结果。预检不是任务创建，确认按钮由用户操作。
不要请求或上传原始媒体文件；只用已提供的业务工具。
人物口型默认选 high 高精度；只有用户明确要求速度优先或低精度时才选 fast。不要在高精度不可用时自行改为 fast。
回答用日常中文。标题和动作尽量不超过五个字，不说工具名、模型名、预检、提案等内部词；说清能否开始、缺什么和下一步。"""

MATERIAL_PROMPT = """你是 AI 视频日记素材库里的对话助手，只处理当前页面明确选中的一条素材或一个片段。
每轮先调用 read_selected_material 核对当前对象真实的“我的标签”。只有用户明确要求修改标签并能确定替换、添加或移除及对应文字时，才调用 update_selected_material_tags 一次；“把标签改成……”表示替换，“再加上/添加……”表示添加，“移除/去掉……”表示移除。对象范围由服务端绑定，不能询问、推测或指定其他素材/片段，也不能改系统标签、片段描述、转写、用途或标签夹。
如果用户没有说清操作方式或标签内容，先用日常中文问清，不执行修改。普通标签直接保存，不创建剪辑提案或创作任务，也不要求二次确认。只有工具写后回读成功才可说已保存；失败要如实说明，不得根据请求或模型回复冒称成功。回答仅围绕所选对象当前我的标签，不泄露路径、成员、工作区或其他素材内容。"""

SCRIPT_PROMPT = """你是 AI 视频日记当前创作任务的文案助手。每轮先调用 read_current_task，读取已确认选题、本次时长和允许使用的资料；需要补充事实时调用 find_knowledge。
你只能使用工具列出的当前任务来源 ID 作为文案证据，不能编造引用、事实、素材或已完成的制作结果。资料不足就问一个具体问题。
用户要起草或修改文案时，调用 preview_script_draft 保存待确认文案。修改已有草稿时沿用其段落 ID；先说明想怎么改，再根据用户本轮要求提交新的完整草稿。每轮最多保存一次。
工具只保存待确认草稿，不会确认文案、生成配音或开始制作。请用户在工作台检查并明确确认文案；不要声称已开始制作。
不要请求、读取或发送原始媒体文件。回答用日常中文，不说工具名、模型名、预检、版本号等内部词。"""

TOPIC_PROMPT = """你是 AI 视频日记当前创作任务的选题助手。每轮先调用 read_current_task；用户明确要求生成选题时，调用 generate_creation_task_topics 一次。
工具只会按当前任务已确认的目标生成候选；不得替用户选择或确认，也不得声称任务已进入文案或制作。若用户尚未明确要求生成选题，先问一个具体问题。
回答用日常中文，说明候选已可在工作台查看和选择；不要声称已开始制作。"""


def _agent_root(settings: Settings, workspace_id: str) -> Path:
    parent = Path(settings.model_root_path).expanduser().parent
    if len(parent.parts) >= 3 and parent.parts[1] == "Volumes" and not Path(*parent.parts[:3]).is_mount():
        raise _fail(FAILURE_RUNTIME_MISSING, explanation="素材与模型盘未连接，对话上下文没有地方保存。",
                    repair_action="连接设置中选择的素材与模型盘后重试。")
    if not parent.is_dir():
        raise _fail(FAILURE_RUNTIME_MISSING, explanation="素材与模型盘未连接，对话上下文没有地方保存。",
                    repair_action="连接设置中选择的素材与模型盘后重试。")
    return workspace_pi_config_dir(workspace_id, parent / "agent")


def _configured_model(settings: Settings) -> tuple[str, str, str]:
    endpoint = urlsplit(settings.effective_llm_base_url)
    if endpoint.scheme != "https" or not endpoint.hostname or endpoint.hostname in {"localhost", "127.0.0.1", "::1"}:
        raise _fail(FAILURE_RUNTIME_MISSING, explanation="对话须选择外部文字模型，请先在设置里选 MiniMax 或 DeepSeek。",
                    repair_action="在设置页连接 MiniMax，或主动选择 DeepSeek；不会自动切换服务。")
    key = settings.effective_llm_api_key
    if not key:
        raise _fail(FAILURE_RUNTIME_MISSING, explanation="外部文字模型的 API Key 还没填，所以这一轮对话没有开始。",
                    repair_action="在设置页填写当前所选服务的 API Key 并保存后重试。")
    return settings.effective_llm_base_url, settings.effective_llm_model, key


def _pi_executable() -> str | None:
    configured = os.getenv("AI_VIDEO_DIARY_PI_EXECUTABLE")
    if configured:
        return configured if Path(configured).is_file() else None
    discovered = shutil.which("pi")
    if discovered:
        return discovered
    # Desktop apps launched from Finder do not inherit the interactive nvm PATH.
    candidates = sorted((Path.home() / ".nvm" / "versions" / "node").glob("v*/bin/pi"), reverse=True)
    return str(candidates[0]) if candidates else None


def pi_runtime_checks() -> list[dict]:
    """Local conversation dependencies only; no provider request or secret lookup."""
    pi = _pi_executable()
    pi_ready = bool(pi and os.access(pi, os.X_OK))
    node_path = (str(Path(pi).parent) + os.pathsep if pi else "") + os.environ.get("PATH", "")
    node = shutil.which("node", path=node_path)
    node_ready = False
    node_reason = "未找到 Node.js；对话程序还未启动，不代表密钥被拒。"
    if node:
        try:
            result = subprocess.run(
                [node, "--version"], capture_output=True, text=True, timeout=5,
                check=False, env={"PATH": node_path},
            )
        except (OSError, subprocess.TimeoutExpired):
            node_reason = "无法运行 Node.js 版本检查；对话程序还未启动。"
        else:
            version = re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)", result.stdout.strip())
            if result.returncode == 0 and version:
                node_ready = tuple(map(int, version.groups())) >= (22, 19, 0)
                node_reason = (f"Node.js {result.stdout.strip()} 可用。" if node_ready
                               else "Node.js 版本低于 22.19，无法启动当前 Pi。")
            else:
                node_reason = "Node.js 未返回有效版本，无法确认对话程序可启动。"
    extension = Path(os.getenv("AI_VIDEO_DIARY_PI_EXTENSION") or
                     Path(__file__).parents[1] / "agent_tools" / "diary_tools.ts")
    tools_ready = extension.is_file() and os.access(extension, os.R_OK)
    # The shipped extension imports this sibling; file presence is not HTTP/tool execution.
    if extension.name == "diary_tools.ts":
        tools_ready = tools_ready and (extension.parent / "retrieval.ts").is_file() and (extension.parent / "material_tools.ts").is_file()
    pi_reason = "未找到可执行 Pi；这不是密钥保存或鉴权失败。"
    pi_version: str | None = None
    pi_repair_action = str(_FAILURES[FAILURE_RUNTIME_MISSING]["next_step"])
    pi_fix_command = "npm install -g @earendil-works/pi-coding-agent"
    if pi_ready and node_ready:
        # --version exits before Pi loads settings/auth or starts a model session.
        try:
            with TemporaryDirectory(prefix="diary-pi-check-") as isolated_home:
                result = subprocess.run(
                    [str(pi), "--version"], capture_output=True, text=True, timeout=5,
                    check=False, env={"PATH": node_path, "HOME": isolated_home,
                                      "PI_CODING_AGENT_DIR": isolated_home, "PI_OFFLINE": "1"},
                    cwd=isolated_home,
                )
        except (OSError, subprocess.TimeoutExpired):
            pi_ready = False
            pi_reason = "无法运行隔离的 Pi 版本检查，请核对临时目录与程序启动权限；这不是密钥失败。"
            pi_repair_action = "确认系统临时目录可写、Pi 可执行，并在相同运行环境中执行 pi --version 后重试。"
            pi_fix_command = None
        else:
            matched_version = re.fullmatch(r"\d+\.\d+\.\d+", result.stdout.strip())
            pi_ready = result.returncode == 0 and matched_version is not None
            if pi_ready:
                pi_version = result.stdout.strip()
            pi_reason = (f"Pi {result.stdout.strip()} 可启动；实际对话尚未验证。" if pi_ready
                         else "Pi 未返回有效版本；对话尚未开始，不能据此判定密钥失败。")
    elif pi_ready:
        pi_reason = "已找到 Pi；需先修复 Node.js 才能验证启动。"
    return [
        {"id": "pi", "label": "对话程序 Pi", "status": "ready" if pi_ready else "missing",
         "version": pi_version,
         "reason": pi_reason,
         "repair_action": "无需安装；实际对话由功能入口验证。" if pi_ready else pi_repair_action,
         "fix_command": None if pi_ready else pi_fix_command},
        {"id": "pi_node", "label": "对话运行环境 Node.js", "status": "ready" if node_ready else "missing",
         "reason": node_reason,
         "repair_action": "无需处理。" if node_ready else "安装 Node.js 22.19 或更高版本，使 Pi 同目录或 PATH 中的 node --version 可运行，再重新打开应用。",
         "fix_command": None if node_ready else "node --version"},
        {"id": "pi_tools", "label": "对话业务工具", "status": "ready" if tools_ready else "missing",
         "reason": "业务工具文件可读；注册与 HTTP 回调尚未验证。" if tools_ready else "对话业务工具文件缺失或不可读，助手无法读取任务数据。",
         "repair_action": "无需处理；工具调用由实际对话验证。" if tools_ready else "重新安装包含业务工具的桌面包；开发环境核对 AI_VIDEO_DIARY_PI_EXTENSION 及 diary_tools.ts 同目录的 retrieval.ts。",
         "fix_command": None},
    ]


def _last_answer_and_preview(output: str, kind: str = "edit_preview") -> tuple[str, dict | None]:
    answer = ""
    preview = None
    action_result = None
    action_failed = False
    topic_result = None
    topic_failed = False
    material_result = None
    material_failed = False
    material_result_invalid = False
    settled = False
    action_tools = {
        "start_creation_execution": "start",
        "resume_creation_execution": "resume",
        "cancel_creation_execution": "cancel",
    }
    for line in output.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise _fail(
                FAILURE_UNKNOWN,
                explanation="对话助手返回了无法读取的事件，这一轮对话没完成。",
            ) from exc
        if event.get("type") == "tool_execution_end" and event.get("toolName") in {
            "preview_edit", "preview_creation_plan", "preview_script_draft",
            "generate_creation_task_topics", *action_tools,
            "read_selected_material", "update_selected_material_tags",
        }:
            if event.get("toolName") in {"read_selected_material", "update_selected_material_tags"}:
                material_failed = bool(event.get("isError")) or material_failed
                if not event.get("isError"):
                    details = (event.get("result") or {}).get("details") or {}
                    scope = details.get("scope")
                    asset_ref = details.get("asset_ref")
                    segment_ref = details.get("segment_ref")
                    user_tags = details.get("user_tags")
                    system_tags = details.get("system_tags")
                    valid_tags = isinstance(user_tags, list) and all(isinstance(tag, str) for tag in user_tags)
                    valid_scope_ref = (
                        scope == "asset" and isinstance(asset_ref, str) and segment_ref is None
                    ) or (
                        scope == "segment" and isinstance(asset_ref, str)
                        and isinstance(segment_ref, str) and isinstance(system_tags, list)
                        and all(isinstance(tag, str) for tag in system_tags)
                    )
                    if valid_scope_ref and valid_tags:
                        material_result = {
                            "scope": scope,
                            "asset_ref": asset_ref,
                            **({"segment_ref": segment_ref} if scope == "segment" and isinstance(segment_ref, str) else {}),
                            "user_tags": [tag for tag in user_tags if isinstance(tag, str)],
                            **({"system_tags": [tag for tag in system_tags if isinstance(tag, str)]}
                               if scope == "segment" and isinstance(system_tags, list) else {}),
                            **({"action": details["action"]} if details.get("action") in {"replace", "add", "remove"} else {}),
                            **({"changed": details["changed"]} if isinstance(details.get("changed"), bool) else {}),
                            **({"previous_user_tags": [tag for tag in details["previous_user_tags"] if isinstance(tag, str)]}
                               if isinstance(details.get("previous_user_tags"), list) else {}),
                        }
                    else:
                        material_result_invalid = True
            if event.get("toolName") in action_tools:
                action_failed = bool(event.get("isError"))
                if not action_failed:
                    details = (event.get("result") or {}).get("details") or {}
                    if details.get("kind") == "execution_action":
                        action_result = {**details, "action": action_tools[event["toolName"]]}
            if event.get("toolName") == "generate_creation_task_topics":
                topic_failed = bool(event.get("isError"))
                if not topic_failed:
                    details = (event.get("result") or {}).get("details") or {}
                    if details.get("kind") == "topic_generation":
                        topic_result = details.get("topics")
            if not event.get("isError"):
                details = (event.get("result") or {}).get("details") or {}
                if details.get("kind") == kind:
                    preview = details.get("preview")
                    if kind == "topic_generation":
                        preview = {"kind": "topic_generation", "topics": details.get("topics")}
        if event.get("type") == "message_end":
            message = event.get("message") or {}
            if message.get("role") == "assistant" and message.get("stopReason") == "stop":
                answer = "".join(
                    block.get("text", "")
                    for block in message.get("content", [])
                    if block.get("type") == "text"
                ).strip()
        if event.get("type") == "agent_settled":
            settled = True
    if (action_failed and action_result is None) or topic_failed:
        raise _fail(FAILURE_TOOL_FAILED)
    if not settled or not answer:
        cause = _classify_output(output)
        if cause == FAILURE_UNKNOWN:
            raise _fail(
                cause,
                explanation="对话没有得到完整回复，日志里没有留下能说明原因的信息，拿不到更具体的原因。",
            )
        raise _fail(cause)
    if kind == "material_result":
        if material_failed or material_result_invalid:
            raise _fail(FAILURE_TOOL_FAILED)
        if material_result is None:
            return answer, None
        tags = "、".join(material_result["user_tags"]) or "暂无"
        object_label = "整条素材" if material_result["scope"] == "asset" else "当前片段"
        if material_result.get("action") and material_result.get("changed"):
            answer = f"{object_label}的我的标签已保存并回读：{tags}。"
        return answer, {"kind": "material_result", "material_result": material_result}
    if action_result is not None:
        status = action_result.get("status") or {}
        job = status.get("job") if isinstance(status, dict) else None
        job_status = job.get("status") if isinstance(job, dict) else None
        action = action_result.get("action")
        if action == "cancel":
            answer = f"取消操作已返回；当前作业状态：{job_status or '未知'}。"
        elif job_status == "queued":
            answer = "连续制作已提交并排队，尚未完成；可在任务页查看实际进度。"
        elif job_status == "running":
            answer = "连续制作正在运行，尚未完成；可在任务页查看实际进度。"
        elif job_status == "succeeded" and isinstance(status, dict) and status.get("video_ready"):
            answer = "任务记录显示制作已完成，当前成片产物可用。"
        else:
            answer = f"制作动作已返回；当前作业状态：{job_status or '未知'}。请以任务页回读为准。"
        return answer, {"kind": "execution_action", "action": action, "status": status}
    if topic_result is not None:
        topics = topic_result.get("candidates") or []
        count = len(topics)
        return f"已为当前任务生成 {count} 个选题候选；它们尚未被选择或确认，请在工作台查看并明确选择。", {
            "kind": "topic_generation", "topics": topic_result,
        }
    if kind == "edit_preview" and preview is not None:
        # The proposal is the business result; the model's final text stays in
        # the Pi session as evidence and does not define confirmation effects.
        proposal = preview["proposal"]
        operation_type = proposal["operation"]["operation_type"]
        lines = [
            proposal["summary"],
            "待确认，尚未应用修改。",
            f"目标：{'、'.join(proposal['target_refs'])}",
            f"操作：{operation_type}",
            f"修改前：{proposal['preview']['before']['detail']}",
            f"修改后：{proposal['preview']['after']['detail']}",
        ]
        if operation_type == "ChangeCrop":
            lines.append("水平/垂直百分比表示裁剪窗口位置，不是画面宽度或面积比例。")
        lines.extend(proposal["external_effects"])
        if proposal["invalidated_refs"]:
            lines.append("确认后已有结果将失效，请核对预览卡中的“需要重做”列表。")
        lines.append("请在工作台确认后应用本提案；确认修改不等于完整成片已生成，成片以实际输出结果为准。")
        answer = "\n".join(lines)
    return answer, preview


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
) -> tuple[str, dict | None]:
    script_turn = stage == "script"
    topic_turn = stage == "topic"
    tools = ",".join(ASSISTANT_BUSINESS_TOOLS[
        "topic" if topic_turn else "script" if script_turn else "task_edit"
    ])
    return _run_turn(
        settings, workspace_id=workspace_id, task_id=task_id, message=message,
        stage=stage, selected_refs=selected_refs, playhead_ms=playhead_ms,
        authorization=authorization, cookie=cookie,
        local_member_token=local_member_token, caller_user_id=caller_user_id,
        on_event=on_event,
        prompt=TOPIC_PROMPT if topic_turn else SCRIPT_PROMPT if script_turn else SYSTEM_PROMPT,
        tool_names=tools,
        preview_kind="topic_generation" if topic_turn else "script_draft" if script_turn else "edit_preview",
        session_key=(f"task:{task_id}:{conversation_id}:{'topic' if topic_turn else 'script' if script_turn else 'edit'}"
                     if conversation_id else f"task:{task_id}:{'topic' if topic_turn else 'script' if script_turn else 'edit'}"),
        turn_id=turn_id,
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
) -> tuple[str, dict | None]:
    return _run_turn(
        settings, workspace_id=workspace_id, task_id="", message=message,
        stage="", selected_refs=[], playhead_ms=None,
        authorization=authorization, cookie=cookie,
        local_member_token=local_member_token, caller_user_id=caller_user_id,
        on_event=on_event,
        prompt=PLAN_PROMPT, tool_names=",".join(ASSISTANT_BUSINESS_TOOLS["home"]),
        preview_kind="plan_preview", session_key=f"new-plan:{conversation_id}", turn_id=turn_id,
    )


def run_material_turn(
    settings: Settings,
    *,
    workspace_id: str,
    conversation_id: str,
    message: str,
    selection: dict[str, str],
    authorization: str | None = None,
    cookie: str | None = None,
    local_member_token: str | None = None,
    caller_user_id: str | None = None,
    on_event: Callable[[dict[str, object]], None] | None = None,
    turn_id: str | None = None,
) -> tuple[str, dict | None]:
    tool_selection = {
        "scope": selection["scope"],
        "assetId": selection["asset_id"],
        **({"segmentId": selection["segment_id"]} if selection["scope"] == "segment" else {}),
    }
    return _run_turn(
        settings, workspace_id=workspace_id, task_id="", message=message,
        stage="material", selected_refs=[], playhead_ms=None,
        authorization=authorization, cookie=cookie,
        local_member_token=local_member_token, caller_user_id=caller_user_id,
        on_event=on_event, prompt=MATERIAL_PROMPT,
        tool_names=",".join(ASSISTANT_BUSINESS_TOOLS["material"]),
        preview_kind="material_result",
        session_key=(f"material:{conversation_id}:{selection['scope']}:{selection['asset_id']}"
                     f":{selection.get('segment_id', '')}"),
        material_selection=tool_selection,
        turn_id=turn_id,
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
    turn_id: str | None = None,
) -> tuple[str, dict | None]:
    checks = pi_runtime_checks()
    for check in checks:
        if check["status"] != "ready":
            raise _fail(
                FAILURE_RUNTIME_MISSING,
                explanation=f"{check['reason']} 对话没能在本机跑起来，没碰到密钥和网络。",
                repair_action=check["repair_action"],
            )
    pi_executable = _pi_executable()
    extension = Path(os.getenv("AI_VIDEO_DIARY_PI_EXTENSION") or Path(__file__).parents[1] / "agent_tools" / "diary_tools.ts")
    base_url, model, key = _configured_model(settings)
    root = _agent_root(settings, workspace_id)
    # 已解析的可信调用方身份参与会话键：不同成员不共用同一段对话历史。
    # 没有身份时沿用既有单机行为，不额外造第二套隔离。
    caller_scope = f":{caller_user_id}" if caller_user_id else ""
    session_id = uuid5(NAMESPACE_URL, f"ai-video-diary:{workspace_id}:{session_key or task_id}{caller_scope}")
    runtime_root = root / "runtimes" / str(session_id)
    runtime_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    model_definition: dict[str, object] = {"id": model}
    if settings.effective_llm_provider == "minimax" and model == "MiniMax-M3":
        # M3 otherwise puts <think> in content. Pi keeps reasoning_content in
        # thinking blocks/history; only text blocks reach the user's answer.
        model_definition["samplingParams"] = {"reasoning_split": True}
    (runtime_root / "models.json").write_text(json.dumps({
        "providers": {"ai-video-diary": {
            "baseUrl": base_url,
            "api": "openai-completions",
            "apiKey": "$AI_VIDEO_DIARY_LLM_KEY",
            "models": [model_definition],
        }}
    }), encoding="utf-8")
    (runtime_root / "models.json").chmod(0o600)
    env = {
        **os.environ,
        "PI_CODING_AGENT_DIR": str(runtime_root),
        "PI_OFFLINE": "1",
        "PATH": str(Path(pi_executable).parent) + os.pathsep + os.environ.get("PATH", ""),
        "AI_VIDEO_DIARY_LLM_KEY": key,
        "AI_VIDEO_DIARY_AGENT_API_BASE": f"http://127.0.0.1:{int(os.getenv('API_PORT', '8000'))}",
        "AI_VIDEO_DIARY_AGENT_TASK_ID": task_id,
        "AI_VIDEO_DIARY_AGENT_STAGE": stage,
        "AI_VIDEO_DIARY_AGENT_SELECTED_REFS": json.dumps(selected_refs),
        "AI_VIDEO_DIARY_AGENT_PLAYHEAD_MS": "" if playhead_ms is None else str(playhead_ms),
        "AI_VIDEO_DIARY_AGENT_AUTHORIZATION": authorization or "",
        "AI_VIDEO_DIARY_AGENT_COOKIE": cookie or "",
        # 已验证的本地成员令牌：工具回调必须带上，否则会退化成匿名 local 诊断读取。
        "AI_VIDEO_DIARY_AGENT_LOCAL_MEMBER_TOKEN": local_member_token or "",
        "AI_VIDEO_DIARY_AGENT_MATERIAL_SELECTION": json.dumps(material_selection) if material_selection else "",
    }
    cmd = [
        str(pi_executable), "--print", "--mode", "json", "--model", f"ai-video-diary/{model}",
        "--session-id", str(session_id), "--session-dir", str(root / "sessions"),
        "--system-prompt", prompt,
        "--no-builtin-tools", "--no-extensions", "--no-skills", "--no-context-files",
        "--no-prompt-templates", "--no-themes", "--extension", str(extension),
        "--tools", tool_names,
    ]
    with _session_locks_guard:
        session_lock = _session_locks.setdefault(str(session_id), threading.Lock())
    if not session_lock.acquire(blocking=False):
        raise _fail(
            FAILURE_UNKNOWN,
            explanation="同一个任务还有一条对话正在处理，这一轮没有开始，也没有碰过服务。",
        )
    try:
        result = (_run_pi_streaming(
            cmd, message=message, env=env, cwd=runtime_root,
            timeout_seconds=180, on_event=on_event or (lambda _event: None), turn_id=turn_id,
        ) if on_event is not None or turn_id is not None else subprocess.run(
            cmd, input=message, text=True, capture_output=True, timeout=180,
            env=env, cwd=runtime_root, check=False,
        ))
    except subprocess.TimeoutExpired as exc:
        # 请求已经发出去了，所以这是关于服务的一次真实观察。
        _record_service(settings, ok=False, cause=FAILURE_TIMEOUT, workspace_id=workspace_id)
        raise _fail(FAILURE_TIMEOUT) from exc
    except OSError as exc:
        raise _fail(
            FAILURE_RUNTIME_MISSING,
            explanation="启动不了本机的对话程序，助手根本没跑起来，所以没碰到密钥和网络。",
        ) from exc
    finally:
        session_lock.release()
    if result.returncode:
        failure = _fail(_classify_output(result.stdout, result.stderr or ""))
        _record_service(settings, ok=False, cause=failure.cause, workspace_id=workspace_id)
        raise failure
    try:
        answer, preview = _last_answer_and_preview(result.stdout, preview_kind)
    except AssistantUnavailable as exc:
        # 不完整回复也可能是密钥被拒或超时打回来的，日志里能看出来就照实记。
        _record_service(settings, ok=False, cause=exc.cause, workspace_id=workspace_id)
        raise
    # 走到这里说明这一轮真的连了服务、也真的拿到了回复。
    _record_service(settings, ok=True, cause="verified", workspace_id=workspace_id)
    return answer, preview
