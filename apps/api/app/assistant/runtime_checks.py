from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from app.assistant.tool_sources import tool_sources_readable
from app.assistant.failures import _FAILURES, FAILURE_RUNTIME_MISSING

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


def runtime_checks(extension: Path, find_pi) -> list[dict]:
    """Local conversation dependencies only; no provider request or secret lookup."""
    pi = find_pi()
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
    tools_ready = extension.is_file() and os.access(extension, os.R_OK)
    # Follow the deployed source graph; checking the old siblings misses nested
    # runtime modules. File readability remains distinct from tool execution.
    if extension.name == "diary_tools.ts":
        tools_ready = tools_ready and tool_sources_readable(extension)
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
         "repair_action": "无需处理；工具调用由实际对话验证。" if tools_ready else "重新安装包含完整业务工具目录的桌面包；开发环境核对 AI_VIDEO_DIARY_PI_EXTENSION 及其相对导入文件和子目录。",
         "fix_command": None},
    ]
