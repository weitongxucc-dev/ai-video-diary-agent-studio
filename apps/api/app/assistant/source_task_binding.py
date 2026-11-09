"""Bind source adoption to a real consumed home input, rechecked in its writer."""
import hashlib
import json

from app.creation.intent_use_case import CreationIntentUseCase
from app.creation.source_task_use_case import SourceTaskConflict
from app.creation.request_workflow_use_case import current_input, require_running_turn


class SourceTaskBinding:
    def __init__(self, conversations):
        self.conversations = conversations

    def _source(self, conn, turn_id, input_id, actor, request_id):
        member_id = actor.user_id or actor.member_id
        row = conn.execute(
            "SELECT t.*,r.request_context_json FROM assistant_conversation_turns t "
            "JOIN assistant_conversation_request_receipts r ON r.turn_id=t.id "
            "WHERE t.id=? AND r.workspace_id=? AND r.member_id=? AND r.outcome='consumed'",
            (turn_id, actor.workspace_id, member_id)).fetchone()
        if row is None:
            raise SourceTaskConflict("没有当前成员已消费的原片创作输入")
        turn = dict(row)
        turn["events"] = json.loads(turn["events_json"])
        require_running_turn(turn)
        _, message, active = current_input(turn, input_id)
        context = active["request_context"] if active else json.loads(row["request_context_json"])
        if context.get("kind") != "home":
            raise SourceTaskConflict("已有任务的修改必须沿原任务执行，不能另建原片任务")
        latest = conn.execute("SELECT id FROM assistant_conversation_turns WHERE conversation_id=? "
            "AND user_message!='' ORDER BY sequence DESC LIMIT 1", (row["conversation_id"],)).fetchone()
        if latest is None or latest["id"] != turn_id:
            raise SourceTaskConflict("用户输入已经变化，没有沿旧输入创建作品")
        try:
            CreationIntentUseCase.reject_discussion_only_input(message)
        except ValueError as exc:
            raise SourceTaskConflict(str(exc)) from exc
        return {"kind": "conversation_input", "workspace_id": actor.workspace_id, "member_id": member_id,
            "conversation_id": row["conversation_id"], "turn_id": turn_id,
            "input_id": input_id, "request_id": request_id,
            "selected_asset_id": (context.get("target_snapshot", {}).get("material_selection") or {}).get("asset_id"),
            "message_digest": hashlib.sha256(message.encode()).hexdigest()}

    def prepare(self, turn_id, input_id, actor, request_id, payload):
        with self.conversations._connect() as conn:
            source = self._source(conn, turn_id, input_id, actor, request_id)
        try:
            self.conversations.bind_source_task_input(source, payload,
                verify_source=lambda conn: self._source(conn, turn_id, input_id, actor, request_id))
        except ValueError as exc:
            raise SourceTaskConflict(str(exc)) from exc
        def guard(conn):
            if self._source(conn, turn_id, input_id, actor, request_id) != source:
                raise SourceTaskConflict("原片创作输入在采用前已变化")
            original = self.conversations.read_source_task_input(actor.workspace_id, actor.user_id or actor.member_id,
                request_id, connection=conn)
            if original != {"source": source, "payload": payload}:
                raise SourceTaskConflict("原片请求的固定输入已经变化")
        return source, guard
