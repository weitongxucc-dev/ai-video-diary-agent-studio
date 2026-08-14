-- Idempotent first-login bootstrap for an authenticated user. This runs with
-- the caller's RLS identity and never requires a service/secret key.

-- Reassert the minimum browser grants for projects that bootstrapped the
-- identity tables before migration history was recorded.
revoke all on public.profiles from anon, authenticated;
revoke all on public.workspaces from anon, authenticated;
revoke all on public.workspace_members from anon, authenticated;
revoke all on public.onboarding_states from anon, authenticated;

grant select on public.profiles to authenticated;
grant update (display_name) on public.profiles to authenticated;
grant select on public.workspaces to authenticated;
grant insert (owner_id, name) on public.workspaces to authenticated;
grant select on public.workspace_members to authenticated;
grant select on public.onboarding_states to authenticated;
grant insert (workspace_id, user_id, identity_complete)
  on public.onboarding_states to authenticated;
grant update (identity_complete) on public.onboarding_states to authenticated;

alter table public.workspaces
  add constraint workspaces_one_personal_workspace_per_owner unique (owner_id);

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
