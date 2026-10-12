import pytest

from app.source_repository import SourceRepository
from app.sources import KnowledgeItem


def saved(repo):
    return repo.create_knowledge_item_owned(KnowledgeItem(
        id="ki_edit", workspace_id="qa", content_type="file", title="原资料",
        body="原始正文", file_reference="workspace://imports/original.pdf",
        authorization="user-provided", ownership="user-owned",
        source_item_id=None, version="1",
    ), verified_user_id="alice")


def test_edit_preserves_fixed_versions_source_and_survives_restart(tmp_path):
    repo = SourceRepository(tmp_path / "qa.sqlite3")
    original = saved(repo)
    updated = repo.edit_knowledge_item(
        "qa", original.id, expected_version="1", title="修订资料",
        body="# 产品\n- 重量: 20 kg", verified_user_id="alice",
    )
    assert updated.version == "2"
    assert updated.file_reference == original.file_reference
    assert updated.owner_user_id == "alice"
    rebuilt = SourceRepository(tmp_path / "qa.sqlite3")
    versions = {v.version: v for v in rebuilt.list_knowledge_item_versions("qa", original.id)}
    assert versions["1"].body == "原始正文"
    assert versions["2"].body == updated.body
    assert versions["2"].sections and versions["2"].parameters
    assert all(not section.locator.startswith("page") for section in versions["2"].sections)
    assert rebuilt.get_knowledge_item("qa", original.id) == updated
    with pytest.raises(ValueError, match="版本已变化"):
        repo.edit_knowledge_item("qa", original.id, expected_version="1", title="过期写入",
                                 body="不能覆盖新版本", verified_user_id="alice")
    assert rebuilt.get_knowledge_item("qa", original.id).title == "修订资料"


def test_edit_checks_owner_workspace_and_does_not_create_empty_or_duplicate_versions(tmp_path):
    repo = SourceRepository(tmp_path / "qa.sqlite3")
    item = saved(repo)
    for user in ("bob", None):
        with pytest.raises(PermissionError):
            repo.edit_knowledge_item("qa", item.id, expected_version="1", title="改", body="改", verified_user_id=user)
    with pytest.raises(KeyError):
        repo.edit_knowledge_item("other", item.id, expected_version="1", title="改", body="改", verified_user_id="alice")
    with pytest.raises(ValueError, match="不能为空"):
        repo.edit_knowledge_item("qa", item.id, expected_version="1", title="改", body=" ", verified_user_id="alice")
    assert repo.edit_knowledge_item("qa", item.id, expected_version="1", title=item.title,
                                   body=item.body, verified_user_id="alice").version == "1"
    assert repo.list_knowledge_item_versions("qa", item.id) == []
