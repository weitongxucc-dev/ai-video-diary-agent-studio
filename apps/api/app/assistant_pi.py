from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from pathlib import Path
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, uuid5

from app.config import Settings


class AssistantUnavailable(RuntimeError):
    pass


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

SCRIPT_PROMPT = """你是 AI 视频日记当前创作任务的文案助手。每轮先调用 read_current_task，读取已确认选题、本次时长和允许使用的资料；需要补充事实时调用 find_knowledge，需要镜头时调用 find_materials。
你只能使用工具列出的当前任务来源 ID 作为文案证据，不能编造引用、事实、素材或已完成的制作结果。资料不足就问一个具体问题。
用户要起草或修改文案时，调用 preview_script_draft 保存待确认文案。修改已有草稿时沿用其段落 ID；先说明想怎么改，再根据用户本轮要求提交新的完整草稿。每轮最多保存一次。
工具只保存待确认草稿，不会确认文案、生成配音或开始制作。请用户在工作台检查并明确确认文案；不要声称已开始制作。
不要请求、读取或发送原始媒体文件。回答用日常中文，不说工具名、模型名、预检、版本号等内部词。"""


def _agent_root(settings: Settings) -> Path:
    parent = Path(settings.model_root_path).expanduser().parent
    if len(parent.parts) >= 3 and parent.parts[1] == "Volumes" and not Path(*parent.parts[:3]).is_mount():
        raise AssistantUnavailable("素材与模型盘未连接，无法保存对话上下文。")
    if not parent.is_dir():
        raise AssistantUnavailable("素材与模型盘未连接，无法保存对话上下文。")
    root = parent / "agent"
    root.mkdir(mode=0o700, exist_ok=True)
    (root / "sessions").mkdir(mode=0o700, exist_ok=True)
    return root


def _configured_model(settings: Settings) -> tuple[str, str, str]:
    endpoint = urlsplit(settings.effective_llm_base_url)
    if endpoint.scheme != "https" or not endpoint.hostname or endpoint.hostname in {"localhost", "127.0.0.1", "::1"}:
        raise AssistantUnavailable("对话须选择外部文字模型；请在模型设置中选择 MiniMax 或 DeepSeek。")
    key = settings.effective_llm_api_key
    if not key:
        raise AssistantUnavailable("外部文字模型尚未配置 API Key。")
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


def _last_answer_and_preview(output: str, kind: str = "edit_preview") -> tuple[str, dict | None]:
    answer = ""
    preview = None
    settled = False
    for line in output.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise AssistantUnavailable("Pi 返回了无法读取的对话事件。") from exc
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
        raise AssistantUnavailable("对话没有得到完整回复，请检查所选模型连接。")
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
) -> tuple[str, dict | None]:
    script_turn = stage == "script"
    return _run_turn(
        settings, workspace_id=workspace_id, task_id=task_id, message=message,
        stage=stage, selected_refs=selected_refs, playhead_ms=playhead_ms,
        authorization=authorization, cookie=cookie,
        prompt=SCRIPT_PROMPT if script_turn else SYSTEM_PROMPT,
        tool_names=("read_current_task,find_knowledge,find_materials,preview_script_draft"
                    if script_turn else "read_current_task,find_knowledge,find_materials,preview_edit"),
        preview_kind="script_draft" if script_turn else "edit_preview",
    )


def run_plan_turn(
    settings: Settings,
    *,
    workspace_id: str,
    conversation_id: str,
    message: str,
    authorization: str | None = None,
    cookie: str | None = None,
) -> tuple[str, dict | None]:
    return _run_turn(
        settings, workspace_id=workspace_id, task_id="", message=message,
        stage="", selected_refs=[], playhead_ms=None,
        authorization=authorization, cookie=cookie,
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
    prompt: str,
    tool_names: str,
    preview_kind: str,
    session_key: str | None = None,
) -> tuple[str, dict | None]:
    pi_executable = _pi_executable()
    if not pi_executable:
        raise AssistantUnavailable("Pi 未安装或桌面版尚未包含 Pi，当前无法使用对话助手。")
    extension = Path(os.getenv("AI_VIDEO_DIARY_PI_EXTENSION") or Path(__file__).parents[1] / "agent_tools" / "diary_tools.ts")
    if not extension.is_file():
        raise AssistantUnavailable("对话业务工具缺失，请修复桌面应用。")
    base_url, model, key = _configured_model(settings)
    root = _agent_root(settings)
    session_id = uuid5(NAMESPACE_URL, f"ai-video-diary:{workspace_id}:{session_key or task_id}")
    runtime_root = root / "runtimes" / str(session_id)
    runtime_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    (runtime_root / "models.json").write_text(json.dumps({
        "providers": {"ai-video-diary": {
            "baseUrl": base_url,
            "api": "openai-completions",
            "apiKey": "$AI_VIDEO_DIARY_LLM_KEY",
            "models": [{"id": model}],
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
        raise AssistantUnavailable("当前任务还有一条对话正在处理，请等待完成。")
    try:
        result = subprocess.run(
            cmd, input=message, text=True, capture_output=True, timeout=180,
            env=env, cwd=runtime_root, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise AssistantUnavailable("对话超时，任务和时间线没有因此改变。") from exc
    except OSError as exc:
        raise AssistantUnavailable("无法启动 Pi，请检查安装路径和运行权限。") from exc
    finally:
        session_lock.release()
    if result.returncode:
        raise AssistantUnavailable("Pi 对话未完成，请检查模型服务和 Pi 运行状态。")
    return _last_answer_and_preview(result.stdout, preview_kind)
