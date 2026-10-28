"""Home/material planning continuation over named transport checkpoints.

SDK execution, identity reads and DTO construction are answered by the adapter.
Only the existing Conversation owner commits turn results here.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable
from pydantic import ValidationError

from app.assistant.failures import AssistantUnavailable
from app.assistant.turn_control import AssistantStopped
from app.assistant.inputs import active_input, latest_consumed_input


class HomePlanTurnRejected(Exception):
    def __init__(self, status_code, detail):
        super().__init__(str(detail))
        self.status_code, self.detail = status_code, detail


@dataclass(frozen=True)
class ReadConversationOwner:
    conversation_id: str


@dataclass(frozen=True)
class ReadCurrentReader:
    workspace_id: str


@dataclass(frozen=True)
class RunPlanning:
    pass


@dataclass(frozen=True)
class ContinuePlanning:
    latest_request: dict


@dataclass(frozen=True)
class ReadOwnedRequest:
    request_id: str


@dataclass(frozen=True)
class SafeFailureText:
    text: str


@dataclass(frozen=True)
class ForgetTurnControl:
    pass


@dataclass(frozen=True)
class BuildStoppedResponse:
    assistant_text: str
    saved_goal: dict | None


@dataclass(frozen=True)
class NormalizeMaterial:
    value: object


@dataclass(frozen=True)
class MaterialResult:
    scope: str
    asset_ref: str
    segment_ref: str | None
    response: object


@dataclass(frozen=True)
class ReadFrozenSelections:
    pass


@dataclass(frozen=True)
class RecoverSavedRequest:
    turn: dict


@dataclass(frozen=True)
class BuildCompletedResponse:
    reply: str
    request_result: dict | None
    preview: object
    material_result: MaterialResult | None


@dataclass(frozen=True)
class RenderedResponse:
    # Opaque DTO passes back to the adapter; decisions use the plain JSON only.
    response: object
    facts: dict


@dataclass(frozen=True)
class HomeTurnResult:
    rendered: RenderedResponse
    completed: bool


@dataclass(frozen=True)
class HomePlanTurnUseCase:
    get_turn: Callable
    finish_turn: Callable
    find_request_by_key: Callable

    def recover_request(self, turn: dict):
        workspace_id = yield ReadConversationOwner(turn["conversation_id"])
        current_turn = self.get_turn(turn["id"])
        latest = latest_consumed_input(current_turn["events"])
        if latest and latest["status"] == "interrupted":
            return None
        active = active_input(current_turn["events"])
        request_key = active["input_id"] if active else turn["id"]
        owner = yield ReadCurrentReader(workspace_id)
        if not owner:
            return None
        return self.find_request_by_key(workspace_id, request_key, viewer_user_id=owner)

    def execute(self, turn_id: str, message: str, *, material_selection: dict | None = None):
        preview = None
        try:
            reply, preview = yield RunPlanning()
            if isinstance(preview, dict) and preview.get("kind") == "creation_request":
                saved_request = preview["request"]
                save_only = re.search(r"只(?:先)?保存|仅保存|不(?:要|用)(?:准备|规划|出)(?:方案|计划)", message)
                if saved_request["status"] == "awaiting_plan" and not saved_request["clarification_questions"] and not save_only:
                    latest = yield ReadOwnedRequest(saved_request["id"])
                    try:
                        next_reply, next_preview = yield ContinuePlanning(latest)
                        if isinstance(next_preview, dict) and next_preview.get("kind") == "creation_request":
                            reply, preview = next_reply, next_preview
                    except AssistantUnavailable as exc:
                        reply = "制作目标已保存，当前这组方案尚未完成。" + (yield SafeFailureText(str(exc)))
        except AssistantStopped:
            saved = self.get_turn(turn_id)
            saved_goal = preview.get("request") if isinstance(preview, dict) and preview.get("kind") == "creation_request" else None
            rendered = yield BuildStoppedResponse(saved["assistant_text"] if saved else "", saved_goal)
            yield ForgetTurnControl()
            self.finish_turn(turn_id, "stopped", result={
                "kind": "creation_request" if saved_goal else "conversation",
                "creation_request_id": saved_goal["id"] if saved_goal else None,
                "response": rendered.facts})
            return HomeTurnResult(rendered, completed=False)
        except AssistantUnavailable as exc:
            yield ForgetTurnControl()
            safe_message = yield SafeFailureText(str(exc))
            self.finish_turn(turn_id, "failed", result={"kind": "failure", "message": safe_message})
            raise HomePlanTurnRejected(503, str(exc)) from exc

        material_result = None
        if isinstance(preview, dict) and preview.get("kind") == "material_result":
            try:
                material_result = yield NormalizeMaterial(preview.get("material_result"))
            except ValidationError as exc:
                yield ForgetTurnControl()
                self.finish_turn(turn_id, "failed", result={"kind": "failure", "message": "素材结果格式不可核验，未宣称保存。"})
                raise HomePlanTurnRejected(502, "素材结果格式不可核验，未宣称保存。") from exc
        if material_result is not None:
            references = yield ReadFrozenSelections()
            materials = [item["selection"] for item in references if item["kind"] == "material"]
            bound = materials[0] if len(materials) == 1 else material_selection if not materials else None
            if (bound is None or material_result.scope != bound["scope"]
                    or material_result.asset_ref != f"asset:{bound['asset_id']}"
                    or material_result.segment_ref != (f"segment:{bound['segment_id']}" if bound["scope"] == "segment" else None)):
                yield ForgetTurnControl()
                self.finish_turn(turn_id, "failed", result={"kind": "failure", "message": "素材结果没有通过当前选择核对，未宣称保存。"})
                raise HomePlanTurnRejected(502, "素材结果没有通过当前选择核对，未宣称保存。")
            preview = None
        request_result = preview.get("request") if isinstance(preview, dict) and preview.get("kind") == "creation_request" else None
        if request_result is None and material_result is None:
            request_result = yield RecoverSavedRequest(self.get_turn(turn_id))
            if request_result is not None:
                preview = None
        rendered = yield BuildCompletedResponse(reply, request_result, preview, material_result)
        facts = rendered.facts
        creation_request, plan_preview = facts["creation_request"], facts["plan_preview"]
        self.finish_turn(turn_id, "completed", facts["reply"], {
            "kind": "creation_request" if creation_request else "plan_preview" if plan_preview else "material_result" if material_result else "conversation",
            "creation_request_id": creation_request["id"] if creation_request else None,
            "plan_preview": plan_preview,
            "response": facts,
        })
        return HomeTurnResult(rendered, completed=True)
