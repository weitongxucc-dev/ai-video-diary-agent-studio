from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit
from app.config import Settings
from app.workspace_settings import workspace_pi_config_dir
from app.assistant.failures import _fail, FAILURE_RUNTIME_MISSING

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
