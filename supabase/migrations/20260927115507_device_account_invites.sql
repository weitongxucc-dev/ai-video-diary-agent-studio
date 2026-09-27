-- WIL-59: invitation seats for an already provisioned, trusted device.
-- This migration does not register or claim a device. A trusted operator must
-- insert its id, owner and SHA-256 digest of a 32-byte device proof first.
-- The proof is held by the native device runtime, never by the Web renderer.

alter table public.workspaces
  add column kind text not null default 'personal'
    check (kind in ('personal', 'member_private', 'team')),
  add column provisioning_state text not null default 'ready'
    check (provisioning_state in ('ready', 'reserved')),
  add column provisioned_device_id uuid;

alter table public.workspaces
  drop constraint workspaces_one_personal_workspace_per_owner;

create unique index workspaces_one_personal_per_owner
  on public.workspaces (owner_id) where kind = 'personal';
create index workspaces_provisioned_device
  on public.workspaces (provisioned_device_id, provisioning_state);

alter policy "workspaces_create_self_owned" on public.workspaces
  with check (
    (select auth.uid()) = owner_id
    and kind = 'personal'
    and provisioning_state = 'ready'
    and provisioned_device_id is null
  );

alter policy "onboarding_states_create_self" on public.onboarding_states
  with check (
    (select auth.uid()) = user_id
    and exists (
      select 1 from public.workspace_members as member
      join public.workspaces as workspace on workspace.id = member.workspace_id
      where member.workspace_id = onboarding_states.workspace_id
        and member.user_id = (select auth.uid())
        and workspace.provisioning_state = 'ready'
    )
  );
alter policy "onboarding_states_update_self" on public.onboarding_states
  using (
    (select auth.uid()) = user_id
    and exists (
      select 1 from public.workspace_members as member
      join public.workspaces as workspace on workspace.id = member.workspace_id
      where member.workspace_id = onboarding_states.workspace_id
        and member.user_id = (select auth.uid())
        and workspace.provisioning_state = 'ready'
    )
  )
  with check (
    (select auth.uid()) = user_id
    and exists (
      select 1 from public.workspace_members as member
      join public.workspaces as workspace on workspace.id = member.workspace_id
      where member.workspace_id = onboarding_states.workspace_id
        and member.user_id = (select auth.uid())
        and workspace.provisioning_state = 'ready'
    )
  );

-- Existing first-login consumers may still call this RPC. It is constrained
-- to personal workspaces; an invitation must preserve its target workspace.
create or replace function public.bootstrap_personal_workspace(workspace_name text)
returns table (workspace_id uuid, onboarding_complete boolean)
language plpgsql
security invoker
set search_path = ''
as $$
declare
  caller_id uuid := auth.uid();
  personal_workspace_id uuid;
  onboarding_completed_at timestamptz;
begin
  if caller_id is null then
    raise exception 'authentication required' using errcode = '42501';
  end if;

  select workspace.id into personal_workspace_id
  from public.workspaces as workspace
  where workspace.owner_id = caller_id and workspace.kind = 'personal'
  limit 1;

  if personal_workspace_id is null then
    insert into public.workspaces (owner_id, name)
    values (caller_id, left(trim(workspace_name), 80))
    on conflict do nothing
    returning id into personal_workspace_id;

    if personal_workspace_id is null then
      select workspace.id into personal_workspace_id
      from public.workspaces as workspace
      where workspace.owner_id = caller_id and workspace.kind = 'personal'
      limit 1;
    end if;
  end if;

  insert into public.onboarding_states (workspace_id, user_id, identity_complete)
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

-- Reserved member spaces are not onboarded until their invitation is accepted.
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
    join public.workspaces as workspace on workspace.id = member.workspace_id
    where member.workspace_id = target_workspace_id
      and member.user_id = caller_id
      and workspace.provisioning_state = 'ready'
  ) then
    raise exception 'ready workspace membership required' using errcode = '42501';
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

create table public.devices (
  id uuid primary key,
  owner_user_id uuid not null references auth.users (id) on delete restrict,
  proof_digest bytea not null check (octet_length(proof_digest) = 32),
  registered_at timestamptz not null default now(),
  retired_at timestamptz
);

create table public.device_members (
  device_id uuid not null references public.devices (id) on delete restrict,
  user_id uuid not null references auth.users (id) on delete restrict,
  role text not null check (role in ('owner', 'member')),
  status text not null check (status in ('active', 'paused', 'left')),
  joined_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (device_id, user_id)
);
create unique index device_one_active_owner
  on public.device_members (device_id)
  where role = 'owner' and status = 'active';
create index device_members_active_count
  on public.device_members (device_id, status);

create table public.device_invites (
  id uuid primary key default gen_random_uuid(),
  device_id uuid not null references public.devices (id) on delete restrict,
  target_workspace_id uuid not null references public.workspaces (id) on delete restrict,
  issued_by uuid not null references auth.users (id) on delete restrict,
  token_digest bytea not null unique check (octet_length(token_digest) = 32),
  expires_at timestamptz not null,
  revoked_at timestamptz,
  accepted_by uuid references auth.users (id) on delete restrict,
  accepted_at timestamptz,
  created_at timestamptz not null default now(),
  check ((accepted_by is null) = (accepted_at is null))
);
create index device_invites_target
  on public.device_invites (device_id, target_workspace_id, expires_at);

create or replace function app_private.add_device_owner_membership()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  insert into public.device_members (device_id, user_id, role, status)
  values (new.id, new.owner_user_id, 'owner', 'active');
  return new;
end;
$$;
create trigger device_owner_is_first_member
  after insert on public.devices
  for each row execute function app_private.add_device_owner_membership();

alter table public.devices enable row level security;
alter table public.device_members enable row level security;
alter table public.device_invites enable row level security;
revoke all on public.devices from anon, authenticated;
revoke all on public.device_members from anon, authenticated;
revoke all on public.device_invites from anon, authenticated;
revoke all on function app_private.add_device_owner_membership() from public, anon, authenticated;

-- All invitation RPCs call this under their SECURITY DEFINER owner. This is
-- the remote check that prevents bypassing the local API with an ordinary JWT.
create or replace function app_private.require_trusted_device(
  target_device_id uuid,
  device_proof text,
  caller_id uuid,
  owner_required boolean
)
returns uuid
language plpgsql
security invoker
set search_path = ''
as $$
declare
  registered_owner uuid;
begin
  if caller_id is null or device_proof is null
      or device_proof !~ '^[0-9a-f]{64}$' then
    raise exception 'device authorization required' using errcode = '42501';
  end if;
  select device.owner_user_id into registered_owner
  from public.devices as device
  where device.id = target_device_id
    and device.retired_at is null
    and device.proof_digest = pg_catalog.sha256(pg_catalog.decode(device_proof, 'hex'));
  if registered_owner is null then
    raise exception 'device authorization required' using errcode = '42501';
  end if;
  if owner_required and registered_owner <> caller_id then
    raise exception 'device owner required' using errcode = '42501';
  end if;
  if owner_required and not exists (
    select 1 from public.device_members as member
    where member.device_id = target_device_id and member.user_id = caller_id
      and member.role = 'owner' and member.status = 'active'
  ) then
    raise exception 'active device owner required' using errcode = '42501';
  end if;
  return registered_owner;
end;
$$;
revoke all on function app_private.require_trusted_device(uuid, text, uuid, boolean)
  from public, anon, authenticated;

create or replace function public.device_create_member_space(
  target_device_id uuid,
  device_proof text,
  space_name text
)
returns uuid
language plpgsql
security definer
set search_path = ''
as $$
declare
  caller_id uuid := auth.uid();
  created_workspace_id uuid;
begin
  perform app_private.require_trusted_device(
    target_device_id, device_proof, caller_id, true
  );
  if space_name is null or char_length(trim(space_name)) not between 1 and 80 then
    raise exception 'space name must be 1 to 80 characters' using errcode = '22023';
  end if;
  insert into public.workspaces (
    owner_id, name, kind, provisioning_state, provisioned_device_id
  ) values (
    caller_id, trim(space_name), 'member_private', 'reserved', target_device_id
  ) returning id into created_workspace_id;
  return created_workspace_id;
end;
$$;

create or replace function public.device_create_invite(
  target_device_id uuid,
  device_proof text,
  target_workspace_id uuid,
  valid_for_seconds integer
)
returns table (invite_id uuid, invite_token text, expires_at timestamptz)
language plpgsql
security definer
set search_path = ''
as $$
declare
  caller_id uuid := auth.uid();
  raw_token text;
  active_count integer;
begin
  perform app_private.require_trusted_device(
    target_device_id, device_proof, caller_id, true
  );
  if valid_for_seconds is null or valid_for_seconds not between 60 and 86400 then
    raise exception 'invite lifetime must be 60 to 86400 seconds'
      using errcode = '22023';
  end if;
  perform 1 from public.devices as device
  where device.id = target_device_id for update;
  select count(*)::integer into active_count from public.device_members as member
  where member.device_id = target_device_id and member.status = 'active';
  if active_count >= 5 then
    raise exception 'device member limit reached' using errcode = '23514';
  end if;
  if not exists (
    select 1 from public.workspaces as workspace
    where workspace.id = target_workspace_id
      and workspace.owner_id = caller_id
      and workspace.kind = 'member_private'
      and workspace.provisioning_state = 'reserved'
      and workspace.provisioned_device_id = target_device_id
  ) then
    raise exception 'reserved target workspace required' using errcode = '42501';
  end if;
  raw_token := replace(pg_catalog.gen_random_uuid()::text, '-', '')
    || replace(pg_catalog.gen_random_uuid()::text, '-', '');
  return query
    insert into public.device_invites (
      device_id, target_workspace_id, issued_by, token_digest, expires_at
    ) values (
      target_device_id, target_workspace_id, caller_id,
      pg_catalog.sha256(pg_catalog.decode(raw_token, 'hex')),
      now() + make_interval(secs => valid_for_seconds)
    )
    returning id, raw_token, public.device_invites.expires_at;
end;
$$;

create or replace function public.device_revoke_invite(
  target_device_id uuid,
  device_proof text,
  invite_id uuid
)
returns boolean
language plpgsql
security definer
set search_path = ''
as $$
declare
  caller_id uuid := auth.uid();
  invite_row public.device_invites%rowtype;
begin
  perform app_private.require_trusted_device(
    target_device_id, device_proof, caller_id, true
  );
  select * into invite_row from public.device_invites as invite
  where invite.id = invite_id and invite.device_id = target_device_id
  for update;
  if not found then
    raise exception 'invite not found' using errcode = '42501';
  end if;
  if invite_row.accepted_at is not null then
    raise exception 'accepted invite cannot be revoked' using errcode = '23514';
  end if;
  if invite_row.revoked_at is not null then
    return false;
  end if;
  update public.device_invites as invite set revoked_at = now()
  where invite.id = invite_id;
  return true;
end;
$$;

create or replace function public.device_accept_invite(
  target_device_id uuid,
  device_proof text,
  invite_token text
)
returns table (workspace_id uuid, member_count integer, already_accepted boolean)
language plpgsql
security definer
set search_path = ''
as $$
declare
  caller_id uuid := auth.uid();
  registered_owner uuid;
  invitation public.device_invites%rowtype;
  target public.workspaces%rowtype;
  current_count integer;
begin
  registered_owner := app_private.require_trusted_device(
    target_device_id, device_proof, caller_id, false
  );
  if invite_token is null or invite_token !~ '^[0-9a-f]{64}$' then
    raise exception 'valid invite required' using errcode = '22023';
  end if;

  -- Serialize all acceptance and seat-count changes for this device.
  perform 1 from public.devices as device
  where device.id = target_device_id for update;

  select * into invitation from public.device_invites as invite
  where invite.device_id = target_device_id
    and invite.token_digest = pg_catalog.sha256(pg_catalog.decode(invite_token, 'hex'))
  for update;
  if not found then
    raise exception 'valid invite required' using errcode = '42501';
  end if;

  if invitation.accepted_by = caller_id then
    if not exists (
      select 1 from public.device_members as member
      where member.device_id = target_device_id and member.user_id = caller_id
        and member.status = 'active'
    ) then
      raise exception 'accepted member is no longer active' using errcode = '23514';
    end if;
    select count(*)::integer into current_count from public.device_members as member
    where member.device_id = target_device_id and member.status = 'active';
    return query select invitation.target_workspace_id, current_count, true;
    return;
  end if;
  if invitation.accepted_at is not null or invitation.revoked_at is not null
      or invitation.expires_at <= now() then
    raise exception 'invite is no longer available' using errcode = '23514';
  end if;
  if caller_id = registered_owner or exists (
    select 1 from public.device_members as member
    where member.device_id = target_device_id and member.user_id = caller_id
      and member.status = 'active'
  ) then
    raise exception 'account already occupies a device seat' using errcode = '23514';
  end if;
  select count(*)::integer into current_count from public.device_members as member
  where member.device_id = target_device_id and member.status = 'active';
  if current_count >= 5 then
    raise exception 'device member limit reached' using errcode = '23514';
  end if;

  select * into target from public.workspaces as workspace
  where workspace.id = invitation.target_workspace_id for update;
  if not found or target.owner_id <> registered_owner
      or target.kind <> 'member_private'
      or target.provisioning_state <> 'reserved'
      or target.provisioned_device_id <> target_device_id
      or exists (
        select 1 from public.onboarding_states as state
        where state.workspace_id = invitation.target_workspace_id
      )
      or (select count(*) from public.workspace_members as member
          where member.workspace_id = invitation.target_workspace_id) <> 1 then
    raise exception 'reserved empty target workspace required' using errcode = '23514';
  end if;

  insert into public.device_members (device_id, user_id, role, status)
  values (target_device_id, caller_id, 'member', 'active');
  insert into public.workspace_members (workspace_id, user_id, role)
  values (target.id, caller_id, 'owner');
  update public.workspaces as workspace
  set owner_id = caller_id, provisioning_state = 'ready'
  where workspace.id = target.id;
  delete from public.workspace_members as member
  where member.workspace_id = target.id and member.user_id = registered_owner;
  update public.device_invites as invite
  set accepted_by = caller_id, accepted_at = now()
  where invite.id = invitation.id;
  update public.device_invites as invite
  set revoked_at = now()
  where invite.device_id = target_device_id
    and invite.target_workspace_id = target.id
    and invite.id <> invitation.id
    and invite.accepted_at is null and invite.revoked_at is null;
  return query select target.id, current_count + 1, false;
end;
$$;

-- Function grants are the only user-visible write surface. An ordinary JWT
-- still needs the pre-provisioned device proof, which each RPC checks itself.
revoke all on function public.device_create_member_space(uuid, text, text)
  from public, anon, authenticated;
revoke all on function public.device_create_invite(uuid, text, uuid, integer)
  from public, anon, authenticated;
revoke all on function public.device_revoke_invite(uuid, text, uuid)
  from public, anon, authenticated;
revoke all on function public.device_accept_invite(uuid, text, text)
  from public, anon, authenticated;
grant execute on function public.device_create_member_space(uuid, text, text)
  to authenticated;
grant execute on function public.device_create_invite(uuid, text, uuid, integer)
  to authenticated;
grant execute on function public.device_revoke_invite(uuid, text, uuid)
  to authenticated;
grant execute on function public.device_accept_invite(uuid, text, text)
  to authenticated;
