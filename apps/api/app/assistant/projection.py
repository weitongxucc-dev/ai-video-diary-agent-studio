from __future__ import annotations

import json
import re
from app.assistant.catalogue import _PI_TOOL_LABELS


def _safe_business_failure(value: object, *, secrets=()) -> dict:
    """Finite business diagnostics shared by streaming and durable event adapters.

    Legacy text-only failures stay text-only; absent stage/effect is not invented.
    """
    if not isinstance(value, dict):
        return {}
    safe = {}
    for key in ("reason", "repair_action"):
        text = value.get(key)
        if not isinstance(text, str):
            continue
        for secret in secrets:
            if secret:
                text = text.replace(secret, "[已隐藏]")
        text = re.sub(r"\bBearer\s+\S+|\bsk-[A-Za-z0-9_-]+|\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", "[已隐藏]", text, flags=re.I)
        text = re.sub(r"\b(?:authorization|cookie|api[_-]?key|token|secret|password)\s*[:=]\s*[^\s,;]+", "[已隐藏]", text, flags=re.I)
        text = re.sub(r"https?://[^\s）)；;]+|(?:[A-Za-z]:\\|/(?!api/)[A-Za-z0-9_.~-]+/)[^\s）)；;]+", "[已隐藏]", text)
        safe[key] = re.sub(r"[\x00-\x1f\x7f]", " ", text)[:500]
    for key in ("error", "code", "stage", "object_type", "query_tool", "reason_code"):
        if isinstance(value.get(key), str) and re.fullmatch(r"[a-z][a-z0-9_]{0,79}", value[key]):
            safe[key] = value[key]
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
            or type(value["section_index"]) is int and 1 <= value["section_index"] <= 20):
        safe["section_index"] = value["section_index"]
    return {key: text for key, text in safe.items()
            if not isinstance(text, str) or not any(secret and secret in text for secret in secrets)}


def _safe_tool_result_summary(tool_name: str, result: object, failed: bool) -> str:
    if failed:
        if tool_name in {"start_creation_execution", "resume_creation_execution"}:
            missing = sorted(set(re.findall(r"\bDEVICE_CPU_RENDER_[A-Z_]+\b", json.dumps(result, ensure_ascii=False))))
            if missing:
                return "本机渲染尚未完成资源标定，未开始制作；诊断项：" + "、".join(missing)
        details = result.get("details") if isinstance(result, dict) else None
        failure = _safe_business_failure(details.get("failure")) if isinstance(details, dict) else {}
        if failure.get("reason"):
            return failure["reason"] + (" 下一步：" + failure["repair_action"] if failure.get("repair_action") else "")
        return "这一步没有完成；请以页面显示的任务状态和错误提示为准。"
    payload = result if isinstance(result, dict) else {}
    details = payload.get("details") if isinstance(payload.get("details"), dict) else {}
    if details.get("kind") == "task_media_job":
        media_job = details.get("media_job") or {}
        if media_job.get("result_kind") == "observation":
            return "实际输出检查已返回；排队或运行不能视为已检查，结论以真实结果为准。"
        return "已安排，预览尚未完成；完成后请在工作台查看实际预览，再确认是否应用。"
    if tool_name == "read_task_observations":
        return "已读取当前结果检查与历史检查；可用能力、当前性和缺少的证据分别保留。"
    if tool_name == "read_material_library_status":
        return f"已逐页读取可见素材库，共 {details.get('total_count', 0)} 条素材；转写和OCR需另看分项状态。"
    if tool_name == "search_material_library":
        matches = details.get("matches") if isinstance(details.get("matches"), list) else []
        return f"素材检索返回 {len(matches)} 条文件候选，服务端可见匹配总数 {details.get('total_count', 0)}（含不使用条目，非可用候选数）；未自动选择或修改。"
    if tool_name in {"read_selected_material", "update_selected_material_tags"}:
        scope = "整条素材" if details.get("scope") == "asset" else "当前片段"
        if tool_name == "read_selected_material":
            tags = details.get("user_tags") if isinstance(details.get("user_tags"), list) else []
            if details.get("content_withheld"):
                return f"{scope}已设为不使用，未外发文字分析；仅回读原有标签管理信息。"
            return f"已读取{scope}的文字分析状态和我的标签，共 {len(tags)} 项标签。"
        return (f"{scope}的我的标签已提交并回读。" if details.get("changed")
                else f"已回读{scope}的我的标签，内容没有变化。")
    if tool_name in {"start_creation_execution", "resume_creation_execution", "cancel_creation_execution", "read_creation_execution"}:
        action = details.get("action")
        status = details.get("status") if isinstance(details.get("status"), dict) else {}
        job = status.get("job") if isinstance(status.get("job"), dict) else {}
        job_status = job.get("status")
        state = {"queued": "已排队", "running": "正在运行", "succeeded": "已完成",
                 "failed": "失败", "interrupted": "中断", "cancelled": "已取消"}.get(job_status, "状态已返回")
        if action == "cancel":
            return f"取消动作已返回，当前作业{state}。"
        return f"{'原制作作业已回读' if tool_name == 'read_creation_execution' else '制作请求已返回'}，当前作业{state}。"
    if tool_name in {"preview_creation_plan", "prepare_creation_window"}:
        preview = details.get("preview") if isinstance(details.get("preview"), dict) else {}
        if preview.get("kind") == "creation_request":
            saved = preview.get("request") or {}
            return f"总目标 {saved.get('target_count', 0)} 条已保存，已创建 {saved.get('confirmed_count', 0)} 条任务；尚未生成成片。"
        blockers = preview.get("blockers") if isinstance(preview.get("blockers"), list) else []
        return f"新建方案预检已返回，{len(blockers)} 项需处理；尚未创建任务。"
    if tool_name == "preview_script_draft":
        return "实际文案版本已按原委托采用，尚未生成成片。" if (details.get("preview") or {}).get("adopted") else "文案草稿已保存，尚未采用。"
    if tool_name == "apply_typed_edit":
        return "修改保存请求已返回；以原请求回读的时间线版本为准，可整项撤销。"
    if tool_name == "preview_typed_edit":
        return "修改预览已生成；草稿尚未应用。"
    if tool_name in {"undo_last_edit", "redo_last_edit", "discard_edit_proposal"}:
        return "编辑控制已执行并返回持久记录；实际成片以当前有效产物为准。"
    if tool_name == "generate_creation_task_topics":
        topics = details.get("topics") if isinstance(details.get("topics"), dict) else {}
        candidates = topics.get("candidates") if isinstance(topics.get("candidates"), list) else []
        return f"已生成 {len(candidates)} 个选题候选，尚未选择或确认。"
    count_fields = {
        "find_materials": ("matches", "素材候选"),
        "find_material_candidates": ("candidates", "片段候选"),
        "find_knowledge": ("matches", "资料摘录"),
        "read_video_templates": ("templates", "模板"),
        "read_workspace_context": ("tasks", "任务摘要"),
    }
    if tool_name in count_fields:
        field, label = count_fields[tool_name]
        values = details.get(field) if isinstance(details.get(field), list) else []
        return f"已读取 {len(values)} 项{label}。"
    if tool_name == "read_current_task":
        return "当前任务和所选对象状态已读取。"
    if tool_name == "read_clip_candidates":
        return "当前镜头的可用替换候选已读取；尚未修改。"
    if tool_name == "suggest_selected_material_tags":
        return "素材标签建议已读取；尚未保存。"
    if tool_name == "read_preference_memory":
        return "与本次创作有关的偏好已读取。"
    if tool_name == "update_preference_memory":
        return str(details.get("summary") or "长期偏好已保存并回读。")
    return "业务步骤已完成。"


def _business_event_lines(output: str, seen: set[str] | None = None):
    """Keep real nested receipts even when Pi emits only the codemode parent result."""
    seen = seen if seen is not None else set()
    for line in output.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            yield line
            continue
        if event.get("type") == "tool_execution_end":
            call_id = event.get("toolCallId")
            if isinstance(call_id, str):
                seen.add(call_id)
            if event.get("toolName") == "codemode":
                receipts = (event.get("result") or {}).get("details", {}).get("business_receipts", [])
                for receipt in receipts[:8]:
                    name, nested_id = receipt.get("tool_name"), receipt.get("tool_call_id")
                    result = receipt.get("result")
                    if name not in _PI_TOOL_LABELS or name == "codemode" or not isinstance(nested_id, str) or nested_id in seen or not isinstance(result, dict):
                        continue
                    seen.add(nested_id)
                    yield json.dumps({"type": "tool_execution_end", "toolName": name, "toolCallId": nested_id,
                                      "isError": bool(receipt.get("is_error")), "result": {"details": result}}, ensure_ascii=False)
        yield line


class _PiStreamProjector:
    """Project SDK events into a small UI-safe event vocabulary."""

    def __init__(self) -> None:
        self._calls: dict[str, tuple[int, str, str]] = {}
        self._next_id = 0

    def project(self, line: str) -> dict[str, object] | None:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            return None  # The SDK result reader reports malformed protocol messages.
        event_type = event.get("type")
        if event_type == "message_update":
            update = event.get("assistantMessageEvent")
            if isinstance(update, dict) and update.get("type") == "text_delta":
                delta = update.get("delta")
                return {"type": "text_delta", "delta": delta} if isinstance(delta, str) and delta else None
            return None
        if event_type == "tool_execution_start":
            name = event.get("toolName")
            raw_id = event.get("toolCallId")
            if name not in _PI_TOOL_LABELS:
                return None
            self._next_id += 1
            call_key = raw_id if isinstance(raw_id, str) and raw_id else f"call-{self._next_id}"
            label = _PI_TOOL_LABELS[name]
            self._calls[call_key] = (self._next_id, name, label)
            return {"type": "tool_start", "tool_id": self._next_id, "label": label}
        if event_type == "tool_execution_end":
            raw_id = event.get("toolCallId")
            name = event.get("toolName")
            call_key = raw_id if isinstance(raw_id, str) else ""
            stored = self._calls.pop(call_key, None)
            if stored:
                tool_id, stored_name, label = stored
                name = stored_name
            else:
                if name not in _PI_TOOL_LABELS:
                    return None
                self._next_id += 1
                tool_id, label = self._next_id, _PI_TOOL_LABELS[name]
            failed = bool(event.get("isError"))
            details = (event.get("result") or {}).get("details") or {}
            failure = details.get("failure") if isinstance(details.get("failure"), dict) else None
            if failure is None and details.get("kind") == "edit_request_status":
                reason = details.get("failure_reason")
                if isinstance(reason, dict) and isinstance(reason.get("text"), str):
                    failure = {"reason": reason["text"]}
            if failed and failure is None and (event.get("result") or {}).get("details") is None:
                prefix = f'Validation failed for tool "{name}":\n'
                for item in (event.get("result") or {}).get("content", []):
                    raw = item.get("text") if item.get("type") == "text" else None
                    if not isinstance(raw, str) or not raw.startswith(prefix):
                        continue
                    validation = raw[len(prefix):].split("\n\nReceived arguments:", 1)[0]
                    errors = [match.groups() for line in validation.splitlines()
                        if (match := re.fullmatch(r"\s*-\s+([A-Za-z0-9_.\[\]-]+):\s*(.+)", line))]
                    concrete = [(field, reason) for field, reason in errors
                        if reason not in {"must be null", "must match a schema in anyOf"}]
                    reason = "；".join(f"{field} 必须使用数字" if problem == "must be number" else f"{field}: {problem}"
                        for field, problem in concrete)
                    failure = {"reason": "参数校验未通过，工具未执行。" + (reason + "。" if reason else "")}
                    break
            return {
                "type": "tool_end", "tool_id": tool_id, "label": label,
                "ok": not failed,
                "summary": _safe_tool_result_summary(name, event.get("result"), failed),
                **({"failure": _safe_business_failure(failure)} if failed and failure else {}),
            }
        return None
