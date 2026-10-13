"""Current-version, authorized bounded retrieval on the real SQLite repository."""
from datetime import UTC, datetime

import pytest

from app.source_repository import SourceRepository
from app.sources import KnowledgeItem


def save(repo, item_id, *, owner="alice", workspace="qa", body="如何提高剪辑效率", use_state="available"):
    return repo.create_knowledge_item_owned(KnowledgeItem(
        id=item_id, workspace_id=workspace, content_type="text", title=item_id,
        body=body, file_reference="workspace://private/original.md", authorization="private-authorization",
        ownership="user-owned", source_item_id=None, version="1", content_hash="hash-" + item_id,
        use_state=use_state, created_at=datetime(2026, 10, 13, tzinfo=UTC),
    ), verified_user_id=owner)


def search(repo, query="剪辑效率", *, workspace="qa", user="alice", offset=0, limit=5):
    return repo.search_knowledge_items(workspace, query=query, user_id=user, offset=offset, limit=limit)


def test_thousand_current_items_have_stable_full_counts_and_bounded_tail_pages(tmp_path):
    repo = SourceRepository(tmp_path / "sources.sqlite3")
    for index in range(1000):
        save(repo, f"item-{index:04}")
    save(repo, "hidden-private", owner="bob")
    save(repo, "hidden-excluded", use_state="excluded")
    save(repo, "hidden-other-workspace", workspace="other")
    first, total = search(repo)
    tail, tail_total = search(repo, offset=995)
    assert total == tail_total == 1000 and len(first) == len(tail) == 5
    assert [row["id"] for row in first] == [f"item-{index:04}" for index in range(5)]
    assert [row["id"] for row in tail] == [f"item-{index:04}" for index in range(995, 1000)]
    assert first == search(SourceRepository(tmp_path / "sources.sqlite3"))[0]
    assert all(row["version"] == "1" and row["content_hash"] for row in tail)
    assert search(repo, offset=1000) == ([], 1000)
    assert all("body" not in row and "file_reference" not in row and "authorization" not in row for row in first)


def test_search_uses_new_content_and_exclusion_and_current_grant_revocation(tmp_path):
    repo = SourceRepository(tmp_path / "sources.sqlite3")
    item = save(repo, "changing", body="旧内容_oldword")
    assert search(repo, "oldword")[1] == 1
    updated = repo.edit_knowledge_item("qa", item.id, expected_version="1", title="新版资料", body="新内容_newword",
                                       verified_user_id="alice")
    assert search(repo, "oldword") == ([], 0)
    result, total = search(repo, "newword")
    assert total == 1 and result[0]["version"] == "2" and result[0]["content_hash"] == updated.content_hash
    # Fixed old references retain their version; current search does not silently
    # replace the content of an old reference with the updated item.
    assert repo.get_knowledge_reference_readable_by("qa", item.id, "1", user_id="alice").body == "旧内容_oldword"
    grant = repo.grant_knowledge_item("qa", item.id, "target", user_id="alice")
    assert search(repo, "newword", workspace="target", user="bob")[1] == 1
    repo.revoke_knowledge_grant("qa", item.id, grant.id, user_id="alice")
    assert search(repo, "newword", workspace="target", user="bob") == ([], 0)
    repo.set_knowledge_item_use_state("qa", item.id, "excluded")
    assert search(repo, "newword") == ([], 0)
    with pytest.raises(KeyError):
        repo.get_knowledge_reference_readable_by("qa", item.id, "1", user_id="alice")


def test_search_partial_chinese_and_literal_queries_keep_excerpts_near_actual_hits(tmp_path):
    repo = SourceRepository(tmp_path / "sources.sqlite3")
    save(repo, "chinese", body="前言" * 1000 + "提高剪辑效率，用100%努力", use_state="reference_only")
    save(repo, "english", body="Use CAMERA footage")
    rows, total = search(repo, "怎么提高剪辑效率")
    assert total == 1 and "剪辑效率" in rows[0]["excerpt"] and len(rows[0]["excerpt"]) <= 900
    assert search(repo, "CAMERA")[0][0]["id"] == "english"
    assert search(repo, "%")[1] == 1  # SQL wildcard never matches every row.
    assert search(repo, "unmatched") == ([], 0)


def test_search_requires_verified_identity_and_bounded_query(tmp_path):
    repo = SourceRepository(tmp_path / "sources.sqlite3")
    save(repo, "current")
    with pytest.raises(ValueError, match="已验证身份"):
        search(repo, user=None)
    for args in ({"query": " "}, {"query": "x" * 121}, {"query": "剪辑", "limit": 21}, {"query": "剪辑", "offset": -1}):
        with pytest.raises(ValueError, match="有界分页"):
            repo.search_knowledge_items("qa", user_id="alice", **args)
