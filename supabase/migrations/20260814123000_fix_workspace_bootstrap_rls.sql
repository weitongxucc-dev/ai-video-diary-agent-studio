-- The bootstrap RPC inserts a workspace with SECURITY INVOKER and RETURNING.
-- During that RETURNING check, the AFTER INSERT membership trigger is not yet
-- visible to the SELECT policy. The authenticated owner is already derived
-- from auth.uid(), so allow the owner to select their own row directly while
-- retaining membership-based access for future collaborators.

alter policy "workspaces_select_membership"
on public.workspaces
using (
  (select auth.uid()) = owner_id
  or exists (
      select 1
      from public.workspace_members member
      where member.workspace_id = workspaces.id
        and member.user_id = (select auth.uid())
    )
);
