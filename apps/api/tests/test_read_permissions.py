"""读取权限：私有内容只对上传者本人，共享内容对成员；local 保留受控诊断范围。

只用隔离临时库，不连接真实库、不启动认证后端。local 用例只证明「本机产品仍可用」，
不冒充多人成员隔离。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.source_repository import SourceRepository
from app.sources import KnowledgeItem, knowledge_item_readable

ALICE = "user-alice"
BOB = "user-bob"
WORKSPACE = "workspace-read"
LOCAL = "local"


@pytest.fixture
def repo(tmp_path: Path) -> SourceRepository:
    return SourceRepository(tmp_path / "read.sqlite3")


def make_item(
    repo: SourceRepository, item_id: str, owner: str | None, workspace_id: str = WORKSPACE
) -> str:
    return repo.create_knowledge_item(
        KnowledgeItem(
            id=item_id,
            workspace_id=workspace_id,
            content_type="text",
            title=f"资料 {item_id}",
            body="可追溯正文",
            file_reference=None,
            authorization="内部参考",
            ownership="我的内容",
            source_item_id=None,
            version="1",
            owner_user_id=owner,
        )
    ).id


def test_private_is_readable_by_its_owner_only(repo: SourceRepository) -> None:
    item_id = make_item(repo, "ki-private", ALICE)

    assert repo.get_knowledge_item_visibility(WORKSPACE, item_id) == "private"
    assert repo.get_knowledge_item_readable_by(WORKSPACE, item_id, user_id=ALICE).id == item_id
    # 另一位成员读不到：对外是 404，不暴露「存在但你不能看」。
    with pytest.raises(KeyError):
        repo.get_knowledge_item_readable_by(WORKSPACE, item_id, user_id=BOB)


def test_shared_is_readable_by_another_member_and_revoke_takes_effect_immediately(
    repo: SourceRepository,
) -> None:
    item_id = make_item(repo, "ki-shared", ALICE)
    repo.set_knowledge_item_visibility(WORKSPACE, item_id, "shared")

    assert repo.get_knowledge_item_readable_by(WORKSPACE, item_id, user_id=BOB).id == item_id
    assert item_id in [
        item.id for item in repo.list_knowledge_items_readable_by(WORKSPACE, user_id=BOB)
    ]

    # 收回共享：同一成员的每次新读取立即失效。
    repo.set_knowledge_item_visibility(WORKSPACE, item_id, "private")

    with pytest.raises(KeyError):
        repo.get_knowledge_item_readable_by(WORKSPACE, item_id, user_id=BOB)
    assert item_id not in [
        item.id for item in repo.list_knowledge_items_readable_by(WORKSPACE, user_id=BOB)
    ]
    # 上传者本人不受影响。
    assert repo.get_knowledge_item_readable_by(WORKSPACE, item_id, user_id=ALICE).id == item_id


def test_local_diagnostic_keeps_this_machine_usable_but_is_not_member_isolation(
    repo: SourceRepository,
) -> None:
    # 单机无认证：私有内容在本机照常可读，否则本机产品不可用。
    private_id = make_item(repo, "ki-local-private", ALICE, workspace_id=LOCAL)

    readable = [
        item.id for item in repo.list_knowledge_items_readable_by(LOCAL, user_id=None, local_diagnostic=True)
    ]
    assert private_id in readable
    assert repo.get_knowledge_item_readable_by(
        LOCAL, private_id, user_id=None, local_diagnostic=True
    ).id == private_id

    # 诚实边界：同一范围在多成员 workspace 上不成立——无身份不能读到别人的私有内容。
    other = make_item(repo, "ki-ws-private", ALICE)
    with pytest.raises(ValueError):
        repo.list_knowledge_items_readable_by(WORKSPACE, user_id=None)
    with pytest.raises(ValueError):
        repo.get_knowledge_item_readable_by(WORKSPACE, other, user_id=None)


def test_shared_does_not_grant_read_across_workspaces(repo: SourceRepository) -> None:
    repo.create_knowledge_item(
        KnowledgeItem(
            id="ki-other-workspace", workspace_id="workspace-other", content_type="text",
            title="别处资料", body="正文", file_reference=None, authorization="内部参考",
            ownership="我的内容", source_item_id=None, version="1", owner_user_id=ALICE,
        )
    )
    repo.set_knowledge_item_visibility("workspace-other", "ki-other-workspace", "shared")

    # 共享不是无条件跨 workspace 可读。
    with pytest.raises(KeyError):
        repo.get_knowledge_item_readable_by(WORKSPACE, "ki-other-workspace", user_id=ALICE)
    with pytest.raises(KeyError):
        repo.get_knowledge_item_readable_by(WORKSPACE, "ki-other-workspace", user_id=ALICE)


def test_owner_identity_is_bound_from_a_verified_source_and_not_overwritten(
    repo: SourceRepository,
) -> None:
    item_id = make_item(repo, "ki-owner", ALICE)

    assert repo.get_knowledge_item(WORKSPACE, item_id).owner_user_id == ALICE
    # 归属不是可随意改写的字段：重复写入不会改成别人。
    assert repo.set_knowledge_item_owner(WORKSPACE, item_id, BOB) == ALICE
    assert repo.get_knowledge_item(WORKSPACE, item_id).owner_user_id == ALICE
    # 没有上传者的行不会被谁随手认领成自己的。
    orphan = make_item(repo, "ki-orphan", None)
    repo.set_knowledge_item_owner(WORKSPACE, orphan, BOB)
    assert repo.get_knowledge_item(WORKSPACE, orphan).owner_user_id == BOB


def test_policy_helper_matches_the_query_boundary() -> None:
    assert knowledge_item_readable(
        user_id=ALICE, local_diagnostic=False, visibility="private", owner_user_id=ALICE
    ) is True
    assert knowledge_item_readable(
        user_id=BOB, local_diagnostic=False, visibility="private", owner_user_id=ALICE
    ) is False
    assert knowledge_item_readable(
        user_id=BOB, local_diagnostic=False, visibility="shared", owner_user_id=ALICE
    ) is True
    # 认证态缺可信身份：不是「只看共享」，而是不可读。
    assert knowledge_item_readable(
        user_id=None, local_diagnostic=False, visibility="shared", owner_user_id=ALICE
    ) is False
    # local 受控诊断：本机私有可读。
    assert knowledge_item_readable(
        user_id=None, local_diagnostic=True, visibility="private", owner_user_id=None
    ) is True
    # 可见范围未知按保守一侧处理，不当作共享。
    assert knowledge_item_readable(
        user_id=ALICE, local_diagnostic=False, visibility="unexpected", owner_user_id=ALICE
    ) is False