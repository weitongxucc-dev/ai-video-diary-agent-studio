from __future__ import annotations

import json
import math
import re
from app.assistant.catalogue import _PI_TOOL_LABELS, SCRIPT_WINDOW_TOOL_OPERATIONS


from app.assistant.business_failure import _safe_business_failure
from app.assistant.script_window_receipts import project_script_window_request


def _sdk_parameter_failure(tool_name: str, result: dict) -> dict | None:
    prefix = f'Validation failed for tool "{tool_name}":\n'
    for item in result.get("content", []):
        raw = item.get("text") if item.get("type") == "text" else None
        if not isinstance(raw, str) or not raw.startswith(prefix):
            continue
        validation, separator, _ = raw[len(prefix):].partition("\n\nReceived arguments:")
        if not separator:
            continue
        errors = [match.groups() for line in validation.splitlines()
            if (match := re.fullmatch(r"\s*-\s+([A-Za-z0-9_.\[\]-]{1,160}):\s*(.+)", line))]
        if not errors:
            continue
        reasons = []
        for field, problem in errors[:8]:
            expected_type = re.fullmatch(r"expected (number|string|integer|boolean|object|array), got [a-z]+", problem)
            if problem == "must be number" or expected_type and expected_type[1] == "number":
                reasons.append(f"{field} 必须使用数字")
            elif problem == "missing":
                reasons.append(f"{field} 为必填参数")
            elif expected_type:
                reasons.append(f"{field}: expected {expected_type[1]}")
            elif problem in {"must be string", "must be integer", "must be boolean", "must be object", "must be array"}:
                reasons.append(f"{field}: {problem}")
            elif re.fullmatch(r"must have required properties [A-Za-z_][A-Za-z0-9_]{0,63}(?:, [A-Za-z_][A-Za-z0-9_]{0,63}){0,7}", problem):
                reasons.append(f"{field}: {problem}")
        reason = "；".join(reasons)
        return {"error": "tool_arguments_invalid", "tool_name": tool_name,
            "reason": "参数校验未通过，工具未执行。" + (reason + "。" if reason else ""),
            "repair_action": "按工具参数契约补齐或修正上述字段后再提交；本次工具未执行。",
            "stage": "parameter_validation", "submission_effect": "not_submitted"}
    return None


def media_processing_state_message(media_job: dict) -> str:
    if media_job.get('status') == 'succeeded':
        return '媒体处理结果已回读；产物及其采用关系以该作业记录为准。'
    if media_job.get('status') in {'failed', 'cancelled', 'interrupted'}:
        return '媒体处理未成功完成，请查看原作业的实际原因与恢复方式。'
    return '已安排，预览尚未完成；完成后请在工作台查看实际预览，再确认是否应用。'


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
        return media_processing_state_message(media_job)
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


def _safe_compaction_event(value: dict) -> dict | None:
    if value.get('type') != 'context_compaction' or value.get('status') not in {'started','completed','cancelled','failed'}:
        return None
    safe = {'type':'context_compaction','status':value['status'],
        'trigger':value.get('trigger') if value.get('trigger') in {'manual','threshold','overflow'} else None}
    for field,keys in (('sdk_estimate',{'tokens_before','tokens_after','context_window'}),
            ('provider_usage',{'input','output','cacheRead','cacheWrite','totalTokens'})):
        if isinstance(value.get(field),dict):
            safe[field] = {key:number for key,number in value[field].items() if key in keys and type(number) is int and number >= 0}
    if value.get('failure_origin') in {'sdk','sdk_abort','host_deadline'}:
        safe['failure_origin'] = value['failure_origin']
        # Upstream error bodies can echo prompts and credentials. Persist only
        # controlled classes and descriptions, never the supplied error text.
        code,description = {
            'sdk':('sdk_error_unknown','SDK报告上下文压缩失败，具体上游原因尚未核实。'),
            'sdk_abort':('sdk_abort','SDK上下文压缩已取消，原会话保留。'),
            'host_deadline':('host_deadline','宿主压缩阶段等待截止，不能据此判定供应商已接收或失败。'),
        }[value['failure_origin']]
        safe['reason_code'],safe['reason'] = code,description
    if type(value.get('limit_seconds')) in {int,float} and math.isfinite(value['limit_seconds']) and value['limit_seconds'] > 0:
        safe['limit_seconds'] = value['limit_seconds']
    return safe


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
        if event_type in {'compaction_start','compaction_end'}:
            started = event_type == 'compaction_start'
            result = event.get('result') or {}
            status = ('started' if started else 'cancelled' if event.get('aborted') is True
                else 'failed' if event.get('errorMessage') or not result else 'completed')
            projected = {'type':'context_compaction','status':status,
                'trigger':event.get('reason') if event.get('reason') in {'manual','threshold','overflow'} else None}
            estimates = ({'tokens_before':(event.get('contextUsage') or {}).get('tokens'),
                'context_window':(event.get('contextUsage') or {}).get('contextWindow')} if started else
                {'tokens_before':result.get('tokensBefore'),'tokens_after':result.get('estimatedTokensAfter')})
            projected['sdk_estimate'] = {key:value for key,value in estimates.items() if type(value) is int and value >= 0}
            usage = result.get('usage')
            if isinstance(usage,dict):
                projected['provider_usage'] = {key:value for key,value in usage.items()
                    if key in {'input','output','cacheRead','cacheWrite','totalTokens'} and type(value) is int and value >= 0}
            if status == 'failed':
                projected['failure_origin'] = 'sdk'
            elif status == 'cancelled':
                projected['failure_origin'] = 'sdk_abort'
            return _safe_compaction_event(projected)
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
            summary_result = event.get("result")
            failure = details.get("failure") if isinstance(details.get("failure"), dict) else None
            if failure is None and details.get("kind") == "edit_request_status":
                reason = details.get("failure_reason")
                if isinstance(reason, dict) and isinstance(reason.get("text"), str):
                    failure = {"reason": reason["text"]}
            if failed and failure is None and (event.get("result") or {}).get("details") in (None, {}):
                failure = _sdk_parameter_failure(name, event.get("result") or {})
                if failure:
                    summary_result = {"details": {"failure": failure}}
            window = (project_script_window_request(details, expected_operation=SCRIPT_WINDOW_TOOL_OPERATIONS[name], is_error=failed)
                      if name in SCRIPT_WINDOW_TOOL_OPERATIONS else None)
            invocation_input = details.get('invocation_input_id')
            if window is not None and (not isinstance(invocation_input,str)
                    or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,127}',invocation_input)
                    or not {'input_id','input_turn_id'} <= details.keys()
                    or any(details[key] != window.get(key) for key in ('input_id','input_turn_id'))
                    or window.get('input_id') is not None and window['input_id'] != invocation_input):
                window = None
            return {
                "type": "tool_end", "tool_id": tool_id, "label": label,
                "ok": not failed,
                "summary": _safe_tool_result_summary(name, summary_result, failed),
                **({"failure": _safe_business_failure(failure)} if failed and failure else {}),
                **({"script_window_request": window,"invocation_input_id":invocation_input} if window is not None else {}),
            }
        return None
