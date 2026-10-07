-- Native backend capability: a permission bit for one original device/user/
-- workspace tuple, never a login, roster or private-content read capability.
create function public.device_creation_permit(
  target_device_id uuid, device_proof text,
  target_user_id uuid, target_workspace_id uuid
) returns boolean
language plpgsql stable security definer set search_path = ''
as $$
begin
  if device_proof is null or device_proof !~ '^[0-9a-f]{64}$' then
    return false;
  end if;
  return exists (
    select 1 from public.devices as device
    join public.device_members as member
      on member.device_id = device.id and member.user_id = target_user_id
    join public.workspace_members as workspace_member
      on workspace_member.user_id = member.user_id
      and workspace_member.workspace_id = target_workspace_id
    join public.workspaces as workspace
      on workspace.id = workspace_member.workspace_id
    where device.id = target_device_id and device.retired_at is null
      and device.proof_digest = pg_catalog.sha256(pg_catalog.decode(device_proof, 'hex'))
      and member.status = 'active' and workspace.provisioning_state = 'ready'
  );
end;
$$;
revoke all on function public.device_creation_permit(uuid, text, uuid, uuid) from public;
grant execute on function public.device_creation_permit(uuid, text, uuid, uuid)
  to anon, authenticated;
