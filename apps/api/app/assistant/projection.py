from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone
from pathlib import PurePosixPath
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
    if media_job.get('result_kind') == 'observation':
        status = media_job.get('status')
        if status == 'succeeded' and isinstance(media_job.get('result'), dict):
            return '实际输出检查已完成；请按分项证据查看结果，未提供的语义或听感不能当作已通过。'
        if status in {'stale', 'expired'}:
            return '原实际输出检查已失效，仅保留历史；请按当前条件重新检查。'
        if status in {'failed', 'cancelled', 'interrupted'}:
            return '实际输出检查未完成；请查看原检查的实际原因与恢复方式。'
        return '实际输出检查已安排；尚未取得成功检查结果，排队或运行不能视为已经检查。'
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
    if details.get("kind") == "edit_applied":
        edit = details.get("result") if isinstance(details.get("result"), dict) else {}
        operation = edit.get("operation") if isinstance(edit.get("operation"), dict) else {}
        if operation.get("status") == "applied" and operation.get("result_version_id"):
            action = {"ChangeCrop": "画面构图", "ReplaceClip": "镜头替换", "TrimClip": "镜头裁切",
                      "AdjustVisualEffects": "画面效果", "EditAudioTimeline": "音轨调整", "UpdateSubtitleCue": "字幕修改"}.get(operation.get("operation_type"), "修改")
            return f"{action}已保存到时间线，可在当前作品中查看或撤销。"
        return "修改回执已返回，仍需沿原请求核对保存结果。"
    if tool_name == "record_input_understanding":
        understanding = details.get("understanding") if isinstance(details.get("understanding"), dict) else {}
        return {
            "query": "已记录为查询，本次记录未改动作品。",
            "clarification": "已记录待澄清的要求，尚未应用修改。",
            "candidate": "已记录为候选尝试，尚未采用。",
            "effective_modification": "已记录为修改要求，请查看后续保存回执。",
            "adoption": "已记录为采用要求，请查看后续采用回执。",
            "independent_goal": "已记录为独立目标，请查看后续任务保存回执。",
        }.get(understanding.get("kind"), "已记录本次要求，请查看后续操作回执。")
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
    if tool_name == "read_task_media_jobs" and isinstance(details.get("items"), list):
        count = len(details["items"])
        return f"已读取本页 {count} 条媒体作业记录。" if count else "本页未查到媒体作业。"
    if tool_name == "read_execution_capabilities":
        return "已读取执行能力与预算来源。"
    if tool_name == "read_task_edit_capabilities":
        return "已读取当前作品可用的编辑动作。"
    if tool_name == "read_task_quality_check":
        return "已回读原成片的质检状态。"
    if tool_name == "read_clip_candidates":
        return "当前镜头的可用替换候选已读取；尚未修改。"
    if tool_name == "suggest_selected_material_tags":
        return "素材标签建议已读取；尚未保存。"
    if tool_name == "read_preference_memory":
        return "与本次创作有关的偏好已读取。"
    if tool_name == "update_preference_memory":
        return str(details.get("summary") or "长期偏好已保存并回读。")
    return "工具已返回，请查看对应操作回执。"


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
                                      "isError": bool(receipt.get("is_error")), "result": {"details": result},
                                      "timestamp": event.get("timestamp")}, ensure_ascii=False)
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


_PUBLIC_ACTIVITY_MESSAGES = {
    "confirmation_wait": "等待确认后继续。",
    "unknown": "本轮结果仍待核实，请先回查原请求。",
    "error": "本次助手调用未能正常返回。",
}


_PUBLIC_TOOL_TARGETS = {
    "read_workspace_context": "当前工作区",
    "read_current_task": "当前作品",
    "read_video_templates": "视频模板库",
    "read_editing_resource_catalog": "剪辑资源库",
    "read_material_library_status": "素材库",
    "search_material_library": "素材库",
    "find_materials": "素材库",
    "read_selected_material": "所选素材",
    "read_clip_candidates": "当前镜头",
    "read_task_edit_capabilities": "当前作品",
    "read_narration_segments": "作品段落",
    "read_script_sections": "文案段落",
    "read_voice_profiles": "声音库",
    "open_task_working_context": "任务工作区",
    "bash": "任务工作区",
}
_PUBLIC_EXECUTABLES = frozenset({
    "ffprobe", "ffmpeg", "ls", "python", "python3", "node", "cat", "rg", "wc", "find", "pwd",
})
_PUBLIC_FILE_EXTENSIONS = frozenset({
    ".md", ".txt", ".srt", ".vtt", ".json", ".csv", ".py", ".js", ".mjs", ".ts",
    ".html", ".css", ".png", ".jpg", ".webp", ".wav", ".mp4", ".mov",
})
_PUBLIC_FILENAME = re.compile(r"[\w][\w .-]{0,63}")
_OPAQUE_VALUE = re.compile(r"(?i)(?:\bsk-|\b(?:token|secret|password|api[_ -]?key)\b|[a-z0-9_-]{28,})")


def _tool_display_target(name: str, arguments: object) -> str | None:
    if name in {"read", "write", "edit"} and isinstance(arguments, dict):
        raw = arguments.get("path")
        if not isinstance(raw, str) or not raw or len(raw) > 1024 or any(char in raw for char in ("\\", ":", "\x00")):
            return None
        path = PurePosixPath(raw)
        if path.is_absolute() and path.parts[:3] not in {
                ("/", "task", "work"), ("/", "task", "inputs"), ("/", "task", "outputs")}:
            return None
        if any(part == ".." for part in path.parts) or path.name.startswith((".", "_")):
            return None
        if (_PUBLIC_FILENAME.fullmatch(path.name) and not _OPAQUE_VALUE.search(path.name)
                and path.suffix.lower() in _PUBLIC_FILE_EXTENSIONS):
            return path.name
    return _PUBLIC_TOOL_TARGETS.get(name)


def _command_executable(arguments: object) -> str | None:
    command = arguments.get("command") if isinstance(arguments, dict) else None
    if not isinstance(command, str):
        return None
    leading = command.split(None, 1)
    if leading and leading[0] in _PUBLIC_EXECUTABLES:
        return leading[0]
    return None


def public_event_at(value: object) -> str | None:
    if not isinstance(value, str) or len(value) > 40:
        return None
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    except (ValueError, OverflowError):
        return None


def _observed_event_at(event: dict) -> str | None:
    stamp = event.get("timestamp")
    if type(stamp) not in {int, float}:
        return None
    try:
        return datetime.fromtimestamp(stamp / 1000, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    except (ValueError, OSError, OverflowError):
        return None


def public_tool_event(event: dict, safe_text) -> dict | None:
    kind, tool_id = event.get("type"), event.get("tool_id")
    if kind not in {"tool_start", "tool_progress", "tool_end", "confirmation_wait"} or type(tool_id) is not int or tool_id < 0:
        return None
    projected = {"type": kind, "tool_id": tool_id, "label": safe_text(str(event.get("label", "助手操作")))}
    if (target := event.get("display_target")) and isinstance(target, str) and _PUBLIC_FILENAME.fullmatch(target):
        if not _OPAQUE_VALUE.search(target) and safe_text(target) == target:
            projected["display_target"] = target
    if at := public_event_at(event.get("at")):
        projected["at"] = at
    if kind in {"tool_start", "tool_end"} and isinstance(event.get("activity_label"), str):
        projected["activity_label"] = safe_text(event["activity_label"])
    if kind == "tool_progress" and type(progress := event.get("progress")) in {int, float} and 0 <= progress <= 1:
        projected["progress"] = progress
    if kind == "confirmation_wait":
        projected["message"] = _PUBLIC_ACTIVITY_MESSAGES[kind]
    if kind == "tool_end":
        projected.update(ok=event.get("ok") is True, summary=safe_text(str(event.get("summary", ""))))
        if isinstance(event.get("failure"), dict):
            projected["failure"] = {key: safe_text(value) if isinstance(value, str) else value
                for key, value in _safe_business_failure(event["failure"]).items()}
    return projected


PUBLIC_ACTIVITY_TYPES = frozenset({
    "agent_start", "agent_end", "text_delta", "assistant_text", "tool_start",
    "tool_progress", "tool_end", "confirmation_wait", "unknown", "error",
})


def public_activity_event(event: dict, safe_text) -> dict | None:
    kind = event.get("type")
    at = public_event_at(event.get("at"))
    projected = {"type": kind, **({"at": at} if at else {})}
    if kind in {"agent_start", "agent_end"}:
        return projected
    if kind == "assistant_text" and isinstance(event.get("text"), str):
        text = safe_text(public_assistant_text(event["text"]))
        return {**projected, "text": text} if text else None
    if kind in {"unknown", "error"}:
        return {**projected, "message": _PUBLIC_ACTIVITY_MESSAGES[kind]}
    return public_tool_event(event, safe_text)


class PublicReasoningFilter:
    """Drop explicitly marked private blocks, including tags split across chunks."""
    _tags = ("<think>", "</think>", "<analysis>", "</analysis>", "<reasoning>", "</reasoning>")
    _pattern = re.compile("|".join(map(re.escape, _tags)), re.IGNORECASE)

    def __init__(self) -> None:
        self._pending = ""
        self._sections: list[str] = []
        self._line_start = True

    def feed(self, value: str, *, final: bool = False) -> str:
        text = self._pending + value
        stop = len(text)
        if not final:
            lower = text.lower()
            for tag in self._tags:
                for length in range(min(len(tag) - 1, len(text)), 0, -1):
                    if lower.endswith(tag[:length]):
                        stop = min(stop, len(text) - length)
                        break
        self._pending = text[stop:]
        visible = []

        def add_visible(piece: str) -> None:
            visible.append(piece)
            if "\n" in piece:
                self._line_start = not piece.rsplit("\n", 1)[1].strip()
            elif piece.strip():
                self._line_start = False

        start = 0
        for match in self._pattern.finditer(text, 0, stop):
            tag = match[0].lower()
            closing = tag.startswith("</")
            if self._sections:
                if closing and tag == self._sections[-1]:
                    self._sections.pop()
                start = match.end()
                continue

            add_visible(text[start:match.start()])
            if closing:
                add_visible(match[0])
            else:
                closing_tag = "</" + tag[1:]
                closing_at = text.lower().find(closing_tag, match.end(), stop)
                body = text[match.end():closing_at if closing_at >= 0 else stop]
                context = text[max(start, match.start() - 24):match.start()] + body[:96]
                quoted = bool(re.search(r"示例|范例|标签|example|sample|tag", context, re.IGNORECASE))
                if quoted:
                    add_visible(match[0])
                else:
                    self._sections.append(closing_tag)
            start = match.end()
        if not self._sections:
            add_visible(text[start:stop])
        return "".join(visible)


def public_assistant_text(value: object) -> str:
    return PublicReasoningFilter().feed(str(value or ""), final=True).strip()


def public_history_turn(turn: dict, safe_text) -> dict:
    """Project saved turns again so legacy raw assistant text cannot reach clients."""
    result_turn = dict(turn)
    result_turn["assistant_text"] = public_assistant_text(turn.get("assistant_text"))
    events = []
    reasoning = PublicReasoningFilter()
    for event in turn.get("events", []):
        if not isinstance(event, dict):
            continue
        if event.get("type") == "text_delta" and isinstance(event.get("delta"), str):
            delta = reasoning.feed(event["delta"])
            if delta:
                at = public_event_at(event.get("at"))
                events.append({"type": "text_delta", "delta": safe_text(delta), **({"at": at} if at else {})})
        elif event.get("type") in PUBLIC_ACTIVITY_TYPES:
            projected = public_activity_event(event, safe_text)
            if projected is not None:
                events.append(projected)
        elif event.get("type") == "context_compaction":
            projected = _safe_compaction_event(event)
            if projected is not None:
                events.append(projected)
        else:
            events.append(event)
    tail = reasoning.feed("", final=True)
    if tail:
        events.append({"type": "text_delta", "delta": safe_text(tail)})
    result_turn["events"] = events
    result = turn.get("result")
    if isinstance(result, dict):
        result = dict(result)
        response = result.get("response")
        if isinstance(response, dict):
            response = dict(response)
            if isinstance(response.get("reply"), str):
                response["reply"] = PublicReasoningFilter().feed(response["reply"], final=True).strip()
            result["response"] = response
        if isinstance(result.get("message"), str):
            result["message"] = PublicReasoningFilter().feed(result["message"], final=True).strip()
        result_turn["result"] = result
    return result_turn



class _PiStreamProjector:
    """Project SDK events into a small UI-safe event vocabulary."""

    def __init__(self) -> None:
        self._calls: dict[str, tuple[int, str, str | None, str | None]] = {}
        self._next_id = 0
        self._saw_text_delta = False

    def project(self, line: str) -> dict[str, object] | None:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            return None  # The SDK result reader reports malformed protocol messages.
        if not isinstance(event, dict):
            return None
        event_type = event.get("type")
        at = _observed_event_at(event)
        timestamp = {"at": at} if at else {}
        if event_type == "agent_start":
            return {"type": "agent_start", **timestamp}
        if event_type == "agent_end":
            return {"type": "agent_end", **timestamp} if event.get("willRetry") is not True else None
        if event_type in {"error", "unknown"}:
            return {"type": event_type, "message": _PUBLIC_ACTIVITY_MESSAGES[event_type], **timestamp}
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
                if isinstance(delta, str) and delta:
                    self._saw_text_delta = True
                    return {"type": "text_delta", "delta": delta, **timestamp}
                return None
            return None
        if event_type == "message_end":
            message = event.get("message") or {}
            if message.get("role") != "assistant":
                return None
            saw_delta = self._saw_text_delta
            self._saw_text_delta = False
            if saw_delta:
                return {"type": "text_end"}
            if message.get("stopReason") in {"error", "aborted"}:
                return None  # The host reports the final failure after retries settle.
            text = "".join(item["text"] for item in message.get("content", [])
                           if item.get("type") == "text" and isinstance(item.get("text"), str))
            if visible := public_assistant_text(text):
                return {"type": "assistant_text", "text": visible, **timestamp}
            return None
        if event_type == "tool_execution_update":
            raw_id = event.get("toolCallId")
            stored = self._calls.get(raw_id) if isinstance(raw_id, str) else None
            if stored is None or stored[1] != event.get("toolName"):
                return None
            tool_id, name, target, _command = stored
            partial = event.get("partialResult") or {}
            progress = (partial.get("details") or {}).get("progress")
            return {"type": "tool_progress", "tool_id": tool_id, "label": _PI_TOOL_LABELS[name],
                **({"progress": progress} if type(progress) in {int, float} and 0 <= progress <= 1 else {}),
                **timestamp, **({"display_target": target} if target else {})}
        if event_type == "tool_execution_start":
            name = event.get("toolName")
            raw_id = event.get("toolCallId")
            if name not in _PI_TOOL_LABELS:
                return None
            self._next_id += 1
            call_key = raw_id if isinstance(raw_id, str) and raw_id else f"call-{self._next_id}"
            label = _PI_TOOL_LABELS[name]
            arguments = event.get("args")
            target = _tool_display_target(name, arguments)
            command = _command_executable(arguments) if name == "bash" else None
            self._calls[call_key] = (self._next_id, name, target, command)
            return {"type": "tool_start", "tool_id": self._next_id, "label": label,
                "activity_label": f"正在{label}", **timestamp,
                **({"display_target": target} if target else {})}
        if event_type == "tool_execution_end":
            raw_id = event.get("toolCallId")
            name = event.get("toolName")
            call_key = raw_id if isinstance(raw_id, str) else ""
            stored = self._calls.pop(call_key, None)
            if stored:
                tool_id, name, target, command = stored
            else:
                if name not in _PI_TOOL_LABELS:
                    return None
                self._next_id += 1
                tool_id = self._next_id
                target, command = _tool_display_target(name, None), None
            label = _PI_TOOL_LABELS[name]
            failed = bool(event.get("isError"))
            details = (event.get("result") or {}).get("details") or {}
            summary_result = event.get("result")
            failure = details.get("failure") if isinstance(details.get("failure"), dict) else None
            unknown = failure is not None and failure.get("submission_effect") == "unknown"
            if name == "read" and unknown:
                # Native file reads cannot submit a business write.
                failure = {key: value for key, value in failure.items() if key != "submission_effect"}
                summary_result = {"details": {"failure": failure}}
                failed, unknown = True, False
            preview = details.get("preview") or {}
            waiting = (
                name in {"preview_creation_plan", "prepare_creation_window"}
                and details.get("kind") == "plan_preview" and preview.get("kind") == "creation_request"
                and (preview.get("request") or {}).get("status") == "awaiting_confirmation"
            ) or (
                name in {"preview_typed_edit", "preview_edit_selection"}
                and details.get("kind") == "edit_preview"
                and (preview.get("proposal") or {}).get("status") == "proposed"
            )
            if waiting and not failed and not unknown:
                return {"type": "confirmation_wait", "tool_id": tool_id, "label": label,
                    "message": _PUBLIC_ACTIVITY_MESSAGES["confirmation_wait"], **timestamp,
                    **({"display_target": target} if target else {})}
            native = details.get("native_execution")
            if (command and isinstance(native, dict) and native.get("process_state") == "exited"
                    and native.get("release_state") == "released" and native.get("job_id") and native.get("request_id")):
                target = command
            if name == "read_creation_skill" and not failed:
                skill = details.get("name")
                if isinstance(skill, str) and _PUBLIC_FILENAME.fullmatch(skill) and not _OPAQUE_VALUE.search(skill):
                    target = skill
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
                "type": "tool_end", "tool_id": tool_id, "label": label, **timestamp,
                **({"display_target": target} if target else {}),
                "ok": not (failed or unknown),
                "summary": (_PUBLIC_ACTIVITY_MESSAGES["unknown"] if unknown
                            else _safe_tool_result_summary(name, summary_result, failed)),
                **({"activity_label": f"已{label}" if label.startswith("读取") else "已返回操作回执"}
                   if not (failed or unknown) else {}),
                **({"failure": _safe_business_failure(failure)} if (failed or unknown) and failure else {}),
                **({"script_window_request": window,"invocation_input_id":invocation_input} if window is not None else {}),
            }
        return None
