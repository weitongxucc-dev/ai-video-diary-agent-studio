"""内容可见性地基（父条目 WIL-110 R05 / 子 issue WIL-112 第①段）。

本段只做 knowledge_items 这张表 + 三张表的列落地。任务与模板的 private 写入
在第②段（碰 production.py），本段不越权。

诚实边界：这里只是**应用层过滤**，不构成安全隔离。方案 A 下 member 不带身份头
仍能以 owner 身份调后端，不得把这里的测试当作「权限已隔离」的证据。

全部使用 tmp_path 合成库，样本取最小；不打开真实用户库 agent.sqlite3。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.database_migrations import (
    CONTENT_VISIBILITY_DEFAULT,
    CONTENT_VISIBILITY_LEGACY,
    CONTENT_VISIBILITY_TABLES,
    ensure_content_visibility_columns,
    rollback_content_visibility_columns,
)
from app.source_repository import SourceRepository
from app.sources import KnowledgeItem

WORKSPACE = "workspace_visibility"


def _legacy_knowledge_db(path: Path, rows: int = 2) -> sqlite3.Connection:
    """造一个「迁移前」的库：knowledge_items 已建，但没有 visibility 列。"""
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE knowledge_items (
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
            created_at TEXT NOT NULL
        )
        """
    )
    for index in range(rows):
        conn.execute(
            "INSERT INTO knowledge_items (id, workspace_id, content_type, title, body, "
            "authorization, ownership, version, created_at) "
            "VALUES (?, ?, 'note', ?, ?, 'owned', 'self', '1', ?)",
            (f"ki_legacy_{index}", WORKSPACE, f"旧条目{index}", "旧内容", f"2026-01-0{index + 1}T00:00:00+00:00"),
        )
    conn.commit()
    return conn


# --------------------------------------------------------------------------
# 一、迁移能跑，且能回滚
# --------------------------------------------------------------------------


def test_migration_runs_and_backfills_legacy_rows_to_shared(tmp_path):
    """正迁移：加列 + 存量行回填 shared。"""
    conn = _legacy_knowledge_db(tmp_path / "legacy.sqlite3", rows=2)
    try:
        ensure_content_visibility_columns(conn)
        values = [row[0] for row in conn.execute("SELECT visibility FROM knowledge_items")]
        assert values == [CONTENT_VISIBILITY_LEGACY, CONTENT_VISIBILITY_LEGACY], values
        # 派单验收：SELECT ... WHERE visibility IS NULL 应返回 0 行
        nulls = conn.execute("SELECT COUNT(*) FROM knowledge_items WHERE visibility IS NULL").fetchone()[0]
        assert nulls == 0, f"仍有 {nulls} 行 visibility 为 NULL"
    finally:
        conn.close()


def test_migration_is_idempotent_on_reopen(tmp_path):
    """重复执行不报错、不重复加列（重开安全）。"""
    conn = _legacy_knowledge_db(tmp_path / "legacy.sqlite3", rows=1)
    try:
        ensure_content_visibility_columns(conn)
        ensure_content_visibility_columns(conn)
        columns = [row["name"] for row in conn.execute("PRAGMA table_info(knowledge_items)")]
        assert columns.count("visibility") == 1, columns
    finally:
        conn.close()


def test_migration_skips_missing_tables_on_bare_database(tmp_path):
    """裸库（表还不存在）上必须安全跳过——迁移链会独立应用到裸库。"""
    conn = sqlite3.connect(tmp_path / "bare.sqlite3")
    conn.row_factory = sqlite3.Row
    try:
        ensure_content_visibility_columns(conn)  # 不应抛错
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert not (tables & set(CONTENT_VISIBILITY_TABLES)), "不应凭空建表"
    finally:
        conn.close()


def test_rollback_removes_the_column_and_keeps_rows(tmp_path):
    """可逆：回滚删列，且行数不变（不误删数据）。"""
    conn = _legacy_knowledge_db(tmp_path / "legacy.sqlite3", rows=2)
    try:
        ensure_content_visibility_columns(conn)
        before = conn.execute("SELECT COUNT(*) FROM knowledge_items").fetchone()[0]
        removed = rollback_content_visibility_columns(conn)
        assert "knowledge_items" in removed, removed
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(knowledge_items)")}
        assert "visibility" not in columns, columns
        after = conn.execute("SELECT COUNT(*) FROM knowledge_items").fetchone()[0]
        assert before == after == 2, f"行数被改动：{before} -> {after}"
        assert conn.execute("SELECT COUNT(*) FROM knowledge_items WHERE title = '旧条目0'").fetchone()[0] == 1
    finally:
        conn.close()


def test_row_count_reconciles_across_migration(tmp_path):
    """迁移前后行数对账：证明没有误删数据。"""
    conn = _legacy_knowledge_db(tmp_path / "legacy.sqlite3", rows=3)
    try:
        before = conn.execute("SELECT COUNT(*) FROM knowledge_items").fetchone()[0]
        ensure_content_visibility_columns(conn)
        after = conn.execute("SELECT COUNT(*) FROM knowledge_items").fetchone()[0]
        assert before == after == 3, f"行数对不上：{before} -> {after}"
    finally:
        conn.close()


# --------------------------------------------------------------------------
# 二、新建行默认 private（与存量 shared 是两回事）
# --------------------------------------------------------------------------


def test_new_knowledge_item_defaults_to_private(tmp_path):
    """新建知识条目落库应为 private。"""
    repository = SourceRepository(tmp_path / "studio.sqlite3")
    item = repository.create_knowledge_item(
        KnowledgeItem(
            id="ki_new_1", workspace_id=WORKSPACE, content_type="note",
            title="新写的笔记", body="内容", file_reference=None, source_item_id=None,
            authorization="owned", ownership="self", version="1",
        )
    )
    assert item.id == "ki_new_1"
    assert repository.get_knowledge_item_visibility(WORKSPACE, "ki_new_1") == CONTENT_VISIBILITY_DEFAULT


def test_legacy_and_new_coexist_with_different_visibility(tmp_path):
    """存量 shared、新建 private，两者共存——这正是默认值两件事的区别。"""
    conn = _legacy_knowledge_db(tmp_path / "studio.sqlite3", rows=1)
    conn.close()
    repository = SourceRepository(tmp_path / "studio.sqlite3")  # 打开时补列并回填
    assert repository.get_knowledge_item_visibility(WORKSPACE, "ki_legacy_0") == CONTENT_VISIBILITY_LEGACY
    repository.create_knowledge_item(
        KnowledgeItem(
            id="ki_new_2", workspace_id=WORKSPACE, content_type="note",
            title="新条目", body="内容", file_reference=None, source_item_id=None,
            authorization="owned", ownership="self", version="1",
        )
    )
    counts = repository.count_knowledge_items_by_visibility(WORKSPACE)
    assert counts[CONTENT_VISIBILITY_LEGACY] == 1, counts
    assert counts[CONTENT_VISIBILITY_DEFAULT] == 1, counts


# --------------------------------------------------------------------------
# 三、可见范围过滤（应用层，不是安全隔离）
# --------------------------------------------------------------------------


def test_visibility_scope_filters_private_from_shared(tmp_path):
    """传 shared 时取不到 private 行，反之亦然。"""
    repository = SourceRepository(tmp_path / "studio.sqlite3")
    for index, title in enumerate(("甲", "乙")):
        repository.create_knowledge_item(
            KnowledgeItem(
                id=f"ki_{index}", workspace_id=WORKSPACE, content_type="note",
                title=title, body="内容", file_reference=None, source_item_id=None,
                authorization="owned", ownership="self", version="1",
            )
        )
    repository.set_knowledge_item_visibility(WORKSPACE, "ki_0", CONTENT_VISIBILITY_LEGACY)
    repository.set_knowledge_item_visibility(WORKSPACE, "ki_1", CONTENT_VISIBILITY_DEFAULT)

    shared_only = repository.list_knowledge_items(WORKSPACE, visibilities=(CONTENT_VISIBILITY_LEGACY,))
    assert [item.id for item in shared_only] == ["ki_0"]

    private_only = repository.list_knowledge_items(WORKSPACE, visibilities=(CONTENT_VISIBILITY_DEFAULT,))
    assert [item.id for item in private_only] == ["ki_1"]

    # 不过滤时旧行为不变，两条都在
    assert len(repository.list_knowledge_items(WORKSPACE)) == 2


def test_empty_visibility_scope_returns_nothing(tmp_path):
    repository = SourceRepository(tmp_path / "studio.sqlite3")
    repository.create_knowledge_item(
        KnowledgeItem(
            id="ki_0", workspace_id=WORKSPACE, content_type="note", title="甲",
            body="内容", file_reference=None, source_item_id=None,
            authorization="owned", ownership="self", version="1",
        )
    )
    assert repository.list_knowledge_items(WORKSPACE, visibilities=()) == []


def test_visibility_never_crosses_workspace(tmp_path):
    """过滤不跨工作区：别的工作区的条目不因可见范围变化而出现。"""
    repository = SourceRepository(tmp_path / "studio.sqlite3")
    repository.create_knowledge_item(
        KnowledgeItem(
            id="ki_a", workspace_id="ws_a", content_type="note", title="A",
            body="内容", file_reference=None, source_item_id=None,
            authorization="owned", ownership="self", version="1",
        )
    )
    assert repository.list_knowledge_items("ws_b", visibilities=(CONTENT_VISIBILITY_LEGACY,)) == []
    with pytest.raises(KeyError):
        repository.get_knowledge_item_visibility("ws_b", "ki_a")


def test_set_visibility_rejects_unknown_value(tmp_path):
    repository = SourceRepository(tmp_path / "studio.sqlite3")
    repository.create_knowledge_item(
        KnowledgeItem(
            id="ki_0", workspace_id=WORKSPACE, content_type="note", title="甲",
            body="内容", file_reference=None, source_item_id=None,
            authorization="owned", ownership="self", version="1",
        )
    )
    with pytest.raises(ValueError):
        repository.set_knowledge_item_visibility(WORKSPACE, "ki_0", "public")
