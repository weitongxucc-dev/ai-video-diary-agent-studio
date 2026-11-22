import { NextRequest, NextResponse } from 'next/server';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({ refreshSession: vi.fn(), copyRefreshedSession: vi.fn() }));
vi.mock('@/lib/supabase/middleware', () => ({
  refreshSession: mocks.refreshSession,
  copyRefreshedSession: mocks.copyRefreshedSession,
  redirectWithRefreshedCookies: vi.fn(),
}));
import { middleware } from './middleware';

let fetchMock: ReturnType<typeof vi.fn<typeof fetch>>;
let refreshed: NextResponse;
beforeEach(() => {
  vi.clearAllMocks(); vi.stubEnv('NEXT_PUBLIC_AUTH_MODE', 'supabase');
  refreshed = NextResponse.next();
  mocks.copyRefreshedSession.mockImplementation((response: NextResponse) => response);
  mocks.refreshSession.mockResolvedValue({ userId: 'verified-user', accessToken: 'verified-token', onboardingComplete: false, response: refreshed });
  fetchMock = vi.fn<typeof fetch>().mockResolvedValue(Response.json({ status: 'workspace-repair' }));
  vi.stubGlobal('fetch', fetchMock);
});
afterEach(() => { vi.unstubAllEnvs(); vi.unstubAllGlobals(); });

for (const onboardingComplete of [false, true]) {
  it(`forwards workspace diagnostics only after session verification; onboarding=${onboardingComplete}`, async () => {
    mocks.refreshSession.mockResolvedValue({ userId: 'verified-user', accessToken: 'verified-token', onboardingComplete, response: refreshed });
    const request = new NextRequest('http://localhost:3000/api/runtime/health', { headers: { Authorization: 'Bearer renderer-forgery', 'X-Workspace-Id': 'selected-workspace' } });
    const response = await middleware(request);
    expect(response.status).toBe(200);
    expect(mocks.refreshSession).toHaveBeenCalledWith(request);
    expect(fetchMock).toHaveBeenCalledOnce();
    const headers = new Headers(fetchMock.mock.calls[0][1]?.headers);
    expect(headers.get('Authorization')).toBe('Bearer verified-token');
    expect(headers.get('X-Workspace-Id')).toBe('selected-workspace');
    expect(mocks.copyRefreshedSession).toHaveBeenCalledWith(expect.anything(), refreshed);
  });
}

for (const invalid of [
  { userId: null, accessToken: null },
  { userId: 'verified-user', accessToken: null },
  { userId: null, accessToken: 'unverified-token' },
]) {
  it(`rejects an unverified diagnostic session without proxying ${JSON.stringify(invalid)}`, async () => {
    mocks.refreshSession.mockResolvedValue({ ...invalid, onboardingComplete: false, response: refreshed });
    const response = await middleware(new NextRequest('http://localhost:3000/api/runtime/health', { headers: { Authorization: 'Bearer renderer-forgery' } }));
    expect(response.status).toBe(401);
    expect(await response.json()).toMatchObject({ error: 'authentication_required' });
    expect(fetchMock).not.toHaveBeenCalled();
    expect(mocks.copyRefreshedSession).toHaveBeenCalledWith(response, refreshed);
  });
}

it('keeps the existing version probe anonymous and leaves product onboarding gates intact', async () => {
  const version = await middleware(new NextRequest('http://localhost:3000/api/version'));
  expect(version.status).toBe(200);
  expect(mocks.refreshSession).not.toHaveBeenCalled();
  expect(new Headers(fetchMock.mock.calls[0][1]?.headers).has('Authorization')).toBe(false);
  fetchMock.mockClear();
  const blocked = await middleware(new NextRequest('http://localhost:3000/api/creation-tasks'));
  expect(blocked.status).toBe(403); expect(await blocked.json()).toMatchObject({ error: 'onboarding_required' });
  expect(fetchMock).not.toHaveBeenCalled();
});

it('retains local diagnostic access without starting the formal login flow', async () => {
  vi.stubEnv('NEXT_PUBLIC_AUTH_MODE', 'local');
  const response = await middleware(new NextRequest('http://localhost:3000/api/runtime/health'));
  expect(response.status).toBe(200); expect(fetchMock).toHaveBeenCalledOnce(); expect(mocks.refreshSession).not.toHaveBeenCalled();
});

it('forwards a JSON body through the middleware proxy without losing the API response', async () => {
  vi.stubEnv('NEXT_PUBLIC_AUTH_MODE', 'local');
  const body = { scope_kind: 'home', scope_id: 'home' };
  fetchMock.mockResolvedValueOnce(Response.json({ accepted: true }, { status: 201 }));

  const response = await middleware(new NextRequest('http://localhost:3000/api/assistant/conversations/current', {
    method: 'POST',
    headers: { 'content-type': 'application/json', 'content-length': '999' },
    body: JSON.stringify(body),
  }));

  expect(response.status).toBe(201);
  expect(await response.json()).toEqual({ accepted: true });
  const [, init] = fetchMock.mock.calls[0];
  expect(init?.method).toBe('POST');
  expect(new Headers(init?.headers).has('content-length')).toBe(false);
  expect(new TextDecoder().decode(init?.body as ArrayBuffer)).toBe(JSON.stringify(body));
});

it('keeps a non-2xx API response visible instead of converting it to a proxy failure', async () => {
  vi.stubEnv('NEXT_PUBLIC_AUTH_MODE', 'local');
  fetchMock.mockResolvedValueOnce(Response.json({ error: 'assistant_draft_conflict' }, { status: 409 }));

  const response = await middleware(new NextRequest('http://localhost:3000/api/assistant/conversations/current', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ scope_kind: 'home', scope_id: 'home' }),
  }));

  expect(response.status).toBe(409);
  expect(await response.json()).toEqual({ error: 'assistant_draft_conflict' });
});
