const assert = require('node:assert/strict');
const { EventEmitter, once } = require('node:events');
const fs = require('node:fs/promises');
const http = require('node:http');
const os = require('node:os');
const path = require('node:path');
const vm = require('node:vm');
const { createSectionCandidateConfirmation } = require('../runtime/section-candidate-confirmation');
const { registerDesktopIpc } = require('../runtime/ipc-handlers');
const { createServiceSupervisor } = require('../runtime/service-supervisor');

const taskId = 'task_isolated';
const proposalId = 'proposal_isolated';
const webOrigin = 'http://localhost:3000';
const readPath = `/api/creation-tasks/${taskId}/edits/${proposalId}/section-candidate-confirmation`;
const result = { operation: {
  id: 'operation_original', result_version_id: 'timeline_adopted', task_id: taskId,
  proposal_id: proposalId, base_version_id: 'timeline_before', status: 'applied',
} };

async function main() {
  const resourcesPath = await fs.mkdtemp(path.join(os.tmpdir(), 'section-confirmation-'));
  await fs.writeFile(path.join(resourcesPath, 'api-server'), '', { mode: 0o700 });
  const child = new EventEmitter();
  let apiControl;
  let postOutcome = 'success';
  let appliedRequestId;
  const posts = [];
  const log = [];
  const server = http.createServer(async (request, response) => {
    if (request.url === '/api/version') { response.end('{}'); return; }
    assert.equal(request.url, `/api/creation-tasks/${taskId}/edits/${proposalId}/confirm`);
    assert.equal(request.method, 'POST');
    assert.equal(request.headers['x-yaohe-desktop-control'], apiControl);
    for (const header of ['cookie', 'authorization', 'x-local-member-token', 'x-workspace-id']) {
      assert.equal(request.headers[header], undefined);
    }
    const bytes = [];
    for await (const chunk of request) bytes.push(chunk);
    const payload = JSON.parse(Buffer.concat(bytes));
    posts.push(payload);
    if (postOutcome !== 'dropped-before-commit') appliedRequestId = payload.request_id;
    if (postOutcome.startsWith('dropped')) request.socket.destroy();
    else response.end(JSON.stringify(result));
  });
  const services = createServiceSupervisor({
    resourcesPath, apiEnvironment: port => ({ TEST_PORT: String(port) }),
    spawnApi: (_binary, _args, { env }) => {
      apiControl = env.YAOHE_DESKTOP_CONTROL_TOKEN;
      server.listen(Number(env.TEST_PORT), '127.0.0.1');
      return child;
    },
    attachChildOutput() {}, recordLifecycle: (event, fields) => log.push({ event, fields }), onFailure() {},
  });
  const reads = [];
  const cookies = new EventEmitter();
  let overrides = {};
  const session = {
    cookies,
    async fetch(target, options) {
      assert.equal(this, session);
      const url = new URL(target);
      assert.equal(url.origin, webOrigin);
      assert.equal(url.pathname, readPath);
      assert.equal(options.method, 'GET');
      assert.equal(options.credentials, 'include');
      assert.equal(options.redirect, 'error');
      assert.equal(options.cache, 'no-store');
      assert.deepEqual(options.headers, { Accept: 'application/json' });
      assert.deepEqual([...url.searchParams.keys()], ['request_id']);
      const requestId = url.searchParams.get('request_id');
      assert.match(requestId, /^[0-9a-f-]{36}$/);
      reads.push(requestId);
      const binding = {
        proposal_id: proposalId, request_id: requestId, job_id: 'job_fixed', publication_id: 'publication_fixed',
        section_id: 'section_fixed', base_version_id: 'timeline_before',
        selection_digest: 'a'.repeat(64), session_fingerprint: 'b'.repeat(64), ...overrides,
      };
      return Response.json(requestId === appliedRequestId ? { ...binding, status: 'applied', result } : {
        ...binding, status: 'proposed', selection_id: `selection_${reads.length}`,
        expires_at: '2099-01-01T00:00:00Z', before_text: '原始段落', after_text: overrides.after_text ?? '候选段落',
        knowledge_evidence: [{ kind: 'knowledge', binding_status: 'fixed', object_id: '资料甲',
          version_or_identity: '文档固定版', locator: '第 3 页' }],
        impact: ['旧成片需要重新合成'],
      });
    },
  };
  function windowFixture() {
    const contents = Object.assign(new EventEmitter(), { session, isDestroyed: () => false,
      mainFrame: { origin: webOrigin, url: `${webOrigin}/projects/${taskId}`, processId: 1, routingId: 2,
        frameTreeNodeId: 3, isDestroyed: () => false, detached: false } });
    return { webContents: contents, isDestroyed: () => false };
  }
  let window = windowFixture();
  const eventFor = () => ({ sender: window.webContents, senderFrame: { ...window.webContents.mainFrame } });
  let act = () => ({ response: 1 });
  let dialogs = 0;
  const confirmation = createSectionCandidateConfirmation({
    getWindow: () => window, webOrigin, getApiEndpoint: services.getApiEndpoint,
    getTransport: services.sectionCandidateConfirmationTransport,
    async showMessageBox(parent, options) {
      assert.equal(parent, window);
      assert.deepEqual(options.buttons, ['取消', '采用']);
      assert.equal(options.defaultId, 0);
      assert.equal(options.cancelId, 0);
      assert.match(options.detail, /原始段落.*候选段落.*文档固定版.*第 3 页.*旧成片需要重新合成/s);
      assert.doesNotMatch(options.detail, /selection_[0-9]+/);
      dialogs++;
      return act();
    },
  });
  const handlers = new Map();
  registerDesktopIpc({
    ipcMain: { handle: (name, action) => handlers.set(name, action) },
    services, sectionCandidateConfirmation: confirmation,
  });
  const channel = 'confirm-section-candidate';
  const invoke = (event = eventFor(), ...args) => handlers.get(channel)(event, taskId, proposalId, ...args);
  let preload;
  vm.runInNewContext(await fs.readFile(path.join(__dirname, '../preload.js'), 'utf8'), {
    process: { platform: process.platform },
    require(specifier) {
      assert.equal(specifier, 'electron');
      return {
        contextBridge: { exposeInMainWorld: (_name, api) => { preload = api; } },
        ipcRenderer: { send() {}, invoke: (name, ...args) => handlers.get(name)(eventFor(), ...args) },
      };
    },
  });

  try {
    await services.startApiServer();
    const event = eventFor();
    assert.equal((await invoke({ ...event, senderFrame: { ...event.senderFrame, routingId: 9 } })).status, 'rejected');
    assert.equal((await invoke({ ...event, sender: windowFixture().webContents })).status, 'rejected');
    assert.equal((await invoke(event, { identity: 'renderer-forgery' })).status, 'rejected');
    assert.equal((await preload.confirmSectionCandidate(taskId, { proposalId, path: '/caller' })).status, 'rejected');
    assert.equal(reads.length, 0);
    assert.equal(dialogs, 0);

    act = () => ({ response: 0 });
    assert.deepEqual(await invoke(), { status: 'canceled' });
    assert.equal(reads.length, 1);
    act = () => {
      window.webContents.emit('did-start-navigation', { isMainFrame: true, isSameDocument: true });
      return { response: 1 };
    };
    assert.equal((await invoke()).status, 'rejected', 'in-page workbench navigation invalidates a pending dialog');
    act = () => {
      window.webContents.emit('did-start-navigation', { isMainFrame: true, isSameDocument: false });
      return { response: 1 };
    };
    assert.equal((await invoke()).status, 'rejected', 'same-origin document reload invalidates a pending dialog');
    const oldWindow = window;
    act = () => {
      window = windowFixture();
      return { response: 1 };
    };
    assert.equal((await invoke()).status, 'rejected', 'switching the main window invalidates the original');
    assert.equal(oldWindow.webContents.listenerCount('did-start-navigation'), 0);
    act = () => {
      cookies.emit('changed', {}, { domain: 'localhost', name: 'ai-video-diary-local-member-token-port-3000' });
      return { response: 1 };
    };
    assert.equal((await invoke()).status, 'rejected', 'switching local sessions blocks a pending choice');
    for (const changed of [
      { session_fingerprint: 'c'.repeat(64) }, { selection_digest: 'f'.repeat(64) },
      { base_version_id: 'timeline_other' }, { after_text: '未在确认时显示的候选' },
    ]) {
      overrides = {};
      act = () => { overrides = changed; return { response: 1 }; };
      assert.equal((await invoke()).status, 'rejected');
    }
    assert.equal(posts.length, 0, 'cancel and all invalidation cases have no write');
    assert.equal(cookies.listenerCount('changed'), 0);
    assert.equal(window.webContents.listenerCount('did-start-navigation'), 0);

    overrides = {};
    act = () => ({ response: 1 });
    postOutcome = 'dropped-before-commit';
    const uncertain = await preload.confirmSectionCandidate(taskId, proposalId);
    assert.equal(uncertain.status, 'unknown');
    assert.equal(posts.length, 1);
    assert.deepEqual(reads.slice(-3), Array(3).fill(uncertain.requestId), 'read back the original immediately on transport loss');
    const beforeQuery = dialogs;
    assert.deepEqual(await preload.querySectionCandidateConfirmation(taskId, proposalId), { status: 'proposed' });
    assert.equal(dialogs, beforeQuery, 'query is read-only and cannot open an adoption dialog');
    assert.equal(posts.length, 1);
    assert.equal(reads.at(-1), uncertain.requestId);
    overrides = { session_fingerprint: 'e'.repeat(64) };
    assert.equal((await preload.confirmSectionCandidate(taskId, proposalId)).status, 'rejected');
    assert.equal(dialogs, beforeQuery);
    assert.equal(posts.length, 1);

    overrides = {};
    postOutcome = 'dropped-after-commit';
    const receipt = await preload.confirmSectionCandidate(taskId, proposalId);
    assert.deepEqual(receipt, {
      status: 'applied', requestId: uncertain.requestId, operationId: 'operation_original', resultVersionId: 'timeline_adopted',
    });
    assert.deepEqual(posts[1], {
      request_id: uncertain.requestId, expected_base_version_id: 'timeline_before',
      expected_input_digest: 'a'.repeat(64), desktop_selection_id: `selection_${reads.length - 1}`,
    });
    assert.equal(posts[0].request_id, posts[1].request_id, 'explicit retry reuses the same request');
    assert.notEqual(posts[0].desktop_selection_id, posts[1].desktop_selection_id);
    const dialogCount = dialogs;
    assert.deepEqual(await preload.confirmSectionCandidate(taskId, proposalId), receipt);
    assert.equal(dialogs, dialogCount);
    assert.equal(posts.length, 2);
    assert.doesNotMatch(JSON.stringify(receipt), /desktop_selection_id|selection_digest|session_fingerprint/);
    assert.equal(JSON.stringify([receipt, log, services.getServiceStatus()]).includes(apiControl), false);
  } finally {
    if (server.listening) {
      server.closeAllConnections();
      server.close();
      await once(server, 'close');
    }
    await fs.rm(resourcesPath, { recursive: true, force: true });
  }
  console.log('native section confirmation: PASS (real private loopback, guarded mocked window and session, original receipt recovery)');
}

main().catch(error => { console.error(error); process.exitCode = 1; });
