from __future__ import annotations

import shutil
import socket
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from app.auth import AuthPrincipal
from app.config import Settings
from app.device_accounts import (
    DeviceAccountError,
    DeviceAccountService,
    DeviceCredential,
)


ROOT = Path(__file__).resolve().parents[3]
PROOF = "ab" * 32
OTHER_PROOF = "cd" * 32


def _sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


@pytest.fixture(scope="module")
def pg():
    if not all(shutil.which(name) for name in ("initdb", "pg_ctl", "psql")):
        pytest.skip("isolated PostgreSQL binaries are unavailable")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix="device-accounts-pg-", dir="/tmp") as pg_dir:
        subprocess.run(
            ["initdb", "-D", pg_dir, "-A", "trust", "-U", "postgres"],
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            [
                "pg_ctl", "-D", pg_dir,
                "-o", f"-k {pg_dir} -p {port}",
                "-l", f"{pg_dir}/server.log", "start",
            ],
            check=True,
            capture_output=True,
            text=True,
        )

        def run(
            sql: str,
            *,
            actor: str | None = None,
            check: bool = True,
            verbosity: str | None = None,
        ):
            identity = ""
            if actor is not None:
                identity = (
                    "SET ROLE authenticated;\n"
                    f"SET request.jwt.claim.sub = {_sql_string(actor)};\n"
                )
            args = [
                "psql", "-X", "-A", "-t", "-F", "|",
                "-v", "ON_ERROR_STOP=1",
                "-h", pg_dir, "-p", str(port), "-U", "postgres", "-d", "postgres",
            ]
            if verbosity is not None:
                # Per-call only: psql renders the error text in the server
                # locale, so assertions on the wording break under a non-English
                # PostgreSQL. "sqlstate" keeps the localized prefix but always
                # prints the locale-independent SQLSTATE code.
                args += ["-v", f"VERBOSITY={verbosity}"]
            result = subprocess.run(
                args,
                input=identity + sql,
                text=True,
                capture_output=True,
            )
            if check and result.returncode:
                raise AssertionError(result.stderr)
            return result

        run.socket_dir = pg_dir
        run.port = port

        try:
            run(
                """
                CREATE ROLE anon;
                CREATE ROLE authenticated;
                CREATE SCHEMA auth;
                CREATE TABLE auth.users (
                  id uuid PRIMARY KEY,
                  raw_user_meta_data jsonb NOT NULL DEFAULT '{}'::jsonb
                );
                CREATE FUNCTION auth.uid() RETURNS uuid LANGUAGE sql STABLE AS $$
                  SELECT nullif(current_setting('request.jwt.claim.sub', true), '')::uuid
                $$;
                GRANT USAGE ON SCHEMA auth TO authenticated;
                GRANT EXECUTE ON FUNCTION auth.uid() TO authenticated;
                """
            )
            for migration in sorted((ROOT / "supabase/migrations").glob("*.sql")):
                run(migration.read_text(encoding="utf-8"))
            yield run
        finally:
            subprocess.run(
                ["pg_ctl", "-D", pg_dir, "stop", "-m", "immediate"],
                check=True,
                capture_output=True,
                text=True,
            )


def _last(result: subprocess.CompletedProcess[str]) -> str:
    return result.stdout.strip().splitlines()[-1]


def _users_and_device(pg, member_count: int, *, proof: str = PROOF):
    owner = str(uuid4())
    members = [str(uuid4()) for _ in range(member_count)]
    device = str(uuid4())
    values = ",".join(f"({_sql_string(user)})" for user in [owner, *members])
    pg(f"INSERT INTO auth.users(id) VALUES {values};")
    pg(
        "INSERT INTO public.devices(id,owner_user_id,proof_digest) VALUES "
        f"({_sql_string(device)},{_sql_string(owner)},"
        f"sha256(decode({_sql_string(proof)},'hex')));"
    )
    return owner, members, device


def _target(pg, owner: str, device: str, proof: str = PROOF) -> str:
    return _last(pg(
        "SELECT public.device_create_member_space("
        f"{_sql_string(device)},{_sql_string(proof)},'成员空间');",
        actor=owner,
    ))


def _invite(pg, owner: str, device: str, target: str, proof: str = PROOF):
    row = _last(pg(
        "SELECT * FROM public.device_create_invite("
        f"{_sql_string(device)},{_sql_string(proof)},{_sql_string(target)},3600);",
        actor=owner,
    ))
    invite_id, token, _expiry = row.split("|")
    return invite_id, token


def _accept(pg, member: str, device: str, token: str, proof: str = PROOF, *, check=True):
    return pg(
        "SELECT * FROM public.device_accept_invite("
        f"{_sql_string(device)},{_sql_string(proof)},{_sql_string(token)});",
        actor=member,
        check=check,
    )


def test_owner_seat_target_transfer_idempotency_and_private_rls(pg):
    owner, [member], device = _users_and_device(pg, 1)
    personal = _last(pg(
        "SELECT workspace_id FROM public.bootstrap_personal_workspace('主账号个人空间');",
        actor=owner,
    ))
    target = _target(pg, owner, device)
    second_reserved_target = _target(pg, owner, device)
    assert second_reserved_target != target
    assert _last(pg(
        "SELECT count(*) FROM public.device_members "
        f"WHERE device_id={_sql_string(device)} AND status='active';"
    )) == "1"
    assert personal != target
    assert _last(pg(
        "SELECT workspace_id FROM public.bootstrap_personal_workspace('重复登录');",
        actor=owner,
    )) == personal
    # The unique index name is catalog text, never localized by PostgreSQL, so
    # this pins *which* one-per-owner guard rejected the second row instead of
    # pinning the English wording of the error class.
    assert "workspaces_one_personal_per_owner" in pg(
        "INSERT INTO public.workspaces(owner_id,name) VALUES "
        f"({_sql_string(owner)},'重复个人空间');",
        check=False,
    ).stderr
    # RLS rejections are raised by PostgreSQL itself, not by our RPC, so the
    # message text is localized; 42501 (insufficient_privilege) is not.
    assert "42501" in pg(
        "INSERT INTO public.onboarding_states(workspace_id,user_id,identity_complete) "
        f"VALUES ({_sql_string(target)},{_sql_string(owner)},true);",
        actor=owner,
        check=False,
        verbosity="sqlstate",
    ).stderr
    assert "ready workspace membership required" in pg(
        f"SELECT public.complete_own_onboarding({_sql_string(target)});",
        actor=owner,
        check=False,
    ).stderr
    invite_id, token = _invite(pg, owner, device, target)

    assert _last(_accept(pg, member, device, token)) == f"{target}|2|f"
    assert _last(_accept(pg, member, device, token)) == f"{target}|2|t"
    assert "device owner required" in pg(
        "SELECT public.device_create_member_space("
        f"{_sql_string(device)},{_sql_string(PROOF)},'越权空间');",
        actor=member,
        check=False,
    ).stderr
    stranger = str(uuid4())
    pg(f"INSERT INTO auth.users(id) VALUES ({_sql_string(stranger)});")
    assert "invite is no longer available" in _accept(
        pg, stranger, device, token, check=False
    ).stderr
    assert _last(pg(
        "SELECT count(*) FROM public.device_members "
        f"WHERE device_id={_sql_string(device)} AND status='active';"
    )) == "2"
    assert _last(pg(
        "SELECT owner_id::text || '|' || kind || '|' || provisioning_state "
        f"FROM public.workspaces WHERE id={_sql_string(target)};"
    )) == f"{member}|member_private|ready"
    assert _last(pg(
        f"SELECT count(*) FROM public.workspaces WHERE id={_sql_string(target)};",
        actor=owner,
    )) == "0"
    assert _last(pg(
        "SELECT count(*) FROM public.workspace_members "
        f"WHERE workspace_id={_sql_string(target)} AND user_id={_sql_string(owner)};"
    )) == "0"
    assert _last(pg(
        f"SELECT count(*) FROM public.workspaces WHERE id={_sql_string(target)};",
        actor=member,
    )) == "1"
    assert "accepted invite cannot be revoked" in pg(
        "SELECT public.device_revoke_invite("
        f"{_sql_string(device)},{_sql_string(PROOF)},{_sql_string(invite_id)});",
        actor=owner,
        check=False,
    ).stderr


def test_revoked_expired_wrong_device_and_direct_table_writes_fail(pg):
    owner, [member], device = _users_and_device(pg, 1)
    other_owner, _, other_device = _users_and_device(pg, 0, proof=OTHER_PROOF)
    target = _target(pg, owner, device)
    invite_id, token = _invite(pg, owner, device, target)

    assert _last(pg(
        "SELECT public.device_revoke_invite("
        f"{_sql_string(device)},{_sql_string(PROOF)},{_sql_string(invite_id)});",
        actor=owner,
    )) == "t"
    assert "invite is no longer available" in _accept(
        pg, member, device, token, check=False
    ).stderr
    assert "valid invite required" in _accept(
        pg, member, other_device, token, OTHER_PROOF, check=False
    ).stderr
    assert "device authorization required" in _accept(
        pg, member, device, token, OTHER_PROOF, check=False
    ).stderr
    # 42501 is insufficient_privilege. A privilege denial names no catalog
    # object, so the SQLSTATE class is the only locale-independent handle.
    assert "42501" in pg(
        "INSERT INTO public.device_members(device_id,user_id,role,status) VALUES "
        f"({_sql_string(other_device)},{_sql_string(member)},'member','active');",
        actor=other_owner,
        check=False,
        verbosity="sqlstate",
    ).stderr
    assert _last(pg(
        "SELECT has_function_privilege('anon', "
        "'public.device_accept_invite(uuid,text,text)', 'EXECUTE');"
    )) == "f"
    assert _last(pg(
        "SELECT has_table_privilege('authenticated', "
        "'public.device_members', 'INSERT');"
    )) == "f"
    assert _last(pg(
        "SELECT relrowsecurity FROM pg_class "
        "WHERE oid='public.device_invites'::regclass;"
    )) == "t"

    _, expiring = _invite(pg, owner, device, target)
    pg(
        "UPDATE public.device_invites SET expires_at=now()-interval '1 second' "
        f"WHERE token_digest=sha256(decode({_sql_string(expiring)},'hex'));"
    )
    assert "invite is no longer available" in _accept(
        pg, member, device, expiring, check=False
    ).stderr


def test_concurrent_fifth_and_sixth_member_cannot_overfill(pg):
    owner, members, device = _users_and_device(pg, 5)
    tokens = [_invite(pg, owner, device, _target(pg, owner, device))[1]
              for _ in members]
    for member, token in zip(members[:3], tokens[:3]):
        assert _accept(pg, member, device, token).returncode == 0

    locker = subprocess.Popen(
        [
            "psql", "-X", "-q", "-A", "-t", "-v", "ON_ERROR_STOP=1",
            "-h", pg.socket_dir, "-p", str(pg.port), "-U", "postgres", "-d", "postgres",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    try:
        assert locker.stdin is not None and locker.stdout is not None
        locker.stdin.write(
            "BEGIN;\nSELECT 1 FROM public.devices WHERE id="
            f"{_sql_string(device)} FOR UPDATE;\nSELECT 'LOCK_HELD';\n"
        )
        locker.stdin.flush()
        assert any("LOCK_HELD" in locker.stdout.readline() for _ in range(2))
        with ThreadPoolExecutor(max_workers=2) as pool:
            pending = [
                pool.submit(_accept, pg, member, device, token, check=False)
                for member, token in zip(members[3:], tokens[3:])
            ]
            time.sleep(0.2)
            assert all(not future.done() for future in pending)
            locker.stdin.write("COMMIT;\n\\q\n")
            locker.stdin.flush()
            outcomes = [future.result() for future in pending]
    finally:
        if locker.poll() is None:
            locker.terminate()
        locker.communicate(timeout=5)
    assert sorted(result.returncode == 0 for result in outcomes) == [False, True]
    assert "device member limit reached" in " ".join(
        result.stderr for result in outcomes if result.returncode
    )
    rejected_index = next(index for index, result in enumerate(outcomes) if result.returncode)
    assert "device member limit reached" in _accept(
        pg, members[3 + rejected_index], device, tokens[3 + rejected_index], check=False
    ).stderr
    assert _last(pg(
        "SELECT count(*) FROM public.device_members "
        f"WHERE device_id={_sql_string(device)} AND status='active';"
    )) == "5"
    another_target = _target(pg, owner, device)
    assert "device member limit reached" in pg(
        "SELECT * FROM public.device_create_invite("
        f"{_sql_string(device)},{_sql_string(PROOF)},"
        f"{_sql_string(another_target)},3600);",
        actor=owner,
        check=False,
    ).stderr


def test_target_with_prior_onboarding_cannot_be_transferred(pg):
    owner, [member], device = _users_and_device(pg, 1)
    target = _target(pg, owner, device)
    _, token = _invite(pg, owner, device, target)
    pg(
        "INSERT INTO public.onboarding_states(workspace_id,user_id,identity_complete) "
        f"VALUES ({_sql_string(target)},{_sql_string(owner)},true);"
    )
    assert "reserved empty target workspace required" in _accept(
        pg, member, device, token, check=False
    ).stderr
    assert _last(pg(
        "SELECT count(*) FROM public.device_members "
        f"WHERE device_id={_sql_string(device)} AND status='active';"
    )) == "1"
    assert _last(pg(
        f"SELECT owner_id FROM public.workspaces WHERE id={_sql_string(target)};"
    )) == owner


def test_failure_after_seat_insert_rolls_back_every_acceptance_write(pg):
    owner, [member], device = _users_and_device(pg, 1)
    target = _target(pg, owner, device)
    _, token = _invite(pg, owner, device, target)
    pg(
        """
        CREATE FUNCTION public.reject_target_transfer_for_test()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF new.id::text = current_setting('test.reject_target', true) THEN
            RAISE EXCEPTION 'forced transfer failure';
          END IF;
          RETURN new;
        END;
        $$;
        CREATE TRIGGER reject_target_transfer_for_test
        BEFORE UPDATE OF owner_id ON public.workspaces
        FOR EACH ROW EXECUTE FUNCTION public.reject_target_transfer_for_test();
        """
    )
    try:
        failed = pg(
            f"SET test.reject_target = {_sql_string(target)};\n"
            "SELECT * FROM public.device_accept_invite("
            f"{_sql_string(device)},{_sql_string(PROOF)},{_sql_string(token)});",
            actor=member,
            check=False,
        )
        assert "forced transfer failure" in failed.stderr
        assert _last(pg(
            "SELECT count(*) FROM public.device_members "
            f"WHERE device_id={_sql_string(device)} AND status='active';"
        )) == "1"
        assert _last(pg(
            "SELECT count(*) FROM public.workspace_members "
            f"WHERE workspace_id={_sql_string(target)} AND user_id={_sql_string(member)};"
        )) == "0"
        assert _last(pg(
            "SELECT owner_id::text || '|' || provisioning_state "
            f"FROM public.workspaces WHERE id={_sql_string(target)};"
        )) == f"{owner}|reserved"
        assert _last(pg(
            "SELECT count(*) FROM public.device_invites "
            f"WHERE token_digest=sha256(decode({_sql_string(token)},'hex')) "
            "AND accepted_at IS NOT NULL;"
        )) == "0"
    finally:
        pg("DROP TRIGGER reject_target_transfer_for_test ON public.workspaces;")
        pg("DROP FUNCTION public.reject_target_transfer_for_test();")


def test_python_adapter_consumes_verified_actor_and_never_logs_proof(monkeypatch, tmp_path):
    settings = Settings(
        deepseek_api_key="sk-test",
        supabase_url="https://example.supabase.co",
        supabase_publishable_key="public-key",
        database_path=str(tmp_path / "unused.sqlite"),
    )
    device = DeviceCredential(str(uuid4()), PROOF)
    service = DeviceAccountService(settings, device)
    assert PROOF not in repr(device)
    principal = AuthPrincipal(user_id=str(uuid4()), workspace_id=str(uuid4()), access_token="user-jwt")
    calls = []

    def fake_post(url, *, headers, json, timeout):
        calls.append((url, headers, json, timeout))
        if url.endswith("device_create_member_space"):
            return httpx.Response(200, json="00000000-0000-0000-0000-000000000001")
        if url.endswith("device_create_invite"):
            return httpx.Response(200, json=[{
                "invite_id": "00000000-0000-0000-0000-000000000002",
                "invite_token": "ef" * 32,
                "expires_at": "2026-09-28T00:00:00Z",
            }])
        if url.endswith("device_revoke_invite"):
            return httpx.Response(200, json=True)
        return httpx.Response(200, json=[{
            "workspace_id": "00000000-0000-0000-0000-000000000001",
            "member_count": 2,
            "already_accepted": False,
        }])

    monkeypatch.setattr(httpx, "post", fake_post)
    assert service.create_member_space(principal, "成员空间") == "00000000-0000-0000-0000-000000000001"
    assert calls[0][1]["Authorization"] == "Bearer user-jwt"
    assert calls[0][2]["target_device_id"] == device.device_id
    assert calls[0][2]["device_proof"] == PROOF
    assert calls[0][2]["space_name"] == "成员空间"
    invite = service.create_invite(
        principal, "00000000-0000-0000-0000-000000000001", valid_for_seconds=600
    )
    assert invite.target_workspace_id == "00000000-0000-0000-0000-000000000001"
    assert invite.token == "ef" * 32
    assert invite.token not in repr(invite)
    assert calls[1][2]["valid_for_seconds"] == 600
    assert service.revoke_invite(principal, invite.id) is True
    acceptance = service.accept_invite(principal, invite.token)
    assert acceptance.member_count == 2
    assert acceptance.already_accepted is False
    with pytest.raises(DeviceAccountError, match="verified account identity"):
        service.create_member_space(
            AuthPrincipal(user_id="local", workspace_id="local", authenticated=False),
            "bad",
        )
    assert len(calls) == 4
