-- AI 视频日记 Agent Studio
-- Invite-only beta identity, personal workspaces and resumable onboarding.
--
-- Security model:
--   * Auth owns identities in auth.users.
--   * Every public table has RLS enabled.
--   * The browser can only create a workspace for itself and can never grant
--     roles. A trigger creates the initial owner membership.
--   * Invite delivery / membership administration remains a trusted-server
--     operation and is deliberately not exposed through the Data API.

create schema if not exists app_private;

create type public.workspace_role as enum ('owner', 'member');

create table public.profiles (
  id uuid primary key references auth.users (id) on delete cascade,
  display_name text,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now()),
  constraint profiles_display_name_length
    check (display_name is null or char_length(trim(display_name)) between 1 and 80)
);

create table public.workspaces (
  id uuid primary key default gen_random_uuid(),
  owner_id uuid not null references auth.users (id) on delete restrict,
  name text not null,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now()),
  constraint workspaces_name_length check (char_length(trim(name)) between 1 and 80)
);

create table public.workspace_members (
  workspace_id uuid not null references public.workspaces (id) on delete cascade,
  user_id uuid not null references auth.users (id) on delete cascade,
  role public.workspace_role not null default 'member',
  created_at timestamptz not null default timezone('utc', now()),
  primary key (workspace_id, user_id)
);

create table public.onboarding_states (
  workspace_id uuid primary key references public.workspaces (id) on delete cascade,
  user_id uuid not null references auth.users (id) on delete cascade,
  identity_complete boolean not null default false,
  local_paths_complete boolean not null default false,
  ai_connection_complete boolean not null default false,
  health_check_complete boolean not null default false,
  first_episode_complete boolean not null default false,
  completed_at timestamptz,
  updated_at timestamptz not null default timezone('utc', now()),
  constraint onboarding_state_workspace_member_fk
    foreign key (workspace_id, user_id)
    references public.workspace_members (workspace_id, user_id)
    on delete cascade,
  constraint onboarding_completed_only_after_all_steps
    check (
      completed_at is null
      or (
        identity_complete
        and local_paths_complete
        and ai_connection_complete
        and health_check_complete
        and first_episode_complete
      )
    )
);

create index workspace_members_user_id_idx on public.workspace_members (user_id);
create index onboarding_states_user_id_idx on public.onboarding_states (user_id);
create index workspaces_owner_id_idx on public.workspaces (owner_id);
create index onboarding_states_workspace_user_idx
  on public.onboarding_states (workspace_id, user_id);

create or replace function app_private.set_updated_at()
returns trigger
language plpgsql
security invoker
set search_path = public, pg_temp
as $$
begin
  new.updated_at = timezone('utc', now());
  return new;
end;
$$;

create or replace function app_private.handle_new_auth_user()
returns trigger
language plpgsql
security definer
set search_path = public, pg_temp
as $$
begin
  insert into public.profiles (id, display_name)
  values (
    new.id,
    nullif(trim(coalesce(new.raw_user_meta_data ->> 'display_name', '')), '')
  )
  on conflict (id) do nothing;
  return new;
end;
$$;

create or replace function app_private.create_owner_membership()
returns trigger
language plpgsql
security definer
set search_path = public, pg_temp
as $$
begin
  insert into public.workspace_members (workspace_id, user_id, role)
  values (new.id, new.owner_id, 'owner')
  on conflict (workspace_id, user_id) do nothing;
  return new;
end;
$$;

create trigger profiles_set_updated_at
before update on public.profiles
for each row execute function app_private.set_updated_at();

create trigger workspaces_set_updated_at
before update on public.workspaces
for each row execute function app_private.set_updated_at();

create trigger onboarding_states_set_updated_at
before update on public.onboarding_states
for each row execute function app_private.set_updated_at();

create trigger on_auth_user_created
after insert on auth.users
for each row execute function app_private.handle_new_auth_user();

create trigger on_workspace_created
after insert on public.workspaces
for each row execute function app_private.create_owner_membership();

revoke all on function app_private.set_updated_at() from public;
revoke all on function app_private.handle_new_auth_user() from public;
revoke all on function app_private.create_owner_membership() from public;

alter table public.profiles enable row level security;
alter table public.workspaces enable row level security;
alter table public.workspace_members enable row level security;
alter table public.onboarding_states enable row level security;

grant usage on schema public to authenticated;
grant select, update on public.profiles to authenticated;
grant select, insert on public.workspaces to authenticated;
grant select on public.workspace_members to authenticated;
grant select, insert, update on public.onboarding_states to authenticated;

create policy "profiles_select_self"
on public.profiles for select to authenticated
using ((select auth.uid()) = id);

create policy "profiles_update_self"
on public.profiles for update to authenticated
using ((select auth.uid()) = id)
with check ((select auth.uid()) = id);

create policy "workspaces_select_membership"
on public.workspaces for select to authenticated
using (
  exists (
    select 1
    from public.workspace_members member
    where member.workspace_id = workspaces.id
      and member.user_id = (select auth.uid())
  )
);

create policy "workspaces_create_self_owned"
on public.workspaces for insert to authenticated
with check ((select auth.uid()) = owner_id);

create policy "workspace_members_select_self"
on public.workspace_members for select to authenticated
using ((select auth.uid()) = user_id);

create policy "onboarding_states_select_self"
on public.onboarding_states for select to authenticated
using ((select auth.uid()) = user_id);

create policy "onboarding_states_create_self"
on public.onboarding_states for insert to authenticated
with check (
  (select auth.uid()) = user_id
  and exists (
    select 1
    from public.workspace_members member
    where member.workspace_id = onboarding_states.workspace_id
      and member.user_id = (select auth.uid())
  )
);

create policy "onboarding_states_update_self"
on public.onboarding_states for update to authenticated
using ((select auth.uid()) = user_id)
with check (
  (select auth.uid()) = user_id
  and exists (
    select 1
    from public.workspace_members member
    where member.workspace_id = onboarding_states.workspace_id
      and member.user_id = (select auth.uid())
  )
);
