import { afterEach, beforeEach, expect, it, vi } from 'vitest';
const mocks = vi.hoisted(() => ({ local: false, session: vi.fn() }));
vi.mock('../auth/routing', () => ({ isLocalAuthMode: () => mocks.local }));
vi.mock('../supabase/client', () => ({ createClient: () => ({ auth: { getSession: mocks.session } }) }));
import { CompanionError, createCompanionPairing, readCompanionContext } from '../api/companion';
const identity = { device_id: 'device-a', workspace_id: 'workspace-a', member_id: 'member-a', role: 'owner' as const, name: null };
let fetchMock: ReturnType<typeof vi.fn>;
beforeEach(() => {
  vi.clearAllMocks(); mocks.local = false;
  mocks.session.mockResolvedValue({ data: { session: { access_token: 'formal-access', user: { id: 'member-a' } } }, error: null });
  fetchMock = vi.fn(async (path: string) => Response.json(path.endsWith('/foundation') ? { profile: { workspace_id: 'workspace-a' } } : identity)); vi.stubGlobal('fetch', fetchMock);
  window.localStorage.setItem('ai-video-diary-local-member-token', 'stale-local-token');
});
afterEach(() => { vi.unstubAllGlobals(); window.localStorage.clear(); });
it('selects the verified current workspace and carries only formal bearer identity', async () => {
  const context = await readCompanionContext();
  expect(context).toEqual({ ...identity, accessToken: 'formal-access' });
  expect(fetchMock.mock.calls.map(call => call[0])).toEqual(['/api/production/foundation', '/api/companion/session']);
  expect(fetchMock.mock.calls[1][1]).toMatchObject({ headers: { Authorization: 'Bearer formal-access', 'X-Workspace-Id': 'workspace-a' }, redirect: 'error', credentials: 'same-origin', cache: 'no-store' });
  expect(fetchMock.mock.calls[1][1].headers).not.toHaveProperty('X-Local-Member-Token');
});
it('rejects changed login identity and never adopts another member', async () => {
  mocks.session.mockResolvedValueOnce({ data: { session: { access_token: 'formal-access', user: { id: 'member-a' } } }, error: null });
  mocks.session.mockResolvedValueOnce({ data: { session: { access_token: 'other', user: { id: 'member-b' } } }, error: null });
  await expect(readCompanionContext()).rejects.toThrow('登录账号已变化');
});
it('reads device role from the host and rejects a wrong selected workspace', async () => {
  fetchMock.mockImplementation(async (path: string) => Response.json(path.endsWith('/foundation') ? { profile: { workspace_id: 'workspace-a' } } : { ...identity, workspace_id: 'other' }));
  await expect(readCompanionContext()).rejects.toThrow('账号或工作区不一致');
});
it('local mode never contacts the account host', async () => {
  mocks.local = true; await expect(readCompanionContext()).rejects.toBeInstanceOf(CompanionError); expect(fetchMock).not.toHaveBeenCalled(); expect(mocks.session).not.toHaveBeenCalled();
});
it('pair ticket is bound to the same verified workspace and device', async () => {
  const context = { ...identity, accessToken: 'formal-access' };
  fetchMock.mockResolvedValueOnce(Response.json({ token: 'short-ticket', expires_at: new Date(Date.now() + 600_000).toISOString(), workspace_id: 'other', device_id: 'device-a' }));
  await expect(createCompanionPairing(context)).rejects.toThrow('配对回执');
  expect(fetchMock.mock.calls[0][1]).toMatchObject({ method: 'POST', headers: { Authorization: 'Bearer formal-access', 'X-Workspace-Id': 'workspace-a' } });
});
