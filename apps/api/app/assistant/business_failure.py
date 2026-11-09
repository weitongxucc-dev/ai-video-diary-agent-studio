"""Finite public failure diagnostics shared by business and host projections."""
import re
from app.assistant.catalogue import _PI_TOOL_LABELS


def _safe_failure_text(value: object, secrets=()) -> str | None:
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


def _safe_script_candidate_location(value: object) -> dict:
    if not isinstance(value, dict):
        return {}
    field = value.get("candidate_field")
    section = value.get("candidate_section_index")
    fragment = value.get("candidate_fragment_index")
    digest = value.get("candidate_field_digest")
    if (value.get("error") in ("script_fact_unsupported", "script_goal_conflict")
            and isinstance(field, str) and field in {"title", "content", "visual_prompt"}
            and type(value.get("section_index")) is int and 1 <= value["section_index"] <= 9007199254740991
            and type(section) is int and 0 <= section <= 9007199254740991
            and (field == "title" if section == 0 else section == value.get("section_index"))
            and type(fragment) is int and 0 <= fragment <= {"title": 0, "content": 19, "visual_prompt": 4}[field]
            and isinstance(digest, str) and re.fullmatch(r"[a-f0-9]{64}", digest)):
        return {"candidate_section_index": section, "candidate_field": field,
            "candidate_fragment_index": fragment, "candidate_field_digest": digest}
    return {}


def _safe_business_failure(value: object, *, secrets=()) -> dict:
    """Finite business diagnostics shared by streaming and durable event adapters.

    Legacy text-only failures stay text-only; absent stage/effect is not invented.
    """
    if not isinstance(value, dict):
        return {}
    safe = {}
    for key in ("reason", "impact", "repair_action"):
        text = _safe_failure_text(value.get(key), secrets)
        if text is not None:
            safe[key] = text
    for key in ("error", "code", "stage", "object_type", "query_tool", "reason_code", "failure_category"):
        if isinstance(value.get(key), str) and re.fullmatch(r"[a-z][a-z0-9_]{0,79}", value[key]):
            safe[key] = value[key]
    if isinstance(value.get("tool_name"), str) and value["tool_name"] in _PI_TOOL_LABELS:
        safe["tool_name"] = value["tool_name"]
    for key in ("request_id", "window_id", "current_window_id", "plan_id", "task_id", "object_id", "job_id"):
        if (isinstance(value.get(key), str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", value[key])
                and not any(secret and secret in value[key] for secret in secrets)):
            safe[key] = value[key]
    for key in ("expected_revision", "current_revision"):
        if type(value.get(key)) is int and 0 <= value[key] <= 9007199254740991:
            safe[key] = value[key]
    if isinstance(value.get("request_key"), str) and re.fullmatch(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", value["request_key"], flags=re.I):
        safe["request_key"] = value["request_key"]
    for key in ("expected_input_fingerprint", "current_input_fingerprint"):
        if isinstance(value.get(key), str) and re.fullmatch(r"[a-f0-9]{64}", value[key]):
            safe[key] = value[key]
    if value.get("submission_effect") in ("not_submitted", "rejected", "unknown"):
        safe["submission_effect"] = value["submission_effect"]
    if isinstance(value.get("query_url"), str) and re.fullmatch(
            r"/api/(?:creation-requests|creation-tasks)/[A-Za-z0-9_/-]{1,350}(?:\?phase=(?:apply|preview))?", value["query_url"]):
        safe["query_url"] = value["query_url"]
    if type(value.get("retryable")) is bool:
        safe["retryable"] = value["retryable"]
    if "section_index" in value and (value["section_index"] is None
            or type(value["section_index"]) is int and 1 <= value["section_index"] <= 9007199254740991):
        safe["section_index"] = value["section_index"]
    safe.update(_safe_script_candidate_location(value))
    issues = value.get("review_issues")
    if (value.get("error") in ("script_fact_unsupported", "script_goal_conflict")
            and isinstance(issues, list) and issues):
        reviewed = []
        for issue in issues:
            location = _safe_script_candidate_location(issue)
            if not location:
                continue
            explanation = issue.get("review_explanation")
            if (issue.get("reason_code") != ("unsupported_fact" if issue["error"] == "script_fact_unsupported" else "goal_conflict")
                    or not isinstance(explanation, str) or not explanation.strip() or len(explanation) > 300):
                continue
            reviewed.append({"error": issue["error"], "reason_code": issue["reason_code"], "section_index": issue["section_index"],
                **location, "review_explanation": _safe_failure_text(explanation, secrets)[:300]})
        if reviewed:
            safe["review_issues"] = reviewed
    return {key: text for key, text in safe.items()
            if not isinstance(text, str) or not any(secret and secret in text for secret in secrets)}
