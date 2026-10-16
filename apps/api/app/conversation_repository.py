from __future__ import annotations



import json
import hashlib
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import NAMESPACE_URL, uuid4, uuid5
from app.assistant.inputs import active_input, latest_consumed_input
from app.assistant.goal_inputs import input_goal_authorization


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ConversationTurnAlreadyRunning(RuntimeError):
    pass


class ConversationDraftVersionConflict(RuntimeError):
    def __init__(self, current: dict) -> None:
        self.current = current
        super().__init__("对话草稿已在其他位置更新，请重新读取后再编辑。")


class ConversationReferenceVersionConflict(RuntimeError):
    pass


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
            if "selected_reference_json" not in conversation_columns:
                conn.execute("ALTER TABLE assistant_conversations ADD COLUMN selected_reference_json TEXT")
            if "reference_version" not in conversation_columns:
                conn.execute("ALTER TABLE assistant_conversations ADD COLUMN reference_version INTEGER NOT NULL DEFAULT 0")
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
        value = dict(row)
        reference_json = value.pop("selected_reference_json", None)
        value["selected_reference"] = json.loads(reference_json) if reference_json else None
        return value

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
                "AND (current.scope_kind='home' AND current.scope_id='home' OR (c.scope_kind=current.scope_kind AND c.scope_id=current.scope_id))",
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
            if scope_kind == "home" and scope_id == "home":
                rows = conn.execute(
                    "SELECT * FROM assistant_conversations WHERE workspace_id=? AND member_id=? "
                    "ORDER BY updated_at DESC, created_at DESC", (workspace_id, member_id),
                ).fetchall()
            elif scope_kind == "material" and scope_id == "library":
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

    def select_current(self, conversation_id: str, workspace_id: str, member_id: str, *, workspace_session: bool = False) -> dict | None:
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
                (workspace_id, member_id, "home" if workspace_session else row["scope_kind"], "home" if workspace_session else row["scope_id"], conversation_id, now),
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

    def save_reference(self, conversation_id: str, workspace_id: str, member_id: str,
                       reference: dict | None, expected_reference_version: int) -> dict | None:
        from app.assistant.references import normalize_selection
        reference = normalize_selection(reference)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM assistant_conversations WHERE id=? AND workspace_id=? AND member_id=?", (conversation_id, workspace_id, member_id)).fetchone()
            if row is None:
                return None
            if row["reference_version"] != expected_reference_version:
                raise ConversationReferenceVersionConflict("引用已在其他位置更新，请重新读取这段对话后选择或取消；没有覆盖较新引用。")
            conn.execute("UPDATE assistant_conversations SET selected_reference_json=?, reference_version=reference_version+1, updated_at=? WHERE id=? AND workspace_id=? AND member_id=?",
                         (json.dumps(reference,ensure_ascii=False) if reference is not None else None,_now(),conversation_id,workspace_id,member_id))
            saved = conn.execute("SELECT * FROM assistant_conversations WHERE id=?", (conversation_id,)).fetchone()
        return self._conversation(saved)

    def enqueue_input(self, conversation_id, workspace_id, member_id, **input_fields):
        from app.assistant.inputs import enqueue_input
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            return enqueue_input(conn, conversation_id=conversation_id, workspace_id=workspace_id,
                                 member_id=member_id, **input_fields)

    def list_inputs(self, conversation_id, workspace_id, member_id):
        from app.assistant.inputs import list_inputs
        with self._connect() as conn:
            return list_inputs(conn, conversation_id, workspace_id, member_id)

    def transition_input(self, conversation_id, workspace_id, member_id, input_id, status, *, reason=None):
        from app.assistant.inputs import transition_input
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            item = transition_input(conn, conversation_id=conversation_id, workspace_id=workspace_id,
                                    member_id=member_id, input_id=input_id, status=status, reason=reason)
            if status == "consumed":
                root = self._goal_root(conn, item["target_turn_id"])
                if root is not None and root["goal"].get("source_input_id") != input_id:
                    self._write_goal(conn, root, {**root["goal"], "status":"invalidated",
                        "waiting_reason":"consumed_input_superseded_business_goal", "unfinished_steps":[]})
            return item

    def create_turn_idempotent(
        self, conversation_id: str, workspace_id: str, member_id: str, user_message: str,
        client_request_id: str, expected_draft_version: int, request_context: dict | None = None,
        *, expected_reference_version: int | None = None,
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
                    and {key: value for key, value in json.loads(existing["request_context_json"]).items() if key != "target_snapshot"}
                        == {key: value for key, value in (request_context or {}).items() if key != "target_snapshot"}
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
            if expected_reference_version is not None:
                if current["reference_version"] != expected_reference_version:
                    raise ConversationReferenceVersionConflict("引用已变化，本轮没有发送，请检查后重新发送。")
                if (request_context or {}).get("selected_reference") != current["selected_reference"]:
                    raise ConversationReferenceVersionConflict("已核验引用与保存引用不一致，本轮没有发送。")
            elif (request_context or {}).get("selected_reference") is not None:
                raise ConversationReferenceVersionConflict("发送引用需要明确引用版本，本轮没有发送。")
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
            self._invalidate_conversation_goals(conn, conversation_id, "new_user_instruction")
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

    def request_context(self, client_request_id: str, conversation_id: str, workspace_id: str, member_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT request_context_json FROM assistant_conversation_request_receipts WHERE client_request_id=? AND conversation_id=? AND workspace_id=? AND member_id=? AND outcome='consumed'",
                (client_request_id, conversation_id, workspace_id, member_id),
            ).fetchone()
        return json.loads(row["request_context_json"]) if row else None

    def task_turn_source(self, turn_id: str, workspace_id: str, member_id: str, task_id: str) -> dict:
        """Read the consumed, trusted task snapshot for an active tool call."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT t.user_message,t.status,t.events_json,r.request_context_json,t.conversation_id "
                "FROM assistant_conversation_turns t JOIN assistant_conversation_request_receipts r ON r.turn_id=t.id "
                "WHERE t.id=? AND r.workspace_id=? AND r.member_id=? AND r.outcome='consumed'",
                (turn_id, workspace_id, member_id),
            ).fetchone()
        context = json.loads(row["request_context_json"]) if row else {}
        if row is None or row["status"] != "running" or context.get("kind") != "task" or context.get("task_id") != task_id:
            raise ValueError("类型化编辑不属于当前有效助手轮次")
        events = json.loads(row["events_json"])
        latest = latest_consumed_input(events)
        if latest and latest["status"] == "interrupted":
            raise ValueError("最新已消费输入已中断，不能回退到先前要求写入")
        active = active_input(events)
        if active:
            context = active["request_context"]
            if context.get("kind") != "task" or context.get("task_id") != task_id:
                raise ValueError("追加输入不属于当前工具目标")
            return {"context": context, "raw_command": active["message"],
                    "conversation_id": row["conversation_id"], "input_id": active["input_id"], "input_turn_id":turn_id}
        original = row["user_message"]
        if context.get("source_kind") == "business_event":
            with self._connect() as conn:
                root = self._goal_root(conn, turn_id)
            if (root is None or root["goal"].get("current_turn_id") != turn_id or root["goal"].get("status") != "running"
                    or context.get("goal_revision",0) != root["goal"].get("revision",0)
                    or context.get("source_input_id") != root["goal"].get("source_input_id")):
                raise ValueError("原创作目标已停止，不能沿后台回合提交修改")
            original = root["goal"]["original_instruction"]
        return {"context": context, "raw_command": original, "conversation_id": row["conversation_id"],
                **({"input_id":context["source_input_id"],"input_turn_id":context["source_input_turn_id"]}
                   if context.get("source_input_id") else {})}

    def task_result_source(self, turn_id: str, workspace_id: str, member_id: str, task_id: str, *,
                           input_id: str | None = None, input_turn_id: str | None = None) -> dict:
        """Restore a result's fixed receipt; this grants no permission to write."""
        with self._connect() as conn:
            row = conn.execute("SELECT t.*,r.request_context_json FROM assistant_conversation_turns t "
                "JOIN assistant_conversation_request_receipts r ON r.turn_id=t.id WHERE t.id=? "
                "AND r.workspace_id=? AND r.member_id=? AND r.outcome='consumed'",
                (turn_id,workspace_id,member_id)).fetchone()
            if row is None:
                raise ValueError("结果不属于原助手轮次")
            context = json.loads(row["request_context_json"])
            original = row["user_message"]
            if input_id:
                inherited = (context.get("source_kind") == "business_event"
                    and (context.get("source_input_id"),context.get("source_input_turn_id")) == (input_id,input_turn_id))
                if input_turn_id != turn_id and not inherited:
                    raise ValueError("结果输入不属于该工具轮次")
                owner = conn.execute("SELECT t.events_json FROM assistant_conversation_turns t "
                    "JOIN assistant_conversation_request_receipts r ON r.turn_id=t.id WHERE t.id=? "
                    "AND t.conversation_id=? AND r.workspace_id=? AND r.member_id=? AND r.outcome='consumed'",
                    (input_turn_id,row["conversation_id"],workspace_id,member_id)).fetchone()
                receipt = next((item for item in json.loads(owner["events_json"]) if item.get("type") == "assistant_input"
                    and item["input_id"] == input_id and item["status"] in {"consumed","completed","interrupted"}),None) if owner else None
                if receipt is None:
                    raise ValueError("结果输入没有实际消费回执")
                if inherited:
                    original = context["goal_instruction"]
                else:
                    context, original = receipt["request_context"], receipt["message"]
            elif input_turn_id or context.get("source_input_id"):
                raise ValueError("结果缺少原输入归属")
            elif context.get("source_kind") == "business_event":
                root = conn.execute("SELECT user_message FROM assistant_conversation_turns WHERE id=? AND conversation_id=?",
                    (context["original_turn_id"],row["conversation_id"])).fetchone()
                original = context.get("goal_instruction",root["user_message"])
            if context.get("kind") != "task" or context.get("task_id") != task_id:
                raise ValueError("结果不属于原任务快照")
            return {"context":context,"raw_command":original,"conversation_id":row["conversation_id"],
                **({"input_id":input_id,"input_turn_id":input_turn_id} if input_id else {})}

    @staticmethod
    def _write_goal(conn: sqlite3.Connection, root: dict, goal: dict) -> None:
        result = json.loads(conn.execute("SELECT result_json FROM assistant_conversation_turns WHERE id=?",
                                        (root["id"],)).fetchone()["result_json"] or "{}")
        result["goal_continuation"] = goal
        conn.execute("UPDATE assistant_conversation_turns SET result_json=?,updated_at=? WHERE id=?",
                     (json.dumps(result, ensure_ascii=False), _now(), root["id"]))

    @staticmethod
    def _goal_revision_matches(root: dict | None, expected: dict | None) -> bool:
        return bool(root and (expected is None or
            (root["goal"].get("revision",0),root["goal"].get("source_input_id")) ==
            (expected.get("revision",0),expected.get("source_input_id"))))

    @staticmethod
    def _goal_root(conn: sqlite3.Connection, turn_id: str) -> dict | None:
        source = conn.execute("SELECT r.request_context_json FROM assistant_conversation_request_receipts r "
                              "WHERE r.turn_id=? AND r.outcome='consumed'", (turn_id,)).fetchone()
        if source is None:
            return None
        context = json.loads(source["request_context_json"])
        root_id = context.get("original_turn_id", turn_id) if context.get("source_kind") == "business_event" else turn_id
        row = conn.execute("SELECT t.*,r.workspace_id,r.member_id,r.request_context_json FROM assistant_conversation_turns t "
                           "JOIN assistant_conversation_request_receipts r ON r.turn_id=t.id "
                           "WHERE t.id=? AND r.outcome='consumed'", (root_id,)).fetchone()
        if row is None:
            return None
        result = json.loads(row["result_json"] or "{}")
        goal = result.get("goal_continuation")
        if not goal:
            return None
        return {**dict(row), "context": goal["request_context"] if goal.get("source_input_id") else json.loads(row["request_context_json"]), "goal": goal}

    @classmethod
    def _invalidate_conversation_goals(cls, conn: sqlite3.Connection, conversation_id: str, reason: str) -> None:
        rows = conn.execute("SELECT id FROM assistant_conversation_turns WHERE conversation_id=? "
                            "AND json_extract(result_json,'$.goal_continuation.status') IN "
                            "('running','waiting_result','waiting_adoption','waiting_identity','waiting_unknown')",
                            (conversation_id,)).fetchall()
        for row in rows:
            root = cls._goal_root(conn, row["id"])
            if root:
                cls._write_goal(conn, root, {**root["goal"], "status": "invalidated", "waiting_reason": reason,
                                           "unfinished_steps": []})

    def record_task_execution(self, turn_id: str, workspace_id: str, member_id: str,
                              task_id: str, execution_status: dict, *, current_source: bool = True, input_id: str | None = None) -> bool:
        """Preserve a host-verified execution receipt before the model can finish."""
        job_id = execution_status["job"]["id"]
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT t.result_json,t.events_json,r.request_context_json FROM assistant_conversation_turns t "
                "JOIN assistant_conversation_request_receipts r ON r.turn_id=t.id WHERE t.id=? "
                "AND r.workspace_id=? AND r.member_id=? AND r.outcome='consumed'",
                (turn_id, workspace_id, member_id)).fetchone()
            if row is None or json.loads(row["request_context_json"]).get("task_id") != task_id:
                raise ValueError("制作回执不属于原助手任务轮次")
            result = json.loads(row["result_json"] or "{}")
            actions = result.get("goal_business_actions", [])
            proof = {"kind": "execution_status", "id": job_id, **({"input_id":input_id} if input_id else {})}
            if proof not in actions:
                actions = [*actions, proof]
            result["goal_business_actions"] = actions
            active = latest_consumed_input(json.loads(row["events_json"]))
            current_input_id = active["input_id"] if active else json.loads(row["request_context_json"]).get("source_input_id")
            if input_id == current_input_id:
                result["response"] = {**result.get("response", {}), "execution_status": execution_status}
            conn.execute("UPDATE assistant_conversation_turns SET result_json=?,updated_at=? WHERE id=?",
                (json.dumps(result, ensure_ascii=False), _now(), turn_id))
            if current_source:
                self._register_task_goal(conn, turn_id, running_execution=True)
            else:
                root = self._goal_root(conn, turn_id)
                if (root and root["goal"].get("source_input_id") == input_id
                        and root["goal"]["status"] not in {"invalidated", "no_progress", "limit_reached"}):
                    self._write_goal(conn, root, {**root["goal"], "status": "invalidated",
                        "waiting_reason": "unrelated_timeline_change", "unfinished_steps": []})
        return True

    def register_task_goal(self, turn_id: str, *, running_execution: bool = False) -> dict | None:
        """Attach execution checkpoints to a real user's existing task turn.

        This is neither another task nor a model-authored authorization. The
        immutable request receipt continues to own the original instruction.
        """
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            return self._register_task_goal(conn, turn_id, running_execution=running_execution)

    def _register_task_goal(self, conn: sqlite3.Connection, turn_id: str, *, running_execution: bool = False) -> dict | None:
        row = conn.execute("SELECT t.*,r.workspace_id,r.member_id,r.request_context_json "
                           "FROM assistant_conversation_turns t JOIN assistant_conversation_request_receipts r ON r.turn_id=t.id "
                           "WHERE t.id=? AND r.outcome='consumed'", (turn_id,)).fetchone()
        if row is None or row["status"] not in ({"running", "completed", "failed"} if running_execution else {"completed"}):
            return None
        original_context=json.loads(row["request_context_json"])
        root_id=original_context.get("original_turn_id",turn_id) if original_context.get("source_kind") == "business_event" else turn_id
        root_row=conn.execute("SELECT * FROM assistant_conversation_turns WHERE id=? AND conversation_id=?",
                              (root_id,row["conversation_id"])).fetchone()
        authorization=input_goal_authorization(conn,root_row,row)
        if latest_consumed_input(json.loads(row["events_json"])) and authorization is None:
            return None
        context=authorization["request_context"] if authorization else original_context
        if context.get("kind") != "task" or not context.get("task_id") or (not authorization and (not row["user_message"] or context.get("source_kind"))):
            return None
        latest_user=conn.execute("SELECT id FROM assistant_conversation_turns WHERE conversation_id=? AND user_message!='' ORDER BY sequence DESC LIMIT 1",(row["conversation_id"],)).fetchone()
        if latest_user is None or latest_user["id"] != root_id:
            return None
        result=json.loads(row["result_json"] or "{}")
        previous=json.loads(root_row["result_json"] or "{}").get("goal_continuation")
        input_id=authorization["source_input_id"] if authorization else None
        if previous and previous.get("source_input_id") == input_id:
            return previous
        response=result.get("response",{})
        proofs=[proof for proof in result.get("goal_business_actions",[]) if proof.get("input_id") == input_id]
        execution=((response.get("execution_status") or {}).get("job") or {}).get("id")
        submitted_execution=any(proof["kind"] == "execution_status" and proof["id"] == execution for proof in proofs)
        if running_execution and not submitted_execution:
            return None
        if authorization and not proofs:
            return None
        if not any(response.get(key) for key in ("media_job","edit_preview","edit_request")) and not submitted_execution:
            return None
        waiting="waiting_adoption" if response.get("edit_preview") else "waiting_unknown" if (response.get("edit_request") or {}).get("status") in {"result_unknown","unknown"} else "waiting_result"
        goal={"original_turn_id":root_id,"original_instruction":authorization["original_instruction"] if authorization else root_row["user_message"],
            "workspace_id":row["workspace_id"],"member_id":row["member_id"],"task_id":context["task_id"],
            "conversation_id":row["conversation_id"],"request_context":context,"target_snapshot":context.get("target_snapshot",{}),
            "status":waiting,"unfinished_steps":["await_real_adoption" if waiting == "waiting_adoption" else "observe_actual_result","complete_original_instruction"],
            "round_count":0,"handled_event_ids":[],"current_turn_id":turn_id,
            "revision":max((previous or {}).get("revision",0)+1,authorization["input_sequence"]) if authorization else 0,
            **({key:value for key,value in authorization.items() if key not in {"input_sequence","original_instruction","request_context"}} if authorization else {})}
        self._write_goal(conn,{"id":root_id},goal)
        return goal

    def get_task_goal(self, turn_id: str) -> dict | None:
        with self._connect() as conn:
            root = self._goal_root(conn, turn_id)
        return root["goal"] if root else None

    def record_goal_output_checkpoint(self, turn_id: str, checkpoint: dict | None, *, expected_goal: dict | None = None) -> bool:
        """A bounded host observation projection; never certifies the whole instruction."""
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            root = self._goal_root(conn, turn_id)
            if not self._goal_revision_matches(root, expected_goal) or root["goal"]["status"] in {"invalidated", "no_progress", "limit_reached"}:
                return False
            goal = dict(root["goal"])
            if checkpoint:
                goal["completed_output_checkpoint"] = checkpoint
            else:
                goal.pop("completed_output_checkpoint", None)
            self._write_goal(conn, root, goal)
        return True

    @staticmethod
    def _source_slot_key(name: str, input_refs: list[dict]) -> str:
        # Source identity is its named output and fixed input revisions. Code
        # formatting, call IDs and job digests do not create a new output slot.
        inputs = sorted(input_refs, key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":")))
        return hashlib.sha256(json.dumps([name, inputs], sort_keys=True, separators=(",", ":"),
            ensure_ascii=False).encode()).hexdigest()

    def record_goal_completed_sources(self, turn_id: str, slots: list[dict], *, expected_goal: dict | None = None) -> bool:
        """Store only source facts verified by the host's actual job/artifact reader."""
        completed = {}
        required = {"name", "requested_output", "input_refs", "job_id", "source_ref", "artifact_id", "sha256",
                    "size_bytes", "width", "height", "base_version_id"}
        for slot in slots:
            if not required.issubset(slot) or slot["name"] != slot["requested_output"]["name"]:
                raise ValueError("已完成来源缺少实际输出声明与登记事实")
            key = self._source_slot_key(slot["name"], slot["input_refs"])
            # The earliest actual success remains the canonical result of a
            # fulfilled slot; duplicate later programs cannot replace it.
            completed.setdefault(key, {**slot, "slot_key": key})
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            root = self._goal_root(conn, turn_id)
            if not self._goal_revision_matches(root, expected_goal) or root["goal"]["status"] in {"invalidated", "no_progress", "limit_reached"}:
                return False
            self._write_goal(conn, root, {**root["goal"], "completed_source_slots": list(completed.values())})
        return True

    def completed_goal_source(self, turn_id: str, *, requested_outputs: list[dict], input_refs: list[dict]) -> dict | None:
        """Find an already fulfilled slot for this bound automatic child only.

        The API must still verify current source access and actual artifact hash
        before returning the original receipt. This is not a synthetic success.
        """
        if len(requested_outputs) != 1:
            return None  # Existing media-script contract produces one PNG.
        with self._connect() as conn:
            root = self._goal_root(conn, turn_id)
            receipt = conn.execute("SELECT request_context_json FROM assistant_conversation_request_receipts WHERE turn_id=?",
                                   (turn_id,)).fetchone()
            turn = conn.execute("SELECT status FROM assistant_conversation_turns WHERE id=?", (turn_id,)).fetchone()
            if (root is None or receipt is None or turn["status"] != "running"
                    or json.loads(receipt["request_context_json"]).get("source_kind") != "business_event"
                    or root["goal"]["status"] != "running" or root["goal"]["current_turn_id"] != turn_id):
                return None
            key = self._source_slot_key(requested_outputs[0]["name"], input_refs)
            return next((slot for slot in root["goal"].get("completed_source_slots", []) if slot["slot_key"] == key), None)

    def goal_event_candidates(self, *, limit: int = 8) -> list[dict]:
        """Read unconsumed trusted results already projected into existing turns."""
        candidates = []
        with self._connect() as conn:
            roots = conn.execute("SELECT id FROM assistant_conversation_turns WHERE "
                                 "json_extract(result_json,'$.goal_continuation.status') IN "
                                 "('running','waiting_result','waiting_adoption','waiting_identity','waiting_unknown') "
                                 "ORDER BY updated_at LIMIT ?", (limit,)).fetchall()
            for root_row in roots:
                root = self._goal_root(conn, root_row["id"])
                if root is None:
                    continue
                turns = conn.execute("SELECT t.id,t.status,t.events_json,r.request_context_json "
                                     "FROM assistant_conversation_turns t JOIN assistant_conversation_request_receipts r ON r.turn_id=t.id "
                                     "WHERE t.conversation_id=? AND t.sequence>=? ORDER BY t.sequence",
                                     (root["conversation_id"], root["sequence"])).fetchall()
                for turn in turns:
                    context = json.loads(turn["request_context_json"])
                    if turn["id"] != root["id"] and context.get("original_turn_id") != root["id"]:
                        continue
                    if turn["status"] == "running":
                        continue
                    for event in json.loads(turn["events_json"]):
                        if event.get("type") == "business_result" and event.get("event_id") and event["event_id"] not in root["goal"]["handled_event_ids"] and event.get("input_id") == root["goal"].get("source_input_id"):
                            candidates.append({"root": root, "source_turn_id": turn["id"], "event": event})
                            break
                    if candidates and candidates[-1]["root"]["id"] == root["id"]:
                        break
        return candidates

    def update_goal_checkpoint(self, root_id: str, *, status: str, waiting_reason: str,
                               event_id: str | None = None, next_checkpoint: str | None = None,
                               expected_goal: dict | None = None) -> bool:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            root = self._goal_root(conn, root_id)
            if not self._goal_revision_matches(root, expected_goal) or root["goal"]["status"] in {"invalidated", "no_progress", "limit_reached"}:
                return False
            goal = {**root["goal"], "status": status, "waiting_reason": waiting_reason}
            if event_id and event_id not in goal["handled_event_ids"]:
                goal["handled_event_ids"] = [*goal["handled_event_ids"], event_id]
            if status == "waiting_adoption":
                goal["unfinished_steps"] = ["await_real_adoption", "complete_original_instruction"]
                goal["next_checkpoint"] = "await_real_adoption"
            elif next_checkpoint is not None:
                goal["next_checkpoint"] = next_checkpoint
                goal["unfinished_steps"] = [next_checkpoint, "complete_original_instruction"]
            self._write_goal(conn, root, goal)
        return True

    def invalidate_task_goal(self, turn_id: str, reason: str, *, expected_goal: dict | None = None) -> bool:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            root = self._goal_root(conn, turn_id)
            if not self._goal_revision_matches(root, expected_goal):
                return False
            self._write_goal(conn, root, {**root["goal"], "status": "invalidated", "waiting_reason": reason,
                                        "unfinished_steps": []})
        return True

    def claim_goal_event(self, candidate: dict, task_version: dict, *, max_rounds: int) -> dict | None:
        """Consume a business event and establish one empty-human-message turn.

        The event is consumed before any model/HTTP side effect. An interrupted
        claim is not replayed; only a new actual business result can advance it.
        """
        root_id, event = candidate["root"]["id"], candidate["event"]
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            root = self._goal_root(conn, root_id)
            if root is None or root["goal"]["status"] not in {"running", "waiting_result", "waiting_adoption", "waiting_identity", "waiting_unknown"}:
                return None
            goal = root["goal"]
            expected=candidate["root"]["goal"]
            if (goal.get("revision",0),goal.get("source_input_id")) != (expected.get("revision",0),expected.get("source_input_id")) or event.get("input_id") != goal.get("source_input_id"):
                return None
            if event["event_id"] in goal["handled_event_ids"]:
                return None
            latest_user = conn.execute("SELECT id FROM assistant_conversation_turns WHERE conversation_id=? "
                                       "AND user_message!='' ORDER BY sequence DESC LIMIT 1", (root["conversation_id"],)).fetchone()
            if latest_user is None or latest_user["id"] != root_id:
                self._write_goal(conn, root, {**goal, "status": "invalidated", "waiting_reason": "new_user_instruction"})
                return None
            if conn.execute("SELECT 1 FROM assistant_conversation_turns WHERE conversation_id=? AND status='running'",
                            (root["conversation_id"],)).fetchone():
                return None
            if goal["round_count"] >= max_rounds:
                self._write_goal(conn, root, {**goal, "status": "limit_reached", "waiting_reason": "bounded_model_rounds"})
                return None
            event_source = conn.execute("SELECT events_json FROM assistant_conversation_turns WHERE id=? AND conversation_id=?",
                                        (candidate["source_turn_id"], root["conversation_id"])).fetchone()
            source_root = self._goal_root(conn, candidate["source_turn_id"])
            if (event_source is None or source_root is None or source_root["id"] != root_id
                    or (source_root["workspace_id"], source_root["member_id"]) != (root["workspace_id"], root["member_id"])
                    or event not in json.loads(event_source["events_json"])):
                raise ValueError("接续只能消费已经核实并持久化的业务事件")
            turn_id = str(uuid5(NAMESPACE_URL, f"yaohe-goal:{root_id}:{goal['revision']}:{event['event_id']}" if goal.get("source_input_id") else f"yaohe-goal:{root_id}:{event['event_id']}"))
            context = {**root["context"], "source_kind": "business_event", "original_turn_id": root_id,
                       "source_turn_id": candidate["source_turn_id"], "business_event_id": event["event_id"],
                       "goal_revision":goal.get("revision",0), "goal_instruction":goal["original_instruction"],
                       **({"source_input_id":goal["source_input_id"],"source_input_turn_id":goal["source_input_turn_id"]} if goal.get("source_input_id") else {}),
                       "target_snapshot": {**root["context"].get("target_snapshot", {}), **task_version}}
            sequence = conn.execute("SELECT COALESCE(MAX(sequence),0)+1 FROM assistant_conversation_turns WHERE conversation_id=?",
                                    (root["conversation_id"],)).fetchone()[0]
            now = _now()
            source_event = {"type": "goal_continuation_started", "source_kind": "business_event",
                            "original_turn_id": root_id, "source_turn_id": candidate["source_turn_id"], "event_id": event["event_id"]}
            conn.execute("INSERT INTO assistant_conversation_turns "
                         "(id,conversation_id,sequence,user_message,assistant_text,status,events_json,result_json,created_at,updated_at) "
                         "VALUES(?,?,?,'','','running',?,NULL,?,?)",
                         (turn_id, root["conversation_id"], sequence, json.dumps([source_event]), now, now))
            conn.execute("INSERT INTO assistant_conversation_request_receipts "
                         "(client_request_id,conversation_id,workspace_id,member_id,scope_kind,scope_id,user_message,"
                         "expected_draft_version,request_context_json,outcome,turn_id,created_at) VALUES(?,?,?,?,?,?,'',0,?,'consumed',?,?)",
                         (turn_id, root["conversation_id"], root["workspace_id"], root["member_id"], "task", root["context"]["task_id"],
                          json.dumps(context, ensure_ascii=False), turn_id, now))
            goal = {**goal, "status": "running", "round_count": goal["round_count"] + 1,
                    "target_snapshot": {**goal["target_snapshot"], **task_version}, "current_turn_id": turn_id,
                    "handled_event_ids": [*goal["handled_event_ids"], event["event_id"]],
                    "unfinished_steps": [goal.get("next_checkpoint", "evaluate_actual_result"), "complete_original_instruction"]}
            checkpoint = goal.get("completed_output_checkpoint")
            if checkpoint and checkpoint.get("timeline_version_id") != goal["target_snapshot"].get("timeline_version_id"):
                goal.pop("completed_output_checkpoint")
            self._write_goal(conn, root, goal)
        return {"turn_id": turn_id, "conversation_id": root["conversation_id"], "goal": goal,
                "request_context": context, "event": event, "source_turn_id": candidate["source_turn_id"]}

    def settle_goal_round(self, claim: dict) -> dict | None:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            root = self._goal_root(conn, claim["turn_id"])
            if (root is None or root["goal"]["status"] != "running" or root["goal"]["current_turn_id"] != claim["turn_id"]
                    or root["goal"].get("revision",0) != claim["goal"].get("revision",0)):
                return None
            turn = conn.execute("SELECT status,result_json FROM assistant_conversation_turns WHERE id=?", (claim["turn_id"],)).fetchone()
            if turn["status"] == "running":
                raise ValueError("接续回合尚未持久结束")
            result = json.loads(turn["result_json"] or "{}")
            response = result.get("response", {})
            source = conn.execute("SELECT result_json FROM assistant_conversation_turns WHERE id=?",
                                  (claim["source_turn_id"],)).fetchone()

            def receipt_ids(item):
                return {"media_job": (item.get("media_job") or {}).get("job_id"),
                        "edit_preview": ((item.get("edit_preview") or {}).get("proposal") or {}).get("id"),
                        "edit_request": (item.get("edit_request") or {}).get("request_id"),
                        "execution_status": ((item.get("execution_status") or {}).get("job") or {}).get("id")}

            ids = receipt_ids(response)
            original_ids = receipt_ids(json.loads(root["result_json"] or "{}").get("response", {}))
            source_ids = receipt_ids(json.loads(source["result_json"] or "{}").get("response", {}))
            trigger_ids = {"media_job": claim["event"].get("job_id"),
                           "edit_preview": claim["event"].get("proposal_id"),
                           "edit_request": claim["event"].get("request_id"),
                           "execution_status": claim["event"].get("job_id")}
            # Only the host writes this proof after reading the actual job /
            # intent / request source and checking that it belongs to this
            # child. A model result or another terminal read is not progress.
            owned = {(item["kind"], item["id"]) for item in result.get("goal_business_actions", [])}
            linked = {kind: bool(identifier and ((kind, identifier) in owned or identifier in {
                original_ids[kind], source_ids[kind], trigger_ids[kind]})) for kind, identifier in ids.items()}
            new_action = any(identifier and (kind, identifier) in owned and identifier != trigger_ids[kind]
                             for kind, identifier in ids.items())
            proposal = (response.get("edit_preview") or {}).get("proposal") or {}
            media = response.get("media_job") or {}
            execution = (response.get("execution_status") or {}).get("job") or {}
            request = response.get("edit_request") or {}
            pending = {"queued", "running", "interrupted"}
            if proposal.get("status") == "proposed" and linked["edit_preview"]:
                status, steps = "waiting_adoption", ["await_real_adoption", "complete_original_instruction"]
            elif request.get("status") in {"unknown", "result_unknown"} and linked["edit_request"]:
                status, steps = "waiting_unknown", ["read_original_request_result"]
            elif (new_action or media.get("status") in pending and linked["media_job"]
                    or execution.get("status") in pending and linked["execution_status"]):
                status, steps = "waiting_result", ["observe_actual_result", "complete_original_instruction"]
            elif turn["status"] in {"failed", "stopped"}:
                status, steps = "waiting_unknown", ["read_original_request_result"]
            else:
                status, steps = "no_progress", root["goal"].get("unfinished_steps", [])
            current_goal = dict(root["goal"])
            checkpoint = current_goal.get("completed_output_checkpoint")
            if new_action or checkpoint and checkpoint.get("timeline_version_id") != current_goal["target_snapshot"].get("timeline_version_id"):
                current_goal.pop("completed_output_checkpoint", None)
            goal = {**current_goal, "status": status, "unfinished_steps": steps,
                    "next_checkpoint": steps[0] if steps else None,
                    "waiting_reason": ("bounded_output_checkpoint_completed" if current_goal.get("completed_output_checkpoint")
                        else "no_new_business_action") if status == "no_progress" else status}
            self._write_goal(conn, root, goal)
        return goal

    def business_turn_context(self, turn_id: str, workspace_id: str, member_id: str, task_id: str, *, source: dict | None = None) -> dict | None:
        if source is not None:
            try:
                original = self.task_result_source(turn_id,workspace_id,member_id,task_id,
                    input_id=source.get("input_id"),input_turn_id=source.get("input_turn_id"))
            except ValueError:
                return None
            turn = self.get_turn(turn_id)
            return {"status":turn["status"],"conversation_id":original["conversation_id"],"context":original["context"]}
        with self._connect() as conn:
            row = conn.execute(
                "SELECT t.status,t.conversation_id,t.events_json,r.request_context_json FROM assistant_conversation_turns t "
                "JOIN assistant_conversations c ON c.id=t.conversation_id "
                "JOIN assistant_conversation_request_receipts r ON r.turn_id=t.id AND r.conversation_id=c.id "
                "WHERE t.id=? AND c.workspace_id=? AND c.member_id=? AND r.workspace_id=c.workspace_id "
                "AND r.member_id=c.member_id AND r.outcome='consumed'", (turn_id, workspace_id, member_id),
            ).fetchone()
        context = json.loads(row["request_context_json"]) if row else {}
        events = json.loads(row["events_json"]) if row else []
        active = active_input(events)
        if active:
            context = active["request_context"]
        if row is None or context.get("kind") != "task" or context.get("task_id") != task_id:
            return None
        return {"status": row["status"], "conversation_id": row["conversation_id"], "context": context}

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

    def project_business_result(self, turn_id: str, workspace_id: str, member_id: str,
                                event: dict, *, response_patch: dict, message: str) -> bool:
        """Project one real business receipt into its existing settled turn.

        The event identity fences replay across API restarts. This neither
        creates a user message nor resumes a stopped/failed model turn.
        """
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT t.*,r.request_context_json FROM assistant_conversation_turns t JOIN assistant_conversations c ON c.id=t.conversation_id "
                "JOIN assistant_conversation_request_receipts r ON r.turn_id=t.id AND r.outcome='consumed' "
                "WHERE t.id=? AND c.workspace_id=? AND c.member_id=?",
                (turn_id, workspace_id, member_id),
            ).fetchone()
            if row is None or row["status"] == "running":
                return False
            events = json.loads(row["events_json"])
            if any(item.get("event_id") == event["event_id"] for item in events):
                return True
            events.append(event)
            result = json.loads(row["result_json"]) if row["result_json"] else {}
            active = latest_consumed_input(events)
            current_input_id = active["input_id"] if active else json.loads(row["request_context_json"]).get("source_input_id")
            current_input = event.get("input_id") == current_input_id
            if current_input:
                result["continuation"] = event
            text = row["assistant_text"]
            shutdown_goal = self._goal_root(conn, turn_id) if row["status"] == "failed" and self.recoverable_business_failure(result) else None
            shutdown_result = bool(shutdown_goal
                and shutdown_goal["goal"]["status"] in {"running", "waiting_result", "waiting_adoption", "waiting_unknown"}
                and shutdown_goal["goal"]["current_turn_id"] == turn_id
                and (shutdown_goal["goal"]["workspace_id"], shutdown_goal["goal"]["member_id"], shutdown_goal["goal"]["task_id"]) == (
                    workspace_id, member_id, event.get("task_id")))
            if current_input and (row["status"] == "completed" or shutdown_result):
                response = {**result.get("response", {}), **response_patch}
                text = text + ("\n\n" if text else "") + message
                response["reply"] = text
                result.update(kind="task_turn", response=response,
                              edit_preview=bool(response.get("edit_preview")))
            now = _now()
            conn.execute("UPDATE assistant_conversation_turns SET events_json=?,result_json=?,assistant_text=?,updated_at=? WHERE id=?",
                (json.dumps(events, ensure_ascii=False), json.dumps(result, ensure_ascii=False), text, now, turn_id))
            conn.execute("UPDATE assistant_conversations SET updated_at=? WHERE id=?", (now, row["conversation_id"]))
        return True

    @staticmethod
    def recoverable_business_failure(result: dict) -> bool:
        """Host interruption or a verified execution receipt, never an arbitrary tool failure."""
        execution = ((result.get("response") or {}).get("execution_status") or {}).get("job") or {}
        return (result.get("kind") == "failure"
            or result.get("reason") in {"api_shutdown", "api_restarted", "goal_runner_interrupted"}
            or any(item.get("kind") == "execution_status" and item.get("id") == execution.get("id")
                for item in result.get("goal_business_actions", [])))

    @staticmethod
    def _preserve_execution_result(previous: dict, incoming: dict, *, input_id: str | None = None) -> dict:
        result = dict(incoming)
        if previous.get("goal_continuation"):
            result["goal_continuation"] = previous["goal_continuation"]
        proofs = [item for item in previous.get("goal_business_actions", []) if item.get("kind") == "execution_status"]
        if proofs:
            result["goal_business_actions"] = [*proofs, *[item for item in result.get("goal_business_actions", []) if item not in proofs]]
            execution = previous.get("response", {}).get("execution_status")
            response = result.get("response", {})
            current_execution = any(item["id"] == (execution or {}).get("job",{}).get("id")
                and item.get("input_id") == input_id for item in proofs)
            if current_execution and not response.get("execution_status"):
                result["response"] = {**response, "execution_status": execution}
        return result

    def finish_turn(self, turn_id: str, status: str, assistant_text: str | None = None,
                    result: dict | None = None) -> dict | None:
        if status not in {"completed", "failed", "stopped"}:
            raise ValueError("invalid assistant turn terminal status")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            fields = ["status=?", "updated_at=?"]
            values: list[str] = [status, _now()]
            previous_events = conn.execute("SELECT t.events_json,r.request_context_json FROM assistant_conversation_turns t "
                "LEFT JOIN assistant_conversation_request_receipts r ON r.turn_id=t.id AND r.outcome='consumed' WHERE t.id=?", (turn_id,)).fetchone()
            if previous_events is not None:
                from app.assistant.inputs import settle_inputs
                fields.append("events_json=?")
                values.append(json.dumps(settle_inputs(json.loads(previous_events["events_json"]),
                    "turn_ended_before_input_receipt"), ensure_ascii=False))
            if assistant_text is not None:
                fields.append("assistant_text=?")
                values.append(assistant_text)
            if result is not None:
                fields.append("result_json=?")
                previous = conn.execute("SELECT result_json FROM assistant_conversation_turns WHERE id=?", (turn_id,)).fetchone()
                latest = latest_consumed_input(json.loads(previous_events["events_json"])) if previous_events else None
                source_input = latest["input_id"] if latest else json.loads((previous_events["request_context_json"] if previous_events else None) or "{}").get("source_input_id")
                merged = self._preserve_execution_result(json.loads(previous["result_json"] or "{}") if previous else {}, result, input_id=source_input)
                values.append(json.dumps(merged, ensure_ascii=False))
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
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute("SELECT t.id,t.result_json,t.events_json,r.request_context_json FROM assistant_conversation_turns t "
                "LEFT JOIN assistant_conversation_request_receipts r ON r.turn_id=t.id AND r.outcome='consumed' WHERE t.status='running'").fetchall()
            for row in rows:
                from app.assistant.inputs import settle_inputs
                latest = latest_consumed_input(json.loads(row["events_json"]))
                source_input = latest["input_id"] if latest else json.loads(row["request_context_json"] or "{}").get("source_input_id")
                result = self._preserve_execution_result(json.loads(row["result_json"] or "{}"), {"reason": "api_restarted"}, input_id=source_input)
                conn.execute("UPDATE assistant_conversation_turns SET status='failed',result_json=?,events_json=?,updated_at=? WHERE id=?",
                    (json.dumps(result, ensure_ascii=False), json.dumps(settle_inputs(json.loads(row["events_json"]), "api_restarted"), ensure_ascii=False), now, row["id"]))
            conn.execute(
                "UPDATE assistant_conversations SET updated_at=? WHERE id IN "
                "(SELECT DISTINCT conversation_id FROM assistant_conversation_turns WHERE updated_at=? AND status='failed')",
                (now, now),
            )
        return len(rows)

    def list_turns(self, conversation_id: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM assistant_conversation_turns WHERE conversation_id=? ORDER BY sequence ASC",
                (conversation_id,),
            ).fetchall()
        return [{**dict(row), "events": json.loads(row["events_json"]),
                 "result": json.loads(row["result_json"]) if row["result_json"] else None}
                for row in rows]


    def consumed_turn_request_context(self, turn_id, conversation_id, workspace_id, member_id):
        with self._connect() as conn:
            receipt = conn.execute("SELECT r.request_context_json,t.events_json FROM assistant_conversation_request_receipts r "
                "JOIN assistant_conversation_turns t ON t.id=r.turn_id AND t.conversation_id=r.conversation_id "
                "WHERE r.turn_id=? AND r.conversation_id=? AND r.workspace_id=? AND r.member_id=? AND r.outcome='consumed'",
                (turn_id, conversation_id, workspace_id, member_id)).fetchone()
        context = json.loads(receipt['request_context_json']) if receipt else {}
        active = active_input(json.loads(receipt["events_json"])) if receipt else None
        return active["request_context"] if active else context
