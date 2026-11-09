"""Original media-script submission and request recovery, without HTTP state."""
from dataclasses import asdict, dataclass
from pathlib import Path
import json

from app.creation.editing.exact_preview_use_case import EditPreviewRejected
from app.media_script import MediaScriptError
from app.media_script_tools import ScriptToolError, ScriptToolConflict, ScriptToolUnknown


class ScriptSubmissionRejected(Exception):
    def __init__(self, status_code, detail):
        super().__init__(detail)
        self.status_code, self.detail = status_code, detail


def tool_rejection(exc):
    status = 403 if isinstance(exc, PermissionError) else 404 if isinstance(exc, KeyError) else 409 if isinstance(exc, (ScriptToolConflict, ScriptToolUnknown)) else 422
    return ScriptSubmissionRejected(status, "配方不存在或不可访问" if isinstance(exc, KeyError) else str(exc))


def response_snapshot(body):
    # Match JSONResponse serialization before storing an independent plain body.
    return json.loads(json.dumps(body, ensure_ascii=False, allow_nan=False,
        separators=(",", ":")).encode("utf-8"))


@dataclass(frozen=True)
class SubmissionReceipt:
    status_code: int
    body: dict

    @classmethod
    def create(cls, status_code, body):
        return cls(status_code, response_snapshot(body))


@dataclass(frozen=True)
class ScriptSource:
    request_id: str | None
    assistant_turn_id: str | None
    expected_timeline_version_id: str | None
    expected_timeline_sequence: int
    requested_outputs: list[dict]
    input_refs: list[dict]


@dataclass(frozen=True)
class RecipeExpansion:
    source: ScriptSource
    reference: dict


@dataclass(frozen=True)
class ScriptSubmission:
    task_id: str
    request_id: str | None
    payload: dict
    source: ScriptSource | None
    tool_id: str | None = None


@dataclass(frozen=True)
class WriteAccess:
    task_id: str


@dataclass(frozen=True)
class RequireActor:
    workspace_id: str | None = None


@dataclass(frozen=True)
class SourceActor:
    workspace_id: str
    member_id: str


@dataclass(frozen=True)
class InputOrigin:
    source: dict


@dataclass(frozen=True)
class ReadWorkspace:
    pass


@dataclass(frozen=True)
class ReadTask:
    workspace_id: str
    task_id: str


@dataclass(frozen=True)
class ReadDraft:
    pass


@dataclass(frozen=True)
class ReadConfiguration:
    pass


@dataclass(frozen=True)
class QueueScript:
    actor: object
    task_id: str
    operation: str
    arguments: dict


@dataclass(frozen=True)
class WakeContinuations:
    pass


class MediaScriptSubmissionUseCase:
    def __init__(self, *, request_receipts, read_version, conversations,
                 expand_recipe, completed_receipt):
        self.request_receipts = request_receipts
        self.read_version, self.conversations = read_version, conversations
        self.expand_recipe, self.completed_receipt = expand_recipe, completed_receipt

    def record(self, task, event_id, receipt):
        self.request_receipts().record_request_result(task, event_id,
            status_code=receipt.status_code, response=response_snapshot(receipt.body))
        yield WakeContinuations()

    def prepare_source(self, task_id, payload: ScriptSource):
        task, member_id = (yield WriteAccess(task_id))
        version = self.read_version(task.workspace_id, task.current_timeline_version_id) if task.current_timeline_version_id else None
        if (payload.expected_timeline_version_id, payload.expected_timeline_sequence) != (task.current_timeline_version_id, version.sequence if version else 0):
            raise ScriptSubmissionRejected(status_code=409, detail="脚本目标版本已变化")
        source = {"workspace_id": task.workspace_id, "task_id": task.id, "member_id": member_id,
            "request_id": str(payload.request_id), "expected_timeline_version_id": payload.expected_timeline_version_id,
            "expected_timeline_sequence": payload.expected_timeline_sequence}
        if payload.assistant_turn_id is not None:
            try:
                origin = self.conversations.task_turn_source(str(payload.assistant_turn_id), task.workspace_id, member_id, task.id)
            except ValueError as exc:
                raise ScriptSubmissionRejected(status_code=409, detail=str(exc)) from exc
            snapshot = origin["context"].get("target_snapshot", {})
            if (snapshot.get("timeline_version_id"), snapshot.get("timeline_sequence")) != (task.current_timeline_version_id, version.sequence if version else 0):
                raise ScriptSubmissionRejected(status_code=409, detail="原助手目标版本已失效")
            source.update(assistant_turn_id=str(payload.assistant_turn_id), conversation_id=origin["conversation_id"], raw_command=origin["raw_command"],
                          **{key: origin[key] for key in ("input_id", "input_turn_id") if key in origin})
        yield InputOrigin(source)
        actor = yield SourceActor(task.workspace_id, member_id)
        if actor is not None:
            source["actor_identity"] = actor.identity_payload()
        if payload.assistant_turn_id is not None:
            slot = self.conversations.completed_goal_source(str(payload.assistant_turn_id),
                requested_outputs=payload.requested_outputs,
                input_refs=payload.input_refs)
            if slot is not None:
                actor = yield RequireActor(task.workspace_id)
                source["reused_completed_source"] = self.completed_receipt(task, actor, str(payload.assistant_turn_id), slot,
                    contract=payload.requested_outputs[0],
                    input_refs=payload.input_refs)
        return source

    def submit(self, command: ScriptSubmission):
        task_id, payload, tool_id = command.task_id, command, command.tool_id
        operation = 'media_script_tool_preview' if tool_id is not None else 'media_script_preview'
        edit_request_event = None
        if payload.request_id is not None:
            task, member_id = (yield WriteAccess(task_id))
            try:
                edit_request_event, fresh = self.request_receipts().claim_request(task, member_id, payload.request_id, 'script',
                    {**payload.payload, **({'tool_id': tool_id} if tool_id is not None else {})})
            except EditPreviewRejected as exc:
                raise ScriptSubmissionRejected(exc.status_code, exc.detail) from exc
            if not fresh:
                result = self.request_receipts().read_request_result(task, edit_request_event)
                if result is not None:
                    return SubmissionReceipt.create(result["status_code"], result["response"])
                raise ScriptSubmissionRejected(status_code=409, detail='原预览请求结果尚未确定，请查询原请求，不重复执行')
        try:
            script_payload = payload.source
            if tool_id is not None:
                actor = (yield RequireActor())
                try:
                    expanded = self.expand_recipe(actor, tool_id, payload.payload)
                    script_payload = expanded.source
                except (KeyError, PermissionError, ScriptToolError) as exc:
                    raise tool_rejection(exc) from exc
            typed_source = (yield from self.prepare_source(task_id, script_payload))
            if typed_source.get('reused_completed_source') is not None:
                result = SubmissionReceipt.create(200, typed_source['reused_completed_source'])
                if edit_request_event:
                    (yield from self.record(task, edit_request_event, result))
                return result
        except ScriptSubmissionRejected as exc:
            if edit_request_event:
                (yield from self.record(task, edit_request_event, SubmissionReceipt.create(exc.status_code, {'detail': exc.detail})))
            raise
        try:
            if tool_id is not None:
                actor = (yield RequireActor())
                try:
                    self.expand_recipe(actor, tool_id, payload.payload)
                except (KeyError, PermissionError, ScriptToolError) as exc:
                    raise tool_rejection(exc) from exc
            try:
                (yield ReadConfiguration())
            except MediaScriptError as exc:
                raise ScriptSubmissionRejected(status_code=503, detail=str(exc)) from exc
        except ScriptSubmissionRejected as exc:
            if edit_request_event:
                (yield from self.record(task, edit_request_event, SubmissionReceipt.create(exc.status_code, {'detail': exc.detail})))
            raise
        workspace_id = yield ReadWorkspace()
        actor = (yield RequireActor(workspace_id))
        task = (yield ReadTask(workspace_id, task_id))
        if task.owner_user_id and task.owner_user_id != (actor.user_id or actor.member_id):
            raise ScriptSubmissionRejected(status_code=403, detail='当前成员不能安排此任务的媒体处理')
        values = {'_media_kind': 'media_script', '_draft': (yield ReadDraft()),
                  '_typed_source': typed_source}
        toolchain, _budget = (yield ReadConfiguration())
        values['_script_toolchain'] = {key: str(value) if isinstance(value, Path) else value for key, value in asdict(toolchain).items()}
        if tool_id is not None:
            values['_recipe_ref'] = self.expand_recipe(actor, tool_id, payload.payload).reference
        try:
            if tool_id is not None:
                values['tool_id'] = tool_id
            values['payload'] = payload.payload
            receipt = yield QueueScript(actor, task_id, operation, values)
            if edit_request_event:
                (yield from self.record(task, edit_request_event, SubmissionReceipt.create(202, receipt)))
            receipt['result'] = None
            return SubmissionReceipt.create(202, receipt)
        except PermissionError as exc:
            raise ScriptSubmissionRejected(status_code=403, detail=str(exc)) from exc
        except (ValueError, KeyError) as exc:
            raise ScriptSubmissionRejected(status_code=422, detail=str(exc)) from exc
