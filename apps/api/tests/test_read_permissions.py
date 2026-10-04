"""读取权限：私有内容只对上传者本人，共享内容对成员；local 保留受控诊断范围。

只用隔离临时库，不连接真实库、不启动认证后端。local 用例只证明「本机产品仍可用」，
不冒充多人成员隔离。
"""

from __future__ import annotations

from dataclasses import replace
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

def test_local_members_in_one_workspace_get_per_member_private_isolation(
    repo: SourceRepository,
) -> None:
    # 本地产品：多名成员同在 local workspace。私有隔离靠各自的 member_id，
    # 而不是「有令牌就当 owner 全私有可见」。
    owner_upload = make_item(repo, "ki-by-owner", "member-owner", workspace_id=LOCAL)
    other_upload = make_item(repo, "ki-by-other", "member-other", workspace_id=LOCAL)
    repo.set_knowledge_item_visibility(LOCAL, owner_upload, "shared")
    repo.set_knowledge_item_visibility(LOCAL, other_upload, "shared")

    # 另一位成员看得到共享的两条。
    other_reads = [
        item.id for item in repo.list_knowledge_items_readable_by(LOCAL, user_id="member-other")
    ]
    assert set(other_reads) == {owner_upload, other_upload}
    # 私有隔离：把其中一条转回私有后，只对上传者本人可见。
    repo.set_knowledge_item_visibility(LOCAL, other_upload, "private")
    assert repo.get_knowledge_item_readable_by(LOCAL, other_upload, user_id="member-other").id == other_upload
    with pytest.raises(KeyError):
        repo.get_knowledge_item_readable_by(LOCAL, other_upload, user_id="member-owner")


def test_member_scoped_reads_require_the_member_id_not_local_diagnostic(
    repo: SourceRepository,
) -> None:
    # 已进入成员范围（认证态或带成员令牌）就必须给出可信 id；缺 id 是错误，
    # 不会静默走 local 诊断全私有绕行。
    item_id = make_item(repo, "ki-member-only", "member-owner", workspace_id=LOCAL)
    with pytest.raises(ValueError):
        repo.list_knowledge_items_readable_by(LOCAL, user_id=None, local_diagnostic=False)
    with pytest.raises(ValueError):
        repo.get_knowledge_item_readable_by(LOCAL, item_id, user_id=None, local_diagnostic=False)


def test_ownership_is_bound_from_the_verified_identity_not_the_dataclass_field(
    repo: SourceRepository,
) -> None:
    # 调用方可以在 dataclass 里随便填 owner_user_id，但归属只认显式的已验证身份。
    item = KnowledgeItem(
        id="ki-bound", workspace_id=WORKSPACE, content_type="text", title="绑定测试",
        body="正文", file_reference=None, authorization="内部参考", ownership="我的内容",
        source_item_id=None, version="1", owner_user_id="forged-owner",
    )

    created = repo.create_knowledge_item_owned(item, verified_user_id=ALICE)

    assert created.owner_user_id == ALICE
    assert repo.get_knowledge_item(WORKSPACE, "ki-bound").owner_user_id == ALICE
    # 没有可解析身份时归属留空：不算任何成员的私有资产。
    anonymous = repo.create_knowledge_item_owned(
        replace(item, id="ki-unbound"), verified_user_id=None
    )
    assert anonymous.owner_user_id is None
