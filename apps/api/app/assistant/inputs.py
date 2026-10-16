"""User input receipts in the existing turn event log; no second runtime queue."""
from __future__ import annotations
import json
from datetime import datetime, timezone


class AssistantInputConflict(ValueError):
    pass


def _now():
    return datetime.now(timezone.utc).isoformat()


def settle_inputs(events, reason):
    for item in events:
        if item.get("type") == "assistant_input" and item["status"] in {"accepted", "consumed"}:
            item.update(status="rejected" if item["status"] == "accepted" else "interrupted",
                        reason=reason, updated_at=_now())
    return events


def list_inputs(conn, conversation_id, workspace_id, member_id):
    rows = conn.execute("SELECT t.events_json FROM assistant_conversation_turns t JOIN assistant_conversations c ON c.id=t.conversation_id WHERE c.id=? AND c.workspace_id=? AND c.member_id=? ORDER BY t.sequence", (conversation_id, workspace_id, member_id)).fetchall()
    return [event for row in rows for event in json.loads(row["events_json"]) if event.get("type") == "assistant_input"]


def enqueue_input(conn, *, conversation_id, workspace_id, member_id, target_turn_id,
                  client_request_id, message, mode, expected_draft_version,
                  expected_reference_version, request_context):
    if mode not in {"steer", "follow_up"} or not message.strip() or not client_request_id:
        raise AssistantInputConflict("追加输入缺少文字、请求身份或有效模式。")
    chat = conn.execute("SELECT * FROM assistant_conversations WHERE id=? AND workspace_id=? AND member_id=?", (conversation_id, workspace_id, member_id)).fetchone()
    if chat is None:
        raise KeyError("对话不可用")
    binding = {"target_turn_id": target_turn_id, "message": message, "mode": mode,
               "expected_draft_version": expected_draft_version, "expected_reference_version": expected_reference_version,
               "request_context": request_context}
    for previous in list_inputs(conn, conversation_id, workspace_id, member_id):
        if previous["input_id"] == client_request_id:
            if any(previous[key] != value for key, value in binding.items()):
                raise AssistantInputConflict("此追加请求身份已用于不同文字、目标或版本。")
            return previous, False
    turn = conn.execute("SELECT * FROM assistant_conversation_turns WHERE id=? AND conversation_id=?", (target_turn_id, conversation_id)).fetchone()
    if turn is None or turn["status"] != "running":
        raise AssistantInputConflict("目标轮次已结束，请按新一轮发送；草稿仍保留。")
    if chat["draft_version"] != expected_draft_version or chat["draft_text"] != message:
        raise AssistantInputConflict("追加输入草稿已变化，请重新读取后发送。")
    selected = json.loads(chat["selected_reference_json"]) if chat["selected_reference_json"] else None
    if expected_reference_version != chat["reference_version"] or request_context.get("selected_reference") != selected:
        raise AssistantInputConflict("追加输入引用已变化，请重新选择；没有改绑目标。")
    now = _now()
    item = {"type": "assistant_input", "input_id": client_request_id, **binding,
            "status": "accepted", "created_at": now, "updated_at": now, "reason": None}
    events = json.loads(turn["events_json"])
    events.append(item)
    conn.execute("UPDATE assistant_conversation_turns SET events_json=?,updated_at=? WHERE id=?", (json.dumps(events, ensure_ascii=False), now, target_turn_id))
    conn.execute("UPDATE assistant_conversations SET draft_text='',draft_version=draft_version+1,updated_at=? WHERE id=?", (now, conversation_id))
    return item, True


def latest_consumed_input(events: list[dict]) -> dict | None:
    """Result provenance follows consumption order, including interrupted input."""
    return next((item for item in reversed(events) if item.get("type") == "assistant_input"
        and item["status"] in {"consumed", "completed", "interrupted"}),None)


def active_input(events: list[dict]) -> dict | None:
    """Interrupted input cannot restore a preceding input's write permission."""
    latest = latest_consumed_input(events)
    if latest and latest["status"] != "interrupted":
        return latest
    return None


def transition_input(conn, *, conversation_id, workspace_id, member_id, input_id, status, reason=None):
    allowed = {"withdrawn": {"accepted"}, "consumed": {"accepted"}, "rejected": {"accepted"},
               "completed": {"consumed"}, "interrupted": {"consumed"}}
    if status not in allowed:
        raise AssistantInputConflict("无效的追加输入状态")
    rows = conn.execute("SELECT t.* FROM assistant_conversation_turns t JOIN assistant_conversations c ON c.id=t.conversation_id WHERE c.id=? AND c.workspace_id=? AND c.member_id=? ORDER BY t.sequence", (conversation_id, workspace_id, member_id)).fetchall()
    for row in rows:
        events = json.loads(row["events_json"])
        for item in events:
            if item.get("type") != "assistant_input" or item["input_id"] != input_id:
                continue
            if item["status"] == status:
                return item
            if item["status"] not in allowed[status] or (status == "consumed" and row["status"] != "running"):
                raise AssistantInputConflict("追加输入已处理，不能撤回或重复消费。")
            item.update(status=status, reason=reason, updated_at=_now())
            conn.execute("UPDATE assistant_conversation_turns SET events_json=?,updated_at=? WHERE id=?", (json.dumps(events, ensure_ascii=False), item["updated_at"], row["id"]))
            return item
    raise KeyError("追加输入不可用")
