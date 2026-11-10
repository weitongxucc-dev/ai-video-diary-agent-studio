from __future__ import annotations

import json
from app.assistant.projection import _business_event_lines
from app.assistant.business_failure import _safe_business_failure
from app.assistant.catalogue import SCRIPT_WINDOW_TOOL_OPERATIONS
from app.assistant.script_window_receipts import project_script_window_request, script_window_message
from app.assistant.failures import _fail, _classify_output, FAILURE_UNKNOWN, FAILURE_TOOL_FAILED



def quality_continuation_message(value: dict) -> str:
    return {'recorded': '已回读原质检续作回执；请读取实际质检结果，回执不表示检查通过。',
        'rejected': '原质检续作被拒绝；原失败阶段保留，请处理具体原因。',
        'not_submitted': '原质检续作未提交；请核对阻断原因。'}.get(value.get('status'),
        '原质检续作结果尚未知；只查询原Job和失败前驱，不重复提交或另建检查。')


def resolve_quality_continuation(details: dict, *, task_id: str, invocation_input_id: str, read_original) -> dict:
    if (details.get('kind') != 'quality_continuation' or details.get('task_id') != task_id
            or details.get('invocation_input_id') != invocation_input_id):
        raise ValueError('质检回执不属于当前任务输入')
    pair = [details.get(key) for key in ('job_id', 'failed_request_id')]
    if any(not isinstance(value, str) or not value for value in pair):
        raise ValueError('质检回执缺少原Job或失败前驱')
    # Only locators are consumed; status and successor come from the owner.
    return read_original(*pair)


def storyboard_regeneration_message(value: dict) -> str:
    kind = value["kind"]
    if kind == "storyboard_regeneration_unknown":
        return "故事板重算结果尚未知；原请求已保留，先查询原请求，不重复提交。"
    if kind == "storyboard_regeneration_rejected":
        return "故事板重算请求已拒绝，原稿保留；请按实际原因处理。"
    status = value["receipt"]["status"]
    if status == "succeeded":
        return ("已回读原故事板保存结果；当前采用版本以任务页为准，本回执不表示新视频已生成。"
            if value["action"] == "query" else "故事板版本已保存，可撤销；尚未生成新视频。")
    return {
        "queued": "故事板重算已排队，尚未完成；未生成新视频。",
        "running": "故事板正在重新编排，尚未完成；未生成新视频。",
        "interrupted": "故事板重算已中断，原稿保留；未生成新视频。",
        "failed": "故事板重算失败，原稿保留；未生成新视频。",
        "cancelled": "故事板重算已取消，原稿保留；未生成新视频。",
    }[status]


def segment_revision_message(value: dict) -> str:
    revision = value.get('revision')
    if revision is None:
        effect = (value.get('failure') or {}).get('submission_effect')
        return {'not_submitted': '本段修改未提交；请按原原因处理后再决定下一步。',
                'rejected': '本段修改已被拒绝；未沿此请求采用修改。'}.get(effect,
                '本段修改结果尚未知；先查询原请求，不重复提交。')
    adoption = (revision.get('adoption') or {}).get('status')
    if adoption == 'current':
        return '本段结果已被当前作品采用；整片输出和实际播放状态仍须分别核对。'
    if adoption in {'superseded', 'invalidated'}:
        return '原段产物保留为历史；当前作品未采用这一段结果。'
    if adoption == 'candidate':
        return '本段产物已登记，尚未被当前作品采用。'
    return {'queued': '本段修改已排队，尚未完成或采用。',
            'running': '正在处理本段修改，尚未确认采用结果。',
            'interrupted': '本段修改已中断；原请求和已保存成果保留。',
            'failed': '本段修改未完成；请按原回执处理，保留已有成果。',
            'cancelled': '本段修改已取消；已有成果以原回执为准。',
            'succeeded': '本段执行已完成；采用关系仍须按原请求核对。'}.get(revision.get('status'),
            '原段修改回执已返回；采用关系和产物以实际回执为准。')


def resolve_segment_revision(details: dict, *, task_id: str, input_id: str, read_original) -> dict:
    """Resolve only this concrete request through its authorized business owner."""
    request_id = details.get('request_id')
    action = details.get('action')
    if not isinstance(request_id, str) or not request_id or action not in {'submit', 'query', 'resume'}:
        raise ValueError('段修改原请求身份或动作不可核验')
    failure = _safe_business_failure(details.get('failure'))
    try:
        revision = read_original(request_id)
    except KeyError:
        # Absence of a journal is not evidence that the write never happened.
        failure = {**failure, 'submission_effect': failure.get('submission_effect', 'unknown'),
            'reason': failure.get('reason') or '原段修改请求尚未取得可核验回执。',
            'repair_action': failure.get('repair_action') or '查询原请求，不换新请求重复提交。'}
        return {'kind': 'segment_revision', 'action': action, 'request_id': request_id,
            'revision': None, 'failure': failure}
    if revision.get('task_id') != task_id or revision.get('request_id') != request_id:
        raise ValueError('段修改原请求不属于当前可信任务')
    if action == 'submit' and revision.get('input_id') != input_id:
        raise ValueError('段修改不属于本次可信输入，不能据旧结果宣布新修改完成')
    return {'kind': 'segment_revision', 'action': action, 'request_id': request_id, 'revision': revision}


def execution_readiness_message(readiness: dict | None) -> str:
    if readiness is None:
        return ''
    if readiness.get('status') == 'not_ready':
        reason = {'task_authorization_unavailable': '原任务尚无后台执行授权',
            'task_authorization_unverified': '原任务授权尚未核验', 'task_authorization_expired': '原授权已过期',
            'task_authorization_revoked': '原授权已撤销', 'task_authorization_outside_scope': '修改超出原授权范围',
            'original_execution_identity_differs': '原执行身份已变化',
            'original_execution_identity_unverified': '原执行身份尚未核验'}.get(readiness.get('reason_code'))
        return '本次保存时后台执行授权未就绪' + (f'（{reason}）' if reason else '') + '；修改已保留。'
    if readiness.get('status') == 'authorization_ready':
        return '本次保存时后台执行授权已核验；设备准入尚未检查。'
    return ''


def timeline_restore_message(value: dict) -> str:
    receipt = value.get('receipt') or {}
    if receipt.get('status') == 'applied':
        return '原修改的撤销或重做已保存为新版本；实际采用和产物以当前作品为准。' + execution_readiness_message(receipt.get('execution_readiness'))
    if receipt.get('status') == 'refused' or (value.get('failure') or {}).get('submission_effect') == 'rejected':
        return '原撤销或重做已被拒绝；请按具体冲突处理，保留当前作品。'
    if (value.get('failure') or {}).get('submission_effect') == 'not_submitted':
        return '撤销或重做未提交；原作品保留。'
    return '撤销或重做结果尚未知；查询原请求，不重复提交。'


def resolve_timeline_restore(details: dict, *, task_id: str, read_original) -> dict:
    request_id, action = details.get('request_id'), details.get('action')
    if not isinstance(request_id, str) or not request_id or action not in {'undo', 'redo', 'query'}:
        raise ValueError('原撤销重做请求身份不可核验')
    failure = _safe_business_failure(details.get('failure'))
    try:
        receipt = read_original(request_id)
    except KeyError:
        receipt = None
        failure = {**failure, 'submission_effect': failure.get('submission_effect', 'unknown'),
            'reason': failure.get('reason') or '原撤销重做尚未取得可核验回执。',
            'repair_action': failure.get('repair_action') or '查询原请求，不换新身份重复提交。'}
    if receipt is not None and (receipt.get('task_id') != task_id or receipt.get('request_id') != request_id
            or action != 'query' and receipt.get('action') != action):
        raise ValueError('撤销重做回执不属于原任务或动作')
    return {'kind': 'timeline_restore_request', 'action': action, 'request_id': request_id,
        'receipt': receipt, **({'failure': failure} if receipt is None and failure else {})}


def refresh_original_receipts(turn: dict, *, task_id: str, input_id: str, read_segment, read_restore) -> dict:
    """Refresh this HTTP read projection only; saved inputs and journals stay owned."""
    result = turn.get('result') or {}
    response = result.get('response')
    if result.get('kind') != 'task_turn' or not isinstance(response, dict):
        return turn
    response = dict(response)
    for field, reader in (('segment_revision', read_segment), ('timeline_restore', read_restore)):
        original = response.get(field)
        if not isinstance(original, dict):
            continue
        query = {**original, 'action': 'query'}
        try:
            response[field] = (resolve_segment_revision(query, task_id=task_id, input_id=input_id, read_original=reader)
                if field == 'segment_revision' else resolve_timeline_restore(query, task_id=task_id, read_original=reader))
        except (PermissionError, ValueError) as exc:
            response[field] = {**query, 'revision' if field == 'segment_revision' else 'receipt': None,
                'failure': _safe_business_failure({'reason': str(exc), 'submission_effect': 'unknown',
                    'repair_action': '核对原任务和请求权限后再查询，不重复提交。'})}
    return {**turn, 'result': {**result, 'response': response}}


def project_business_result(result: dict, kind: str = "edit_preview") -> tuple[str, dict | None]:
    """Project typed SDK completion and real business receipts, never CLI history."""
    answer = result["answer"]
    output = json.dumps(result, ensure_ascii=False)
    preview = None
    media_job = None
    action_result = None
    action_failed = False
    topic_result = None
    script_result = None
    script_window_result = None
    script_window_attempted = False
    topic_failed = False
    material_result = None
    material_write_result = None
    material_write_attempted = False
    material_failed = False
    material_result_invalid = False
    settled = result["status"] == "settled"
    render_configuration_failed = False
    request_planning_failed = False
    edit_control_result = None
    edit_control_failed = False
    action_tools = {
        "start_creation_execution": "start",
        "resume_creation_execution": "resume",
        "cancel_creation_execution": "cancel",
    }
    orchestration_failed = False
    unresolved_syntax_error = False
    quality_continuation = None
    edit_request_status = None
    edit_applied = None
    script_tool_result = None
    script_tool_status = None
    storyboard_result = None
    storyboard_attempted = False
    segment_result = None
    restore_result = None
    try:
        events = [json.loads(line) for line in _business_event_lines("\n".join(json.dumps(event) for event in result["tool_results"]))]
    except json.JSONDecodeError as exc:
        raise _fail(FAILURE_UNKNOWN, explanation="对话助手返回了无法读取的事件，这一轮对话没完成。") from exc
    latest_input = ({"invocation_input_id":result["input_id"]} if "input_id" in result else
        next((event["result"]["details"] for event in reversed(events)
            if "invocation_input_id" in ((event.get("result") or {}).get("details") or {})),None))
    for event in events:
        details = (event.get("result") or {}).get("details") or {}
        if (latest_input is not None and event.get("type") == "tool_execution_end"
                and ("invocation_input_id" not in details or details["invocation_input_id"] != latest_input["invocation_input_id"])):
            continue  # History remains in the SDK log; only this input owns the reply.
        if event.get('type') == 'tool_execution_end' and event.get('toolName') in {
                'continue_task_quality_check', 'read_task_quality_continuation'}:
            details = (event.get('result') or {}).get('details') or {}
            if (details.get('kind') == 'quality_continuation' and details.get('job_id')
                    and details.get('failed_request_id')):
                quality_continuation = details
        if event.get('type')=='tool_execution_end' and event.get('toolName') in SCRIPT_WINDOW_TOOL_OPERATIONS:
            script_window_attempted=True
            operation=SCRIPT_WINDOW_TOOL_OPERATIONS[event['toolName']]
            projected=project_script_window_request(details,expected_operation=operation,is_error=bool(event.get('isError')))
            if projected is not None:
                script_window_result=projected
        if (event.get('type') == 'tool_execution_end'
                and event.get('toolName') in {'revise_narration_segment', 'read_narration_revision', 'resume_narration_revision'}
                and details.get('kind') == 'segment_revision'):
            segment_result = details
        if (event.get('type') == 'tool_execution_end'
                and event.get('toolName') in {'undo_last_edit', 'redo_last_edit', 'read_timeline_restore_request'}
                and details.get('kind') == 'timeline_restore_request'):
            restore_result = details
        if event.get("type") == "tool_execution_end" and event.get("toolName") in {"regenerate_storyboard", "read_storyboard_regeneration"}:
            storyboard_attempted = True
            action = "submit" if event["toolName"] == "regenerate_storyboard" else "query"
            source = {key: details[key] for key in ("input_id", "input_turn_id") if key in details}
            if details.get("kind") == "storyboard_regeneration_receipt" and not event.get("isError"):
                storyboard_result = {"kind": details["kind"], "action": action, "receipt": details["receipt"],
                    "tool_call_id": event.get("toolCallId"), **source}
            elif action == "submit" and details.get("kind") in {"storyboard_regeneration_unknown", "storyboard_regeneration_rejected"}:
                storyboard_result = {key: details[key] for key in ("kind", "request_id", "query_url", "failure")}
                storyboard_result.update(action=action, tool_call_id=event.get("toolCallId"), **source)
        if event.get("type") == "tool_execution_end" and event.get("toolName") in {"apply_typed_edit", "preview_typed_edit", "read_typed_edit_request", "preview_media_script", "preview_media_script_tool", "read_media_script_request", "observe_task_output", "read_task_observation_request"}:
            status_details = (event.get("result") or {}).get("details") or {}
            if status_details.get("kind") == "edit_request_status":
                edit_request_status = status_details
            elif status_details.get("kind") == "edit_applied" and not event.get("isError"):
                edit_applied = status_details
                edit_request_status = None
        if event.get("type") == "tool_execution_end" and event.get("toolName") in {"save_media_script_tool", "update_media_script_tool", "revoke_media_script_tool", "read_media_script_tool_creation"}:
            tool_details = (event.get("result") or {}).get("details") or {}
            if tool_details.get("kind") == "media_script_tool_result" and not event.get("isError"):
                script_tool_result = tool_details
                script_tool_status = None
            elif tool_details.get("kind") == "media_script_tool_status":
                script_tool_status = tool_details
                script_tool_result = None
        if event.get("type") == "tool_execution_end" and event.get("toolName") == "codemode":
            result = event.get("result") or {}
            details = result.get("details") or {}
            if event.get("isError"):
                syntax_before_calls = (details.get("calls") == [] and details.get("business_receipts") == []
                    and details.get("incomplete_calls") is False
                    and any(block.get("type") == "text" and "SyntaxError:" in block.get("text", "")
                            for block in result.get("content", [])))
                if syntax_before_calls:
                    unresolved_syntax_error = True
                else:
                    orchestration_failed = True
            elif event.get("isError") is False and details.get("incomplete_calls") is False and details.get("business_receipts"):
                unresolved_syntax_error = False
        if event.get("type") == "tool_execution_end" and event.get("toolName") in {
            "preview_typed_edit", "read_task_media_job", "read_typed_edit_request", "preview_media_script", "preview_media_script_tool", "read_media_script_request", "observe_task_output", "read_task_observation_request", "preview_creation_plan", "prepare_creation_window", "preview_script_draft",
            "preview_edit_selection", "discard_edit_proposal", "undo_last_edit", "redo_last_edit",
            "generate_creation_task_topics", "adopt_creation_task_topic", "read_creation_execution", *action_tools,
            "read_selected_material", "update_selected_material_tags",
            "read_material_library_status", "search_material_library",
        }:
            if event.get("toolName") in {"discard_edit_proposal", "undo_last_edit", "redo_last_edit"}:
                edit_control_failed = bool(event.get("isError")) or edit_control_failed
                if not event.get("isError"):
                    details = (event.get("result") or {}).get("details") or {}
                    if details.get("kind") == "edit_control":
                        edit_control_result = details
            if event.get("toolName") in {"preview_creation_plan", "prepare_creation_window"}:
                request_planning_failed = bool(event.get("isError")) or request_planning_failed
            if event.get("toolName") in {"read_material_library_status", "search_material_library"}:
                material_failed = bool(event.get("isError")) or material_failed
            if event.get("toolName") in {"read_selected_material", "update_selected_material_tags"}:
                material_write_attempted = material_write_attempted or event.get("toolName") == "update_selected_material_tags"
                material_failed = bool(event.get("isError")) or material_failed
                if not event.get("isError"):
                    details = (event.get("result") or {}).get("details") or {}
                    scope = details.get("scope")
                    asset_ref = details.get("asset_ref")
                    segment_ref = details.get("segment_ref")
                    user_tags = details.get("user_tags")
                    system_tags = details.get("system_tags")
                    valid_tags = isinstance(user_tags, list) and all(isinstance(tag, str) for tag in user_tags)
                    valid_scope_ref = (
                        scope == "asset" and isinstance(asset_ref, str) and segment_ref is None
                    ) or (
                        scope == "segment" and isinstance(asset_ref, str)
                        and isinstance(segment_ref, str) and isinstance(system_tags, list)
                        and all(isinstance(tag, str) for tag in system_tags)
                    )
                    if valid_scope_ref and valid_tags:
                        material_result = {
                            "scope": scope,
                            "asset_ref": asset_ref,
                            **({"segment_ref": segment_ref} if scope == "segment" and isinstance(segment_ref, str) else {}),
                            "user_tags": [tag for tag in user_tags if isinstance(tag, str)],
                            **({"system_tags": [tag for tag in system_tags if isinstance(tag, str)]}
                               if scope == "segment" and isinstance(system_tags, list) else {}),
                            **({"action": details["action"]} if details.get("action") in {"replace", "add", "remove"} else {}),
                            **({"changed": details["changed"]} if isinstance(details.get("changed"), bool) else {}),
                            **({"previous_user_tags": [tag for tag in details["previous_user_tags"] if isinstance(tag, str)]}
                               if isinstance(details.get("previous_user_tags"), list) else {}),
                        }
                        if event.get("toolName") == "update_selected_material_tags":
                            material_write_result = dict(material_result)
                    else:
                        material_result_invalid = True
            if event.get("isError") and event.get("toolName") in action_tools:
                result_text = json.dumps(event.get("result") or {}, ensure_ascii=False)
                render_configuration_failed = "DEVICE_CPU_RENDER_" in result_text
            if event.get("toolName") in action_tools:
                action_failed = bool(event.get("isError"))
                if not action_failed:
                    details = (event.get("result") or {}).get("details") or {}
                    if details.get("kind") == "execution_action":
                        action_result = {**details, "action": action_tools[event["toolName"]]}
            if event.get("toolName") == "read_creation_execution" and not event.get("isError"):
                details = (event.get("result") or {}).get("details") or {}
                if details.get("kind") == "execution_result":
                    if action_result is None or action_result.get("action") is None:
                        action_result = details
                    else:
                        submitted_job_id = ((action_result.get("status") or {}).get("job") or {}).get("id")
                        read_job_id = ((details.get("status") or {}).get("job") or {}).get("id")
                        if submitted_job_id and submitted_job_id == read_job_id:
                            action_result = {**action_result, "status": details["status"]}
            if event.get("toolName") in {"generate_creation_task_topics", "adopt_creation_task_topic"}:
                topic_failed = bool(event.get("isError"))
                if not topic_failed:
                    details = (event.get("result") or {}).get("details") or {}
                    if details.get("kind") == "topic_generation":
                        topic_result = details.get("topics")
            if event.get("toolName") == "preview_script_draft" and kind in {"script_draft", "topic_generation"} and not event.get("isError"):
                details = (event.get("result") or {}).get("details") or {}
                if details.get("kind") == "script_draft":
                    script_result = details.get("preview")
            if not event.get("isError"):
                details = (event.get("result") or {}).get("details") or {}
                if event.get("toolName") in {"preview_typed_edit", "read_task_media_job", "read_typed_edit_request", "preview_media_script", "preview_media_script_tool", "read_media_script_request", "observe_task_output", "read_task_observation_request"}:
                    kind = "edit_preview"
                if kind == "edit_preview" and details.get("kind") == "task_media_job":
                    media_job = details["media_job"]
                if details.get("kind") == kind:
                    preview = details.get("preview")
                    if kind == "topic_generation":
                        preview = {"kind": "topic_generation", "topics": details.get("topics")}
    if script_window_result is not None:
        return script_window_message(script_window_result),script_window_result
    if script_window_attempted:
        raise _fail(FAILURE_TOOL_FAILED,explanation='文案窗口操作没有可信业务回执；请核对工具参数或具体失败原因，未宣称已保存或采用。')
    if restore_result is not None:
        return timeline_restore_message(restore_result), restore_result
    if storyboard_result is not None:
        return storyboard_regeneration_message(storyboard_result), storyboard_result
    if storyboard_attempted:
        raise _fail(FAILURE_TOOL_FAILED, explanation="故事板重算没有返回可核验的原请求回执，未确认完成。")
    if edit_applied is not None:
        readiness_message = execution_readiness_message((edit_applied.get('result') or {}).get('execution_readiness'))
        # A later provider/tool failure cannot erase a committed edit. A real
        # render receipt takes precedence when the same input did both actions.
        if action_result is not None:
            action = action_result.get("action")
            return ("修改已保存，后续制作已有实际回执；完成状态以当前作业为准。" if action else
                    "修改已保存；已读取原制作作业，本轮未新增制作请求。") + readiness_message, {
                **action_result, "kind": "execution_action" if action else "execution_result", "applied_edit": edit_applied}
        return ("修改已保存，可撤销；后续制作没有取得成功提交回执。" if action_failed else
                "修改已保存到可撤销的时间线版本；本次尚未重新生成视频。") + readiness_message, edit_applied
    if (action_failed and action_result is None) or topic_failed:
        if render_configuration_failed:
            raise _fail(FAILURE_TOOL_FAILED, explanation="本机渲染尚未完成配置，本轮未生成新成片；当前修改已保留。",
                        repair_action="查看本轮工具诊断中的资源标定原因，请设备管理员完成运行配置后再制作。")
        raise _fail(FAILURE_TOOL_FAILED)
    if not settled or not answer:
        cause = _classify_output(output)
        if cause == FAILURE_UNKNOWN:
            raise _fail(
                cause,
                explanation="对话没有得到完整回复，日志里没有留下能说明原因的信息，拿不到更具体的原因。",
            )
        raise _fail(cause)
    if script_tool_status is not None and preview is None:
        if media_job is not None:
            script_tool_status = {**script_tool_status, "related_media_job": media_job}
        return "工具写入尚未取得可核验结果；原定位和输入已保留，只读取结果，不重复写入。", script_tool_status
    if script_tool_result is not None and preview is None:
        if media_job is not None:
            script_tool_result = {**script_tool_result, "related_media_job": media_job}
        action = script_tool_result.get("action")
        message = {"save": "可复用图片工具已保存；未应用图片或生成成片。", "query": "已回读原工具创建结果，不代表其他新输入已经保存。",
                   "update": "可复用工具的新版本已保存；既有媒体保持不变。", "revoke": "工具已停用；已存在的合法图片和视频仍保留。"}.get(action, "工具实际回执已返回。")
        return message, script_tool_result
    if segment_result is not None:
        return segment_revision_message(segment_result), segment_result
    if quality_continuation is not None:
        return quality_continuation_message(quality_continuation), quality_continuation
    if edit_request_status is not None and preview is None:
        if media_job is not None:
            edit_request_status = {**edit_request_status, "related_media_job": media_job}
        message = "请求尚未返回可核验结果；原请求定位已保留，先回读结果，不重复提交。"
        if edit_request_status.get("status") == "not_submitted":
            message = "本次请求未提交；原请求定位和具体原因已保留。"
        elif edit_request_status.get("status") == "rejected":
            message = "本次请求已被拒绝；请按实际原因处理，未沿该请求应用修改。"
        if media_job is not None:
            message += " 先前已经返回的后台作业回执仍保留；不表示图层已应用或成片已生成。"
        return message, edit_request_status
    if kind == "plan_preview" and request_planning_failed and preview is None:
        raise _fail(FAILURE_TOOL_FAILED, explanation="制作目标没有返回可核验的保存回执，本轮没有确认新任务或成片。")
    if edit_control_failed:
        raise _fail(FAILURE_TOOL_FAILED, explanation="这次撤销、重做或取消修改没有完成，请查看本轮具体失败原因。")
    if edit_control_result is not None:
        action = edit_control_result["action"]
        answer = {"undo": "已整体撤销上一步编辑，保留了可恢复的历史版本。",
                  "redo": "已重做刚才撤销的整项编辑，保留了历史版本。",
                  "reject": "已取消这份待确认的修改方案；草稿和成片未改变。"}[action]
        return answer, edit_control_result
    if kind in {"edit_preview", "plan_preview"} and material_write_attempted:
        if (material_failed or material_result_invalid or material_write_result is None
                or "action" not in material_write_result or "changed" not in material_write_result):
            raise _fail(FAILURE_TOOL_FAILED)
    if kind == "material_result" or (kind == "plan_preview" and material_write_result is not None and preview is None):
        if material_failed or material_result_invalid:
            raise _fail(FAILURE_TOOL_FAILED)
        if material_result is None:
            return answer, None
        tags = "、".join(material_result["user_tags"]) or "暂无"
        object_label = "整条素材" if material_result["scope"] == "asset" else "当前片段"
        if material_result.get("action") and material_result.get("changed"):
            answer = f"{object_label}的我的标签已保存并回读：{tags}。"
        return answer, {"kind": "material_result", "material_result": material_result}
    if action_result is not None:
        status = action_result.get("status") or {}
        job = status.get("job") if isinstance(status, dict) else None
        job_status = job.get("status") if isinstance(job, dict) else None
        action = action_result.get("action")
        if action is None:
            state = {"queued":"排队中", "running":"运行中", "succeeded":"成功", "failed":"失败",
                "interrupted":"已中断", "cancelled":"已取消"}.get(job_status, job_status or "未知")
            answer = f"已查询原制作作业；当前作业状态：{state}。"
            if job_status == "succeeded" and isinstance(status, dict) and status.get("video_ready"):
                answer += "当前成片产物可用。"
            answer += "本轮未提交新的开始或恢复请求。"
        elif action == "cancel":
            answer = f"取消操作已返回；当前作业状态：{job_status or '未知'}。"
        elif job_status == "queued":
            answer = "连续制作已提交并排队，尚未完成；可在任务页查看实际进度。"
        elif job_status == "running":
            answer = "连续制作正在运行，尚未完成；可在任务页查看实际进度。"
        elif job_status == "succeeded" and isinstance(status, dict) and status.get("video_ready"):
            answer = "任务记录显示制作已完成，当前成片产物可用。"
        else:
            answer = f"制作动作已返回；当前作业状态：{job_status or '未知'}。请以任务页回读为准。"
        return answer, {"kind": "execution_result" if action is None else "execution_action", "action": action, "status": status,
            **{key:action_result[key] for key in ("input_id","input_turn_id") if key in action_result}}
    if script_result is not None:
        return ("实际文案版本已按原委托采用，可以继续制作；尚未生成成片。" if script_result.get("adopted")
            else answer), script_result
    if topic_result is not None:
        topics = topic_result.get("candidates") or []
        count = len(topics)
        return ("真实主题已按原委托采用，可以继续文案。" if topic_result.get("selected_topic_id") else
            f"已为当前任务生成 {count} 个选题候选；尚未采用。"), {
            "kind": "topic_generation", "topics": topic_result,
        }
    if kind == "plan_preview" and isinstance(preview, dict) and preview.get("kind") == "creation_request":
        saved = preview["request"]
        lines = [f"制作目标已保存：共 {saved['target_count']} 条视频。"]
        questions = saved["clarification_questions"]
        if questions:
            lines.extend(questions)
        elif request_planning_failed:
            lines.append("当前这组方案没有准备完成，请查看本轮失败提示后修复；目标仍保留。")
        elif saved.get("current_window"):
            count = len(saved["current_window"]["entries"])
            lines.append(f"当前 {count} 条方案已按原委托采用，任务已保存。" if saved["current_window"]["status"] == "confirmed"
                else f"当前 {count} 条讨论方案已保存，尚未采用。" if (saved.get("intent") or {}).get("kind") == "discussion"
                else f"已准备当前 {count} 条方案，等你检查和采用。")
        else:
            lines.append("制作目标尚待规划，可以在工作台准备当前这组具体方案。")
        lines.append("尚未生成成片；后续进度以独立任务的实际结果为准。")
        return "\n".join(lines), preview
    if kind == "edit_preview" and media_job is not None:
        result = media_job.get("result")
        proposal = result.get("proposal") if isinstance(result, dict) else None
        if media_job.get("status") == "succeeded" and isinstance(proposal, dict) and proposal.get("operation") and proposal.get("preview"):
            preview, media_job = result, None
    if kind == "edit_preview" and media_job is not None:
        if media_job.get("result_kind") == "graphic_source":
            source = media_job.get("result")
            if media_job.get("status") == "succeeded" and isinstance(source, dict) and source.get("source_ref") and source.get("artifact_id"):
                answer = "可编辑PNG来源已生成；尚未应用图层、改变当前草稿或生成成片。"
            elif media_job.get("status") in {"queued", "running"}:
                answer = "图片来源程序已安排，后台尚未返回成功来源；未应用图层或生成成片。"
            else:
                answer = "图片来源程序尚无可用成功结果，请按实际作业状态与原因处理；未应用图层或生成成片。"
        elif media_job.get("result_kind") == "observation":
            if media_job.get("status") == "succeeded" and isinstance(media_job.get("result"), dict):
                answer = "实际输出检查已完成；请按分项证据查看结果，未提供的语义或听感不能当作已通过。"
            else:
                answer = "实际输出检查已安排；尚未取得成功检查结果，排队或运行不能视为已经检查。"
        elif media_job.get('status') == 'succeeded':
            answer = '媒体处理结果已回读；产物及其采用关系以该作业记录为准。'
        elif media_job.get('status') in {'failed', 'cancelled', 'interrupted'}:
            answer = '媒体处理未成功完成，请查看原作业的实际原因与恢复方式。'
        else:
            answer = "已安排，预览尚未完成；完成后请在工作台查看实际预览，再确认是否应用。"
        preview = {"kind": "media_job", "media_job": media_job}
    elif kind == "edit_preview" and preview is not None:
        # The proposal is the business result; the model's final text stays in
        # the Pi session as evidence and does not define confirmation effects.
        proposal = preview["proposal"]
        operation_type = proposal["operation"]["operation_type"]
        lines = [
            proposal["summary"],
            "待确认，尚未应用修改。",
            f"修改前：{proposal['preview']['before']['detail']}",
            f"修改后：{proposal['preview']['after']['detail']}",
        ]
        if operation_type == "ChangeCrop":
            lines.append("水平/垂直百分比表示裁剪窗口位置，不是画面宽度或面积比例。")
        lines.extend(proposal["external_effects"])
        if proposal["invalidated_refs"]:
            lines.append("确认后已有结果将失效，请核对预览卡中的“需要重做”列表。")
        lines.append("请在工作台确认后应用本提案；确认修改不等于完整成片已生成，成片以实际输出结果为准。")
        answer = "\n".join(lines)
    if kind == "edit_preview" and material_write_result is not None:
        tags = "、".join(material_write_result["user_tags"]) or "暂无"
        object_label = "所选镜头对应的素材片段" if material_write_result["scope"] == "segment" else "所选素材"
        state = "已保存并回读" if material_write_result["changed"] else "已核对，无需重复修改"
        tag_answer = f"{object_label}的我的标签{state}：{tags}。"
        answer = f"{tag_answer}\n{answer}" if preview is not None else tag_answer
    if (orchestration_failed or unresolved_syntax_error) and preview is not None:
        answer = "后续工具编排未完成；已经返回的真实预览保留，尚未应用修改。\n" + answer
    return answer, preview


def read_sdk_result(output: str, kind: str = "edit_preview") -> tuple[str, dict | None]:
    result = None
    for line in output.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise _fail(FAILURE_UNKNOWN, explanation="创作会话返回了无法读取的协议消息。") from exc
        if event.get("type") == "creation_session_result":
            if result is not None or event.get("protocol") != 1:
                raise _fail(FAILURE_UNKNOWN, explanation="创作会话结果协议或身份不可核验。")
            result = event
    if (result is None or result.get("owner") != "pi-coding-agent-sdk"
            or result.get("status") not in {"settled", "failed"}
            or not isinstance(result.get("answer"), str)
            or not isinstance(result.get("tool_results"), list)
            or any(not isinstance(item, dict) or item.get("type") != "tool_execution_end"
                   for item in result["tool_results"])):
        raise _fail(FAILURE_UNKNOWN, explanation="创作会话没有返回可核验的完成结果，请查询已有操作回执。")
    return project_business_result(result, kind)
