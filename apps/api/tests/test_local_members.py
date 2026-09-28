from __future__ import annotations

import pytest

from app.local_members import LocalMember, LocalMembersError, LocalMemberService


def _service(tmp_path) -> LocalMemberService:
    return LocalMemberService(tmp_path / "agent.sqlite3")


def _owner(service: LocalMemberService) -> LocalMember:
    return next(m for m in service.list_members() if m.role == "owner")


def test_seed_owner_is_present(tmp_path):
    service = _service(tmp_path)

    members = service.list_members()

    assert len(members) == 1
    assert members[0].name == "本机创作者"
    assert members[0].role == "owner"


def test_add_and_list_member(tmp_path):
    service = _service(tmp_path)

    added = service.add_member("小李", "member")

    assert added.name == "小李"
    assert added.role == "member"
    assert {m.name for m in service.list_members()} == {"本机创作者", "小李"}


def test_add_member_rejects_empty_name(tmp_path):
    service = _service(tmp_path)

    with pytest.raises(LocalMembersError) as exc:
        service.add_member("   ", "member")

    assert exc.value.kind == "invalid"


def test_add_member_rejects_duplicate_name(tmp_path):
    service = _service(tmp_path)
    service.add_member("小李", "member")

    with pytest.raises(LocalMembersError) as exc:
        service.add_member("小李", "member")

    assert exc.value.kind == "conflict"


def test_add_member_rejects_invalid_role(tmp_path):
    service = _service(tmp_path)

    with pytest.raises(LocalMembersError) as exc:
        service.add_member("小李", "admin")

    assert exc.value.kind == "invalid"


def test_remove_member(tmp_path):
    service = _service(tmp_path)
    member = service.add_member("小李", "member")

    service.remove_member(member.member_id)

    assert {m.name for m in service.list_members()} == {"本机创作者"}


def test_remove_last_owner_is_rejected(tmp_path):
    service = _service(tmp_path)
    owner = _owner(service)

    with pytest.raises(LocalMembersError) as exc:
        service.remove_member(owner.member_id)

    assert exc.value.kind == "conflict"
    assert _owner(service).member_id == owner.member_id


def test_remove_unknown_member_is_not_found(tmp_path):
    service = _service(tmp_path)

    with pytest.raises(LocalMembersError) as exc:
        service.remove_member("no-such-id")

    assert exc.value.kind == "not_found"


def test_set_role_promotes_member(tmp_path):
    service = _service(tmp_path)
    member = service.add_member("小李", "member")

    updated = service.set_role(member.member_id, "owner")

    assert updated.role == "owner"
    by_id = {m.member_id: m for m in service.list_members()}
    assert by_id[member.member_id].role == "owner"


def test_demote_owner_is_rejected_when_last_owner(tmp_path):
    service = _service(tmp_path)
    owner = _owner(service)

    with pytest.raises(LocalMembersError) as exc:
        service.set_role(owner.member_id, "member")

    assert exc.value.kind == "conflict"
    assert _owner(service).role == "owner"


def test_demote_owner_allowed_when_another_owner_remains(tmp_path):
    service = _service(tmp_path)
    first_owner = _owner(service)
    second_owner = service.add_member("小李", "owner")

    demoted = service.set_role(first_owner.member_id, "member")

    assert demoted.role == "member"
    assert {m.member_id for m in service.list_members() if m.role == "owner"} == {
        second_owner.member_id
    }


def test_set_role_rejects_invalid_role(tmp_path):
    service = _service(tmp_path)
    member = service.add_member("小李", "member")

    with pytest.raises(LocalMembersError) as exc:
        service.set_role(member.member_id, "admin")

    assert exc.value.kind == "invalid"


def test_set_role_unknown_member_is_not_found(tmp_path):
    service = _service(tmp_path)

    with pytest.raises(LocalMembersError) as exc:
        service.set_role("no-such-id", "owner")

    assert exc.value.kind == "not_found"


def test_invite_code_join_adds_member(tmp_path):
    service = _service(tmp_path)
    code = service.create_invite_code()

    joined = service.join(code, "小王")

    assert joined.name == "小王"
    assert joined.role == "member"
    assert joined.member_id in {m.member_id for m in service.list_members()}


def test_invite_code_is_one_time(tmp_path):
    service = _service(tmp_path)
    code = service.create_invite_code()

    service.join(code, "小王")

    with pytest.raises(LocalMembersError) as exc:
        service.join(code, "小张")

    assert exc.value.kind == "invalid"


def test_join_rejects_unknown_code(tmp_path):
    service = _service(tmp_path)

    with pytest.raises(LocalMembersError) as exc:
        service.join("no-such-code", "小王")

    assert exc.value.kind == "invalid"
