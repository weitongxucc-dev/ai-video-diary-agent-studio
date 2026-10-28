"""Verify recipe registration receipts through the original metadata owners."""
from __future__ import annotations

import json
from urllib.parse import quote
from uuid import UUID, NAMESPACE_URL, uuid5
from pydantic import ValidationError

from app.creation.contracts import TaskEvent
from app.media_script_tools import ScriptToolError, ScriptToolConflict, ScriptToolUnknown
from app.models import utc_now
from app.schemas import (AssistantScriptToolReceipt, MediaScriptToolSaveRequest,
                         MediaScriptToolUpdateRequest, MediaScriptToolRevokeRequest)


class ScriptToolReceiptRejected(Exception):
    def __init__(self, detail):
        self.detail = detail
        super().__init__(str(detail))


class ScriptToolLookupRejected(Exception):
    """Preserve the registry reader's original error for transport projection."""
    def __init__(self, reason):
        self.reason = reason
        super().__init__(str(reason))


def tool_metadata(value):
    return {key: item for key, item in value.items() if key != 'definition'}


class AssistantScriptToolResultBinding:
    def __init__(self, production, conversations, registry, *, verify_reader):
        self.production = production
        self.conversations = conversations
        self.registry = registry
        self.verify_reader = verify_reader

    def read_created(self, actor, request_id):
        actor = self.verify_reader(actor)
        try:
            return self.registry.get_created_for_request(actor, request_id)
        except (KeyError, PermissionError, ScriptToolError) as exc:
            raise ScriptToolLookupRejected(exc) from exc

    def read_tool(self, actor, tool_id, *, version=None):
        actor = self.verify_reader(actor)
        try:
            return self.registry.get(actor, tool_id, version=version)
        except (KeyError, PermissionError, ScriptToolError) as exc:
            raise ScriptToolLookupRejected(exc) from exc

    def origin(self, workspace_id, member_id, task_id, turn_id, details):
        try:
            return self.conversations.task_result_source(turn_id, workspace_id, member_id, task_id,
                input_id=details.get('input_id'), input_turn_id=details.get('input_turn_id'))
        except ValueError as exc:
            raise ScriptToolReceiptRejected(str(exc)) from exc

    def check_save_result(self, workspace_id, task_id, tool, submitted_input):
        producer = self.production.job_records.get_job_run(workspace_id, tool['source_job_id'])
        if producer.task_id != task_id or tool['version'] != 1:
            raise ScriptToolReceiptRejected(detail='工具登记结果不属于原任务的v1')
        if submitted_input is not None and any((tool[key] != submitted_input[field] for key, field in (('source_job_id', 'producer_job_id'), ('name', 'name'), ('description', 'description'), ('parameter_schema', 'parameter_schema')))):
            raise ScriptToolReceiptRejected(detail='工具登记结果与原提交输入不一致')

    def resolve(self, workspace_id, task_id, member_id, turn_id, actor, details):
        if (actor.user_id or actor.member_id) != member_id:
            raise ScriptToolReceiptRejected(detail='工具回执不属于当前对话成员')
        action = details.get('action')
        recorded = details['kind'] == 'media_script_tool_result'
        status = 'recorded' if recorded else details.get('status')
        if action not in {'save', 'query', 'update', 'revoke'} or status not in {'recorded', 'unknown', 'rejected', 'not_submitted'}:
            raise ScriptToolReceiptRejected(detail='工具回执动作或状态不可核验')
        submitted, expected, request_id, tool = (None, None, None, None)
        if action in {'save', 'query'}:
            try:
                request_id = UUID(str(details['request_id']))
            except (KeyError, ValueError) as exc:
                raise ScriptToolReceiptRejected(detail='工具登记缺少有效原请求身份') from exc
            query_url = f'/api/media-script-tools/requests/{request_id}'
            if action == 'save' and details.get('query_url') != query_url:
                raise ScriptToolReceiptRejected(detail='工具登记查询定位不可核验')
            if not recorded:
                call_id = details.get('tool_call_id')
                if action != 'save' or details.get('task_id') != task_id or details.get('assistant_turn_id') != turn_id or (not isinstance(call_id, str)) or (not 1 <= len(call_id) <= 128) or (request_id != uuid5(NAMESPACE_URL, json.dumps([task_id, turn_id, call_id], ensure_ascii=False, separators=(',', ':')))):
                    raise ScriptToolReceiptRejected(detail='未知工具登记的原轮次和调用来源不可核验')
                raw = details.get('submitted_input')
                if not isinstance(raw, dict) or set(raw) - {'producer_job_id', 'name', 'description', 'parameter_schema'}:
                    raise ScriptToolReceiptRejected(detail='工具登记原输入不可核验')
                try:
                    submitted = MediaScriptToolSaveRequest.model_validate({**raw, 'request_id': request_id}).model_dump(mode='json', exclude={'request_id', 'assistant_turn_id'})
                except ValidationError as exc:
                    raise ScriptToolReceiptRejected(detail='工具登记原输入不符合实际API契约') from exc
                if len(json.dumps(submitted, ensure_ascii=False).encode()) > 65536:
                    raise ScriptToolReceiptRejected(detail='工具登记原输入超过有界元数据限制')
            try:
                tool = self.read_created(actor, request_id)
            except ScriptToolLookupRejected as exc:
                if recorded or not isinstance(exc.reason, (KeyError, ScriptToolConflict, ScriptToolUnknown)):
                    raise
            if tool is not None:
                if action == 'save':
                    self.check_save_result(workspace_id, task_id, tool, submitted)
                status = 'recorded'
            elif status == 'unknown':
                binding = self.origin(workspace_id, member_id, task_id, turn_id, details)
                snapshot = binding['context']['target_snapshot']
                source = {'workspace_id': workspace_id, 'task_id': task_id, 'member_id': member_id, 'conversation_id': binding['conversation_id'], 'assistant_turn_id': turn_id, **{key: binding[key] for key in ('input_id', 'input_turn_id') if key in binding}, 'expected_timeline_version_id': snapshot['timeline_version_id'], 'expected_timeline_sequence': snapshot['timeline_sequence'], 'actor_identity': actor.identity_payload()}
                registration_id = self.registry.registration_identity(actor, request_id)[1]
                event_id = f'event_script_tool_locator_{turn_id}_{request_id}'
                try:
                    created_at = self.production.execution_records.get_task_event(workspace_id, event_id).created_at
                except KeyError:
                    created_at = utc_now()
                self.production.execution_records.append_task_event(TaskEvent(id=event_id, workspace_id=workspace_id, task_id=task_id, job_id=None, event_type='assistant_script_tool_located', payload={'source': source, 'request_id': str(request_id), 'query_url': query_url, 'submitted_input': submitted, 'registration_id': registration_id}, created_at=created_at))
        else:
            reported = details.get('tool') if recorded else details
            tool_id = reported.get('tool_id') if isinstance(reported, dict) else None
            if not isinstance(tool_id, str) or not 1 <= len(tool_id) <= 120:
                raise ScriptToolReceiptRejected(detail='工具回执身份不可核验')
            query_url = f"/api/media-script-tools/{quote(tool_id, safe='')}"
            if not recorded and details.get('query_url') != query_url:
                raise ScriptToolReceiptRejected(detail='未知工具写入查询定位不可核验')
            try:
                expected = MediaScriptToolRevokeRequest.model_validate({'expected_version': details.get('expected_version')}).expected_version
                schema = MediaScriptToolUpdateRequest if action == 'update' else MediaScriptToolRevokeRequest
                submitted = schema.model_validate(details.get('submitted_input')).model_dump(mode='json')
                if submitted['expected_version'] != expected:
                    raise ValueError('expected version mismatch')
            except (ValidationError, ValueError) as exc:
                raise ScriptToolReceiptRejected(detail='工具写入原版本和输入不可核验') from exc
            if recorded:
                version = reported.get('version')
                if type(version) is not int or version < 1:
                    raise ScriptToolReceiptRejected(detail='工具回执版本不可核验')
                tool = self.read_tool(actor, tool_id, version=version)
                if action == 'revoke' and tool['active'] is not False or (expected is not None and version != expected + (1 if action == 'update' else 0)):
                    raise ScriptToolReceiptRejected(detail='工具版本未证实本次回执')
                if action == 'update' and submitted is not None and any((tool[key] != submitted[field] for key, field in (('source_job_id', 'producer_job_id'), ('name', 'name'), ('description', 'description'), ('parameter_schema', 'parameter_schema')))):
                    raise ScriptToolReceiptRejected(detail='工具更新版本与原输入不一致')
            else:
                tool = self.read_tool(actor, tool_id)
        receipt = AssistantScriptToolReceipt(action=action, status=status, request_id=request_id, tool_id=tool['tool_id'] if tool else None, tool_version=tool['version'] if tool else None, expected_version=expected, query_url=query_url, submitted_input=submitted, tool=tool_metadata(tool) if tool else None)
        if status == 'recorded':
            messages = {'save': '工具元数据已登记，已回读原v1。', 'query': '已回读原登记请求的实际v1。', 'update': '已回读工具的实际更新版本。', 'revoke': '已核验工具停用状态。'}
            message = messages[action] + '回读工具元数据不会生成图片或改变当前草稿、成片。'
        elif action in {'update', 'revoke'}:
            message = f"本次工具写入{('结果未知' if status == 'unknown' else '已拒绝')}；已读取当前HEAD v{tool['version']}，不能将HEAD变化归因于本次请求，未重复写入。"
        else:
            state_text = {'unknown': '结果未知', 'rejected': '请求已拒绝', 'not_submitted': '请求未提交'}[status]
            message = f'工具登记{state_text}；原UUID、输入和查询定位已保留，只回读原请求，不重复保存。'
        return (receipt, message)
