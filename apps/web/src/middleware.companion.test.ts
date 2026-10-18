import { NextRequest } from 'next/server';
import { afterEach, expect, it, vi } from 'vitest';
const mocks = vi.hoisted(() => ({ refreshSession: vi.fn() }));
vi.mock('@/lib/supabase/middleware', () => ({ refreshSession: mocks.refreshSession, copyRefreshedSession: (response: Response) => response, redirectWithRefreshedCookies: vi.fn() }));
import { middleware } from './middleware';
afterEach(() => { vi.unstubAllEnvs(); vi.unstubAllGlobals(); });
it('companion proxy preserves explicit workspace while replacing renderer JWT with verified cookie JWT', async () => {
  vi.stubEnv('NEXT_PUBLIC_AUTH_MODE', 'supabase');
  mocks.refreshSession.mockResolvedValue({ userId: 'member-a', accessToken: 'verified-jwt', onboardingComplete: true, response: null });
  const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => Response.json({ verified: true })); vi.stubGlobal('fetch', fetchMock);
  await middleware(new NextRequest('http://localhost:3000/api/companion/session', { headers: { Authorization: 'Bearer renderer-forgery', 'X-Workspace-Id': 'workspace-a' } }));
  const headers = fetchMock.mock.calls[0][1]!.headers as Headers;
  expect(headers.get('Authorization')).toBe('Bearer verified-jwt'); expect(headers.get('X-Workspace-Id')).toBe('workspace-a'); expect(headers.has('X-Local-Member-Token')).toBe(false);
});
