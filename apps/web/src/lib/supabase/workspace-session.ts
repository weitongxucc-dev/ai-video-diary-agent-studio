import type { SupabaseClient } from '@supabase/supabase-js';

/** One server-side projection shared by middleware and desktop UI startup. */
export async function readVerifiedWorkspaceSession(supabase: SupabaseClient) {
  const { data, error } = await supabase.auth.getClaims();
  if (error || !data?.claims?.sub) {
    return { userId: null, accessToken: null, workspaceId: null, onboardingComplete: false, onboardingReadFailed: false };
  }
  const { data: sessionData } = await supabase.auth.getSession();
  const { data: onboardingState, error: onboardingError } = await supabase
    .from('onboarding_states')
    .select('workspace_id, completed_at, workspaces!inner(owner_id)')
    .eq('user_id', data.claims.sub)
    .eq('workspaces.owner_id', data.claims.sub)
    .maybeSingle();
  return {
    userId: data.claims.sub,
    accessToken: sessionData.session?.access_token ?? null,
    workspaceId: typeof onboardingState?.workspace_id === 'string' ? onboardingState.workspace_id : null,
    onboardingComplete: !onboardingError && Boolean(onboardingState?.completed_at),
    onboardingReadFailed: Boolean(onboardingError),
  };
}
