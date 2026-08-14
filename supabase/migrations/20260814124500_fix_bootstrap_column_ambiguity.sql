-- RETURNS TABLE exposes workspace_id as a PL/pgSQL output variable. Use the
-- primary-key constraint name and explicit table aliases so column references
-- cannot collide with that variable during the first-login bootstrap.

create or replace function public.bootstrap_personal_workspace(workspace_name text)
returns table (workspace_id uuid, onboarding_complete boolean)
language plpgsql
security invoker
set search_path = public, pg_temp
as $$
declare
  caller_id uuid := auth.uid();
  personal_workspace_id uuid;
  onboarding_completed_at timestamptz;
begin
  if caller_id is null then
    raise exception 'authentication required' using errcode = '42501';
  end if;

  select id into personal_workspace_id
  from public.workspaces
  where owner_id = caller_id
  limit 1;

  if personal_workspace_id is null then
    insert into public.workspaces (owner_id, name)
    values (caller_id, left(trim(workspace_name), 80))
    on conflict (owner_id) do nothing
    returning id into personal_workspace_id;

    if personal_workspace_id is null then
      select id into personal_workspace_id
      from public.workspaces
      where owner_id = caller_id
      limit 1;
    end if;
  end if;

  insert into public.onboarding_states (
    workspace_id,
    user_id,
    identity_complete
  )
  values (personal_workspace_id, caller_id, true)
  on conflict on constraint onboarding_states_pkey do update
    set identity_complete = true;

  select state.completed_at into onboarding_completed_at
  from public.onboarding_states as state
  where state.workspace_id = personal_workspace_id
    and state.user_id = caller_id;

  return query select personal_workspace_id, onboarding_completed_at is not null;
end;
$$;

revoke all on function public.bootstrap_personal_workspace(text) from public;
revoke all on function public.bootstrap_personal_workspace(text) from anon;
grant execute on function public.bootstrap_personal_workspace(text) to authenticated;
