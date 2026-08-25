-- A packaged desktop app must never contain a service-role secret. This RPC
-- lets the already-authenticated user complete only their own workspace after
-- the local API has passed its readiness checks.

create or replace function public.complete_own_onboarding(target_workspace_id uuid)
returns boolean
language plpgsql
security definer
set search_path = ''
as $$
declare
  caller_id uuid := auth.uid();
begin
  if caller_id is null then
    raise exception 'authentication required' using errcode = '42501';
  end if;

  if not exists (
    select 1
    from public.workspace_members as member
    where member.workspace_id = target_workspace_id
      and member.user_id = caller_id
  ) then
    raise exception 'workspace membership required' using errcode = '42501';
  end if;

  update public.onboarding_states as state
  set local_paths_complete = true,
      ai_connection_complete = true,
      health_check_complete = true,
      first_episode_complete = true,
      completed_at = now()
  where state.workspace_id = target_workspace_id
    and state.user_id = caller_id;

  return found;
end;
$$;

revoke all on function public.complete_own_onboarding(uuid) from public;
revoke all on function public.complete_own_onboarding(uuid) from anon;
grant execute on function public.complete_own_onboarding(uuid) to authenticated;
