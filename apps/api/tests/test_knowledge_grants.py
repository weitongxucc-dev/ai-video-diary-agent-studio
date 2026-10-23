from __future__ import annotations

from dataclasses import replace

import pytest

from app.local_members import LocalMemberService
from app.source_repository import SourceRepository
from app.sources import KnowledgeItem, SourceConnector, SourceItem
from app.source_types import source_item_identity


def test_grant_survives_reopen_and_revoke_blocks_reads_and_fixed_references(tmp_path):
    members = LocalMemberService(tmp_path / "members.sqlite3")
    uploader, token_a = members.join(members.create_invite_code(), "甲")
    reader, token_b = members.join(members.create_invite_code(), "乙")
    assert members.resolve_member(token_a).member_id == uploader.member_id
    assert members.resolve_member(token_b).member_id == reader.member_id
    path = tmp_path / "sources.sqlite3"
    repo = SourceRepository(path)
    repo.upsert_connector(SourceConnector(
        id="files-a", workspace_id="workspace-a", platform="knowledge_files",
        protocol="local_file", account_or_rule="用户提供", frequency="on_demand",
        permissions=("read:source_items",),
    ))
    source = SourceItem(
        id="source-v1", workspace_id="workspace-a", connector_id="files-a",
        source_label="file_import", source_url=None, content_type="text/markdown",
        author=None, published_at=None, body="第一版原文", subtitles=None,
        media_references=(), license="user_provided", title="资料",
        file_reference="资料.md", content_hash="hash-v1",
    )
    repo.save_source_items("workspace-a", [source])
    item = repo.save_source_item_to_knowledge(
        "workspace-a", source.id, ownership="甲所有", authorization="甲允许引用",
        verified_owner_user_id=uploader.member_id,
    )
    assert repo.list_knowledge_items_readable_by("workspace-b", user_id=reader.member_id) == []
    # Storage-only evidence: no claim that a test workspace name proves device membership.
    grant = repo.grant_knowledge_item(
        "workspace-a", item.id, "workspace-b", user_id=uploader.member_id,
    )
    assert repo.grant_knowledge_item("workspace-a", item.id, "workspace-b", user_id=uploader.member_id) == grant
    repo = SourceRepository(path)
    assert [row.id for row in repo.list_knowledge_items_readable_by("workspace-b", user_id=reader.member_id)] == [item.id]
    original = repo.get_knowledge_item_readable_by("workspace-b", item.id, user_id=reader.member_id)
    assert (original.workspace_id, original.owner_user_id) == ("workspace-a", uploader.member_id)
    changed = replace(source, id="source-v2", body="第二版原文", content_hash="hash-v2")
    repo.save_source_items("workspace-a", [changed])
    updated = repo.save_source_item_to_knowledge(
        "workspace-a", changed.id, ownership="甲所有", authorization="甲允许引用",
        verified_owner_user_id=uploader.member_id,
    )
    assert updated.id == item.id and updated.version == "2"
    fixed = repo.get_knowledge_version_readable_by("workspace-b", item.id, "1", user_id=reader.member_id)
    assert fixed.body == "第一版原文" and fixed.content_hash == "hash-v1"
    referenced = repo.get_knowledge_reference_readable_by("workspace-b", item.id, "1", user_id=reader.member_id)
    assert referenced.body == fixed.body and referenced.workspace_id == "workspace-a"
    assert referenced.owner_user_id == uploader.member_id
    window_args = {'kind': 'knowledge', 'object_id': item.id, 'version_or_identity': '1',
                   'user_id': reader.member_id, 'offset': 1, 'limit': 3}
    window = repo.read_fixed_reference_window('workspace-b', **window_args)
    assert window['status'] == 'matched'
    assert (window['text'], window['total_chars'], window['content_digest']) == ('一版原', 5, 'hash-v1')
    assert window['text_offset'] == 1 and window['truncated']
    assert repo.read_fixed_reference_window('workspace-b', **{**window_args, 'version_or_identity': 'unknown'})['status'] == 'missing'
    assert repo.read_fixed_reference_window('workspace-b', **{**window_args, 'offset': 5})['text'] == ''
    with pytest.raises(ValueError):
        repo.read_fixed_reference_window('workspace-b', **{**window_args, 'limit': 8001})
    with pytest.raises(ValueError):
        repo.read_fixed_reference_window('workspace-b', **{**window_args, 'user_id': None})
    repo.set_knowledge_item_use_state("workspace-a", item.id, "excluded")
    assert repo.read_fixed_reference_window('workspace-b', **window_args)['status'] == 'unavailable'
    assert repo.list_knowledge_items_readable_by("workspace-b", user_id=reader.member_id) == []
    with pytest.raises(KeyError):
        repo.get_knowledge_reference_readable_by(
            "workspace-b", item.id, "1", user_id=reader.member_id
        )
    repo.set_knowledge_item_use_state("workspace-a", item.id, "available")
    assert repo.read_fixed_reference_window('workspace-c', **window_args)['status'] == 'unavailable'
    with pytest.raises(KeyError):
        repo.get_knowledge_item_readable_by("workspace-c", item.id, user_id=reader.member_id)
    revoked = repo.revoke_knowledge_grant("workspace-a", item.id, grant.id, user_id=uploader.member_id)
    assert revoked.revoked_by == uploader.member_id and revoked.revoked_at is not None
    assert repo.revoke_knowledge_grant("workspace-a", item.id, grant.id, user_id=uploader.member_id) == revoked
    repo = SourceRepository(path)
    assert repo.list_knowledge_items_readable_by("workspace-b", user_id=reader.member_id) == []
    with pytest.raises(KeyError):
        repo.get_knowledge_version_readable_by("workspace-b", item.id, "1", user_id=reader.member_id)
    with pytest.raises(KeyError):
        repo.get_knowledge_reference_readable_by("workspace-b", item.id, "1", user_id=reader.member_id)
    # Revocation does not remove original ownership, versions or audit history.
    assert repo.get_knowledge_item("workspace-a", item.id).owner_user_id == uploader.member_id
    assert [snapshot.version for snapshot in repo.list_knowledge_item_versions("workspace-a", item.id)] == ["1", "2"]
    assert repo.list_knowledge_grants("workspace-a", item.id, user_id=uploader.member_id) == [revoked]
    assert repo.read_fixed_reference_window('workspace-b', **window_args)['status'] == 'unavailable'

    source_args = {'kind': 'source', 'object_id': source.id, 'version_or_identity': source_item_identity(source),
                   'user_id': uploader.member_id, 'offset': 1, 'limit': 3}
    assert repo.read_fixed_reference_window('workspace-a', **source_args)['text'] == '一版原'
    assert repo.read_fixed_reference_window('workspace-b', **source_args)['status'] == 'missing'
    repo.save_source_items('workspace-a', [replace(source, body='同一ID的后来正文')])
    changed_window = repo.read_fixed_reference_window('workspace-a', **source_args)
    assert changed_window['status'] == 'changed' and 'text' not in changed_window
    unknown_window = repo.read_fixed_reference_window('workspace-a', **{**source_args, 'version_or_identity': None})
    assert unknown_window['status'] == 'unknown' and 'text' not in unknown_window
    subtitle_source = replace(source, body=None, subtitles='字幕中的原依据')
    repo.save_source_items('workspace-a', [subtitle_source])
    assert repo.read_fixed_reference_window('workspace-a', **{**source_args,
        'version_or_identity': source_item_identity(subtitle_source), 'offset': 0})['text'] == '字幕中'


def test_only_original_uploader_can_grant_or_revoke(tmp_path):
    repo = SourceRepository(tmp_path / "sources.sqlite3")
    item = repo.create_knowledge_item(KnowledgeItem(
        id="private-a", workspace_id="workspace-a", content_type="text",
        title="私有资料", body="私有原文", file_reference=None,
        authorization="自有", ownership="甲", source_item_id=None, version="1",
        owner_user_id="uploader-a",
    ))
    for actor in ("device-owner", "reader-b", ""):
        with pytest.raises(KeyError):
            repo.grant_knowledge_item("workspace-a", item.id, "workspace-b", user_id=actor)
    grant = repo.grant_knowledge_item("workspace-a", item.id, "workspace-b", user_id="uploader-a")
    fixed = repo.get_knowledge_version_readable_by("workspace-b", item.id, "1", user_id="reader-b")
    assert fixed.body == item.body and fixed.content_hash is None
    window = repo.read_fixed_reference_window('workspace-b', kind='knowledge', object_id=item.id,
        version_or_identity='1', user_id='reader-b')
    assert window['status'] == 'unknown' and 'text' not in window
    with pytest.raises(KeyError):
        repo.get_knowledge_version_readable_by("workspace-b", item.id, "2", user_id="reader-b")
    with pytest.raises(KeyError):
        repo.revoke_knowledge_grant("workspace-a", item.id, grant.id, user_id="device-owner")
    with pytest.raises(KeyError):
        repo.list_knowledge_grants("workspace-b", item.id, user_id="reader-b")
    with pytest.raises(ValueError):
        repo.get_knowledge_item_readable_by("workspace-b", item.id, user_id=None)
    assert repo.list_knowledge_items_readable_by("workspace-b", user_id=None, local_diagnostic=True) == []
    repo.revoke_knowledge_grant("workspace-a", item.id, grant.id, user_id="uploader-a")
    with pytest.raises(KeyError):
        repo.get_knowledge_version_readable_by("workspace-b", item.id, "1", user_id="reader-b")
    legacy = repo.create_knowledge_item(replace(item, id="legacy", owner_user_id=None))
    with pytest.raises(KeyError):
        repo.grant_knowledge_item("workspace-a", legacy.id, "workspace-b", user_id="device-owner")
