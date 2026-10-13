from __future__ import annotations

from app.config import Settings, record_llm_service_verification
from app.assistant.failures import _FAILURES

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
