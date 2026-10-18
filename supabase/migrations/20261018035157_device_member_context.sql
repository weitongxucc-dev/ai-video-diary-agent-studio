-- One authenticated caller's current binding. No roster, proof or other space
-- metadata is returned, and clients never gain SELECT on device membership.
create function public.device_member_context(
  target_device_id uuid, device_proof text, target_workspace_id uuid
)
returns table (device_id uuid, workspace_id uuid, member_id uuid, role text)
language plpgsql stable security definer set search_path = ''
as $$
declare
  caller_id uuid := auth.uid();
begin
  if caller_id is null or not public.device_creation_permit(
    target_device_id, device_proof, caller_id, target_workspace_id
  ) then
    raise exception 'active device and workspace membership required' using errcode = '42501';
  end if;
  return query
    select target_device_id, target_workspace_id, caller_id, member.role
    from public.device_members as member
    where member.device_id = target_device_id and member.user_id = caller_id
      and member.status = 'active';
end;
$$;

revoke all on function public.device_member_context(uuid, text, uuid)
  from public, anon, authenticated;
grant execute on function public.device_member_context(uuid, text, uuid)
  to authenticated;
