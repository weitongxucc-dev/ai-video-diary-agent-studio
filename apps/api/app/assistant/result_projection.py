from __future__ import annotations

import json
from app.assistant.projection import _business_event_lines
from app.assistant.failures import _fail, _classify_output, FAILURE_UNKNOWN, FAILURE_TOOL_FAILED

def project_business_result(result: dict, kind: str = "edit_preview") -> tuple[str, dict | None]:
    """Project typed SDK completion and real business receipts, never CLI history."""
    answer = result["answer"]
    output = json.dumps(result, ensure_ascii=False)
    preview = None
    media_job = None
    action_result = None
    action_failed = False
    topic_result = None
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
    edit_request_status = None
    edit_applied = None
    script_tool_result = None
    script_tool_status = None
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
            "generate_creation_task_topics", "read_creation_execution", *action_tools,
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
            if event.get("toolName") == "generate_creation_task_topics":
                topic_failed = bool(event.get("isError"))
                if not topic_failed:
                    details = (event.get("result") or {}).get("details") or {}
                    if details.get("kind") == "topic_generation":
                        topic_result = details.get("topics")
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
    if edit_applied is not None:
        return "修改已保存到可撤销的时间线版本；本次尚未重新生成视频。", edit_applied
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
    if kind == "edit_preview" and material_write_attempted:
        if (material_failed or material_result_invalid or material_write_result is None
                or "action" not in material_write_result or "changed" not in material_write_result):
            raise _fail(FAILURE_TOOL_FAILED)
    if kind == "material_result":
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
    if topic_result is not None:
        topics = topic_result.get("candidates") or []
        count = len(topics)
        return f"已为当前任务生成 {count} 个选题候选；它们尚未被选择或确认，请在工作台查看并明确选择。", {
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
            lines.append(f"已准备当前 {len(saved['current_window']['entries'])} 条方案，等你检查和确认。")
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
