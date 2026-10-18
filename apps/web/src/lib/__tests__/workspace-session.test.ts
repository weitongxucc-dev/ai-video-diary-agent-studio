import type { SupabaseClient } from '@supabase/supabase-js';
import { beforeEach, expect, it, vi } from 'vitest';
const mocks = vi.hoisted(() => ({ createClient: vi.fn() }));
vi.mock('@/lib/supabase/server', () => ({ createClient: mocks.createClient }));
import { readVerifiedWorkspaceSession } from '../supabase/workspace-session';
import { readWorkspaceSession } from '@/app/auth/workspace-session';

function fixture({ userId = 'user-a' as string | null, token = 'server-only-token' as string | null, completed = true, readError = false } = {}) {
  const query = { select: vi.fn(), eq: vi.fn(), maybeSingle: vi.fn(async () => ({ data: { workspace_id: 'workspace-a', completed_at: completed ? '2026-10-18' : null }, error: readError ? { message: 'private database diagnostic' } : null })) };
  query.select.mockReturnValue(query); query.eq.mockReturnValue(query);
  const client = { auth: { getClaims: vi.fn(async () => ({ data: { claims: userId ? { sub: userId } : null }, error: null })), getSession: vi.fn(async () => ({ data: { session: token ? { access_token: token } : null } })) }, from: vi.fn(() => query) };
  return { client: client as unknown as SupabaseClient, query, from: client.from };
}
beforeEach(() => { vi.clearAllMocks(); });
it('uses the same verified subject and owner-filtered onboarding projection as middleware', async () => {
  const current = fixture(); const value = await readVerifiedWorkspaceSession(current.client);
  expect(value).toMatchObject({ userId: 'user-a', workspaceId: 'workspace-a', onboardingComplete: true });
  expect(current.from).toHaveBeenCalledWith('onboarding_states'); expect(current.query.eq.mock.calls).toEqual([['user_id', 'user-a'], ['workspaces.owner_id', 'user-a']]);
});
it('returns a scoped ready snapshot without exposing credentials to the client', async () => {
  mocks.createClient.mockResolvedValue(fixture().client);
  const snapshot = await readWorkspaceSession(); expect(snapshot).toEqual({ status: 'ready', userId: 'user-a', workspaceId: 'workspace-a' }); expect(JSON.stringify(snapshot)).not.toContain('server-only-token');
});
it('does not enable workspace business before onboarding completion', async () => {
  mocks.createClient.mockResolvedValue(fixture({ completed: false }).client); expect(await readWorkspaceSession()).toEqual({ status: 'onboarding', userId: 'user-a' });
});
it('does not accept an unverified subject or a missing server session token', async () => {
  const anonymous = fixture({ userId: null }); mocks.createClient.mockResolvedValueOnce(anonymous.client); expect(await readWorkspaceSession()).toEqual({ status: 'signed_out' }); expect(anonymous.from).not.toHaveBeenCalled();
  mocks.createClient.mockResolvedValueOnce(fixture({ token: null }).client); expect(await readWorkspaceSession()).toEqual({ status: 'signed_out' });
});
it('keeps failed readiness reads unavailable without returning raw diagnostics', async () => {
  mocks.createClient.mockResolvedValue(fixture({ readError: true }).client); expect(await readWorkspaceSession()).toEqual({ status: 'unavailable' });
  mocks.createClient.mockRejectedValue(new Error('private configuration detail')); expect(await readWorkspaceSession()).toEqual({ status: 'unavailable' });
});
