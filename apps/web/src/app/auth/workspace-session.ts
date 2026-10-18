'use server';

import { createClient } from '@/lib/supabase/server';
import { readVerifiedWorkspaceSession } from '@/lib/supabase/workspace-session';

export type WorkspaceSessionSnapshot =
  | { status: 'ready'; userId: string; workspaceId: string }
  | { status: 'onboarding'; userId: string }
  | { status: 'signed_out' | 'unavailable' };

/** No client-supplied identity and no JWT in the returned UI projection. */
export async function readWorkspaceSession(): Promise<WorkspaceSessionSnapshot> {
  try {
    const session = await readVerifiedWorkspaceSession(await createClient());
    if (!session.userId || !session.accessToken) return { status: 'signed_out' };
    if (session.onboardingReadFailed) return { status: 'unavailable' };
    if (!session.onboardingComplete) return { status: 'onboarding', userId: session.userId };
    if (!session.workspaceId) return { status: 'unavailable' };
    return { status: 'ready', userId: session.userId, workspaceId: session.workspaceId };
  } catch {
    return { status: 'unavailable' };
  }
}
