import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { LOCAL_MEMBER_TOKEN_HEADER, LOCAL_MEMBER_TOKEN_STORAGE_KEY } from '../local-member-auth';

beforeEach(() => {
  vi.resetModules();
  vi.unstubAllEnvs();
  window.localStorage.setItem(LOCAL_MEMBER_TOKEN_STORAGE_KEY, 'synthetic-member');
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  window.localStorage.clear();
});

it('JSON, stream and FormData use one identity boundary without corrupting body headers', async () => {
  const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => {
    if (init?.headers && (init.headers as Record<string, string>).Accept === 'text/event-stream') {
      return new Response('data: {"type":"result","result":{"reply":"fixture"}}\n\n');
    }
    return Response.json({ transcript: '实际测试文字', fixture: true });
  });
  vi.stubGlobal('fetch', fetchMock);
  const { request, setServerAuthorizationProvider } = await import('../api/transport');
  const provider = vi.fn(async () => 'Bearer server-fixture');
  setServerAuthorizationProvider(provider);
  const { creationPlanRequest, assistantStreamRequest } = await import('../api/creation-transport');
  const { transcribeMaterialVoiceNote } = await import('../api/materials');
  const signal = new AbortController().signal;
  await request('/api/test-json');
  await creationPlanRequest('/api/test-creation');
  expect(await assistantStreamRequest('/api/test-stream', {}, vi.fn())).toEqual({ reply: 'fixture' });
  expect(await transcribeMaterialVoiceNote(new Blob(['synthetic'], { type: 'audio/wav' }), signal)).toBe('实际测试文字');
  expect(provider).not.toHaveBeenCalled();
  expect(fetchMock.mock.calls.map(call => call[0])).toEqual([
    '/api/test-json', '/api/test-creation', '/api/test-stream', '/api/voice/transcribe',
  ]);
  for (const [, init] of fetchMock.mock.calls) {
    expect(init).toMatchObject({ cache: 'no-store', headers: { [LOCAL_MEMBER_TOKEN_HEADER]: 'synthetic-member' } });
  }
  const upload = fetchMock.mock.calls[3][1]!;
  expect(upload.body).toBeInstanceOf(FormData);
  expect(upload.signal).toBe(signal);
  expect(upload.headers).not.toHaveProperty('Content-Type');
});

it('Blob media reads retain the selected source and trusted member identity', async () => {
  const fetchMock = vi.fn(async () => new Response('synthetic image'));
  vi.stubGlobal('fetch', fetchMock);
  const { readCreationTaskGraphicSource } = await import('../api/task-media');
  const signal = new AbortController().signal;
  await readCreationTaskGraphicSource('task/one', 'artifact/two', signal);
  expect(fetchMock).toHaveBeenCalledWith('/api/creation-tasks/task%2Fone/graphics/source/artifact%2Ftwo', {
    signal, cache: 'no-store', headers: { [LOCAL_MEMBER_TOKEN_HEADER]: 'synthetic-member' },
  });
});

it('server requests use only the existing verified provider and preserve caller cancellation', async () => {
  vi.stubGlobal('window', undefined);
  vi.stubEnv('API_PORT', '9319');
  const fetchMock = vi.fn(async () => Response.json({}));
  vi.stubGlobal('fetch', fetchMock);
  const { request, setServerAuthorizationProvider } = await import('../api/transport');
  const provider = vi.fn(async () => 'Bearer verified-server-fixture');
  setServerAuthorizationProvider(provider);
  const signal = new AbortController().signal;
  await request('/api/test-server', { signal, headers: { Authorization: 'untrusted-override' } });
  expect(provider).toHaveBeenCalledOnce();
  expect(fetchMock).toHaveBeenCalledWith('http://127.0.0.1:9319/api/test-server', expect.objectContaining({
    signal, headers: { 'Content-Type': 'application/json', Authorization: 'Bearer verified-server-fixture' },
  }));
});
