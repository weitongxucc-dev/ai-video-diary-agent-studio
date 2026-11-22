#!/usr/bin/env python3
"""Replay the OpenFilm fixture through projection and durable conversation facts.

This is a replay-only acceptance entry. It does not contact a model provider or
claim a successful real conversation.
"""
from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path


API_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = API_ROOT.parent.parent
FIXTURE_PATH = REPOSITORY_ROOT / "docs/reference/openfilm-chat/replay-fixture.json"
sys.path.insert(0, str(API_ROOT))

from app.assistant.projection import _PiStreamProjector, public_history_turn  # noqa: E402
from app.assistant.stream_response import AssistantStreamTextSanitizer  # noqa: E402
from app.conversation_repository import ConversationRepository  # noqa: E402
from app.schemas import AssistantStreamEvent  # noqa: E402


def assistant_message(text: str) -> dict:
    return {"role": "assistant", "content": [{"type": "text", "text": text}]}


def raw_events(fixture: dict) -> list[tuple[int, int, dict]]:
    result: list[tuple[int, int, dict]] = []
    for index, item in enumerate(fixture["events"]):
        event_type = item["type"]
        timestamp = item.get("t", 0)
        if event_type == "agent_start" or event_type == "agent_end":
            result.append((timestamp, index, {"type": event_type, **({"willRetry": False} if event_type == "agent_end" else {})}))
        elif event_type == "assistant_text":
            result.append((timestamp, index, {"type": "message_end", "message": assistant_message(item["text"])}))
        elif event_type == "tool_start":
            tool_name = "read" if item["kind"] == "read_file" else "bash"
            result.append((timestamp, index, {"type": "tool_execution_start", "toolCallId": item["id"], "toolName": tool_name, "args": {}}))
        elif event_type == "tool_end":
            tool_name = "read" if next(event for event in fixture["events"] if event.get("id") == item["id"])["kind"] == "read_file" else "bash"
            result.append((timestamp, index, {"type": "tool_execution_end", "toolCallId": item["id"],
                                               "toolName": tool_name, "isError": item.get("status") == "error",
                                               "result": {"details": {}}}))
    return sorted(result, key=lambda value: (value[0], value[1]))


def project_fixture(fixture: dict) -> list[dict]:
    projector = _PiStreamProjector()
    sanitizer = AssistantStreamTextSanitizer("replay", known_secrets=[], safe_text=lambda text, _: text)
    events = []
    for timestamp, _, raw in raw_events(fixture):
        projected = projector.project(json.dumps({**raw, "timestamp": timestamp}))
        if projected is None:
            continue
        safe = sanitizer.activity_event(projected)
        if safe is not None:
            events.append(AssistantStreamEvent.model_validate(safe).model_dump(mode="json", exclude_none=True))
    return events


def replay_facts(events: list[dict], fixture: dict) -> dict:
    starts = {event["tool_id"]: datetime.fromisoformat(event["at"]).timestamp() * 1000
              for event in events if event["type"] == "tool_start"}
    checkpoint_facts = {}
    for checkpoint in fixture["checkpoints"]:
        at_ms = checkpoint["at_ms"]
        observed = [event for event in events if event.get("at") and datetime.fromisoformat(event["at"]).timestamp() * 1000 <= at_ms]
        observed_starts = {event["tool_id"] for event in observed if event["type"] == "tool_start"}
        observed_ends = {event["tool_id"] for event in observed if event["type"] == "tool_end"}
        active = {tool_id: at_ms - starts[tool_id] for tool_id in observed_starts - observed_ends}
        checkpoint_facts[checkpoint["id"]] = {"observed_events": len(observed), "active_tools_ms": active}
    return checkpoint_facts


def persistence_facts(events: list[dict]) -> dict:
    with tempfile.TemporaryDirectory(prefix="yaohe-openfilm-replay-") as directory:
        repo = ConversationRepository(Path(directory) / "conversation.sqlite")
        chat = repo.create("replay-workspace", "replay-member", "home", "home")
        draft = repo.save_draft(chat["id"], "replay-workspace", "replay-member", "OpenFilm 回放", 0)
        turn, created = repo.create_turn_idempotent(chat["id"], "replay-workspace", "replay-member",
                                                    "OpenFilm 回放", "replay-request", draft["draft_version"])
        if not created or turn is None:
            raise RuntimeError("无法创建回放轮次")
        for event in events:
            repo.append_event(turn["id"], event)
        repo.finish_turn(turn["id"], "completed", result={"kind": "replay_only"})
        saved = repo.get_turn(turn["id"])
        if saved is None:
            raise RuntimeError("回放轮次没有持久化")
        reopened = public_history_turn(saved, lambda text: text)
        if reopened["events"] != events:
            raise RuntimeError("持久化回读事件与回放事件不一致")
        return {"round_trip": True, "event_count": len(events), "turn_status": saved["status"]}


def stop_and_input_facts() -> dict:
    with tempfile.TemporaryDirectory(prefix="yaohe-openfilm-state-") as directory:
        repo = ConversationRepository(Path(directory) / "state.sqlite")
        stop_chat = repo.create("workspace", "member", "home", "home")
        draft = repo.save_draft(stop_chat["id"], "workspace", "member", "停止回放", 0)
        stop_turn, _ = repo.create_turn_idempotent(stop_chat["id"], "workspace", "member", "停止回放", "stop", draft["draft_version"])
        repo.append_event(stop_turn["id"], {"type": "tool_start", "tool_id": 1, "label": "执行任务程序"})
        repo.finish_turn(stop_turn["id"], "stopped")
        saved_stop = repo.get_turn(stop_turn["id"])

        input_chat = repo.create("workspace", "member", "task", "task-replay")
        draft = repo.save_draft(input_chat["id"], "workspace", "member", "原始要求", 0)
        input_turn, _ = repo.create_turn_idempotent(input_chat["id"], "workspace", "member", "原始要求", "input-turn", draft["draft_version"], {"kind": "task", "task_id": "task-replay"})
        current = repo.get(input_chat["id"], "workspace", "member")
        draft = repo.save_draft(input_chat["id"], "workspace", "member", "追加要求", current["draft_version"])
        receipt, _ = repo.enqueue_input(input_chat["id"], "workspace", "member", target_turn_id=input_turn["id"],
                                        client_request_id="input-1", message="追加要求", mode="steer",
                                        expected_draft_version=draft["draft_version"], expected_reference_version=0,
                                        request_context={"kind": "task", "task_id": "task-replay", "selected_reference": None})
        repo.transition_input(input_chat["id"], "workspace", "member", "input-1", "consumed")
        repo.transition_input(input_chat["id"], "workspace", "member", "input-1", "completed")
        saved_input = repo.list_inputs(input_chat["id"], "workspace", "member")[0]
        return {"stopped_turn": saved_stop["status"], "stopped_tool_preserved": saved_stop["events"][0]["type"] == "tool_start",
                "assistant_input": {"accepted": receipt["status"], "final": saved_input["status"], "input_id": saved_input["input_id"]}}


def main() -> None:
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    events = project_fixture(fixture)
    persisted = persistence_facts(events)
    states = stop_and_input_facts()
    print(json.dumps({"mode": "replay_only", "fixture": fixture["meta"]["name"],
                      "event_order": [event["type"] for event in events],
                      "checkpoint_facts": replay_facts(events, fixture),
                      "persistence": persisted, "stop_and_input": states,
                      "provider_contacted": False}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
