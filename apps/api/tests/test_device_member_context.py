"""Apply migrations only to a disposable local PostgreSQL, never the cloud."""
from uuid import uuid4

from test_device_accounts import pg as pg, _users_and_device, _target, _invite, _accept, _last, PROOF, OTHER_PROOF


def call(pg, device, workspace, actor, proof=PROOF):
    return pg(f"SELECT * FROM public.device_member_context('{device}','{proof}','{workspace}');",
        actor=actor, check=False, verbosity='sqlstate')


def test_context_returns_device_role_instead_of_workspace_owner_role(pg):
    owner, [member], device = _users_and_device(pg, 1)
    workspace = _target(pg, owner, device)
    _, token = _invite(pg, owner, device, workspace)
    _accept(pg, member, device, token)
    assert _last(pg(f"SELECT role FROM public.workspace_members WHERE workspace_id='{workspace}' AND user_id='{member}';")) == 'owner'
    result = call(pg, device, workspace, member)
    assert result.returncode == 0
    assert _last(result) == '|'.join([device, workspace, member, 'member'])
    own_space = _last(pg("SELECT workspace_id FROM public.bootstrap_personal_workspace('owner');", actor=owner))
    assert _last(call(pg, device, own_space, owner)) == '|'.join([device, own_space, owner, 'owner'])


def test_context_does_not_grant_roster_access_or_accept_anonymous_calls(pg):
    owner, _, device = _users_and_device(pg, 0)
    workspace = _last(pg("SELECT workspace_id FROM public.bootstrap_personal_workspace('owner');", actor=owner))
    assert _last(pg("SELECT has_function_privilege('anon','public.device_member_context(uuid,text,uuid)','EXECUTE');")) == 'f'
    assert _last(pg("SELECT has_table_privilege('authenticated','public.device_members','SELECT');")) == 'f'
    result = pg(f"SET ROLE anon; SELECT * FROM public.device_member_context('{device}','{PROOF}','{workspace}');", check=False, verbosity='sqlstate')
    assert result.returncode != 0 and '42501' in result.stderr


def test_context_rejects_other_space_wrong_device_proof_and_current_revocation(pg):
    owner, [member], device = _users_and_device(pg, 1)
    workspace = _target(pg, owner, device)
    _, token = _invite(pg, owner, device, workspace)
    _accept(pg, member, device, token)
    for target_device, target_workspace, user, proof in [
        (device, workspace, owner, PROOF),
        (device, str(uuid4()), member, PROOF),
        (str(uuid4()), workspace, member, PROOF),
        (device, workspace, member, OTHER_PROOF),
    ]:
        denied = call(pg, target_device, target_workspace, user, proof)
        assert denied.returncode != 0 and '42501' in denied.stderr
    for status in ('paused', 'left'):
        pg(f"UPDATE public.device_members SET status='{status}' WHERE device_id='{device}' AND user_id='{member}';")
        denied = call(pg, device, workspace, member)
        assert denied.returncode != 0 and '42501' in denied.stderr
    pg(f"UPDATE public.device_members SET status='active' WHERE device_id='{device}' AND user_id='{member}';")
    pg(f"DELETE FROM public.workspace_members WHERE workspace_id='{workspace}' AND user_id='{member}';")
    denied = call(pg, device, workspace, member)
    assert denied.returncode != 0 and '42501' in denied.stderr
