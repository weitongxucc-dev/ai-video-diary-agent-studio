from __future__ import annotations

import json
import re

FAILURE_KEY_REJECTED = "key_rejected"              # 密钥被拒
FAILURE_ADDRESS_UNREACHABLE = "address_unreachable"  # 服务地址不通
FAILURE_TIMEOUT = "timeout"                        # 超时
FAILURE_RUNTIME_MISSING = "runtime_missing"        # 运行环境缺失
FAILURE_TOOL_FAILED = "tool_failed"                # 业务工具失败
FAILURE_OVERLOADED = "overloaded"
FAILURE_QUOTA_EXHAUSTED = "quota_exhausted"
FAILURE_RATE_LIMITED = "rate_limited"
FAILURE_UNKNOWN = "unknown"                        # 拿不到更具体的原因

# service_contacted=True 的分类有实际模型服务访问证据，结论可以写进
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
        "next_step": "安装 Node.js 22.19 或更高版本，再执行 npm install -g @earendil-works/pi-coding-agent@1.5.0；确认 node --version 和 pi --version 可运行后重新打开应用。桌面安装包尚不自带 Node/Pi。",
    },
    FAILURE_TOOL_FAILED: {
        "service_contacted": False,
        "explanation": "本轮业务操作未完成，当前任务修改保留。",
        "next_step": "查看本轮工具记录中的具体阻断，再处理后重试。",
    },
    FAILURE_OVERLOADED: {
        "service_contacted": True,
        "explanation": "模型服务当前过载（529），本轮未取得完整回复；请以已返回的业务回执判断已完成的修改。",
        "next_step": "稍后重试；若已有编辑或制作回执，先查询其结果，不要重复提交。",
    },
    FAILURE_QUOTA_EXHAUSTED: {
        "service_contacted": True,
        "explanation": "模型服务返回套餐用量限制，本轮未取得完整回复；可能是当前计费窗口或额度上限，已保存的任务与业务回执保留。",
        "next_step": "在当前服务商核对窗口限额、套餐额度与余额，限制解除后沿原任务继续；不会自动更换服务或重复提交制作。",
    },
    FAILURE_RATE_LIMITED: {
        "service_contacted": True,
        "explanation": "模型服务返回请求限流（429），本轮未取得完整回复；已保存的任务与业务回执保留。",
        "next_step": "待服务商限流解除后沿原任务继续；已有编辑或制作先查询原回执，不重复提交。",
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


def _provider_failure(output: str) -> str | None:
    for line in output.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        failure = None
        if (event.get("type") == "creation_session_result" and event.get("owner") == "pi-coding-agent-sdk"
                and event.get("protocol") == 1):
            failure = event.get("failure")
        elif event.get("type") == "message_end":
            message = event.get("message") or {}
            if message.get("role") == "assistant" and message.get("stopReason") == "error":
                failure = message.get("errorMessage")
        if not isinstance(failure, str):
            continue
        lowered = failure.lower()
        if any(marker in lowered for marker in ("token plan 用量上限", "insufficient_quota", "credit balance is too low")):
            return FAILURE_QUOTA_EXHAUSTED
        if (re.match(r"^(?:HTTP\s+)?429\b", failure)
                or '"type":"rate_limit_error"' in failure.replace(" ", "")):
            return FAILURE_RATE_LIMITED
        if (re.match(r"^(?:HTTP\s+)?529\b", failure)
                or '"type":"overloaded_error"' in failure.replace(" ", "")):
            return FAILURE_OVERLOADED
    return None


def _classify_output(stdout: str, stderr: str = "") -> str:
    """Read one Pi run's logs and name the failure class, or admit we can't."""
    text = f"{stdout}\n{stderr}".lower()
    provider_failure = _provider_failure(stdout)
    if provider_failure is not None:
        return provider_failure
    if _tool_error_seen(stdout):
        return FAILURE_TOOL_FAILED
    if any(pattern in text for pattern in _KEY_REJECTED_PATTERNS) or re.search(r"\b401\b", text):
        return FAILURE_KEY_REJECTED
    if any(pattern in text for pattern in _TIMEOUT_PATTERNS):
        return FAILURE_TIMEOUT
    if any(pattern in text for pattern in _ADDRESS_PATTERNS):
        return FAILURE_ADDRESS_UNREACHABLE
    if _tool_error_seen(stdout) or any(pattern in stdout for pattern in _TOOL_ERROR_PATTERNS):
        return FAILURE_TOOL_FAILED
    return FAILURE_UNKNOWN
