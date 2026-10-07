from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
from pathlib import Path
from tempfile import TemporaryDirectory
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
不得声称已执行、已保存或已出片，除非工具返回相应事实。
preview_edit 仅生成待确认提案；让用户在工作台检查影响并确认。
界面选中对象是默认范围；用户明确说了其他镜头、字幕轨或配音段时，以用户的话为准，从任务摘要定位并向 preview_edit 提供对应引用和阶段。范围不明确就先问。资料不足或工具失败时说明具体阻断，不虚构结果。
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

SCRIPT_PROMPT = """你是 AI 视频日记当前创作任务的文案助手。每轮先调用 read_current_task，读取已确认选题、本次时长和允许使用的资料；需要补充事实时调用 find_knowledge。
你只能使用工具列出的当前任务来源 ID 作为文案证据，不能编造引用、事实、素材或已完成的制作结果。资料不足就问一个具体问题。
用户要起草或修改文案时，调用 preview_script_draft 保存待确认文案。修改已有草稿时沿用其段落 ID；先说明想怎么改，再根据用户本轮要求提交新的完整草稿。每轮最多保存一次。
工具只保存待确认草稿，不会确认文案、生成配音或开始制作。请用户在工作台检查并明确确认文案；不要声称已开始制作。
不要请求、读取或发送原始媒体文件。回答用日常中文，不说工具名、模型名、预检、版本号等内部词。"""


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
        tools_ready = tools_ready and (extension.parent / "retrieval.ts").is_file()
    pi_reason = "未找到可执行 Pi；这不是密钥保存或鉴权失败。"
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
            pi_ready = result.returncode == 0 and bool(re.fullmatch(r"\d+\.\d+\.\d+", result.stdout.strip()))
            pi_reason = (f"Pi {result.stdout.strip()} 可启动；实际对话尚未验证。" if pi_ready
                         else "Pi 未返回有效版本；对话尚未开始，不能据此判定密钥失败。")
    elif pi_ready:
        pi_reason = "已找到 Pi；需先修复 Node.js 才能验证启动。"
    return [
        {"id": "pi", "label": "对话程序 Pi", "status": "ready" if pi_ready else "missing",
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
    settled = False
    for line in output.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise _fail(
                FAILURE_UNKNOWN,
                explanation="对话助手返回了无法读取的事件，这一轮对话没完成。",
            ) from exc
        if event.get("type") == "tool_execution_end" and event.get("toolName") in {"preview_edit", "preview_creation_plan", "preview_script_draft"}:
            if not event.get("isError"):
                details = (event.get("result") or {}).get("details") or {}
                if details.get("kind") == kind:
                    preview = details.get("preview")
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
    if not settled or not answer:
        cause = _classify_output(output)
        if cause == FAILURE_UNKNOWN:
            # 保留「完整回复」这个说法：用户能认出是没答完，而不是笼统报错。
            raise _fail(
                cause,
                explanation="对话没有得到完整回复，日志里没有留下能说明原因的信息，拿不到更具体的原因。",
            )
        raise _fail(cause)
    return answer, preview


def run_task_turn(
    settings: Settings,
    *,
    workspace_id: str,
    task_id: str,
    message: str,
    stage: str,
    selected_refs: list[str],
    playhead_ms: int | None,
    authorization: str | None = None,
    cookie: str | None = None,
    local_member_token: str | None = None,
    caller_user_id: str | None = None,
) -> tuple[str, dict | None]:
    script_turn = stage == "script"
    return _run_turn(
        settings, workspace_id=workspace_id, task_id=task_id, message=message,
        stage=stage, selected_refs=selected_refs, playhead_ms=playhead_ms,
        authorization=authorization, cookie=cookie,
        local_member_token=local_member_token, caller_user_id=caller_user_id,
        prompt=SCRIPT_PROMPT if script_turn else SYSTEM_PROMPT,
        tool_names=("read_current_task,find_knowledge,preview_script_draft"
                    if script_turn else "read_current_task,find_knowledge,find_materials,preview_edit"),
        preview_kind="script_draft" if script_turn else "edit_preview",
        session_key=f"task:{task_id}:{'script' if script_turn else 'edit'}",
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
) -> tuple[str, dict | None]:
    return _run_turn(
        settings, workspace_id=workspace_id, task_id="", message=message,
        stage="", selected_refs=[], playhead_ms=None,
        authorization=authorization, cookie=cookie,
        local_member_token=local_member_token, caller_user_id=caller_user_id,
        prompt=PLAN_PROMPT, tool_names="read_workspace_context,read_video_templates,find_knowledge,find_materials,preview_creation_plan",
        preview_kind="plan_preview", session_key=f"new-plan:{conversation_id}",
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
        result = subprocess.run(
            cmd, input=message, text=True, capture_output=True, timeout=180,
            env=env, cwd=runtime_root, check=False,
        )
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
