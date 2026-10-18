import { act, cleanup, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { requestLocalMemberIdentityRefresh, useLocalMemberIdentity } from '../useLocalMemberIdentity';
import { LOCAL_MEMBER_TOKEN_STORAGE_KEY } from '@/lib/local-member-auth';

beforeEach(() => { window.localStorage.clear(); });
afterEach(() => { cleanup(); vi.unstubAllEnvs(); vi.unstubAllGlobals(); vi.restoreAllMocks(); window.localStorage.clear(); });

for (const mode of ['supabase', undefined]) {
  it(`mode ${String(mode)} never requests or writes a local identity on mount or refresh`, async () => {
    vi.stubEnv('NEXT_PUBLIC_AUTH_MODE', mode);
    window.localStorage.setItem(LOCAL_MEMBER_TOKEN_STORAGE_KEY, 'retained-local-credential');
    const write = vi.spyOn(Storage.prototype, 'setItem');
    const fetchMock = vi.fn(async () => Response.json({ member_id: 'local-owner', role: 'owner', member_token: 'must-not-save' }));
    vi.stubGlobal('fetch', fetchMock);
    const { result } = renderHook(() => useLocalMemberIdentity());
    expect(result.current).toMatchObject({ token: null, role: null, ready: true, error: null });
    await act(async () => { result.current.refresh(); requestLocalMemberIdentityRefresh(); });
    expect(fetchMock).not.toHaveBeenCalled();
    expect(write).not.toHaveBeenCalled();
    expect(window.localStorage.getItem(LOCAL_MEMBER_TOKEN_STORAGE_KEY)).toBe('retained-local-credential');
    expect(result.current.token).toBeNull(); expect(result.current.role).toBeNull();
  });
}

it('local mode retains whoami, saved credentials, and manual and shared refresh', async () => {
  vi.stubEnv('NEXT_PUBLIC_AUTH_MODE', 'local');
  let token = 'owner-token', role = 'owner';
  const fetchMock = vi.fn(async () => Response.json({ member_id: 'member-a', name: 'local member', role, member_token: token, created_at: '2026-10-18' }));
  vi.stubGlobal('fetch', fetchMock);
  const { result } = renderHook(() => useLocalMemberIdentity());
  await waitFor(() => expect(result.current.ready).toBe(true));
  expect(fetchMock).toHaveBeenCalledWith('/api/members/whoami', expect.anything());
  expect(result.current.token).toBe('owner-token'); expect(result.current.role).toBe('owner');
  expect(window.localStorage.getItem(LOCAL_MEMBER_TOKEN_STORAGE_KEY)).toBe('owner-token');
  role = 'member'; token = 'member-token';
  await act(async () => { result.current.refresh(); });
  await waitFor(() => expect(result.current.role).toBe('member'));
  expect(result.current.token).toBe('member-token');
  token = 'refreshed-token';
  await act(async () => { requestLocalMemberIdentityRefresh(); });
  await waitFor(() => expect(result.current.token).toBe('refreshed-token'));
  expect(window.localStorage.getItem(LOCAL_MEMBER_TOKEN_STORAGE_KEY)).toBe('refreshed-token');
  expect(fetchMock).toHaveBeenCalledTimes(3);
});

it('failed local lookup does not invent an owner or replace the existing credential', async () => {
  vi.stubEnv('NEXT_PUBLIC_AUTH_MODE', 'local');
  window.localStorage.setItem(LOCAL_MEMBER_TOKEN_STORAGE_KEY, 'invalid-token');
  vi.stubGlobal('fetch', vi.fn(async () => Response.json({ detail: '成员凭据无效' }, { status: 401 })));
  const { result } = renderHook(() => useLocalMemberIdentity());
  await waitFor(() => expect(result.current.ready).toBe(true));
  expect(result.current).toMatchObject({ token: null, role: null, error: '成员凭据无效' });
  expect(window.localStorage.getItem(LOCAL_MEMBER_TOKEN_STORAGE_KEY)).toBe('invalid-token');
});
