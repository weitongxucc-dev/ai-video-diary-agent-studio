"""Verify a storyboard tool receipt against its original input and durable job.

Transport identity/access checks remain between the named stages. This owner
only reads; completing the Conversation remains the caller's transaction.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable
from urllib.parse import quote
from uuid import NAMESPACE_URL, UUID, uuid5

from app.creation.execution_control import ExecutionControlRejected


class StoryboardResultRejected(Exception):
    def __init__(self, detail: str, *, status_code: int = 502):
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


@dataclass(frozen=True)
class StoryboardReference:
    workspace_id: str
    task_id: str
    turn_id: str
    member_id: str
    origin: dict
    kind: str
    action: str
    reported: dict | None
    request_id: UUID
    query_url: str


@dataclass(frozen=True)
class UnknownStoryboardReference:
    reference: StoryboardReference


@dataclass(frozen=True)
class StoryboardSuccessorCheck:
    reference: StoryboardReference
    task: object
    version: object
    snapshot: dict
    base: tuple


@dataclass(frozen=True)
class AssistantStoryboardResultBinding:
    result_source: Callable
    current_source: Callable
    read_original: Callable
    read_start_event: Callable
    read_task: Callable
    read_version: Callable
    edited_execution_version: Callable

    def source(self, workspace_id, task_id, member_id, turn_id, details) -> dict:
        try:
            return self.result_source(turn_id, workspace_id, member_id, task_id,
                input_id=details.get("input_id"), input_turn_id=details.get("input_turn_id"))
        except ValueError as exc:
            raise StoryboardResultRejected(str(exc)) from exc

    def reference(self, workspace_id, task_id, member_id, turn_id, details, origin) -> StoryboardReference:
        try:
            current = self.current_source(turn_id, workspace_id, member_id, task_id)
        except ValueError as exc:
            raise StoryboardResultRejected("故事板回执不属于当前有效输入") from exc
        if (current.get("input_id"), current.get("input_turn_id")) != (origin.get("input_id"), origin.get("input_turn_id")):
            raise StoryboardResultRejected("故事板回执属于先前输入，未归入当前要求")
        kind, action = details.get("kind"), details.get("action")
        reported = details.get("receipt") if kind == "storyboard_regeneration_receipt" else None
        try:
            request_id = UUID(str(reported["request_id"] if reported is not None else details["request_id"]))
        except (KeyError, ValueError, TypeError) as exc:
            raise StoryboardResultRejected("故事板回执缺少有效的原请求身份") from exc
        if action not in {"submit", "query"} or action == "query" and reported is None:
            raise StoryboardResultRejected("故事板回执动作不可核验")
        query_url = f"/api/creation-tasks/{quote(task_id, safe='')}/montage/plan/requests/{request_id}"
        if reported is not None and reported.get("task_id") != task_id or reported is None and details.get("query_url") != query_url:
            raise StoryboardResultRejected("故事板原请求定位不属于当前任务")
        if action == "submit":
            call_id = details.get("tool_call_id")
            if (not isinstance(call_id, str) or not 1 <= len(call_id) <= 128 or request_id != uuid5(NAMESPACE_URL,
                    json.dumps([task_id, turn_id, call_id], ensure_ascii=False, separators=(",", ":")))):
                raise StoryboardResultRejected("故事板原请求与本轮实际工具调用不一致")
        return StoryboardReference(workspace_id, task_id, turn_id, member_id, origin,
                                   kind, action, reported, request_id, query_url)

    def read(self, reference: StoryboardReference, actor) -> dict | UnknownStoryboardReference:
        try:
            return self.read_original(reference.workspace_id, reference.task_id, str(reference.request_id), actor)
        except ExecutionControlRejected as exc:
            if exc.status_code != 404 or reference.reported is not None:
                raise StoryboardResultRejected(exc.detail, status_code=exc.status_code) from exc
            return UnknownStoryboardReference(reference)

    def check_original(self, reference: StoryboardReference, actual: dict) -> StoryboardSuccessorCheck | None:
        if reference.reported is not None and reference.reported.get("job_id") != actual["job_id"]:
            raise StoryboardResultRejected("故事板回执与原请求的实际作业不一致")
        if reference.action == "submit":
            original = self.read_start_event(reference.workspace_id, f"event_{actual['job_id']}_start").payload["request_inputs"]
            if original.get("assistant_turn_id") != str(reference.turn_id):
                raise StoryboardResultRejected("故事板原请求不属于本轮提交")
            snapshot = reference.origin["context"]["target_snapshot"]
            base = (actual["base_timeline_version_id"], actual["base_timeline_sequence"])
            if base != (snapshot["timeline_version_id"], snapshot["timeline_sequence"]):
                task = self.read_task(reference.workspace_id, reference.task_id)
                version = self.read_version(reference.workspace_id, base[0])
                return StoryboardSuccessorCheck(reference, task, version, snapshot, base)
        return None

    def check_successor(self, pending: StoryboardSuccessorCheck, *, input_id: str | None) -> None:
        reference = pending.reference
        successor = self.edited_execution_version(pending.task, reference.member_id, reference.turn_id,
            reference.origin, pending.snapshot, pending.version, input_id=input_id)
        if successor is None or pending.base != (successor["timeline_version_id"], successor["timeline_sequence"]):
            raise StoryboardResultRejected("故事板原请求与本轮目标版本不一致")
