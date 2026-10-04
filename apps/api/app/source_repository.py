from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Sequence
from uuid import uuid4

from app.sources import (
    KNOWLEDGE_UNCLASSIFIED,
    KNOWLEDGE_USE_CATEGORIES,
    KnowledgeItem,
    KnowledgeItemVersion,
    SourceConnector,
    SourceItem,
    SourceRunEvidence,
)
from app.database_migrations import (
    CONTENT_VISIBILITY_DEFAULT,
    CONTENT_VISIBILITY_LEGACY,
    ensure_content_visibility_columns,
)
from app.knowledge_conflicts import (
    KnowledgeConflictError,
    ParameterAdjudication,
    ParameterConflict,
    ParameterScope,
    ParameterSide,
    adjudicate_parameter_conflict,
    build_parameter_conflict,
)
from app.knowledge_files import KnowledgeParameter, KnowledgeSection


class SourceRepository:
    """Workspace-partitioned persistence for knowledge and external sources.

    This intentionally uses the application's existing SQLite database path.
    HTTP composition owns the repository instance; this module owns only the
    source/knowledge tables and never falls back across workspace boundaries.
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

    def _validate_knowledge_item(self, item: KnowledgeItem) -> None:
        if (
            not item.title.strip()
            or not all(
                (item.ownership.strip(), item.authorization.strip(), item.version.strip())
            )
        ):
            raise ValueError(
                "knowledge item requires title, ownership, authorization, and version"
            )
        if not item.body and not item.file_reference:
            raise ValueError("knowledge item requires body or file_reference")

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
                use_category, topics, parse_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                _validated_use_category(item.use_category),
                _topics_json(item.topics),
                item.parse_status or _parse_status(item.sections, item.parameters),
            ),
        )

    def save_source_item_to_knowledge(
        self,
        workspace_id: str,
        source_item_id: str,
        *,
        ownership: str,
        authorization: str,
        version: str = "1",
    ) -> KnowledgeItem:
        """保存为知识条目。手动来源保持原有行为，不参与文件版本链。"""
        source = self.get_source_item(workspace_id, source_item_id)
        if source.content_hash is None:
            # 未经过文件解析的来源（如手动链接）保持原有行为。
            return self.create_knowledge_item(
                KnowledgeItem(
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
                )
            )
        return self._save_parsed_source_to_knowledge(
            workspace_id, source, ownership=ownership, authorization=authorization
        )

    def _save_parsed_source_to_knowledge(
        self,
        workspace_id: str,
        source: SourceItem,
        *,
        ownership: str,
        authorization: str,
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
                    source_item_id = ?, fetched_at = ?, source_url = ?,
                    parse_status = ?
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
                    _parse_status(source.sections, source.parameters),
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
        use_category: str | None = None,
        topic: str | None = None,
    ) -> list[KnowledgeItem]:
        """列出知识条目，可按可见范围、用户选择的用途分类和主题筛选。

        ``visibilities=None`` 表示不过滤（保持旧行为，调用方自己负责）。
        传入如 ``("shared",)`` 时，private 行取不到——这就是「可见范围」在
        应用层的实现。

        诚实边界：这里只是**应用层过滤**，不构成安全隔离。方案 A 下 member 不带
        身份头仍能以 owner 身份调后端，真正的约束要等第②段的路由过滤与更后面的
        方案 B。本方法不得被当作权限边界使用。
        """
        self._require_workspace(workspace_id)
        with self._connect() as conn:
            conditions = ["workspace_id = ?"]
            values: list[object] = [workspace_id]
            if visibilities is not None:
                wanted = [str(value).strip() for value in visibilities if str(value).strip()]
                if not wanted:
                    return []
                conditions.append(
                    f"visibility IN ({', '.join('?' for _ in wanted)})"
                )
                values.extend(wanted)
            if use_category is not None:
                # 空字符串就是未分类：只取还没有用户选择的分类的行。
                conditions.append(
                    "use_category IS NULL" if not use_category.strip() else "use_category = ?"
                )
                if use_category.strip():
                    values.append(_validated_use_category(use_category))
            if topic is not None and topic.strip():
                # 主题关系存在 topics JSON 里，按精确值匹配而不是模糊包含。
                conditions.append("EXISTS (SELECT 1 FROM json_each(knowledge_items.topics) WHERE json_each.value = ?)")
                values.append(topic.strip())
            rows = conn.execute(
                "SELECT * FROM knowledge_items WHERE "
                + " AND ".join(conditions)
                + " ORDER BY created_at DESC",
                values,
            ).fetchall()
        return [self._knowledge_item_from_row(row) for row in rows]

    def count_knowledge_items_by_use_category(self, workspace_id: str) -> dict[str, int]:
        """按真实用途分类统计。

        只有真正的 NULL（还没选分类）计入未分类；不把任何其他值静默兜底成
        未分类——出现枚举外的值就按它本名单列，不隐藏异常数据。
        """
        self._require_workspace(workspace_id)
        counts = {name: 0 for name in KNOWLEDGE_USE_CATEGORIES}
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT use_category, COUNT(*) AS total FROM knowledge_items "
                "WHERE workspace_id = ? GROUP BY use_category",
                (workspace_id,),
            ).fetchall()
        for row in rows:
            raw = row["use_category"]
            label = KNOWLEDGE_UNCLASSIFIED if raw is None else str(raw)
            counts[label] = counts.get(label, 0) + int(row["total"])
        return counts

    def list_knowledge_topics(self, workspace_id: str) -> list[str]:
        """列出用户已登记的全部主题/对象关系，按真实使用顺序去重。"""
        self._require_workspace(workspace_id)
        seen: dict[str, None] = {}
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT topics FROM knowledge_items WHERE workspace_id = ? "
                "ORDER BY created_at DESC",
                (workspace_id,),
            ).fetchall()
        for row in rows:
            for topic in json.loads(row["topics"] or "[]"):
                seen.setdefault(str(topic), None)
        return list(seen)

    def set_knowledge_item_classification(
        self,
        workspace_id: str,
        item_id: str,
        *,
        use_category: str | None,
        topics: Sequence[str],
    ) -> KnowledgeItem:
        """保存用户明确选择的用途分类与主题/对象关系。

        两个入参都是用户确认的结果；``None``/空列表表示清空，不从内容推断补齐。
        """
        self._require_workspace(workspace_id)
        category = _validated_use_category(use_category)
        cleaned = _clean_topics(topics)
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE knowledge_items SET use_category = ?, topics = ? "
                "WHERE id = ? AND workspace_id = ?",
                (category, _topics_json(cleaned), item_id, workspace_id),
            )
            if cursor.rowcount == 0:
                raise KeyError(item_id)
        return self.get_knowledge_item(workspace_id, item_id)

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
                    -- 用途分类与主题/对象关系：用户明确选择后落库，不按格式推断。
                    use_category TEXT,
                    topics TEXT NOT NULL DEFAULT '[]',
                    parse_status TEXT,
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
                CREATE TABLE IF NOT EXISTS knowledge_parameter_comparisons (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    object_label TEXT NOT NULL,
                    parameter_key TEXT NOT NULL,
                    unit TEXT NOT NULL,
                    condition TEXT NOT NULL,
                    left_knowledge_item_id TEXT NOT NULL,
                    left_version TEXT NOT NULL,
                    left_value TEXT NOT NULL,
                    left_locator TEXT NOT NULL,
                    right_knowledge_item_id TEXT NOT NULL,
                    right_version TEXT NOT NULL,
                    right_value TEXT NOT NULL,
                    right_locator TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(left_knowledge_item_id) REFERENCES knowledge_items(id),
                    FOREIGN KEY(right_knowledge_item_id) REFERENCES knowledge_items(id)
                );
                CREATE INDEX IF NOT EXISTS idx_parameter_comparisons_workspace
                    ON knowledge_parameter_comparisons(workspace_id, created_at DESC);
                CREATE TABLE IF NOT EXISTS knowledge_parameter_adjudications (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    conflict_id TEXT NOT NULL,
                    operator TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    decided_at TEXT NOT NULL,
                    left_version_ref TEXT NOT NULL,
                    right_version_ref TEXT NOT NULL,
                    retained_side TEXT NOT NULL,
                    retained_value TEXT NOT NULL,
                    corrected_value TEXT,
                    FOREIGN KEY(conflict_id) REFERENCES knowledge_parameter_comparisons(id)
                );
                CREATE INDEX IF NOT EXISTS idx_parameter_adjudications_conflict
                    ON knowledge_parameter_adjudications(conflict_id, decided_at DESC);
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
            # 本卡（WIL-105）知识用途分类、主题关系与解析状态。
            # 升级库只补列：存量行留NULL表示「还没有用户选择的分类」，不按格式回填。
            knowledge_columns = {
                row[1] for row in conn.execute("PRAGMA table_info(knowledge_items)")
            }
            for column, ddl in (
                ("use_category", "TEXT"),
                ("topics", "TEXT NOT NULL DEFAULT '[]'"),
                ("parse_status", "TEXT"),
            ):
                if column not in knowledge_columns:
                    conn.execute(f"ALTER TABLE knowledge_items ADD COLUMN {column} {ddl}")

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
            conn.execute(
                """
                INSERT INTO source_items (
                    id, workspace_id, connector_id, source_label, source_url, content_type,
                    author, published_at, body, subtitles, media_references,
                    license, title, file_reference, created_at, content_hash,
                    sections, parameters, fetched_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            created_at=_parse_datetime(row["created_at"]),
            use_category=row["use_category"],
            topics=tuple(json.loads(row["topics"] or "[]")),
            parse_status=row["parse_status"],
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

    def create_parameter_comparison(
        self,
        workspace_id: str,
        *,
        object_label: str,
        parameter_key: str,
        unit: str,
        condition: str,
        left_knowledge_item_id: str,
        left_version: str,
        left_locator: str,
        right_knowledge_item_id: str,
        right_version: str,
        right_locator: str,
    ) -> ParameterConflict:
        """建立一次参数比较：两侧证据固定到指定知识条目的指定版本。

        两侧的值由用户明确给出（来自那两个版本的真实参数），范围也由用户确认；
        这里只负责落库，不猜对象、同义词或单位换算。
        """
        self._require_workspace(workspace_id)
        missing = [
            name
            for name, value in (
                ("object_label", object_label),
                ("parameter_key", parameter_key),
                ("unit", unit),
                ("condition", condition),
            )
            if not str(value or "").strip()
        ]
        if missing:
            raise KnowledgeConflictError(
                "比较范围需要明确的对象、参数名、单位和适用条件，缺少：" + "、".join(missing)
            )
        left = self._parameter_side(
            workspace_id,
            left_knowledge_item_id,
            left_version,
            parameter_key=parameter_key,
            unit=unit,
            locator=left_locator,
        )
        right = self._parameter_side(
            workspace_id,
            right_knowledge_item_id,
            right_version,
            parameter_key=parameter_key,
            unit=unit,
            locator=right_locator,
        )
        conflict = build_parameter_conflict(
            workspace_id,
            object_label=object_label,
            parameter_key=parameter_key,
            unit=unit,
            condition=condition,
            left=left,
            right=right,
        )
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO knowledge_parameter_comparisons (
                    id, workspace_id, object_label, parameter_key, unit, condition,
                    left_knowledge_item_id, left_version, left_value, left_locator,
                    right_knowledge_item_id, right_version, right_value, right_locator,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    conflict.id,
                    workspace_id,
                    conflict.scope.object_label,
                    conflict.scope.parameter_key,
                    conflict.scope.unit,
                    conflict.scope.condition,
                    left.knowledge_item_id,
                    left.version,
                    left.value,
                    left.locator,
                    right.knowledge_item_id,
                    right.version,
                    right.value,
                    right.locator,
                    conflict.created_at.isoformat(),
                ),
            )
        return conflict

    def _parameter_side(
        self,
        workspace_id: str,
        knowledge_item_id: str,
        version: str,
        *,
        parameter_key: str,
        unit: str,
        locator: str,
    ) -> ParameterSide:
        """取某一侧证据：必须真实存在于当前工作区的那一版，且参数名、单位和
        用户选中的那条出处都对得上。

        单位对不上就是另一个比较范围，不并进来也不换算；同名同单位出现在多个
        段落时以用户选中的出处为准，不自动挑第一条。
        """
        self.get_knowledge_item(workspace_id, knowledge_item_id)
        for stored in self.list_knowledge_item_versions(workspace_id, knowledge_item_id):
            if stored.version != version:
                continue
            for parameter in stored.parameters:
                if parameter.key.strip() != parameter_key.strip():
                    continue
                if (parameter.unit or "").strip() != unit.strip():
                    continue
                if parameter.locator.strip() != locator.strip():
                    continue
                return ParameterSide(
                    knowledge_item_id=knowledge_item_id,
                    version=stored.version,
                    value=parameter.value,
                    locator=parameter.locator,
                    title=stored.title,
                )
            raise KnowledgeConflictError(
                f"版本 {version} 里没有参数「{parameter_key}」（单位 {unit}，"
                f"出处 {locator}）"
            )
        raise KnowledgeConflictError(f"知识条目没有版本 {version}")

    def get_parameter_comparison(
        self, workspace_id: str, comparison_id: str
    ) -> ParameterConflict:
        self._require_workspace(workspace_id)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM knowledge_parameter_comparisons WHERE id = ? AND workspace_id = ?",
                (comparison_id, workspace_id),
            ).fetchone()
            if row is None:
                raise KeyError(comparison_id)
            adjudication_row = conn.execute(
                """
                SELECT * FROM knowledge_parameter_adjudications
                WHERE conflict_id = ? AND workspace_id = ?
                ORDER BY decided_at DESC LIMIT 1
                """,
                (comparison_id, workspace_id),
            ).fetchone()
        return _comparison_from_row(row, adjudication_row)

    def list_parameter_comparisons(
        self,
        workspace_id: str,
        *,
        knowledge_item_id: str | None = None,
    ) -> list[ParameterConflict]:
        self._require_workspace(workspace_id)
        with self._connect() as conn:
            if knowledge_item_id:
                rows = conn.execute(
                    """
                    SELECT * FROM knowledge_parameter_comparisons
                    WHERE workspace_id = ?
                      AND (left_knowledge_item_id = ? OR right_knowledge_item_id = ?)
                    ORDER BY created_at DESC
                    """,
                    (workspace_id, knowledge_item_id, knowledge_item_id),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM knowledge_parameter_comparisons
                    WHERE workspace_id = ? ORDER BY created_at DESC
                    """,
                    (workspace_id,),
                ).fetchall()
            adjudications = {
                item["conflict_id"]: item
                for item in conn.execute(
                    """
                    SELECT a.* FROM knowledge_parameter_adjudications a
                    JOIN knowledge_parameter_comparisons c ON c.id = a.conflict_id
                    WHERE a.workspace_id = ? ORDER BY a.decided_at
                    """,
                    (workspace_id,),
                ).fetchall()
            }
        return [
            _comparison_from_row(row, adjudications.get(str(row["id"])))
            for row in rows
        ]

    def adjudicate_parameter_comparison(
        self,
        workspace_id: str,
        comparison_id: str,
        *,
        operator: str,
        reason: str,
        retained_side: str,
        retained_value: str | None = None,
        corrected_value: str | None = None,
    ) -> ParameterConflict:
        """记录一次人工裁定。历史裁定不覆盖：新裁定追加，旧证据保留。

        裁定绑定在当时的两个版本上；任一侧出现新版本时，比较是新冲突，
        不会自动沿用这里的结论。
        """
        conflict = self.get_parameter_comparison(workspace_id, comparison_id)
        adjudicated = adjudicate_parameter_conflict(
            workspace_id,
            conflict,
            operator=operator,
            reason=reason,
            retained_side=retained_side,
            retained_value=retained_value,
            corrected_value=corrected_value,
        )
        assert adjudicated.adjudication is not None
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO knowledge_parameter_adjudications (
                    id, workspace_id, conflict_id, operator, reason, decided_at,
                    left_version_ref, right_version_ref, retained_side,
                    retained_value, corrected_value
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    adjudicated.adjudication.id,
                    workspace_id,
                    comparison_id,
                    adjudicated.adjudication.operator,
                    adjudicated.adjudication.reason,
                    adjudicated.adjudication.decided_at.isoformat(),
                    adjudicated.adjudication.left_version_ref,
                    adjudicated.adjudication.right_version_ref,
                    adjudicated.adjudication.retained_side,
                    adjudicated.adjudication.retained_value,
                    adjudicated.adjudication.corrected_value,
                ),
            )
        return self.get_parameter_comparison(workspace_id, comparison_id)

    def _require_workspace(self, workspace_id: str) -> None:
        if not workspace_id.strip():
            raise ValueError("workspace_id is required")


def _comparison_from_row(
    row: sqlite3.Row, adjudication_row: sqlite3.Row | None
) -> ParameterConflict:
    adjudication = (
        ParameterAdjudication(
            id=str(adjudication_row["id"]),
            workspace_id=str(adjudication_row["workspace_id"]),
            conflict_id=str(adjudication_row["conflict_id"]),
            operator=str(adjudication_row["operator"]),
            reason=str(adjudication_row["reason"]),
            decided_at=_parse_datetime(adjudication_row["decided_at"]),
            left_version_ref=str(adjudication_row["left_version_ref"]),
            right_version_ref=str(adjudication_row["right_version_ref"]),
            retained_side=str(adjudication_row["retained_side"]),
            retained_value=str(adjudication_row["retained_value"]),
            corrected_value=(
                str(adjudication_row["corrected_value"])
                if adjudication_row["corrected_value"]
                else None
            ),
        )
        if adjudication_row is not None
        else None
    )
    return ParameterConflict(
        id=str(row["id"]),
        workspace_id=str(row["workspace_id"]),
        scope=ParameterScope(
            object_label=str(row["object_label"]),
            parameter_key=str(row["parameter_key"]),
            unit=str(row["unit"]),
            condition=str(row["condition"]),
        ),
        left=ParameterSide(
            knowledge_item_id=str(row["left_knowledge_item_id"]),
            version=str(row["left_version"]),
            value=str(row["left_value"]),
            locator=str(row["left_locator"]),
        ),
        right=ParameterSide(
            knowledge_item_id=str(row["right_knowledge_item_id"]),
            version=str(row["right_version"]),
            value=str(row["right_value"]),
            locator=str(row["right_locator"]),
        ),
        created_at=_parse_datetime(row["created_at"]),
        adjudication=adjudication,
    )


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _validated_use_category(value: str | None) -> str | None:
    """校验用户选择的用途分类；空值就是未分类。

    不按文件格式、来源类型或标题推断分类，也不接受列表外的自由文本。
    """
    if value is None:
        return None
    cleaned = str(value).strip()
    if not cleaned:
        return None
    if cleaned not in KNOWLEDGE_USE_CATEGORIES:
        raise ValueError(
            "use_category must be one of: " + ", ".join(KNOWLEDGE_USE_CATEGORIES)
        )
    return cleaned


def _clean_topics(topics: Sequence[str]) -> list[str]:
    """主题/对象关系只接受用户明确登记的非空文本，去重但不改写。"""
    cleaned: list[str] = []
    for topic in topics:
        text = str(topic).strip()
        if text and text not in cleaned:
            cleaned.append(text)
    return cleaned


def _topics_json(topics: Sequence[str]) -> str:
    return json.dumps(_clean_topics(topics), ensure_ascii=False)


def _parse_status(
    sections: Sequence[KnowledgeSection], parameters: Sequence[KnowledgeParameter]
) -> str:
    """解析状态按这一版真实解析结果落库。

    只要解析出了带定位的小节或显式参数就是 parsed：单个段落定位同样是真实
    结构，不能因为“只有一段”降成纯文本。
    完全没有任何结构记录的行（例如普通手填文本）才记 text_only。
    """
    return "parsed" if (tuple(sections) or tuple(parameters)) else "text_only"


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
