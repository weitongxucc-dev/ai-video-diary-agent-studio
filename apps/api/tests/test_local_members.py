from __future__ import annotations

import dataclasses
import sqlite3
import threading

import pytest

from app.local_members import LocalMember, LocalMembersError, LocalMemberService
from app.database_migrations import LOCAL_MEMBERS_TABLE_DDL


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

    joined, token = service.join(code, "小王")

    assert joined.name == "小王"
    assert joined.role == "member"
    assert joined.member_id in {m.member_id for m in service.list_members()}
    assert token


def test_join_token_resolves_back_to_member(tmp_path):
    service = _service(tmp_path)
    code = service.create_invite_code()

    joined, token = service.join(code, "小王")

    resolved = service.resolve_member(token)
    assert resolved is not None
    assert resolved.member_id == joined.member_id
    assert resolved.role == "member"


def test_resolve_none_is_anonymous(tmp_path):
    service = _service(tmp_path)

    assert service.resolve_member(None) is None
    assert service.resolve_member("") is None


def test_resolve_unknown_token_is_invalid(tmp_path):
    service = _service(tmp_path)

    with pytest.raises(LocalMembersError) as exc:
        service.resolve_member("not-a-real-token")

    assert exc.value.kind == "invalid"


def test_whoami_anonymous_returns_seed_owner_with_token(tmp_path):
    service = _service(tmp_path)
    owner = _owner(service)

    member, token = service.whoami(None)

    assert member.member_id == owner.member_id
    assert member.role == "owner"
    resolved = service.resolve_member(token)
    assert resolved is not None
    assert resolved.member_id == owner.member_id


def test_whoami_with_token_echoes_that_member(tmp_path):
    service = _service(tmp_path)
    code = service.create_invite_code()
    joined, token = service.join(code, "小王")

    member, echoed = service.whoami(token)

    assert member.member_id == joined.member_id
    assert echoed == token


def test_whoami_unknown_token_is_invalid(tmp_path):
    service = _service(tmp_path)

    with pytest.raises(LocalMembersError) as exc:
        service.whoami("not-a-real-token")

    assert exc.value.kind == "invalid"


def test_member_tokens_are_stable_across_reopens(tmp_path):
    service = _service(tmp_path)
    _first, token = service.join(service.create_invite_code(), "小王")

    # Reopening the database (a fresh service on the same file) must keep the
    # token: the NULL-token backfill only fills rows that have none.
    reopened = LocalMemberService(tmp_path / "agent.sqlite3")
    resolved = reopened.resolve_member(token)

    assert resolved is not None
    assert resolved.name == "小王"


def test_every_member_row_has_a_token(tmp_path):
    service = _service(tmp_path)
    service.add_member("小李", "member")
    service.join(service.create_invite_code(), "小王")

    with sqlite3.connect(str(tmp_path / "agent.sqlite3")) as conn:
        tokens = [
            row[0]
            for row in conn.execute(
                "SELECT token FROM local_members ORDER BY created_at"
            )
        ]

    assert len(tokens) == 3  # seed owner + two members
    assert all(token for token in tokens)
    assert len(set(tokens)) == 3


def test_list_members_does_not_expose_tokens(tmp_path):
    service = _service(tmp_path)
    service.add_member("小李", "member")

    fields = {f.name for f in dataclasses.fields(LocalMember)}

    assert "token" not in fields


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


# --- 使用名额上限（auth-workspace-boundary §9：包含主账号在内上限五人）---


def _fill_to_cap(service: LocalMemberService, total: int = 5) -> None:
    """Grow the roster to ``total`` rows; the seed owner is already one of them."""
    for index in range(total - 1):
        service.add_member(f"成员{index}", "member")


def test_seat_cap_counts_the_owner(tmp_path):
    service = _service(tmp_path)
    owner = _owner(service)
    _fill_to_cap(service)

    with pytest.raises(LocalMembersError) as exc:
        service.add_member("第六人", "member")

    assert exc.value.kind == "conflict"
    # The sixth add left nothing behind: same five ids, same five tokens.
    assert [m.member_id for m in service.list_members()][0] == owner.member_id
    assert len(service.list_members()) == 5
    with sqlite3.connect(str(tmp_path / "agent.sqlite3")) as conn:
        rows = conn.execute("SELECT COUNT(*) FROM local_members").fetchone()[0]
        tokens = conn.execute("SELECT COUNT(*) FROM local_members WHERE token IS NOT NULL").fetchone()[0]
    assert rows == 5
    assert tokens == 5


def test_join_is_rejected_once_seats_are_full(tmp_path):
    service = _service(tmp_path)
    _fill_to_cap(service)

    with pytest.raises(LocalMembersError) as exc:
        service.join(service.create_invite_code(), "第六人")

    assert exc.value.kind == "conflict"
    assert len(service.list_members()) == 5


def test_removing_a_member_frees_the_seat(tmp_path):
    service = _service(tmp_path)
    _fill_to_cap(service)
    leaving = service.list_members()[-1]

    service.remove_member(leaving.member_id)

    joined, _token = service.join(service.create_invite_code(), "补位者")
    assert joined.role == "member"
    assert len(service.list_members()) == 5


def test_role_change_does_not_consume_an_extra_seat(tmp_path):
    service = _service(tmp_path)
    _fill_to_cap(service)
    member = service.list_members()[-1]

    promoted = service.set_role(member.member_id, "owner")

    assert promoted.role == "owner"
    assert len(service.list_members()) == 5
    with pytest.raises(LocalMembersError) as exc:
        service.add_member("第六人", "member")
    assert exc.value.kind == "conflict"


def test_concurrent_join_reserves_only_one_seat(tmp_path):
    """auth §9 负向验收：并发接受邀请不能超五人.

    Four seats are used (owner + three members) and eight codes are accepted at
    once. Only one may win the last seat; the rest must be rejected rather than
    queued behind the lock and inserted after the roster is full.
    """
    service = _service(tmp_path)
    _fill_to_cap(service, total=4)
    assert len(service.list_members()) == 4

    codes = [service.create_invite_code() for _ in range(8)]
    barrier = threading.Barrier(len(codes))
    outcomes: list[str] = []
    lock = threading.Lock()

    def accept(index: int) -> None:
        barrier.wait()
        try:
            service.join(codes[index], f"并发{index}")
            outcome = "accepted"
        except LocalMembersError as exc:
            outcome = exc.kind
        with lock:
            outcomes.append(outcome)

    threads = [threading.Thread(target=accept, args=(i,)) for i in range(len(codes))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert outcomes.count("accepted") == 1
    assert outcomes.count("conflict") == len(codes) - 1
    assert len(service.list_members()) == 5


@pytest.mark.parametrize("existing_token_column", [False, True])
def test_constructor_migrates_legacy_tokens_and_reads_under_another_writer(tmp_path, existing_token_column):
    database = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(database) as conn:
        conn.executescript(LOCAL_MEMBERS_TABLE_DDL)
        conn.execute("INSERT INTO local_members VALUES ('legacy', '旧成员', 'member', '2020-01-01')")
        if existing_token_column:
            conn.execute("ALTER TABLE local_members ADD COLUMN token TEXT")
            conn.execute("INSERT INTO local_members VALUES ('stable', '已有令牌', 'member', '2020-01-01', 'stable-token')")
    service = LocalMemberService(database)
    # Migration commits at construction, before any normal read or scheduler lock.
    with sqlite3.connect(database) as conn:
        token = conn.execute("SELECT token FROM local_members WHERE member_id='legacy'").fetchone()[0]
        assert token and conn.execute("SELECT count(*) FROM local_members WHERE token IS NULL").fetchone()[0] == 0
    owner, owner_token = service.whoami(None)
    with sqlite3.connect(database) as writer:
        writer.execute("BEGIN IMMEDIATE")
        assert service.resolve_member(token).member_id == "legacy"
        assert service.whoami(token)[1] == token
        assert service.whoami(None) == (owner, owner_token)
        assert "legacy" in {member.member_id for member in service.list_members()}
    reopened = LocalMemberService(database)
    assert reopened.whoami(token)[1] == token
    if existing_token_column:
        assert reopened.whoami("stable-token")[0].member_id == "stable"
