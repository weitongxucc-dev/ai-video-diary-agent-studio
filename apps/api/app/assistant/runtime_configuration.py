from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit
from app.config import Settings
from app.workspace_settings import workspace_pi_config_dir
from app.assistant.failures import _fail, FAILURE_RUNTIME_MISSING
from app.assistant.provider_catalog import provider_spec

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
    provider = settings.effective_llm_provider.strip()
    model = settings.effective_llm_model.strip()
    if not provider:
        raise _fail(FAILURE_RUNTIME_MISSING, explanation="还没有选择文字模型服务，所以这一轮对话没有开始。",
                    repair_action="在设置页选择一个已保存的文字模型服务后重试。")
    if (settings.llm_model is not None and not settings.llm_model.strip()) or not model:
        raise _fail(FAILURE_RUNTIME_MISSING, explanation="当前文字模型没有填写模型名称，所以这一轮对话没有开始。",
                    repair_action="在设置页填写当前服务支持的模型名称并保存后重试。")
    endpoint = urlsplit(settings.effective_llm_base_url)
    spec = provider_spec(provider)
    local_endpoint = endpoint.hostname in {"localhost", "127.0.0.1", "::1"}
    if (not endpoint.hostname or endpoint.username or endpoint.password
            or (endpoint.scheme != "https" and spec.id not in {"ollama", "lmstudio"})
            or (local_endpoint and spec.id not in {"ollama", "lmstudio"})):
        raise _fail(FAILURE_RUNTIME_MISSING, explanation="对话须选择外部文字模型，请先在设置里选 MiniMax 或 DeepSeek。",
                    repair_action="在设置页连接 MiniMax，或主动选择 DeepSeek；不会自动切换服务。")
    key = settings.effective_llm_api_key or ""
    if spec.auth == "api_key" and not key:
        raise _fail(FAILURE_RUNTIME_MISSING, explanation="外部文字模型的 API Key 还没填，所以这一轮对话没有开始。",
                    repair_action="在设置页填写当前所选服务的 API Key 并保存后重试。")
    return settings.effective_llm_base_url, model, key
