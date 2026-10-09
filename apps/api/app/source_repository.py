from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Sequence
from uuid import uuid4

from app.sources import (
    CONTENT_USE_STATES,
    ContentUsePurpose,
    KnowledgeItem,
    KnowledgeItemVersion,
    KnowledgeGrant,
    SourceConnector,
    SourceItem,
    SourceRunEvidence,
    content_allows_use,
)
from app.database_migrations import (
    CONTENT_VISIBILITY_DEFAULT,
    CONTENT_VISIBILITY_LEGACY,
    ensure_content_visibility_columns,
)
from app.knowledge_files import KnowledgeParameter, KnowledgeSection
from app.knowledge_grants import KNOWLEDGE_GRANTS_DDL


class SourceRepository:
    """Workspace-partitioned persistence for knowledge and external sources.

    This intentionally uses the application's existing SQLite database path.
    HTTP composition owns the repository instance; this module owns only the
    source/knowledge tables. Cross-workspace reads require an explicit active
    knowledge grant; original ownership is never rewritten.
    """

    def __init__(self, database_path: str | Path) -> None:
        self._database_path = Path(database_path).expanduser()
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_database()

    def upsert_connector(self, connector: SourceConnector) -> SourceConnector:
        self._require_workspace(connector.workspace_id)
        platform = connector.platform.strip()
        protocol = connector.protocol.strip()
        account_or_rule = connector.account_or_rule.strip()
        frequency = connector.frequency.strip()
        if not all((platform, protocol, account_or_rule, frequency)):
            raise ValueError(
                "connector requires platform, protocol, account_or_rule, and frequency"
            )
        now = _utc_now()
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT workspace_id FROM source_connectors WHERE id = ?",
                (connector.id,),
            ).fetchone()
            if existing is not None and existing["workspace_id"] != connector.workspace_id:
                raise ValueError("connector id already belongs to another workspace")
            conn.execute(
                """
                INSERT INTO source_connectors (
                    id, workspace_id, platform, protocol, account_or_rule,
                    frequency, cursor_value, permissions, connection_status,
                    latest_run_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    workspace_id = excluded.workspace_id,
                    platform = excluded.platform,
                    protocol = excluded.protocol,
                    account_or_rule = excluded.account_or_rule,
                    frequency = excluded.frequency,
                    cursor_value = excluded.cursor_value,
                    permissions = excluded.permissions,
                    connection_status = excluded.connection_status,
                    latest_run_id = excluded.latest_run_id,
                    updated_at = excluded.updated_at
                """,
                (
                    connector.id,
                    connector.workspace_id,
                    platform,
                    protocol,
                    account_or_rule,
                    frequency,
                    connector.cursor,
                    json.dumps(connector.permissions, ensure_ascii=False),
                    connector.connection_status,
                    connector.latest_run_id,
                    connector.created_at.isoformat(),
                    now.isoformat(),
                ),
            )
        return self.get_connector(connector.workspace_id, connector.id)

    def get_connector(self, workspace_id: str, connector_id: str) -> SourceConnector:
        self._require_workspace(workspace_id)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM source_connectors WHERE id = ? AND workspace_id = ?",
                (connector_id, workspace_id),
            ).fetchone()
        if row is None:
            raise KeyError(connector_id)
        return self._connector_from_row(row)

    def list_connectors(self, workspace_id: str) -> list[SourceConnector]:
        self._require_workspace(workspace_id)
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM source_connectors WHERE workspace_id = ? ORDER BY created_at",
                (workspace_id,),
            ).fetchall()
        return [self._connector_from_row(row) for row in rows]

    def save_source_items(self, workspace_id: str, items: list[SourceItem]) -> list[SourceItem]:
        self._require_workspace(workspace_id)
        with self._connect() as conn:
            self._save_source_items(conn, workspace_id, items)
        return [self.get_source_item(workspace_id, item.id) for item in items]

    def get_source_item(self, workspace_id: str, item_id: str) -> SourceItem:
        self._require_workspace(workspace_id)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM source_items WHERE id = ? AND workspace_id = ?",
                (item_id, workspace_id),
            ).fetchone()
        if row is None:
            raise KeyError(item_id)
        return self._source_item_from_row(row)

    def set_source_item_use_state(
        self, workspace_id: str, item_id: str, use_state: str
    ) -> SourceItem:
        self._require_workspace(workspace_id)
        self._validate_use_state(use_state)
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE source_items SET use_state = ? WHERE id = ? AND workspace_id = ?",
                (use_state, item_id, workspace_id),
            )
        if cursor.rowcount == 0:
            raise KeyError(item_id)
        return self.get_source_item(workspace_id, item_id)

    def get_source_item_for_use(
        self, workspace_id: str, item_id: str, *, purpose: ContentUsePurpose
    ) -> SourceItem:
        item = self.get_source_item(workspace_id, item_id)
        if not content_allows_use(item.use_state, purpose):
            raise KeyError(item_id)
        return item

    def list_source_items(
        self, workspace_id: str, connector_id: str | None = None
    ) -> list[SourceItem]:
        self._require_workspace(workspace_id)
        query = "SELECT * FROM source_items WHERE workspace_id = ?"
        values: list[str] = [workspace_id]
        if connector_id:
            query += " AND connector_id = ?"
            values.append(connector_id)
        query += " ORDER BY created_at DESC"
        with self._connect() as conn:
            rows = conn.execute(query, values).fetchall()
        return [self._source_item_from_row(row) for row in rows]

    def list_source_items_for_use(
        self, workspace_id: str, *, purpose: ContentUsePurpose
    ) -> list[SourceItem]:
        return [
            item for item in self.list_source_items(workspace_id)
            if content_allows_use(item.use_state, purpose)
        ]

    def create_knowledge_item(
        self,
        item: KnowledgeItem,
    ) -> KnowledgeItem:
        self._require_workspace(item.workspace_id)
        self._validate_knowledge_item(item)
        if item.source_item_id:
            self.get_source_item(item.workspace_id, item.source_item_id)
        with self._connect() as conn:
            self._insert_knowledge_item(conn, item)
        return self.get_knowledge_item(item.workspace_id, item.id)

    def create_knowledge_item_owned(
        self, item: KnowledgeItem, *, verified_user_id: str | None
    ) -> KnowledgeItem:
        """保存知识条目，并把上传者归属绑定到这次已验证的身份。

        ``verified_user_id`` 由调用方从实际来源取到——已验证的 Bearer 身份，或
        local_members_service.resolve_member 返回的 member_id。归属不在这里猜，也不
        采信 dataclass 里自带的 owner_user_id：那是调用方可以随便填的字段。
        只有这个显式参数能决定归属。

        归属留空只允许在没有可解析身份的受控本地诊断写入；那种情况下这条内容对
        本机照常可读，但不会被算成任何成员的私有资产。
        """
        owner = str(verified_user_id or "").strip() or None
        return self.create_knowledge_item(replace(item, owner_user_id=owner))

    def _validate_knowledge_item(self, item: KnowledgeItem) -> None:
        if not item.title.strip() or not item.version.strip():
            raise ValueError("knowledge item requires title and version")
        self._validate_use_state(item.use_state)
        if not item.body and not item.file_reference:
            raise ValueError("knowledge item requires body or file_reference")

    @staticmethod
    def _validate_use_state(use_state: str) -> None:
        if use_state not in CONTENT_USE_STATES:
            raise ValueError(f"unknown content use state: {use_state}")

    def set_knowledge_item_use_state(
        self, workspace_id: str, item_id: str, use_state: str
    ) -> KnowledgeItem:
        self._require_workspace(workspace_id)
        self._validate_use_state(use_state)
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE knowledge_items SET use_state = ? WHERE id = ? AND workspace_id = ?",
                (use_state, item_id, workspace_id),
            )
        if cursor.rowcount == 0:
            raise KeyError(item_id)
        return self.get_knowledge_item(workspace_id, item_id)

    def _insert_knowledge_item(
        self, conn: sqlite3.Connection, item: KnowledgeItem
    ) -> None:
        existing = conn.execute(
            "SELECT workspace_id FROM knowledge_items WHERE id = ?", (item.id,)
        ).fetchone()
        if existing is not None:
            raise ValueError("knowledge item id already exists")
        conn.execute(
            """
            INSERT INTO knowledge_items (
                id, workspace_id, content_type, title, body, file_reference,
                authorization, ownership, source_item_id, version, created_at,
                visibility, content_hash, sections, parameters, fetched_at, source_url,
                owner_user_id, use_state
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item.id,
                item.workspace_id,
                item.content_type,
                item.title,
                item.body,
                item.file_reference,
                item.authorization.strip(),
                item.ownership.strip(),
                item.source_item_id,
                item.version.strip(),
                item.created_at.isoformat(),
                # 新建内容默认私有（用户 2026-09-29 拍板），显式写入而不是只靠
                # DDL 默认值：升级来的库没有那个 DEFAULT。
                CONTENT_VISIBILITY_DEFAULT,
                item.content_hash,
                _sections_json(item.sections),
                _parameters_json(item.parameters),
                item.fetched_at.isoformat() if item.fetched_at else None,
                item.source_url,
                # 上传者身份随内容落库，读取时据此判断私有内容归属。
                item.owner_user_id,
                item.use_state,
            ),
        )

    def save_source_item_to_knowledge(
        self,
        workspace_id: str,
        source_item_id: str,
        *,
        ownership: str = "",
        authorization: str = "",
        version: str = "1",
        verified_owner_user_id: str | None = None,
    ) -> KnowledgeItem:
        """保存为知识条目。手动来源保持原有行为，不参与文件版本链。

        上传者归属只认 ``verified_owner_user_id``（由调用方从已验证身份或成员
        令牌解析出）；不采信任何调用方自填的 owner_user_id。
        """
        owner = str(verified_owner_user_id or "").strip() or None
        source = self.get_source_item(workspace_id, source_item_id)
        if source.content_hash is None:
            # 未经过文件解析的来源（如手动链接）保持原有行为。
            return self.create_knowledge_item(
                KnowledgeItem(
                    owner_user_id=owner,
                    id=f"ki_{uuid4().hex}",
                    workspace_id=workspace_id,
                    content_type=source.content_type,
                    title=source.title,
                    body=source.body,
                    file_reference=source.file_reference,
                    authorization=authorization,
                    ownership=ownership,
                    source_item_id=source.id,
                    version=version,
                    use_state=source.use_state,
                )
            )
        return self._save_parsed_source_to_knowledge(
            workspace_id, source, ownership=ownership, authorization=authorization,
            verified_owner_user_id=owner,
        )

    def _save_parsed_source_to_knowledge(
        self,
        workspace_id: str,
        source: SourceItem,
        *,
        ownership: str,
        authorization: str,
        verified_owner_user_id: str | None = None,
    ) -> KnowledgeItem:
        """同内容不新增版本、不回滚旧版；原内容变化时保留旧版并追加新版本。

        文件来源：重复判断按内容指纹，不按随机 source_item_id；同一文件被导入两次、
        不同文件名下同一内容、以及重新导入某个历史版本的内容，都命中已有指纹。

        网页快照：按原网址分组。两个不同网址即使正文完全一样，也是两条独立出处，
        不能把后来的网址存进前一个网址的版本链丢掉它自己的原网址。
        """
        with self._connect() as conn:
            is_web_snapshot = source.source_label == "web_snapshot"
            scope_sql = "AND file_reference = ?" if is_web_snapshot else ""
            scope = (source.file_reference,) if is_web_snapshot else ()
            same_head = conn.execute(
                f"""
                SELECT * FROM knowledge_items
                WHERE workspace_id = ? AND content_hash = ? {scope_sql}
                ORDER BY created_at LIMIT 1
                """,
                (workspace_id, source.content_hash, *scope),
            ).fetchone()
            if same_head is not None:
                return self._knowledge_item_from_row(same_head)
            # 指纹已存在于历史版本：不新建、不改写旧快照，也不把当前版本回滚到旧内容。
            stored_version = conn.execute(
                f"""
                SELECT v.knowledge_item_id AS knowledge_item_id, v.version AS version
                FROM knowledge_item_versions v
                JOIN knowledge_items k ON k.id = v.knowledge_item_id
                WHERE v.workspace_id = ? AND v.content_hash = ?
                  AND k.workspace_id = ? AND k.content_hash <> v.content_hash
                  {"AND k.file_reference = ?" if is_web_snapshot else ""}
                ORDER BY CAST(v.version AS INTEGER) DESC LIMIT 1
                """,
                (workspace_id, source.content_hash, workspace_id, *scope),
            ).fetchone()
            if stored_version is not None:
                head = conn.execute(
                    "SELECT * FROM knowledge_items WHERE id = ? AND workspace_id = ?",
                    (stored_version["knowledge_item_id"], workspace_id),
                ).fetchone()
                return self._knowledge_item_from_row(head)
            # 原内容更新沿用同一知识条目：链按文件引用（网页即原网址）串，不按每次导入的
            # source_item_id。
            parent = (
                conn.execute(
                    """
                    SELECT * FROM knowledge_items
                    WHERE workspace_id = ? AND file_reference = ?
                      AND content_hash IS NOT NULL
                    ORDER BY created_at DESC LIMIT 1
                    """,
                    (workspace_id, source.file_reference),
                ).fetchone()
                if source.file_reference
                else None
            )
            if parent is None:
                parent = conn.execute(
                    """
                    SELECT * FROM knowledge_items
                    WHERE workspace_id = ? AND source_item_id = ?
                      AND content_hash IS NOT NULL
                    ORDER BY created_at DESC LIMIT 1
                    """,
                    (workspace_id, source.id),
                ).fetchone()
        if parent is None:
            item = KnowledgeItem(
                owner_user_id=str(verified_owner_user_id or "").strip() or None,
                id=f"ki_{uuid4().hex}",
                workspace_id=workspace_id,
                content_type=source.content_type,
                title=source.title,
                body=source.body,
                file_reference=source.file_reference,
                authorization=authorization,
                ownership=ownership,
                source_item_id=source.id,
                version="1",
                content_hash=source.content_hash,
                sections=source.sections,
                parameters=source.parameters,
                fetched_at=source.fetched_at,
                source_url=source.source_url,
                use_state=source.use_state,
            )
            self._validate_knowledge_item(item)
            # 知识条目头与它的第一版快照在同一事务写入，回查不会看到没有版本的条目。
            with self._connect() as conn:
                self._insert_knowledge_item(conn, item)
                self._insert_knowledge_version(
                    conn,
                    workspace_id=workspace_id,
                    knowledge_item_id=item.id,
                    version=item.version,
                    title=item.title,
                    body=item.body,
                    file_reference=item.file_reference,
                    content_hash=item.content_hash,
                    source_item_id=source.id,
                    sections=item.sections,
                    parameters=item.parameters,
                    saved_at=item.created_at,
                    fetched_at=source.fetched_at,
                    source_url=source.source_url,
                )
            return self.get_knowledge_item(workspace_id, item.id)
        previous = self._knowledge_item_from_row(parent)
        next_version = str(int(previous.version) + 1)
        # 新版本沿用已保存的归属和授权；校验与手工保存入口走同一条现有规则。
        self._validate_knowledge_item(
            KnowledgeItem(
                id=previous.id,
                workspace_id=workspace_id,
                content_type=source.content_type,
                title=source.title,
                body=source.body,
                file_reference=source.file_reference,
                authorization=previous.authorization,
                ownership=previous.ownership,
                source_item_id=source.id,
                version=next_version,
                content_hash=source.content_hash,
                sections=source.sections,
                parameters=source.parameters,
            )
        )
        # 当前版本头与新版本快照在同一个事务里写入，回查不会看到半套状态。
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE knowledge_items SET
                    content_type = ?, title = ?, body = ?, file_reference = ?,
                    version = ?, content_hash = ?, sections = ?, parameters = ?,
                    source_item_id = ?, fetched_at = ?, source_url = ?
                WHERE id = ? AND workspace_id = ?
                """,
                (
                    source.content_type,
                    source.title,
                    source.body,
                    source.file_reference,
                    next_version,
                    source.content_hash,
                    _sections_json(source.sections),
                    _parameters_json(source.parameters),
                    source.id,
                    source.fetched_at.isoformat() if source.fetched_at else None,
                    source.source_url,
                    previous.id,
                    workspace_id,
                ),
            )
            self._insert_knowledge_version(
                conn,
                workspace_id=workspace_id,
                knowledge_item_id=previous.id,
                version=next_version,
                title=source.title,
                body=source.body,
                file_reference=source.file_reference,
                content_hash=source.content_hash,
                source_item_id=source.id,
                sections=source.sections,
                parameters=source.parameters,
                saved_at=_utc_now(),
                fetched_at=source.fetched_at,
                source_url=source.source_url,
            )
        return self.get_knowledge_item(workspace_id, previous.id)

    def find_version_by_content_hash(
        self,
        workspace_id: str,
        content_hash: str,
        *,
        knowledge_item_id: str | None = None,
    ) -> KnowledgeItemVersion | None:
        """查出某个内容指纹对应的已保存历史版本（重复结果定位）。

        网页快照传 knowledge_item_id：只在自己的版本链里找，不会把另一个网址的
        版本当成这里的重复结果。
        """
        self._require_workspace(workspace_id)
        with self._connect() as conn:
            row = conn.execute(
                f"""
                SELECT * FROM knowledge_item_versions
                WHERE workspace_id = ? AND content_hash = ?
                {"AND knowledge_item_id = ?" if knowledge_item_id else ""}
                ORDER BY saved_at DESC LIMIT 1
                """,
                (workspace_id, content_hash, *((knowledge_item_id,) if knowledge_item_id else ())),
            ).fetchone()
        if row is None:
            return None
        return KnowledgeItemVersion(
            knowledge_item_id=row["knowledge_item_id"],
            version=row["version"],
            content_hash=row["content_hash"],
            source_item_id=row["source_item_id"],
            title=row["title"],
            body=row["body"],
            file_reference=row["file_reference"],
            sections=tuple(
                KnowledgeSection(**section) for section in json.loads(row["sections"])
            ),
            parameters=tuple(
                KnowledgeParameter(**parameter)
                for parameter in json.loads(row["parameters"])
            ),
            saved_at=_parse_datetime(row["saved_at"]),
            fetched_at=_parse_datetime(row["fetched_at"]) if row["fetched_at"] else None,
            source_url=row["source_url"],
        )

    def list_knowledge_item_versions(
        self, workspace_id: str, knowledge_item_id: str
    ) -> list[KnowledgeItemVersion]:
        self._require_workspace(workspace_id)
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM knowledge_item_versions
                WHERE workspace_id = ? AND knowledge_item_id = ?
                ORDER BY CAST(version AS INTEGER), saved_at
                """,
                (workspace_id, knowledge_item_id),
            ).fetchall()
        return [
            KnowledgeItemVersion(
                knowledge_item_id=row["knowledge_item_id"],
                version=row["version"],
                content_hash=row["content_hash"],
                source_item_id=row["source_item_id"],
                title=row["title"],
                body=row["body"],
                file_reference=row["file_reference"],
                sections=tuple(
                    KnowledgeSection(**section) for section in json.loads(row["sections"])
                ),
                parameters=tuple(
                    KnowledgeParameter(**parameter)
                    for parameter in json.loads(row["parameters"])
                ),
                saved_at=_parse_datetime(row["saved_at"]),
                fetched_at=_parse_datetime(row["fetched_at"]) if row["fetched_at"] else None,
                source_url=row["source_url"],
            )
            for row in rows
        ]

    def _insert_knowledge_version(
        self,
        conn: sqlite3.Connection,
        *,
        workspace_id: str,
        knowledge_item_id: str,
        version: str,
        title: str,
        body: str | None,
        file_reference: str | None,
        content_hash: str | None,
        source_item_id: str | None,
        sections: tuple[KnowledgeSection, ...],
        parameters: tuple[KnowledgeParameter, ...],
        saved_at: datetime,
        fetched_at: datetime | None = None,
        source_url: str | None = None,
    ) -> None:
        conn.execute(
            """
            INSERT INTO knowledge_item_versions (
                knowledge_item_id, workspace_id, version, content_hash, title,
                body, file_reference, source_item_id, sections, parameters, saved_at,
                fetched_at, source_url
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                knowledge_item_id,
                workspace_id,
                version,
                content_hash,
                title,
                body,
                file_reference,
                source_item_id,
                _sections_json(sections),
                _parameters_json(parameters),
                saved_at.isoformat(),
                fetched_at.isoformat() if fetched_at else None,
                source_url,
            ),
        )

    def get_knowledge_item(self, workspace_id: str, item_id: str) -> KnowledgeItem:
        self._require_workspace(workspace_id)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM knowledge_items WHERE id = ? AND workspace_id = ?",
                (item_id, workspace_id),
            ).fetchone()
        if row is None:
            raise KeyError(item_id)
        return self._knowledge_item_from_row(row)

    def list_knowledge_items(
        self,
        workspace_id: str,
        *,
        visibilities: Sequence[str] | None = None,
    ) -> list[KnowledgeItem]:
        """列出知识条目，可按可见范围过滤。

        ``visibilities=None`` 表示不过滤（保持旧行为，调用方自己负责）。
        传入如 ``("shared",)`` 时，private 行取不到——这就是「可见范围」在
        应用层的实现。

        诚实边界：这里只是**应用层过滤**，不构成安全隔离。方案 A 下 member 不带
        身份头仍能以 owner 身份调后端，真正的约束要等第②段的路由过滤与更后面的
        方案 B。本方法不得被当作权限边界使用。
        """
        self._require_workspace(workspace_id)
        with self._connect() as conn:
            if visibilities is None:
                rows = conn.execute(
                    "SELECT * FROM knowledge_items WHERE workspace_id = ? AND use_state != 'excluded' ORDER BY created_at DESC",
                    (workspace_id,),
                ).fetchall()
            else:
                wanted = [str(value).strip() for value in visibilities if str(value).strip()]
                if not wanted:
                    return []
                placeholders = ", ".join("?" for _ in wanted)
                rows = conn.execute(
                    f"SELECT * FROM knowledge_items WHERE workspace_id = ? "
                    f"AND visibility IN ({placeholders}) AND use_state != 'excluded' ORDER BY created_at DESC",
                    (workspace_id, *wanted),
                ).fetchall()
        return [self._knowledge_item_from_row(row) for row in rows]

    def list_knowledge_items_for_use(
        self, workspace_id: str, *, purpose: ContentUsePurpose,
        user_id: str | None, local_diagnostic: bool = False,
    ) -> list[KnowledgeItem]:
        return [
            item for item in self.list_knowledge_items_readable_by(
                workspace_id, user_id=user_id, local_diagnostic=local_diagnostic
            )
            if content_allows_use(item.use_state, purpose)
        ]

    @staticmethod
    def _knowledge_access_clause() -> str:
        # Both reads and references consume this one predicate. A shared flag
        # applies only inside the source workspace; cross-space needs a grant.
        return """(
            knowledge_items.use_state != 'excluded' AND (
            (knowledge_items.workspace_id = :workspace_id AND
                (visibility = :shared OR (visibility = :private AND owner_user_id = :user_id)))
            OR EXISTS (
                SELECT 1 FROM knowledge_grants AS grant
                WHERE grant.knowledge_item_id = knowledge_items.id
                  AND grant.source_workspace_id = knowledge_items.workspace_id
                  AND grant.target_workspace_id = :workspace_id
                  AND grant.revoked_at IS NULL
                  AND grant.granted_by = knowledge_items.owner_user_id
            ))
        )"""

    def list_knowledge_items_readable_by(
        self,
        workspace_id: str,
        *,
        user_id: str | None,
        local_diagnostic: bool = False,
    ) -> list[KnowledgeItem]:
        self._require_workspace(workspace_id)
        if user_id is None and not local_diagnostic:
            raise ValueError("读取知识必须带已验证身份，或显式的本地受控诊断范围")
        with self._connect() as conn:
            if local_diagnostic:
                rows = conn.execute(
                    "SELECT * FROM knowledge_items WHERE workspace_id = ? AND use_state != 'excluded' ORDER BY created_at DESC",
                    (workspace_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    f"SELECT * FROM knowledge_items WHERE {self._knowledge_access_clause()} ORDER BY created_at DESC",
                    {"workspace_id": workspace_id, "shared": CONTENT_VISIBILITY_LEGACY,
                     "private": CONTENT_VISIBILITY_DEFAULT, "user_id": user_id},
                ).fetchall()
        return [self._knowledge_item_from_row(row) for row in rows]

    def get_knowledge_item_readable_by(
        self,
        workspace_id: str,
        item_id: str,
        *,
        user_id: str | None,
        local_diagnostic: bool = False,
    ) -> KnowledgeItem:
        self._require_workspace(workspace_id)
        if user_id is None and not local_diagnostic:
            raise ValueError("读取知识必须带已验证身份，或显式的本地受控诊断范围")
        with self._connect() as conn:
            if local_diagnostic:
                row = conn.execute(
                    "SELECT * FROM knowledge_items WHERE workspace_id = ? AND id = ? AND use_state != 'excluded'",
                    (workspace_id, item_id),
                ).fetchone()
            else:
                row = conn.execute(
                    f"SELECT * FROM knowledge_items WHERE id = :item_id AND {self._knowledge_access_clause()}",
                    {"workspace_id": workspace_id, "item_id": item_id,
                     "shared": CONTENT_VISIBILITY_LEGACY, "private": CONTENT_VISIBILITY_DEFAULT,
                     "user_id": user_id},
                ).fetchone()
        if row is None:
            raise KeyError(item_id)
        return self._knowledge_item_from_row(row)

    def get_knowledge_item_for_use(
        self, workspace_id: str, item_id: str, *, purpose: ContentUsePurpose,
        user_id: str | None, local_diagnostic: bool = False,
    ) -> KnowledgeItem:
        """Resolve an authorized item only when its saved use state permits purpose."""
        item = self.get_knowledge_item_readable_by(
            workspace_id, item_id, user_id=user_id, local_diagnostic=local_diagnostic
        )
        if not content_allows_use(item.use_state, purpose):
            raise KeyError(item_id)
        return item

    def get_knowledge_version_readable_by(
        self, workspace_id: str, item_id: str, version: str, *,
        user_id: str | None, local_diagnostic: bool = False,
    ) -> KnowledgeItemVersion:
        item = self.get_knowledge_item_readable_by(
            workspace_id, item_id, user_id=user_id, local_diagnostic=local_diagnostic
        )
        # Manual entries have no snapshot chain: their exact current head is
        # the stored version. Never substitute it for a different version.
        if item.content_hash is None and item.version == version:
            return KnowledgeItemVersion(
                knowledge_item_id=item.id, version=item.version, content_hash=None,
                title=item.title, body=item.body, file_reference=item.file_reference,
                source_item_id=item.source_item_id, sections=item.sections,
                parameters=item.parameters, saved_at=item.created_at,
                fetched_at=item.fetched_at, source_url=item.source_url,
            )
        snapshots = self.list_knowledge_item_versions(item.workspace_id, item.id)
        for snapshot in snapshots:
            if snapshot.version == version:
                return snapshot
        raise KeyError((item_id, version))

    def get_knowledge_reference_readable_by(
        self, workspace_id: str, item_id: str, version: str, *,
        user_id: str | None, local_diagnostic: bool = False,
    ) -> KnowledgeItem:
        """Resolve an exact version only after checking today's read/reference grant."""
        item = self.get_knowledge_item_readable_by(
            workspace_id, item_id, user_id=user_id, local_diagnostic=local_diagnostic
        )
        # Manual entries have a current version but no historical snapshot chain.
        if item.version == version:
            return item
        snapshot = self.get_knowledge_version_readable_by(
            workspace_id, item_id, version, user_id=user_id, local_diagnostic=local_diagnostic
        )
        return replace(
            item, version=snapshot.version, title=snapshot.title, body=snapshot.body,
            file_reference=snapshot.file_reference, content_hash=snapshot.content_hash,
            source_item_id=snapshot.source_item_id, sections=snapshot.sections,
            parameters=snapshot.parameters, fetched_at=snapshot.fetched_at,
            source_url=snapshot.source_url,
        )

    @staticmethod
    def _grant_from_row(row: sqlite3.Row) -> KnowledgeGrant:
        return KnowledgeGrant(
            id=row["id"], knowledge_item_id=row["knowledge_item_id"],
            source_workspace_id=row["source_workspace_id"],
            target_workspace_id=row["target_workspace_id"],
            granted_by=row["granted_by"], granted_at=_parse_datetime(row["granted_at"]),
            revoked_by=row["revoked_by"],
            revoked_at=_parse_datetime(row["revoked_at"]) if row["revoked_at"] else None,
        )

    @staticmethod
    def _require_knowledge_grant_owner(
        conn: sqlite3.Connection, workspace_id: str, item_id: str, user_id: str,
    ) -> None:
        # Owning the device is not owning another member's private original.
        row = conn.execute(
            "SELECT owner_user_id FROM knowledge_items WHERE workspace_id=? AND id=?",
            (workspace_id, item_id),
        ).fetchone()
        if not user_id or row is None or row["owner_user_id"] != user_id:
            raise KeyError(item_id)

    def list_knowledge_grants(
        self, workspace_id: str, item_id: str, *, user_id: str,
    ) -> list[KnowledgeGrant]:
        with self._connect() as conn:
            self._require_knowledge_grant_owner(conn, workspace_id, item_id, user_id)
            rows = conn.execute(
                "SELECT * FROM knowledge_grants WHERE source_workspace_id=? AND knowledge_item_id=? ORDER BY granted_at",
                (workspace_id, item_id),
            ).fetchall()
        return [self._grant_from_row(row) for row in rows]

    def grant_knowledge_item(
        self, workspace_id: str, item_id: str, target_workspace_id: str, *, user_id: str,
    ) -> KnowledgeGrant:
        self._require_workspace(target_workspace_id)
        if target_workspace_id == workspace_id:
            raise ValueError("请选择其他工作空间")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._require_knowledge_grant_owner(conn, workspace_id, item_id, user_id)
            conn.execute(
                """INSERT INTO knowledge_grants
                    (id, knowledge_item_id, source_workspace_id, target_workspace_id, granted_by, granted_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(knowledge_item_id, target_workspace_id) WHERE revoked_at IS NULL DO NOTHING""",
                (str(uuid4()), item_id, workspace_id, target_workspace_id, user_id, _utc_now().isoformat()),
            )
            row = conn.execute(
                "SELECT * FROM knowledge_grants WHERE knowledge_item_id=? AND target_workspace_id=? AND revoked_at IS NULL",
                (item_id, target_workspace_id),
            ).fetchone()
        return self._grant_from_row(row)

    def revoke_knowledge_grant(
        self, workspace_id: str, item_id: str, grant_id: str, *, user_id: str,
    ) -> KnowledgeGrant:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._require_knowledge_grant_owner(conn, workspace_id, item_id, user_id)
            row = conn.execute(
                "SELECT * FROM knowledge_grants WHERE id=? AND source_workspace_id=? AND knowledge_item_id=?",
                (grant_id, workspace_id, item_id),
            ).fetchone()
            if row is None:
                raise KeyError(grant_id)
            if row["revoked_at"] is None:
                conn.execute(
                    "UPDATE knowledge_grants SET revoked_by=?, revoked_at=? WHERE id=?",
                    (user_id, _utc_now().isoformat(), grant_id),
                )
                row = conn.execute("SELECT * FROM knowledge_grants WHERE id=?", (grant_id,)).fetchone()
        return self._grant_from_row(row)

    def set_knowledge_item_owner(
        self, workspace_id: str, item_id: str, owner_user_id: str | None
    ) -> str:
        """记录这条知识的上传者身份；只写空值或本人，不改已有归属。"""
        self._require_workspace(workspace_id)
        owner = str(owner_user_id or "").strip() or None
        with self._connect() as conn:
            row = conn.execute(
                "SELECT owner_user_id FROM knowledge_items WHERE id = ? AND workspace_id = ?",
                (item_id, workspace_id),
            ).fetchone()
            if row is None:
                raise KeyError(item_id)
            existing = str(row["owner_user_id"] or "").strip()
            if existing:
                # 已经有归属就不覆盖：归属不是可随意改写的字段。
                return existing
            conn.execute(
                "UPDATE knowledge_items SET owner_user_id = ? WHERE id = ? AND workspace_id = ?",
                (owner, item_id, workspace_id),
            )
        return owner or ""

    def get_knowledge_item_visibility(self, workspace_id: str, item_id: str) -> str:
        """读单条知识的可见性。

        单独提供而不是塞进 KnowledgeItem：那个 dataclass 定义在 app/sources.py，
        不在本段授权面内，改它就是越权。
        """
        self._require_workspace(workspace_id)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT visibility FROM knowledge_items WHERE id = ? AND workspace_id = ?",
                (item_id, workspace_id),
            ).fetchone()
        if row is None:
            raise KeyError(item_id)
        value = row["visibility"]
        return str(value) if value else CONTENT_VISIBILITY_LEGACY

    def set_knowledge_item_visibility(self, workspace_id: str, item_id: str, visibility: str) -> str:
        """写单条知识的可见性，返回写入后的值。"""
        value = str(visibility).strip()
        if value not in (CONTENT_VISIBILITY_LEGACY, CONTENT_VISIBILITY_DEFAULT):
            raise ValueError("visibility 只能是 shared 或 private")
        self._require_workspace(workspace_id)
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE knowledge_items SET visibility = ? WHERE id = ? AND workspace_id = ?",
                (value, item_id, workspace_id),
            )
            if cursor.rowcount == 0:
                raise KeyError(item_id)
        return value

    def count_knowledge_items_by_visibility(self, workspace_id: str) -> dict[str, int]:
        """按可见性统计，用于迁移对账与测试断言。"""
        self._require_workspace(workspace_id)
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT visibility, COUNT(*) AS total FROM knowledge_items "
                "WHERE workspace_id = ? GROUP BY visibility",
                (workspace_id,),
            ).fetchall()
        counts = {CONTENT_VISIBILITY_LEGACY: 0, CONTENT_VISIBILITY_DEFAULT: 0}
        for row in rows:
            counts[str(row["visibility"])] = int(row["total"])
        return counts

    def record_sync_success(
        self,
        workspace_id: str,
        connector_id: str,
        items: list[SourceItem],
        cursor_after: str | None,
    ) -> SourceRunEvidence:
        connector = self.get_connector(workspace_id, connector_id)
        run = SourceRunEvidence(
            id=f"src_run_{uuid4().hex}",
            workspace_id=workspace_id,
            connector_id=connector_id,
            status="succeeded",
            started_at=_utc_now(),
            finished_at=_utc_now(),
            cursor_before=connector.cursor,
            cursor_after=cursor_after,
            item_count=len(items),
        )
        with self._connect() as conn:
            self._save_source_items(conn, workspace_id, items)
            self._insert_run(conn, run)
            conn.execute(
                """
                UPDATE source_connectors
                SET cursor_value = ?, connection_status = 'connected', latest_run_id = ?, updated_at = ?
                WHERE id = ? AND workspace_id = ?
                """,
                (cursor_after, run.id, run.finished_at.isoformat(), connector_id, workspace_id),
            )
        return run

    def record_sync_failure(
        self,
        workspace_id: str,
        connector_id: str,
        *,
        reason: str,
        impact: str,
        repair_action: str,
        retryable: bool,
        connection_status: str = "failed",
    ) -> SourceRunEvidence:
        connector = self.get_connector(workspace_id, connector_id)
        now = _utc_now()
        run = SourceRunEvidence(
            id=f"src_run_{uuid4().hex}",
            workspace_id=workspace_id,
            connector_id=connector_id,
            status="failed",
            started_at=now,
            finished_at=now,
            cursor_before=connector.cursor,
            cursor_after=connector.cursor,
            item_count=0,
            reason=reason,
            impact=impact,
            repair_action=repair_action,
            retryable=retryable,
        )
        with self._connect() as conn:
            self._insert_run(conn, run)
            conn.execute(
                """
                UPDATE source_connectors
                SET connection_status = ?, latest_run_id = ?, updated_at = ?
                WHERE id = ? AND workspace_id = ?
                """,
                (connection_status, run.id, now.isoformat(), connector_id, workspace_id),
            )
        return run

    def get_run(self, workspace_id: str, run_id: str) -> SourceRunEvidence:
        self._require_workspace(workspace_id)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM source_runs WHERE id = ? AND workspace_id = ?",
                (run_id, workspace_id),
            ).fetchone()
        if row is None:
            raise KeyError(run_id)
        return self._run_from_row(row)

    def list_runs(
        self, workspace_id: str, connector_id: str | None = None
    ) -> list[SourceRunEvidence]:
        self._require_workspace(workspace_id)
        query = "SELECT * FROM source_runs WHERE workspace_id = ?"
        values: list[str] = [workspace_id]
        if connector_id:
            query += " AND connector_id = ?"
            values.append(connector_id)
        query += " ORDER BY finished_at DESC"
        with self._connect() as conn:
            rows = conn.execute(query, values).fetchall()
        return [self._run_from_row(row) for row in rows]

    def _init_database(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                PRAGMA foreign_keys = ON;
                CREATE TABLE IF NOT EXISTS source_connectors (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    protocol TEXT NOT NULL,
                    account_or_rule TEXT NOT NULL,
                    frequency TEXT NOT NULL,
                    cursor_value TEXT,
                    permissions TEXT NOT NULL,
                    connection_status TEXT NOT NULL,
                    latest_run_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_source_connectors_workspace
                    ON source_connectors(workspace_id);
                CREATE TABLE IF NOT EXISTS source_items (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    connector_id TEXT NOT NULL,
                    source_label TEXT NOT NULL DEFAULT '',
                    source_url TEXT,
                    content_type TEXT NOT NULL,
                    author TEXT,
                    published_at TEXT,
                    body TEXT,
                    subtitles TEXT,
                    media_references TEXT NOT NULL,
                    license TEXT NOT NULL,
                    title TEXT NOT NULL,
                    file_reference TEXT,
                    fetched_at TEXT,
                    created_at TEXT NOT NULL,
                    use_state TEXT NOT NULL DEFAULT 'available',
                    FOREIGN KEY(connector_id) REFERENCES source_connectors(id)
                );
                CREATE INDEX IF NOT EXISTS idx_source_items_workspace
                    ON source_items(workspace_id, connector_id, created_at);
                CREATE TABLE IF NOT EXISTS knowledge_items (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    content_type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    body TEXT,
                    file_reference TEXT,
                    authorization TEXT NOT NULL,
                    ownership TEXT NOT NULL,
                    source_item_id TEXT,
                    version TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    -- 新建内容默认私有（用户 2026-09-29 拍板）。注意：这里的 DEFAULT
                    -- 只管 fresh 库的 INSERT；升级库的存量行由
                    -- ensure_content_visibility_columns 回填为 shared。
                    visibility TEXT NOT NULL DEFAULT 'private',
                    -- 上传者身份（已验证的 user_id）；旧行与本地无身份写入留空。
                    owner_user_id TEXT,
                    use_state TEXT NOT NULL DEFAULT 'available',
                    FOREIGN KEY(source_item_id) REFERENCES source_items(id)
                );
                CREATE INDEX IF NOT EXISTS idx_knowledge_items_workspace
                    ON knowledge_items(workspace_id, created_at);
                CREATE TABLE IF NOT EXISTS knowledge_item_versions (
                    knowledge_item_id TEXT NOT NULL,
                    workspace_id TEXT NOT NULL,
                    version TEXT NOT NULL,
                    content_hash TEXT,
                    title TEXT NOT NULL,
                    body TEXT,
                    file_reference TEXT,
                    source_item_id TEXT,
                    sections TEXT NOT NULL DEFAULT '[]',
                    parameters TEXT NOT NULL DEFAULT '[]',
                    fetched_at TEXT,
                    source_url TEXT,
                    saved_at TEXT NOT NULL,
                    PRIMARY KEY (knowledge_item_id, version),
                    FOREIGN KEY(knowledge_item_id) REFERENCES knowledge_items(id)
                );
                CREATE INDEX IF NOT EXISTS idx_knowledge_item_versions_workspace
                    ON knowledge_item_versions(workspace_id, saved_at DESC);
                CREATE TABLE IF NOT EXISTS source_runs (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    connector_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT NOT NULL,
                    cursor_before TEXT,
                    cursor_after TEXT,
                    item_count INTEGER NOT NULL,
                    reason TEXT,
                    impact TEXT,
                    repair_action TEXT,
                    retryable INTEGER,
                    FOREIGN KEY(connector_id) REFERENCES source_connectors(id)
                );
                CREATE INDEX IF NOT EXISTS idx_source_runs_workspace
                    ON source_runs(workspace_id, connector_id, finished_at DESC);
                """
            )
            columns = {
                row[1] for row in conn.execute("PRAGMA table_info(source_items)")
            }
            if "source_label" not in columns:
                conn.execute(
                    "ALTER TABLE source_items ADD COLUMN source_label TEXT NOT NULL DEFAULT ''"
                )
            # 补齐内容可见性列（knowledge_items 属于本段；另两张表由
            # ProductionRepository 打开时补，本段不写 production.py）。
            ensure_content_visibility_columns(conn)
            # 本卡（WIL-103）新增列；升级库在缺列时补齐，fresh 库由上面 DDL 直接建。
            for table in ("source_items", "knowledge_items"):
                existing = {
                    row[1] for row in conn.execute(f"PRAGMA table_info({table})")
                }
                for column, ddl in (
                    ("content_hash", "TEXT"),
                    ("sections", "TEXT NOT NULL DEFAULT '[]'"),
                    ("parameters", "TEXT NOT NULL DEFAULT '[]'"),
                ):
                    if column not in existing:
                        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
            # 本卡（WIL-110）新增上传者身份列：私有内容据此只对上传者可读。
            knowledge_columns = {
                row[1] for row in conn.execute("PRAGMA table_info(knowledge_items)")
            }
            if "owner_user_id" not in knowledge_columns:
                conn.execute("ALTER TABLE knowledge_items ADD COLUMN owner_user_id TEXT")
            for table in ("source_items", "knowledge_items"):
                existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
                if "use_state" not in existing:
                    conn.execute(
                        f"ALTER TABLE {table} ADD COLUMN use_state TEXT NOT NULL DEFAULT 'available'"
                    )
            # 本卡（WIL-134）新增网页快照列：抓取时间和当次读到的网址。
            for table in ("source_items", "knowledge_items"):
                existing = {
                    row[1] for row in conn.execute(f"PRAGMA table_info({table})")
                }
                for column, ddl in (
                    ("fetched_at", "TEXT"),
                    ("source_url", "TEXT"),
                ):
                    if column not in existing:
                        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
            version_columns = {
                row[1]
                for row in conn.execute("PRAGMA table_info(knowledge_item_versions)")
            }
            for column in ("fetched_at", "source_url"):
                if column not in version_columns:
                    conn.execute(
                        f"ALTER TABLE knowledge_item_versions ADD COLUMN {column} TEXT"
                    )

            conn.executescript(KNOWLEDGE_GRANTS_DDL)

    def _insert_run(self, conn: sqlite3.Connection, run: SourceRunEvidence) -> None:
        conn.execute(
            """
            INSERT INTO source_runs (
                id, workspace_id, connector_id, status, started_at, finished_at,
                cursor_before, cursor_after, item_count, reason, impact,
                repair_action, retryable
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run.id, run.workspace_id, run.connector_id, run.status,
                run.started_at.isoformat(), run.finished_at.isoformat(),
                run.cursor_before, run.cursor_after, run.item_count, run.reason,
                run.impact, run.repair_action,
                int(run.retryable) if run.retryable is not None else None,
            ),
        )

    def _save_source_items(
        self,
        conn: sqlite3.Connection,
        workspace_id: str,
        items: list[SourceItem],
    ) -> None:
        for item in items:
            self._validate_source_item(workspace_id, item)
            existing_item = conn.execute(
                "SELECT workspace_id FROM source_items WHERE id = ?", (item.id,)
            ).fetchone()
            if (
                existing_item is not None
                and existing_item["workspace_id"] != workspace_id
            ):
                raise ValueError("source item id already belongs to another workspace")
            connector = conn.execute(
                "SELECT 1 FROM source_connectors WHERE id = ? AND workspace_id = ?",
                (item.connector_id, workspace_id),
            ).fetchone()
            if connector is None:
                raise KeyError(item.connector_id)
            # A normal refresh may update source facts but must not reset an explicit
            # use restriction; callers change it through set_source_item_use_state.
            conn.execute(
                """
                INSERT INTO source_items (
                    id, workspace_id, connector_id, source_label, source_url, content_type,
                    author, published_at, body, subtitles, media_references,
                    license, title, file_reference, created_at, content_hash,
                    sections, parameters, fetched_at, use_state
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    source_label = excluded.source_label,
                    source_url = excluded.source_url,
                    content_type = excluded.content_type,
                    author = excluded.author,
                    published_at = excluded.published_at,
                    body = excluded.body,
                    subtitles = excluded.subtitles,
                    media_references = excluded.media_references,
                    license = excluded.license,
                    title = excluded.title,
                    file_reference = excluded.file_reference,
                    content_hash = excluded.content_hash,
                    sections = excluded.sections,
                    parameters = excluded.parameters,
                    fetched_at = excluded.fetched_at
                WHERE source_items.workspace_id = excluded.workspace_id
                """,
                (
                    item.id,
                    item.workspace_id,
                    item.connector_id,
                    item.source_label,
                    item.source_url,
                    item.content_type,
                    item.author,
                    item.published_at.isoformat() if item.published_at else None,
                    item.body,
                    item.subtitles,
                    json.dumps(item.media_references, ensure_ascii=False),
                    item.license,
                    item.title,
                    item.file_reference,
                    item.created_at.isoformat(),
                    item.content_hash,
                    _sections_json(item.sections),
                    _parameters_json(item.parameters),
                    item.fetched_at.isoformat() if item.fetched_at else None,
                    item.use_state,
                ),
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._database_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _connector_from_row(self, row: sqlite3.Row) -> SourceConnector:
        return SourceConnector(
            id=row["id"], workspace_id=row["workspace_id"], platform=row["platform"],
            protocol=row["protocol"], account_or_rule=row["account_or_rule"],
            frequency=row["frequency"], cursor=row["cursor_value"],
            permissions=tuple(json.loads(row["permissions"])),
            connection_status=row["connection_status"], latest_run_id=row["latest_run_id"],
            created_at=_parse_datetime(row["created_at"]),
        )

    def _source_item_from_row(self, row: sqlite3.Row) -> SourceItem:
        return SourceItem(
            id=row["id"], workspace_id=row["workspace_id"], connector_id=row["connector_id"],
            source_label=row["source_label"], source_url=row["source_url"],
            content_type=row["content_type"], author=row["author"],
            published_at=_parse_datetime(row["published_at"]) if row["published_at"] else None,
            body=row["body"], subtitles=row["subtitles"],
            media_references=tuple(json.loads(row["media_references"])),
            license=row["license"], title=row["title"], file_reference=row["file_reference"],
            content_hash=row["content_hash"],
            sections=tuple(KnowledgeSection(**section) for section in json.loads(row["sections"])),
            parameters=tuple(KnowledgeParameter(**parameter) for parameter in json.loads(row["parameters"])),
            fetched_at=_parse_datetime(row["fetched_at"]) if row["fetched_at"] else None,
            use_state=row["use_state"],
            created_at=_parse_datetime(row["created_at"]),
        )

    def _knowledge_item_from_row(self, row: sqlite3.Row) -> KnowledgeItem:
        return KnowledgeItem(
            id=row["id"], workspace_id=row["workspace_id"], content_type=row["content_type"],
            title=row["title"], body=row["body"], file_reference=row["file_reference"],
            authorization=row["authorization"], ownership=row["ownership"],
            source_item_id=row["source_item_id"], version=row["version"],
            content_hash=row["content_hash"],
            sections=tuple(KnowledgeSection(**section) for section in json.loads(row["sections"])),
            parameters=tuple(KnowledgeParameter(**parameter) for parameter in json.loads(row["parameters"])),
            fetched_at=_parse_datetime(row["fetched_at"]) if row["fetched_at"] else None,
            source_url=row["source_url"],
            owner_user_id=str(row["owner_user_id"]) if row["owner_user_id"] else None,
            use_state=row["use_state"],
            created_at=_parse_datetime(row["created_at"]),
        )

    def _run_from_row(self, row: sqlite3.Row) -> SourceRunEvidence:
        return SourceRunEvidence(
            id=row["id"], workspace_id=row["workspace_id"], connector_id=row["connector_id"],
            status=row["status"], started_at=_parse_datetime(row["started_at"]),
            finished_at=_parse_datetime(row["finished_at"]), cursor_before=row["cursor_before"],
            cursor_after=row["cursor_after"], item_count=row["item_count"], reason=row["reason"],
            impact=row["impact"], repair_action=row["repair_action"],
            retryable=bool(row["retryable"]) if row["retryable"] is not None else None,
        )

    def _validate_source_item(self, workspace_id: str, item: SourceItem) -> None:
        if item.workspace_id != workspace_id:
            raise ValueError("source item workspace does not match save workspace")
        self._validate_use_state(item.use_state)
        if (
            not item.title.strip()
            or not item.source_label.strip()
            or not item.content_type.strip()
            or not item.license.strip()
        ):
            raise ValueError("source item requires source_label, title, content_type, and license")
        if not (
            item.source_url
            or item.file_reference
            or item.body
            or item.subtitles
            or item.media_references
        ):
            raise ValueError("source item requires content, source_url, or file_reference")

    def _require_workspace(self, workspace_id: str) -> None:
        if not workspace_id.strip():
            raise ValueError("workspace_id is required")


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _sections_json(sections: Sequence[KnowledgeSection]) -> str:
    return json.dumps(
        [
            {"locator": section.locator, "title": section.title, "text": section.text}
            for section in sections
        ],
        ensure_ascii=False,
    )


def _parameters_json(parameters: Sequence[KnowledgeParameter]) -> str:
    return json.dumps(
        [
            {
                "key": parameter.key,
                "value": parameter.value,
                "unit": parameter.unit,
                "locator": parameter.locator,
            }
            for parameter in parameters
        ],
        ensure_ascii=False,
    )


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
