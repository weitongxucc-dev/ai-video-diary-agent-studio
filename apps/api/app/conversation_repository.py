from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ConversationTurnAlreadyRunning(RuntimeError):
    pass


class ConversationDraftVersionConflict(RuntimeError):
    def __init__(self, current: dict) -> None:
        self.current = current
        super().__init__("对话草稿已在其他位置更新，请重新读取后再编辑。")


class ConversationTurnRequestConflict(RuntimeError):
    pass


class ConversationRepository:
    """Persist app conversations and turns in the existing application SQLite DB."""

    def __init__(self, database_path: str | Path, *, initialize: bool = True) -> None:
        self.database_path = Path(database_path).expanduser()
        if initialize:
            self.initialize()

    @staticmethod
    def _validate_draft_request(client_request_id: str, expected_draft_version: int) -> None:
        if not client_request_id or len(client_request_id) > 128:
            raise ValueError("client_request_id must contain 1..128 characters")
        if expected_draft_version < 0:
            raise ValueError("expected_draft_version must be non-negative")

    def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS assistant_conversations (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    member_id TEXT NOT NULL,
                    scope_kind TEXT NOT NULL CHECK(scope_kind IN ('home','material','task')),
                    scope_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    draft_text TEXT NOT NULL DEFAULT '',
                    draft_version INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_assistant_conversations_scope
                    ON assistant_conversations(workspace_id, member_id, scope_kind, scope_id, updated_at DESC);
                CREATE TABLE IF NOT EXISTS assistant_conversation_current (
                    workspace_id TEXT NOT NULL,
                    member_id TEXT NOT NULL,
                    scope_kind TEXT NOT NULL CHECK(scope_kind IN ('home','material','task')),
                    scope_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL REFERENCES assistant_conversations(id) ON DELETE CASCADE,
                    selected_at TEXT NOT NULL,
                    PRIMARY KEY(workspace_id, member_id, scope_kind, scope_id)
                );
                CREATE TABLE IF NOT EXISTS assistant_conversation_turns (
                    id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL REFERENCES assistant_conversations(id) ON DELETE CASCADE,
                    sequence INTEGER NOT NULL,
                    user_message TEXT NOT NULL,
                    assistant_text TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL CHECK(status IN ('running','completed','failed','stopped')),
                    events_json TEXT NOT NULL DEFAULT '[]',
                    result_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(conversation_id, sequence)
                );
                CREATE INDEX IF NOT EXISTS idx_assistant_turns_conversation
                    ON assistant_conversation_turns(conversation_id, sequence ASC);
                CREATE TABLE IF NOT EXISTS assistant_conversation_request_receipts (
                    client_request_id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL REFERENCES assistant_conversations(id) ON DELETE CASCADE,
                    workspace_id TEXT NOT NULL,
                    member_id TEXT NOT NULL,
                    scope_kind TEXT NOT NULL CHECK(scope_kind IN ('home','material','task')),
                    scope_id TEXT NOT NULL,
                    user_message TEXT NOT NULL,
                    expected_draft_version INTEGER NOT NULL,
                    request_context_json TEXT NOT NULL DEFAULT '{}',
                    outcome TEXT NOT NULL CHECK(outcome IN ('consumed','recovered')),
                    turn_id TEXT UNIQUE REFERENCES assistant_conversation_turns(id) ON DELETE CASCADE,
                    created_at TEXT NOT NULL,
                    CHECK((outcome='consumed' AND turn_id IS NOT NULL) OR (outcome='recovered' AND turn_id IS NULL))
                );
            """)
            conversation_columns = {row["name"] for row in conn.execute("PRAGMA table_info(assistant_conversations)")}
            if "draft_version" not in conversation_columns:
                conn.execute("ALTER TABLE assistant_conversations ADD COLUMN draft_version INTEGER NOT NULL DEFAULT 0")
            receipt_columns = {row["name"] for row in conn.execute("PRAGMA table_info(assistant_conversation_request_receipts)")}
            if "request_context_json" not in receipt_columns:
                conn.execute("ALTER TABLE assistant_conversation_request_receipts ADD COLUMN request_context_json TEXT NOT NULL DEFAULT '{}'")

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @staticmethod
    def _conversation(row: sqlite3.Row) -> dict:
        return dict(row)

    def create(self, workspace_id: str, member_id: str, scope_kind: str, scope_id: str,
               title: str = "新对话") -> dict:
        conversation_id = str(uuid4())
        now = _now()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO assistant_conversations "
                "(id,workspace_id,member_id,scope_kind,scope_id,title,draft_text,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?, '',?,?)",
                (conversation_id, workspace_id, member_id, scope_kind, scope_id, title, now, now),
            )
            row = conn.execute("SELECT * FROM assistant_conversations WHERE id=?", (conversation_id,)).fetchone()
            conn.execute(
                "INSERT INTO assistant_conversation_current "
                "(workspace_id,member_id,scope_kind,scope_id,conversation_id,selected_at) VALUES(?,?,?,?,?,?) "
                "ON CONFLICT(workspace_id,member_id,scope_kind,scope_id) DO UPDATE SET "
                "conversation_id=excluded.conversation_id,selected_at=excluded.selected_at",
                (workspace_id, member_id, scope_kind, scope_id, conversation_id, now),
            )
        assert row is not None
        return self._conversation(row)

    def current_or_create(self, workspace_id: str, member_id: str, scope_kind: str, scope_id: str) -> dict:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT c.* FROM assistant_conversation_current current "
                "JOIN assistant_conversations c ON c.id=current.conversation_id "
                "WHERE current.workspace_id=? AND current.member_id=? "
                "AND current.scope_kind=? AND current.scope_id=? "
                "AND c.workspace_id=current.workspace_id AND c.member_id=current.member_id "
                "AND c.scope_kind=current.scope_kind AND c.scope_id=current.scope_id",
                (workspace_id, member_id, scope_kind, scope_id),
            ).fetchone()
            if row is not None:
                return self._conversation(row)
            row = conn.execute(
                "SELECT * FROM assistant_conversations WHERE workspace_id=? AND member_id=? "
                "AND scope_kind=? AND scope_id=? ORDER BY updated_at DESC, created_at DESC LIMIT 1",
                (workspace_id, member_id, scope_kind, scope_id),
            ).fetchone()
            if row is not None:
                now = _now()
                conn.execute(
                    "INSERT INTO assistant_conversation_current "
                    "(workspace_id,member_id,scope_kind,scope_id,conversation_id,selected_at) VALUES(?,?,?,?,?,?) "
                    "ON CONFLICT(workspace_id,member_id,scope_kind,scope_id) DO UPDATE SET "
                    "conversation_id=excluded.conversation_id,selected_at=excluded.selected_at",
                    (workspace_id, member_id, scope_kind, scope_id, row["id"], now),
                )
                return self._conversation(row)
            conversation_id = str(uuid4())
            now = _now()
            conn.execute(
                "INSERT INTO assistant_conversations "
                "(id,workspace_id,member_id,scope_kind,scope_id,title,draft_text,created_at,updated_at) "
                "VALUES(?,?,?,?,?,'新对话','',?,?)",
                (conversation_id, workspace_id, member_id, scope_kind, scope_id, now, now),
            )
            conn.execute(
                "INSERT INTO assistant_conversation_current "
                "(workspace_id,member_id,scope_kind,scope_id,conversation_id,selected_at) VALUES(?,?,?,?,?,?) "
                "ON CONFLICT(workspace_id,member_id,scope_kind,scope_id) DO UPDATE SET "
                "conversation_id=excluded.conversation_id,selected_at=excluded.selected_at",
                (workspace_id, member_id, scope_kind, scope_id, conversation_id, now),
            )
            created = conn.execute("SELECT * FROM assistant_conversations WHERE id=?", (conversation_id,)).fetchone()
        assert created is not None
        return self._conversation(created)

    def list_for_scope(self, workspace_id: str, member_id: str, scope_kind: str, scope_id: str) -> list[dict]:
        with self._connect() as conn:
            if scope_kind == "material" and scope_id == "library":
                rows = conn.execute(
                    "SELECT * FROM assistant_conversations WHERE workspace_id=? AND member_id=? "
                    "AND scope_kind='material' ORDER BY updated_at DESC, created_at DESC",
                    (workspace_id, member_id),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM assistant_conversations WHERE workspace_id=? AND member_id=? "
                    "AND scope_kind=? AND scope_id=? ORDER BY updated_at DESC, created_at DESC",
                    (workspace_id, member_id, scope_kind, scope_id),
                ).fetchall()
        return [self._conversation(row) for row in rows]

    def get(self, conversation_id: str, workspace_id: str, member_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM assistant_conversations WHERE id=? AND workspace_id=? AND member_id=?",
                (conversation_id, workspace_id, member_id),
            ).fetchone()
        return self._conversation(row) if row else None

    def select_current(self, conversation_id: str, workspace_id: str, member_id: str) -> dict | None:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM assistant_conversations WHERE id=? AND workspace_id=? AND member_id=?",
                (conversation_id, workspace_id, member_id),
            ).fetchone()
            if row is None:
                return None
            now = _now()
            conn.execute(
                "INSERT INTO assistant_conversation_current "
                "(workspace_id,member_id,scope_kind,scope_id,conversation_id,selected_at) VALUES(?,?,?,?,?,?) "
                "ON CONFLICT(workspace_id,member_id,scope_kind,scope_id) DO UPDATE SET "
                "conversation_id=excluded.conversation_id,selected_at=excluded.selected_at",
                (workspace_id, member_id, row["scope_kind"], row["scope_id"], conversation_id, now),
            )
        return self._conversation(row)

    def update_title(self, conversation_id: str, workspace_id: str, member_id: str, title: str) -> dict | None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE assistant_conversations SET title=?,updated_at=? WHERE id=? AND workspace_id=? AND member_id=?",
                (title, _now(), conversation_id, workspace_id, member_id),
            )
        return self.get(conversation_id, workspace_id, member_id)

    def save_draft(self, conversation_id: str, workspace_id: str, member_id: str,
                   draft_text: str, expected_draft_version: int) -> dict | None:
        """Compare-and-save a draft under its trusted conversation owner."""
        if expected_draft_version < 0:
            raise ValueError("expected_draft_version must be non-negative")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM assistant_conversations WHERE id=? AND workspace_id=? AND member_id=?",
                (conversation_id, workspace_id, member_id),
            ).fetchone()
            if row is None:
                return None
            current = self._conversation(row)
            if current["draft_version"] != expected_draft_version:
                raise ConversationDraftVersionConflict(current)
            cursor = conn.execute(
                "UPDATE assistant_conversations SET draft_text=?,draft_version=draft_version+1,updated_at=? "
                "WHERE id=? AND workspace_id=? AND member_id=? AND draft_version=?",
                (draft_text, _now(), conversation_id, workspace_id, member_id, expected_draft_version),
            )
            if cursor.rowcount != 1:
                row = conn.execute(
                    "SELECT * FROM assistant_conversations WHERE id=? AND workspace_id=? AND member_id=?",
                    (conversation_id, workspace_id, member_id),
                ).fetchone()
                if row is None:
                    return None
                raise ConversationDraftVersionConflict(self._conversation(row))
            updated = conn.execute(
                "SELECT * FROM assistant_conversations WHERE id=? AND workspace_id=? AND member_id=?",
                (conversation_id, workspace_id, member_id),
            ).fetchone()
        assert updated is not None
        return self._conversation(updated)

    def create_turn_idempotent(
        self, conversation_id: str, workspace_id: str, member_id: str, user_message: str,
        client_request_id: str, expected_draft_version: int, request_context: dict | None = None,
    ) -> tuple[dict | None, bool]:
        """Atomically consume a draft version and create at most one turn per request ID.

        The boolean is true only for the call that inserted the durable turn; a replay
        must return the existing turn without launching its business tools again.
        """
        self._validate_draft_request(client_request_id, expected_draft_version)
        context_json = json.dumps(request_context or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conversation = conn.execute(
                "SELECT * FROM assistant_conversations WHERE id=? AND workspace_id=? AND member_id=?",
                (conversation_id, workspace_id, member_id),
            ).fetchone()
            if conversation is None:
                return None, False
            existing = conn.execute(
                "SELECT * FROM assistant_conversation_request_receipts WHERE client_request_id=?",
                (client_request_id,),
            ).fetchone()
            if existing is not None:
                same_request = (
                    existing["workspace_id"] == workspace_id
                    and existing["member_id"] == member_id
                    and existing["conversation_id"] == conversation_id
                    and existing["scope_kind"] == conversation["scope_kind"]
                    and existing["scope_id"] == conversation["scope_id"]
                    and existing["user_message"] == user_message
                    and existing["expected_draft_version"] == expected_draft_version
                    and existing["request_context_json"] == context_json
                )
                if not same_request:
                    raise ConversationTurnRequestConflict("此请求身份已用于其他内容或对话范围。")
                if existing["outcome"] == "recovered":
                    raise ConversationTurnRequestConflict("此请求已恢复为草稿；如需重新发送，请创建新的请求身份。")
                turn = conn.execute(
                    "SELECT * FROM assistant_conversation_turns WHERE id=? AND conversation_id=?",
                    (existing["turn_id"], conversation_id),
                ).fetchone()
                assert turn is not None
                return dict(turn), False

            current = self._conversation(conversation)
            if current["draft_version"] != expected_draft_version:
                raise ConversationDraftVersionConflict(current)
            if current["draft_text"] != user_message:
                raise ConversationDraftVersionConflict(current)
            running = conn.execute(
                "SELECT 1 FROM assistant_conversation_turns WHERE conversation_id=? AND status='running' LIMIT 1",
                (conversation_id,),
            ).fetchone()
            if running is not None:
                raise ConversationTurnAlreadyRunning("此对话已有正在运行的助手轮次。")
            sequence_row = conn.execute(
                "SELECT COALESCE(MAX(sequence),0)+1 AS sequence FROM assistant_conversation_turns WHERE conversation_id=?",
                (conversation_id,),
            ).fetchone()
            turn_id = str(uuid4())
            now = _now()
            conn.execute(
                "INSERT INTO assistant_conversation_turns "
                "(id,conversation_id,sequence,user_message,assistant_text,status,events_json,result_json,created_at,updated_at) "
                "VALUES(?,?,?,?,?,'running','[]',NULL,?,?)",
                (turn_id, conversation_id, int(sequence_row["sequence"]), user_message, "", now, now),
            )
            consumed = conn.execute(
                "UPDATE assistant_conversations SET draft_text='',draft_version=draft_version+1,updated_at=? "
                "WHERE id=? AND workspace_id=? AND member_id=? AND draft_version=?",
                (now, conversation_id, workspace_id, member_id, expected_draft_version),
            )
            if consumed.rowcount != 1:
                row = conn.execute(
                    "SELECT * FROM assistant_conversations WHERE id=? AND workspace_id=? AND member_id=?",
                    (conversation_id, workspace_id, member_id),
                ).fetchone()
                if row is None:
                    return None, False
                raise ConversationDraftVersionConflict(self._conversation(row))
            conn.execute(
                "INSERT INTO assistant_conversation_request_receipts "
                "(client_request_id,conversation_id,workspace_id,member_id,scope_kind,scope_id,user_message,"
                "expected_draft_version,request_context_json,outcome,turn_id,created_at) VALUES(?,?,?,?,?,?,?,?,?, 'consumed',?,?)",
                (client_request_id, conversation_id, workspace_id, member_id, conversation["scope_kind"],
                 conversation["scope_id"], user_message, expected_draft_version, context_json, turn_id, now),
            )
            turn = conn.execute("SELECT * FROM assistant_conversation_turns WHERE id=?", (turn_id,)).fetchone()
        assert turn is not None
        return dict(turn), True

    def restore_unpersisted_turn_draft(
        self, conversation_id: str, workspace_id: str, member_id: str, client_request_id: str,
        user_message: str, expected_draft_version: int,
    ) -> tuple[dict | None, str]:
        """Restore only when the matching request has no durable turn and the draft is unchanged."""
        self._validate_draft_request(client_request_id, expected_draft_version)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conversation = conn.execute(
                "SELECT * FROM assistant_conversations WHERE id=? AND workspace_id=? AND member_id=?",
                (conversation_id, workspace_id, member_id),
            ).fetchone()
            if conversation is None:
                return None, "not_found"
            existing = conn.execute(
                "SELECT * FROM assistant_conversation_request_receipts WHERE client_request_id=?",
                (client_request_id,),
            ).fetchone()
            if existing is not None:
                same_request = (
                    existing["workspace_id"] == workspace_id
                    and existing["member_id"] == member_id
                    and existing["conversation_id"] == conversation_id
                    and existing["scope_kind"] == conversation["scope_kind"]
                    and existing["scope_id"] == conversation["scope_id"]
                    and existing["user_message"] == user_message
                    and existing["expected_draft_version"] == expected_draft_version
                )
                if not same_request:
                    raise ConversationTurnRequestConflict("此请求身份已用于其他内容或对话范围。")
                outcome = "already_recovered" if existing["outcome"] == "recovered" else "already_persisted"
                return self._conversation(conversation), outcome

            current = self._conversation(conversation)
            if current["draft_version"] != expected_draft_version:
                raise ConversationDraftVersionConflict(current)
            cursor = conn.execute(
                "UPDATE assistant_conversations SET draft_text=?,draft_version=draft_version+1,updated_at=? "
                "WHERE id=? AND workspace_id=? AND member_id=? AND draft_version=? "
                "AND NOT EXISTS (SELECT 1 FROM assistant_conversation_request_receipts WHERE client_request_id=?)",
                (user_message, _now(), conversation_id, workspace_id, member_id,
                 expected_draft_version, client_request_id),
            )
            if cursor.rowcount != 1:
                row = conn.execute(
                    "SELECT * FROM assistant_conversations WHERE id=? AND workspace_id=? AND member_id=?",
                    (conversation_id, workspace_id, member_id),
                ).fetchone()
                if row is None:
                    return None, "not_found"
                raise ConversationDraftVersionConflict(self._conversation(row))
            conn.execute(
                "INSERT INTO assistant_conversation_request_receipts "
                "(client_request_id,conversation_id,workspace_id,member_id,scope_kind,scope_id,user_message,"
                "expected_draft_version,request_context_json,outcome,turn_id,created_at) VALUES(?,?,?,?,?,?,?,?, '{}','recovered',NULL,?)",
                (client_request_id, conversation_id, workspace_id, member_id, conversation["scope_kind"],
                 conversation["scope_id"], user_message, expected_draft_version, _now()),
            )
            restored = conn.execute(
                "SELECT * FROM assistant_conversations WHERE id=? AND workspace_id=? AND member_id=?",
                (conversation_id, workspace_id, member_id),
            ).fetchone()
        assert restored is not None
        return self._conversation(restored), "restored"

    def get_turn(self, turn_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM assistant_conversation_turns WHERE id=?", (turn_id,)).fetchone()
        if row is None:
            return None
        return {**dict(row), "events": json.loads(row["events_json"]),
                "result": json.loads(row["result_json"]) if row["result_json"] else None}

    def update_completed_plan_preview(self, turn_id: str, plan_preview: dict) -> bool:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT result_json,status FROM assistant_conversation_turns WHERE id=?",
                (turn_id,),
            ).fetchone()
            if row is None or row["status"] != "completed":
                return False
            existing = json.loads(row["result_json"]) if row["result_json"] else None
            if not isinstance(existing, dict) or existing.get("kind") != "plan_preview":
                return False
            result = {**existing, "kind": "plan_preview", "plan_preview": plan_preview}
            response = result.get("response")
            if isinstance(response, dict):
                result["response"] = {**response, "plan_preview": plan_preview}
            conn.execute(
                "UPDATE assistant_conversation_turns SET result_json=?,updated_at=? "
                "WHERE id=? AND status='completed'",
                (json.dumps(result, ensure_ascii=False), _now(), turn_id),
            )
        return True

    def append_event(self, turn_id: str, event: dict) -> None:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT events_json,assistant_text FROM assistant_conversation_turns WHERE id=? AND status='running'",
                (turn_id,),
            ).fetchone()
            if row is None:
                return
            events = json.loads(row["events_json"])
            events.append(event)
            assistant_text = row["assistant_text"] + (event["delta"] if event.get("type") == "text_delta" else "")
            conn.execute(
                "UPDATE assistant_conversation_turns SET events_json=?,assistant_text=?,updated_at=? WHERE id=? AND status='running'",
                (json.dumps(events, ensure_ascii=False), assistant_text, _now(), turn_id),
            )

    def finish_turn(self, turn_id: str, status: str, assistant_text: str | None = None,
                    result: dict | None = None) -> dict | None:
        if status not in {"completed", "failed", "stopped"}:
            raise ValueError("invalid assistant turn terminal status")
        with self._connect() as conn:
            fields = ["status=?", "updated_at=?"]
            values: list[str] = [status, _now()]
            if assistant_text is not None:
                fields.append("assistant_text=?")
                values.append(assistant_text)
            if result is not None:
                fields.append("result_json=?")
                values.append(json.dumps(result, ensure_ascii=False))
            values.append(turn_id)
            conn.execute(
                f"UPDATE assistant_conversation_turns SET {','.join(fields)} WHERE id=? AND status='running'",
                values,
            )
            row = conn.execute("SELECT * FROM assistant_conversation_turns WHERE id=?", (turn_id,)).fetchone()
        return dict(row) if row else None

    def fail_interrupted_turns(self) -> int:
        """Do not replay a running assistant turn after the API process restarts."""
        now = _now()
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE assistant_conversation_turns SET status='failed',result_json=?,updated_at=? "
                "WHERE status='running'",
                (json.dumps({"reason": "api_restarted"}), now),
            )
            conn.execute(
                "UPDATE assistant_conversations SET updated_at=? WHERE id IN "
                "(SELECT DISTINCT conversation_id FROM assistant_conversation_turns WHERE updated_at=? AND status='failed')",
                (now, now),
            )
        return cursor.rowcount

    def list_turns(self, conversation_id: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM assistant_conversation_turns WHERE conversation_id=? ORDER BY sequence ASC",
                (conversation_id,),
            ).fetchall()
        return [{**dict(row), "events": json.loads(row["events_json"]),
                 "result": json.loads(row["result_json"]) if row["result_json"] else None}
                for row in rows]
