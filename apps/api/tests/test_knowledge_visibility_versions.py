"""WIL-112：知识条目可见性在重解析与新版本保存后仍保持已设置的值。

覆盖此前无直接测试的真实路径（source_repository 的文件版本链）：新建落
private、显式设为 shared 后重解析生成新版本不得改回默认、同内容重复保存也
不得改写设置。全部使用 tmp_path 合成库，不打开真实用户库。
"""
from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path

from app.database_migrations import (
    CONTENT_VISIBILITY_DEFAULT,
    CONTENT_VISIBILITY_LEGACY,
)
from app.source_repository import SourceRepository
from app.sources import KnowledgeItem, SourceConnector, SourceItem

WORKSPACE = "workspace_knowledge_visibility"


def _connector(workspace_id: str = WORKSPACE) -> SourceConnector:
    return SourceConnector(
        id=f"connector_files_{workspace_id}",
        workspace_id=workspace_id,
        platform="knowledge_files",
        protocol="local_file",
        account_or_rule="知识文件导入",
        frequency="on_demand",
        permissions=("read:source_items",),
    )


def _parsed_source(
    repository: SourceRepository,
    item_id: str,
    filename: str,
    body: str,
) -> SourceItem:
    return repository.save_source_items(
        WORKSPACE,
        [
            SourceItem(
                id=item_id,
                workspace_id=WORKSPACE,
                connector_id=f"connector_files_{WORKSPACE}",
                source_label="file_import",
                source_url=None,
                content_type="text/markdown",
                author=None,
                published_at=None,
                body=body,
                subtitles=None,
                media_references=(),
                license="user_provided",
                title="说明书",
                file_reference=filename,
                content_hash=f"hash-{item_id}",
            )
        ],
    )[0]


def _save(repository: SourceRepository, source_item_id: str) -> KnowledgeItem:
    return repository.save_source_item_to_knowledge(
        WORKSPACE,
        source_item_id,
        ownership="自有",
        authorization="用户提供",
    )


def test_new_parsed_knowledge_item_is_private(tmp_path: Path) -> None:
    repository = SourceRepository(tmp_path / "studio.sqlite3")
    repository.upsert_connector(_connector())
    source = _parsed_source(repository, "source_1", "说明书.md", "# 说明书\n\n旧正文")
    saved = _save(repository, source.id)

    assert repository.get_knowledge_item_visibility(WORKSPACE, saved.id) == CONTENT_VISIBILITY_DEFAULT


def test_reparsed_new_version_keeps_explicit_shared_visibility(tmp_path: Path) -> None:
    repository = SourceRepository(tmp_path / "studio.sqlite3")
    repository.upsert_connector(_connector())
    first = _parsed_source(repository, "source_1", "说明书.md", "# 说明书\n\n第一版正文")
    saved = _save(repository, first.id)
    repository.set_knowledge_item_visibility(WORKSPACE, saved.id, CONTENT_VISIBILITY_LEGACY)

    updated = _parsed_source(repository, "source_2", "说明书.md", "# 说明书\n\n第二版正文")
    version_two = _save(repository, updated.id)

    # 同一知识条目追加了新版本，可见性保持显式设置值，不回到新建默认。
    assert version_two.id == saved.id
    assert version_two.version == "2"
    assert (
        repository.get_knowledge_item_visibility(WORKSPACE, version_two.id)
        == CONTENT_VISIBILITY_LEGACY
    )


def test_duplicate_content_save_keeps_explicit_visibility(tmp_path: Path) -> None:
    repository = SourceRepository(tmp_path / "studio.sqlite3")
    repository.upsert_connector(_connector())
    first = _parsed_source(repository, "source_1", "说明书.md", "# 说明书\n\n正文")
    saved = _save(repository, first.id)
    repository.set_knowledge_item_visibility(WORKSPACE, saved.id, CONTENT_VISIBILITY_LEGACY)

    # 同内容副本：新的 source_item_id，内容指纹相同，命中后不应新增条目或改写设置。
    duplicate = replace(
        _parsed_source(repository, "source_copy", "说明书-副本.md", "# 说明书\n\n正文"),
        content_hash=first.content_hash,
    )
    repository.save_source_items(WORKSPACE, [duplicate])
    again = _save(repository, duplicate.id)

    assert again.id == saved.id
    assert (
        repository.get_knowledge_item_visibility(WORKSPACE, again.id)
        == CONTENT_VISIBILITY_LEGACY
    )


def test_legacy_row_stays_shared_when_new_versions_are_added(tmp_path: Path) -> None:
    """存量 shared 行与新建 private 行并存时，重解析不改变两者各自的值。"""
    legacy_path = tmp_path / "studio.sqlite3"
    conn = sqlite3.connect(legacy_path)
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
    conn.execute(
        "INSERT INTO knowledge_items (id, workspace_id, content_type, title, body, "
        "file_reference, authorization, ownership, source_item_id, version, created_at) "
        "VALUES (?, ?, 'note', ?, ?, ?, 'owned', 'self', 'source_legacy', '1', ?)",
        ("ki_legacy", WORKSPACE, "旧资料", "旧内容", "旧资料.md", "2026-01-01T00:00:00+00:00"),
    )
    conn.commit()
    conn.close()

    repository = SourceRepository(legacy_path)
    repository.upsert_connector(_connector())
    # 存量行补列后按 shared 处理，新导入条目落 private。
    assert repository.get_knowledge_item_visibility(WORKSPACE, "ki_legacy") == CONTENT_VISIBILITY_LEGACY

    parsed = _parsed_source(repository, "source_new", "旧资料.md", "# 旧资料\n\n解析正文")
    saved = _save(repository, parsed.id)
    assert repository.get_knowledge_item_visibility(WORKSPACE, saved.id) == CONTENT_VISIBILITY_DEFAULT
    assert repository.get_knowledge_item_visibility(WORKSPACE, "ki_legacy") == CONTENT_VISIBILITY_LEGACY