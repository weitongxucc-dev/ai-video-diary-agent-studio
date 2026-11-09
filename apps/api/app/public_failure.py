"""Finite public failure text, independent of transport and assistant assembly."""
import re


def safe_failure_text(value: object, secrets=()) -> str | None:
    if not isinstance(value, str):
        return None
    text = value
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[已隐藏]")
    text = re.sub(r"\bBearer\s+\S+|\bsk-[A-Za-z0-9_-]+|\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", "[已隐藏]", text, flags=re.I)
    text = re.sub(r"\b(?:authorization|cookie|api[_-]?key|token|secret|password)\s*[:=]\s*[^\s,;]+", "[已隐藏]", text, flags=re.I)
    text = re.sub(r"https?://[^\s）)；;]+|(?:[A-Za-z]:\\|/(?!api/)[A-Za-z0-9_.~-]+/)[^\s）)；;]+", "[已隐藏]", text)
    return re.sub(r"[\x00-\x1f\x7f]", " ", text)[:500]


def safe_rejection(status_code, detail, *, secrets=()):
    """Project only the public fields of a known domain refusal."""
    if type(status_code) is not int or not 400 <= status_code <= 599:
        return None
    if isinstance(detail, str):
        return {"status_code": status_code, "detail": safe_failure_text(detail, secrets)}
    if not isinstance(detail, dict):
        return None
    safe = {}
    for key in ("reason", "impact", "repair_action"):
        value = safe_failure_text(detail.get(key), secrets)
        if value is not None:
            safe[key] = value
    for key in ("error", "code"):
        value = detail.get(key)
        if (isinstance(value, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,79}", value)
                and not any(secret and secret in value for secret in secrets)):
            safe[key] = value
    if type(detail.get("retryable")) is bool:
        safe["retryable"] = detail["retryable"]
    if detail.get("submission_effect") in ("not_submitted", "rejected", "unknown"):
        safe["submission_effect"] = detail["submission_effect"]
    return {"status_code": status_code, "detail": safe}
