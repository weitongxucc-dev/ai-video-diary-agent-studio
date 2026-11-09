"""Workspace text-service configuration diagnostics."""
import hashlib
from urllib.parse import urlparse


class WorkspaceLlmRouteSnapshot:
    def __init__(self, *, settings_for, clock):
        self.settings_for = settings_for
        self.clock = clock

    def snapshot(self, workspace_id: str) -> dict[str, object]:
        """按当前工作区的文字服务配置判断可用性。

        读全局设置会把配置正确的工作区判成「未配置」，所以这里只用该工作区的配置。
        """
        settings_for_workspace = self.settings_for(workspace_id)
        provider_kind = settings_for_workspace.effective_llm_provider
        base_url = settings_for_workspace.effective_llm_base_url
        parsed = urlparse(base_url)
        host = parsed.hostname or "unconfigured"
        try:
            port = f":{parsed.port}" if parsed.port else ""
        except ValueError:
            port = ""
        endpoint_identity = (
            f"{parsed.scheme or 'unknown'}://{host}{port}"
            if host != "unconfigured"
            else "unconfigured"
        )
        execution_location = (
            "local"
            if provider_kind == "ollama" or host in {"localhost", "127.0.0.1", "::1"}
            else "cloud"
        )
        availability = "unverified" if settings_for_workspace.llm_configured else "unconfigured"
        connection_version = hashlib.sha256(
            f"{provider_kind}|{endpoint_identity}|{settings_for_workspace.effective_llm_model}".encode("utf-8")
        ).hexdigest()[:16]
        return {
            "provider_kind": provider_kind,
            "execution_location": execution_location,
            "model_id": settings_for_workspace.effective_llm_model,
            "endpoint_identity": endpoint_identity,
            "connection_version": connection_version,
            "availability": availability,
            "checked_at": self.clock(),
        }
