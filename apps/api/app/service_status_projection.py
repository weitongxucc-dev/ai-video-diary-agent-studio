from __future__ import annotations

from collections.abc import Mapping
from typing import Any

# 四档只解释字段和已发生的调用观测，不保存观测，也不探测服务。
LLM_SERVICE_MISSING = "missing"        # 未填写
LLM_SERVICE_UNVERIFIED = "unverified"  # 已填写，未验证
LLM_SERVICE_AVAILABLE = "available"    # 可用（真的连通过）
LLM_SERVICE_FAILED = "failed"          # 失败（真的试过且没成功）

_LLM_SERVICE_LABELS = {
    LLM_SERVICE_MISSING: "未填写",
    LLM_SERVICE_UNVERIFIED: "已填写，未验证",
    LLM_SERVICE_AVAILABLE: "可用",
    LLM_SERVICE_FAILED: "失败",
}

_LLM_SERVICE_FAILURE_REASONS = {
    "key_rejected": "服务拒绝了这把密钥。这一步是真的连到了服务，是密钥没通过。",
    "address_unreachable": "连不上设置里的服务地址。可能是地址填错，或当前网络到不了这个服务。",
    "timeout": "等模型回复超时，这一轮没有拿到回复。",
}

_LLM_SERVICE_DEFAULT_FAILURE_REASON = (
    "上一次真实尝试没有成功，但日志里没有留下能说明原因的信息，拿不到更具体的原因。"
)


def service_state(
    *, fields_filled: bool, verification: Mapping[str, Any] | None,
) -> str:
    if not fields_filled:
        return LLM_SERVICE_MISSING
    if verification is None:
        return LLM_SERVICE_UNVERIFIED
    return LLM_SERVICE_AVAILABLE if verification["ok"] else LLM_SERVICE_FAILED


def project_service_status(
    *, fields_filled: bool, verification: Mapping[str, Any] | None,
) -> dict[str, Any]:
    state = service_state(fields_filled=fields_filled, verification=verification)
    if state == LLM_SERVICE_MISSING:
        detail = "服务地址、模型或 API Key 没有填全，现在走不了外部文字模型。"
    elif state == LLM_SERVICE_UNVERIFIED:
        detail = (
            "地址、模型和密钥都填了，但还没有真的连过一次服务；"
            "能不能用要等下一次真实对话或写稿才知道。"
        )
    elif state == LLM_SERVICE_AVAILABLE:
        detail = "上一次真实调用成功返回了回复，当前这套地址、模型和密钥连得通。"
    else:
        detail = verification.get("detail") or _LLM_SERVICE_FAILURE_REASONS.get(
            str(verification.get("cause")), _LLM_SERVICE_DEFAULT_FAILURE_REASON
        )
    return {
        "state": state,
        "label": _LLM_SERVICE_LABELS[state],
        "detail": detail,
        "fields_filled": fields_filled,
        "verified": state == LLM_SERVICE_AVAILABLE,
        "cause": str(verification.get("cause")) if verification else "",
        "repair_action": str(verification.get("repair_action", "")) if verification else "",
        "observed_at": str(verification.get("observed_at")) if verification else "",
    }
