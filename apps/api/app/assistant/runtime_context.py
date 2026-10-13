from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

def prepare_runtime(*, root: Path, pi_executable: str, base_url: str, model: str, key: str, provider: str,
    environment, workspace_id: str, session_key: str | None, task_id: str, caller_user_id: str | None,
    stage: str, selected_refs: list[str], playhead_ms: int | None, authorization: str | None, cookie: str | None,
    local_member_token: str | None, material_selection, profile_reference, knowledge_reference, task_version,
    turn_id: str | None, creation_request, message: str, tool_names: str):
    # 已解析的可信调用方身份参与会话键：不同成员不共用同一段对话历史。
    # 没有身份时沿用既有单机行为，不额外造第二套隔离。
    caller_scope = f":{caller_user_id}" if caller_user_id else ""
    session_id = uuid5(NAMESPACE_URL, f"ai-video-diary:{workspace_id}:{session_key or task_id}{caller_scope}")
    runtime_root = root / "runtimes" / str(session_id)
    runtime_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    model_definition: dict[str, object] = {"id": model}
    if provider == "minimax" and model == "MiniMax-M3":
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
        **environment,
        "PI_CODING_AGENT_DIR": str(runtime_root),
        "PI_OFFLINE": "1",
        "PATH": str(Path(pi_executable).parent) + os.pathsep + environment.get("PATH", ""),
        "AI_VIDEO_DIARY_LLM_KEY": key,
        "AI_VIDEO_DIARY_AGENT_API_BASE": f"http://127.0.0.1:{int(environment.get('API_PORT', '8000'))}",
        "AI_VIDEO_DIARY_AGENT_TASK_ID": task_id,
        "AI_VIDEO_DIARY_AGENT_STAGE": stage,
        "AI_VIDEO_DIARY_AGENT_SELECTED_REFS": json.dumps(selected_refs),
        "AI_VIDEO_DIARY_AGENT_PLAYHEAD_MS": "" if playhead_ms is None else str(playhead_ms),
        "AI_VIDEO_DIARY_AGENT_AUTHORIZATION": authorization or "",
        "AI_VIDEO_DIARY_AGENT_COOKIE": cookie or "",
        # 已验证的本地成员令牌：工具回调必须带上，否则会退化成匿名 local 诊断读取。
        "AI_VIDEO_DIARY_AGENT_LOCAL_MEMBER_TOKEN": local_member_token or "",
        "AI_VIDEO_DIARY_AGENT_MATERIAL_SELECTION": json.dumps(material_selection) if material_selection else "",
        "AI_VIDEO_DIARY_AGENT_PROFILE_REFERENCE": json.dumps(profile_reference) if profile_reference else "",
        "AI_VIDEO_DIARY_AGENT_KNOWLEDGE_REFERENCE": json.dumps(knowledge_reference) if knowledge_reference else "",
        "AI_VIDEO_DIARY_AGENT_TASK_VERSION": json.dumps(task_version) if task_version else "",
        "AI_VIDEO_DIARY_AGENT_TURN_ID": turn_id or "",
        "AI_VIDEO_DIARY_AGENT_CREATION_REQUEST_ID": str(creation_request["id"]) if creation_request else "",
        "AI_VIDEO_DIARY_AGENT_USER_INSTRUCTION": message,
        "AI_VIDEO_DIARY_AGENT_TASK_READ_READY": "",
        "AI_VIDEO_DIARY_AGENT_SCRIPT_DRAFT_UPDATED_AT": "",
        "AI_VIDEO_DIARY_AGENT_EDIT_CAPABILITIES": "",
        "AI_VIDEO_DIARY_AGENT_MEDIA_SCRIPT_CAPABILITIES": "",
        "AI_VIDEO_DIARY_AGENT_SCRIPT_TOOL_CAPABILITIES": "",
        "AI_VIDEO_DIARY_AGENT_PI_MODULE": "",
        "AI_VIDEO_DIARY_AGENT_BUSINESS_TOOLS": tool_names,

    }
    # Resolve only the selected Pi installation, never personal extensions or arbitrary modules.
    for parent in Path(pi_executable).resolve().parents:
        if parent.name == "pi-coding-agent" and parent.parent.name == "@earendil-works":
            module = parent / "dist" / "index.js"
            if module.is_file():
                env["AI_VIDEO_DIARY_AGENT_PI_MODULE"] = str(module)
            break
    if not env["AI_VIDEO_DIARY_AGENT_PI_MODULE"]:
        tool_names = ",".join(name for name in tool_names.split(",") if name != "codemode")
    env["AI_VIDEO_DIARY_AGENT_BUSINESS_TOOLS"] = tool_names
    return runtime_root, env, tool_names, session_id
