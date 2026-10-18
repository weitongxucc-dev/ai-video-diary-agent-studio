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

it.each(['JSON', 'stream', 'SSE error'])('preserves the typed task-version conflict from the %s response', async mode => {
  const message = '当前任务版本已变化，请重新查看选中对象后发送。';
  const fetchMock = vi.fn(async () => mode === 'SSE error' ? new Response(`data: ${JSON.stringify({
    type: 'error', status: 409, failure: {code: 'creation_task_timeline_version_conflict'}, message,
  })}\n\n`, {headers: {'Content-Type': 'text/event-stream'}}) : Response.json({detail: {
    error: 'creation_task_timeline_version_conflict', message,
  }}, {status: 409}));
  vi.stubGlobal('fetch', fetchMock);
  const {creationPlanRequest, assistantStreamRequest, CreationPlanApiError} = await import('../api/creation-transport');
  const failure = await (mode === 'JSON' ? creationPlanRequest('/api/task-conflict')
    : assistantStreamRequest('/api/task-conflict', {}, vi.fn())).catch(error => error);
  expect(failure).toBeInstanceOf(CreationPlanApiError);
  expect(failure).toMatchObject({status: 409, code: 'creation_task_timeline_version_conflict', message});
  expect(fetchMock).toHaveBeenCalledOnce();
});

it.each([null, 'another_conflict'])('leaves an ordinary SSE error unchanged for failure code %s', async code => {
  const fetchMock = vi.fn(async () => new Response(`data: ${JSON.stringify({
    type: 'error', status: 409, message: '原错误提示', ...(code ? {failure: {code}} : {}),
  })}\n\n`));
  vi.stubGlobal('fetch', fetchMock);
  const {assistantStreamRequest} = await import('../api/creation-transport');
  await expect(assistantStreamRequest('/api/task-conflict', {}, vi.fn())).rejects.toMatchObject({
    status: 409, code: null, message: '原错误提示',
  });
  expect(fetchMock).toHaveBeenCalledOnce();
});

it.each(['JSON', 'stream'])('leaves a non-target %s HTTP error unchanged', async mode => {
  const fetchMock = vi.fn(async () => Response.json({detail: {
    error: 'another_conflict', message: '不属于本次适配的提示',
  }}, {status: 409}));
  vi.stubGlobal('fetch', fetchMock);
  const {creationPlanRequest, assistantStreamRequest} = await import('../api/creation-transport');
  await expect(mode === 'JSON' ? creationPlanRequest('/api/task-conflict')
    : assistantStreamRequest('/api/task-conflict', {}, vi.fn())).rejects.toMatchObject({
    status: 409, code: 'another_conflict', message: '请求失败：409',
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
