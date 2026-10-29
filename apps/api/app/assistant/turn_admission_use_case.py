"""Conversation turn admission, frozen inputs and original-request replay.

Named checkpoints leave identity and transport reads to the caller. Conversation
CAS, receipts and input transitions remain with the existing durable owner.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from types import SimpleNamespace

from app.assistant.references import AssistantReferenceConflict, timeline_object_refs
from app.conversation_repository import (
    ConversationDraftVersionConflict, ConversationReferenceVersionConflict,
    ConversationTurnAlreadyRunning, ConversationTurnRequestConflict,
)


class TurnRejected(Exception):
    def __init__(self, status_code, detail):
        super().__init__(str(detail))
        self.status_code, self.detail = status_code, detail


@dataclass(frozen=True)
class ReadWorkspace:
    pass


@dataclass(frozen=True)
class ReadConversationOwner:
    conversation_id: str


@dataclass(frozen=True)
class CleanText:
    value: str
    workspace_id: str


@dataclass(frozen=True)
class ValidateReference:
    reference: dict | None


@dataclass(frozen=True)
class ProfileSnapshot:
    kind: str
    profile_id: str
    version: str


@dataclass(frozen=True)
class KnowledgeSnapshot:
    knowledge_item_id: str
    version: str
    locator: str | None


@dataclass(frozen=True)
class AccessTask:
    task_id: str
    write: bool = False


@dataclass(frozen=True)
class ReadSubtitleTracks:
    workspace_id: str
    task_id: str


@dataclass(frozen=True)
class ResolveTurnScope:
    workspace_id: str
    task: object
    selection: object


@dataclass(frozen=True)
class ReadTimelineForTask:
    workspace_id: str
    task_id: str
    version_id: str


@dataclass(frozen=True)
class ReadTaskClipMaterial:
    task: object
    selected_refs: list[str]


@dataclass(frozen=True)
class ReadMaterialSnapshot:
    selection: dict


@dataclass(frozen=True)
class RegisterControl:
    turn_id: str


@dataclass(frozen=True)
class EmitTurnEvent:
    event: dict


@dataclass(frozen=True)
class RenderReplay:
    value: dict


@dataclass(frozen=True)
class RenderRunningTurn:
    reply: str
    provider: str
    model: str
    turn_id: str
    draft_version: int


@dataclass(frozen=True)
class RequestGenerationStop:
    workspace_id: str
    task_id: str
    request_id: str
    member_id: str


@dataclass(frozen=True)
class StopControl:
    turn_id: str


@dataclass(frozen=True)
class InvalidateGoal:
    turn_id: str
    reason: str


class TurnAdmissionUseCase:
    def __init__(self, *, conversations_for):
        self._conversations_for = conversations_for

    def begin(self, conversation_id, scope_kind, scope_id, message,
              client_request_id, expected_draft_version, request_context,
              *, expected_reference_version=None):
        workspace_id, member_id, conversation = yield ReadConversationOwner(conversation_id)
        if (yield CleanText(message, workspace_id)) != message:
            raise TurnRejected(422, "请勿在对话中粘贴密钥或口令。")
        previous = self._conversations_for().request_context(
            str(client_request_id), conversation_id, workspace_id, member_id)
        if expected_reference_version is None:
            if any(request_context.get(key) for key in (
                    "selection", "profile_reference", "knowledge_reference", "selected_object_refs")):
                raise TurnRejected(409, "请重新读取当前选中引用后再发送。")
            reference = None
        elif previous is not None:
            reference = previous.get("selected_reference")
        else:
            if conversation["reference_version"] != expected_reference_version:
                raise TurnRejected(409, "选中引用已变化，请重新读取后再发送。")
            reference = yield ValidateReference(conversation["selected_reference"])
        if expected_reference_version is not None:
            request_context = {**request_context, "selected_reference": reference,
                               "expected_reference_version": expected_reference_version}
            target = dict(request_context.get("target_snapshot") or {})
            target["selected_references"] = (reference["items"] if reference and reference["kind"] == "selection"
                                              else [reference] if reference else [])
            request_context["target_snapshot"] = target
            if previous is not None:
                request_context["target_snapshot"] = previous.get("target_snapshot")
                if request_context.get("kind") == "task":
                    request_context["selected_object_refs"] = previous.get("selected_object_refs", [])
            elif request_context.get("kind") == "task":
                frozen = yield from self.freeze_input(request_context, reference)
                request_context["selected_object_refs"] = frozen["selected_object_refs"]
                request_context["target_snapshot"] = {**target,
                    "material_selection": frozen["target_snapshot"].get("material_selection")}
        try:
            turn, created = self._conversations_for().create_turn_idempotent(
                conversation_id, workspace_id, member_id, message, str(client_request_id),
                expected_draft_version, request_context,
                expected_reference_version=expected_reference_version)
        except (ConversationReferenceVersionConflict, ConversationTurnAlreadyRunning,
                ConversationTurnRequestConflict) as exc:
            raise TurnRejected(409, str(exc)) from exc
        except ConversationDraftVersionConflict as exc:
            raise TurnRejected(409, {"error": "assistant_draft_conflict",
                "message": "发送前草稿版本或正文已变化；较新的草稿已保留，请重新读取后再发送。",
                "current_draft_version": exc.current["draft_version"]}) from exc
        if turn is None:
            raise TurnRejected(404, "对话不存在或当前成员无权访问。")
        if created:
            yield RegisterControl(turn["id"])
            if conversation["title"] == "新对话":
                self._conversations_for().update_title(conversation_id, workspace_id, member_id, message[:36])
        yield EmitTurnEvent({"type": "turn_started", "turn_id": turn["id"], "turn_state": turn["status"],
                            "draft_version": expected_draft_version + 1})
        if not created and turn["status"] != "running":
            yield EmitTurnEvent({"type": "turn_status", "turn_id": turn["id"], "turn_state": turn["status"]})
        return turn["id"], created, turn

    def freeze_input(self, context, reference):
        items = reference["items"] if reference and reference["kind"] == "selection" else [reference] if reference else []
        workspace_id = yield ReadWorkspace()
        snapshot = {"selected_references": items}
        context = {**context, "selected_reference": reference}
        profiles = [item for item in items if item["kind"] in {"person", "voice"}]
        knowledge = [item for item in items if item["kind"] == "knowledge"]
        profile = profiles[0] if len(profiles) == 1 else None
        knowledge_item = knowledge[0] if len(knowledge) == 1 else None
        context["profile_reference"] = ({key: profile[key] for key in ("kind", "profile_id", "version")} if profile else None)
        context["knowledge_reference"] = ({key: knowledge_item[key] for key in ("knowledge_item_id", "version", "locator")
                                           if key in knowledge_item} if knowledge_item else None)
        snapshot["profile_reference"] = ((yield ProfileSnapshot(profile["kind"], profile["profile_id"], profile["version"])) if profile else None)
        snapshot["knowledge_reference"] = ((yield KnowledgeSnapshot(knowledge_item["knowledge_item_id"], knowledge_item["version"], knowledge_item.get("locator"))) if knowledge_item else None)
        if context["kind"] == "task":
            task, _member_id = yield AccessTask(context["task_id"])
            tracks = yield ReadSubtitleTracks(workspace_id, task.id)
            try:
                refs = timeline_object_refs(items, task.id, subtitle_tracks=tracks)
            except AssistantReferenceConflict as exc:
                raise TurnRejected(409, str(exc)) from exc
            stage, refs = yield ResolveTurnScope(workspace_id, task, SimpleNamespace(
                context_stage=context["stage"], selected_object_refs=refs, playhead_ms=None))
            version = ((yield ReadTimelineForTask(workspace_id, task.id, task.current_timeline_version_id))
                       if task.current_timeline_version_id else None)
            context.update(stage=stage, selected_object_refs=refs, playhead_ms=None,
                           expected_timeline_version_id=task.current_timeline_version_id,
                           expected_timeline_sequence=version.sequence if version else 0)
            snapshot.update(timeline_version_id=task.current_timeline_version_id,
                            timeline_sequence=version.sequence if version else 0,
                            material_selection=(yield ReadTaskClipMaterial(task, refs)))
        elif context["kind"] == "material":
            materials = [item for item in items if item["kind"] == "material"]
            selection = materials[0]["selection"] if len(materials) == 1 else None
            context["selection"] = selection
            if selection:
                selection, _asset = yield ReadMaterialSnapshot(selection)
                snapshot.update(selection)
        context["target_snapshot"] = snapshot
        return context

    def verify_input(self, item, *, conversation_id, owner, turn_id):
        try:
            current_workspace, current_member, _current = yield ReadConversationOwner(conversation_id)
            if (current_workspace, current_member) != owner:
                raise PermissionError("当前成员或工作区已变化，请重新发送。")
            frozen = item["request_context"]
            reference = yield ValidateReference(frozen.get("selected_reference"))
            fresh = yield from self.freeze_input(frozen, reference)
            snapshot = fresh["target_snapshot"]
            if frozen["kind"] == "task" and any(snapshot.get(key) != frozen["target_snapshot"].get(key)
                    for key in ("timeline_version_id", "timeline_sequence")):
                raise ValueError("接收追加输入后任务版本发生变化，请重新查看对象后发送；未改绑新版。")
        except TurnRejected as exc:
            raise ValueError(str(exc.detail)) from exc
        material = snapshot.get("material_selection") if fresh["kind"] == "task" else fresh.get("selection")
        delta = {
            "INPUT_ID": item["input_id"], "INPUT_TURN_ID": turn_id,
            "USER_INSTRUCTION": item["message"], "SELECTED_REFS": json.dumps(fresh.get("selected_object_refs", [])),
            "SELECTED_REFERENCES": json.dumps(snapshot.get("selected_references", []), ensure_ascii=False),
            "PLAYHEAD_MS": "", "MATERIAL_SELECTION": json.dumps(material) if material else "",
            "PROFILE_REFERENCE": json.dumps(snapshot["profile_reference"]) if snapshot.get("profile_reference") else "",
            "KNOWLEDGE_REFERENCE": json.dumps(snapshot["knowledge_reference"]) if snapshot.get("knowledge_reference") else "",
            "TASK_VERSION": json.dumps({key: snapshot[key] for key in ("timeline_version_id", "timeline_sequence")}) if fresh["kind"] == "task" else "",
            "TASK_READ_READY": "", "SCRIPT_DRAFT_UPDATED_AT": "", "EDIT_CAPABILITIES": "",
            "MEDIA_SCRIPT_CAPABILITIES": "", "SCRIPT_TOOL_CAPABILITIES": "",
        }
        return {"environment": {f"AI_VIDEO_DIARY_AGENT_{key}": value for key, value in delta.items()},
                "frozen_context": {"kind": fresh["kind"], "task_id": fresh.get("task_id"),
                    "selected_object_refs": fresh.get("selected_object_refs", []), "target_snapshot": snapshot}}

    def replay(self, turn_id, *, provider, model, draft_version):
        turn = self._conversations_for().get_turn(turn_id)
        if turn is None:
            raise TurnRejected(404, "对话轮次不存在。")
        if turn["status"] == "failed":
            failure = turn.get("result") or {}
            raise TurnRejected(503, failure.get("message", "上一轮已失败，请检查后明确重试。"))
        if turn["status"] in {"completed", "stopped"}:
            stored = turn.get("result")
            response = stored.get("response") if isinstance(stored, dict) else None
            if not isinstance(response, dict):
                raise TurnRejected(500, "已持久化轮次缺少原始结果，未重复执行工具。")
            return (yield RenderReplay(response))
        return (yield RenderRunningTurn(turn["assistant_text"], provider, model, turn_id, draft_version))

    def stop(self, conversation_id, turn_id):
        workspace_id, member_id, _row = yield ReadConversationOwner(conversation_id)
        turn = self._conversations_for().get_turn(turn_id)
        if turn is None or turn["conversation_id"] != conversation_id:
            raise TurnRejected(404, "对话轮次不存在。")
        if turn["status"] != "running":
            raise TurnRejected(409, "这一轮已经结束，未重复执行停止。")
        context = self._conversations_for().consumed_turn_request_context(turn_id, conversation_id, workspace_id, member_id)
        if context.get("kind") == "script_generation":
            try:
                yield RequestGenerationStop(workspace_id, context["task_id"], context["generation_request_id"], member_id)
            except KeyError as exc:
                raise TurnRejected(404, "原生成请求不存在") from exc
            except PermissionError as exc:
                raise TurnRejected(403, str(exc)) from exc
            except ValueError as exc:
                raise TurnRejected(409, str(exc)) from exc
            yield StopControl(turn_id)
            return {"turn_id": turn_id, "status": "stopping"}
        if not (yield StopControl(turn_id)):
            raise TurnRejected(409, "这一轮已结束或没有可停止的助手进程。")
        yield InvalidateGoal(turn_id, "user_stopped")
        return {"turn_id": turn_id, "status": "stopping"}
