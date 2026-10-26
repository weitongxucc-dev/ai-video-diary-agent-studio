// One focused save boundary: fake Electron session/window/dialog, own loopback and files.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const os = require('node:os');
const path = require('node:path');
const vm = require('node:vm');
const { EventEmitter } = require('node:events');
const { downloadCreationTaskOutput, downloadSelectedSubtitles } = require('../runtime/media-downloads');
const { createNativeMedia } = require('../runtime/native-media');
const { registerDesktopIpc } = require('../runtime/ipc-handlers');

async function main() {
  let response = { status: 200, type: 'video/mp4', body: 'fixture-video' };
  let behaviour = 'respond';
  let outputHead = null;
  const requests = [];
  const sockets = new Set();
  const server = http.createServer((request, reply) => {
    requests.push({ url: request.url, accept: request.headers.accept });
    const url = new URL(request.url, 'http://fixture');
    if (outputHead && url.pathname === '/api/creation-tasks/task_export/output'
        && url.searchParams.get('expected_version_id') !== outputHead) {
      reply.writeHead(409, { 'content-type': 'application/json' });
      reply.end(JSON.stringify({ detail: '作品版本已变化，请刷新当前任务后重新下载。' }));
      return;
    }
    if (behaviour === 'hang-before-response') return;
    reply.writeHead(response.status, { 'content-type': response.type });
    if (behaviour === 'hang-after-headers') { reply.flushHeaders(); return; }
    reply.end(response.body);
  });
  server.on('connection', (socket) => {
    sockets.add(socket); socket.on('close', () => sockets.delete(socket));
  });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'native-save-auth-'));
  const webOrigin = `http://127.0.0.1:${server.address().port}`;
  const calls = [];
  const session = {
    async fetch(url, init) {
      assert.equal(this, session, 'fetch uses the captured window session receiver');
      assert.equal(new URL(url).origin, webOrigin);
      assert.equal(init.method, 'GET');
      assert.equal(init.credentials, 'include');
      assert.equal(init.redirect, 'error');
      assert.equal(init.cache, 'no-store');
      assert.deepEqual(Object.keys(init.headers), ['Accept']);
      calls.push({ url, accept: init.headers.Accept });
      // Explicit substitute: Node fetch has no Electron cookie jar.
      return fetch(url, init);
    },
  };
  function windowFixture() {
    const window = { destroyed: false, isDestroyed() { return this.destroyed; } };
    const contents = new EventEmitter();
    contents.isDestroyed = () => window.destroyed;
    contents.session = session;
    contents.mainFrame = {
      processId: 1, routingId: 2, frameTreeNodeId: 3, detached: false,
      url: `${webOrigin}/tasks/task_export`, origin: webOrigin,
      isDestroyed: () => window.destroyed,
    };
    window.webContents = contents;
    return window;
  }
  let current = windowFixture();
  const eventFor = (window = current) => ({
    sender: window.webContents,
    // A distinct JS wrapper with the same native frame identity is legitimate.
    senderFrame: { ...window.webContents.mainFrame },
  });
  let dialogAction = async () => ({ canceled: true });
  let dialogCalls = 0;
  let beforeFetch = null;
  let afterDownload = null;
  let shortTimeout = null;
  let apiState = 'ready';
  const media = createNativeMedia({
    getWindow: () => current, webOrigin,
    showSaveDialog: async () => { dialogCalls += 1; return dialogAction(); },
    getMediaDirectory: () => root,
    // Deliberately not the loopback server port: saves must use the Web origin.
    getApiEndpoint: () => ({ port: 1, state: apiState }), recordLifecycle: () => {},
    downloadCreationTaskOutput: async (...args) => {
      if (beforeFetch) beforeFetch();
      await downloadCreationTaskOutput(...args);
      if (afterDownload) afterDownload();
    },
    downloadSelectedSubtitles: async (context, selection, temporaryPath, timeoutMs) => {
      assert.equal(timeoutMs, 30000, 'native entry preserves the production timeout');
      await downloadSelectedSubtitles(context, selection, temporaryPath, shortTimeout ?? timeoutMs);
    },
  });
  const handlers = new Map();
  registerDesktopIpc({ ipcMain: { handle: (name, callback) => handlers.set(name, callback) }, media });
  let preloadApi;
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../preload.js'), 'utf8'), {
    process: { platform: process.platform },
    require(name) {
      assert.equal(name, 'electron');
      return {
        contextBridge: { exposeInMainWorld: (_name, api) => { preloadApi = api; } },
        ipcRenderer: { send() {}, invoke: (channel, ...args) => handlers.get(channel)(eventFor(), ...args) },
      };
    },
  });
  const selection = { taskId: 'task_export', expectedVersionId: 'version_2', mode: 'zh-en', zhTrackId: 'subtitle_zh', enTrackId: 'subtitle_en',
    url: 'https://untrusted.invalid/file', authorization: 'renderer-forgery' };
  const noPartials = () => assert.equal(fs.readdirSync(root).filter((name) => name.includes('.partial-')).length, 0);
  async function invoke(channel, args, file, action) {
    dialogAction = action || (async () => ({ canceled: false, filePath: path.join(root, file) }));
    const contents = current.webContents;
    const before = contents.listenerCount('did-start-navigation');
    try { return await (channel === 'save-rendered-video'
      ? preloadApi.saveRenderedVideo(...args) : handlers.get(channel)(eventFor(), ...args)); }
    finally { assert.equal(contents.listenerCount('did-start-navigation'), before, 'per-save listener is released'); }
  }
  try {
    const formats = [
      ['save-rendered-video', ['task_export', 'version_2'], 'video.mp4', 'video/mp4', 'fixture-video'],
      ['save-cover-image', ['task_export', 'cover_fixture'], 'cover.png', 'image/png', 'fixture-image'],
      ['save-subtitle-file', [selection], 'subtitles.srt', 'application/x-subrip', '1\n00:00:00,400 --> 00:00:02,800\n中文\nEnglish\n'],
    ];
    for (const [channel, args, file, type, body] of formats) {
      response = { status: 200, type: `${type}; charset=utf-8`, body };
      assert.deepEqual(await invoke(channel, args, file), { saved: true, fileName: file });
      assert.equal(fs.readFileSync(path.join(root, file), 'utf8'), body);
      assert.equal(calls.at(-1).accept, type);
      assert.deepEqual(await invoke(channel, args, '', async () => ({ canceled: true })), { saved: false, canceled: true });
    }
    assert.equal(requests[0].url, '/api/creation-tasks/task_export/output?expected_version_id=version_2');
    assert.equal(requests[1].url, '/api/creation-tasks/task_export/covers/cover_fixture/image');
    const subtitleUrl = new URL(requests[2].url, webOrigin);
    assert.equal(subtitleUrl.pathname, '/api/creation-tasks/task_export/output/selected-subtitles');
    assert.deepEqual(Object.fromEntries(subtitleUrl.searchParams), {
      expected_version_id: 'version_2', mode: 'zh-en', zh_track_id: 'subtitle_zh', en_track_id: 'subtitle_en',
    });
    const beforeInvalid = dialogCalls;
    assert.equal((await handlers.get('save-rendered-video')(eventFor(), '/tmp/untrusted.mp4')).saved, false);
    assert.equal((await handlers.get('save-cover-image')(eventFor(), 'task_export', '/tmp/source.png')).saved, false);
    assert.equal((await handlers.get('save-subtitle-file')(eventFor(), { ...selection, mode: 'wrong' })).saved, false);
    assert.equal(dialogCalls, beforeInvalid);
    const beforeMissingVersion = { requests: requests.length, dialogs: dialogCalls };
    for (const version of [undefined, null, '', ' ', '/tmp/version']) {
      const result = await preloadApi.saveRenderedVideo('task_export', version);
      assert.equal(result.saved, false);
      assert.match(result.error, /时间线版本无效/);
    }
    assert.deepEqual({ requests: requests.length, dialogs: dialogCalls }, beforeMissingVersion);

    response = { status: 200, type: 'video/mp4', body: 'version-2-video' };
    outputHead = 'version_2';
    const changedDuringDialog = await invoke('save-rendered-video', ['task_export', 'version_2'], 'stale-video.mp4', async () => {
      outputHead = 'version_3';
      return { canceled: false, filePath: path.join(root, 'stale-video.mp4') };
    });
    assert.equal(changedDuringDialog.saved, false);
    assert.match(changedDuringDialog.error, /作品版本已变化/);
    assert.equal(requests.at(-1).url, '/api/creation-tasks/task_export/output?expected_version_id=version_2');
    assert.equal(fs.existsSync(path.join(root, 'stale-video.mp4')), false);
    noPartials();
    const existingDestination = path.join(root, 'existing-video.mp4');
    fs.writeFileSync(existingDestination, 'previous-export');
    const refusedOverwrite = await invoke('save-rendered-video', ['task_export', 'version_2'], 'existing-video.mp4');
    assert.equal(refusedOverwrite.saved, false);
    assert.match(refusedOverwrite.error, /作品版本已变化/);
    assert.equal(fs.readFileSync(existingDestination, 'utf8'), 'previous-export');
    noPartials();
    outputHead = 'version_2';
    assert.deepEqual(await invoke('save-rendered-video', ['task_export', 'version_2'], 'bound-video.mp4'),
      { saved: true, fileName: 'bound-video.mp4' });
    assert.equal(fs.readFileSync(path.join(root, 'bound-video.mp4'), 'utf8'), 'version-2-video');
    outputHead = null;

    for (const [channel, args, file, status, type, body, error] of [
      ['save-rendered-video', ['task_export', 'version_2'], 'missing.mp4', 404, 'application/json', '{}', /HTTP 404/],
      ['save-cover-image', ['task_export','cover_fixture'], 'wrong.png', 200, 'text/html', 'html', /没有返回可保存/],
      ['save-subtitle-file', [selection], 'stale.srt', 409, 'application/json', JSON.stringify({ detail: '当前字幕版本已变化，请重新选择后保存。' }), /当前字幕版本已变化/],
      ['save-rendered-video', ['task_export', 'version_2'], 'empty.mp4', 200, 'video/mp4', '', /空的成片/],
      ['save-subtitle-file', [selection], 'empty.srt', 200, 'application/x-subrip', '', /空的字幕/],
    ]) {
      response = { status, type, body };
      const result = await invoke(channel, args, file);
      assert.equal(result.saved, false); assert.match(result.error, error);
      assert.equal(fs.existsSync(path.join(root, file)), false); noPartials();
    }
    response = { status: 200, type: 'application/x-subrip', body: 'subtitle' };
    shortTimeout = 150;
    for (const mode of ['hang-before-response', 'hang-after-headers']) {
      behaviour = mode;
      const started = Date.now();
      const result = await invoke('save-subtitle-file', [selection], `${mode}.srt`);
      assert.equal(result.saved, false); assert.match(result.error, /长时间没有返回/);
      assert.ok(Date.now()-started < 3000);
      assert.equal(fs.existsSync(path.join(root, `${mode}.srt`)), false); noPartials();
    }
    behaviour = 'respond'; shortTimeout = null;
    response = { status: 200, type: 'video/mp4', body: 'video' };
    await assert.rejects(invoke('save-rendered-video', ['task_export', 'version_2'], '', async () => { throw new Error('dialog failure'); }), /dialog failure/);
    const blockedDir = path.join(root, 'directory-destination'); fs.mkdirSync(blockedDir);
    assert.equal((await invoke('save-rendered-video', ['task_export', 'version_2'], 'directory-destination')).saved, false);
    assert.ok(fs.statSync(blockedDir).isDirectory()); noPartials();

    const beforeRefusal = { requests: requests.length, dialogs: dialogCalls };
    for (const event of [null, {}, { sender: new EventEmitter(), senderFrame: current.webContents.mainFrame },
      { sender: current.webContents, senderFrame: { ...current.webContents.mainFrame, routingId: 99 } },
      { sender: current.webContents, senderFrame: { ...current.webContents.mainFrame, detached: true } }]) {
      assert.equal((await handlers.get('save-rendered-video')(event, 'task_export', 'version_2')).saved, false);
    }
    for (const [url, origin] of [['about:blank', webOrigin], [`${webOrigin}/path`, 'https://untrusted.invalid']]) {
      const old = current.webContents.mainFrame;
      current.webContents.mainFrame = { ...old, url, origin };
      assert.equal((await handlers.get('save-rendered-video')(eventFor(), 'task_export', 'version_2')).saved, false);
      current.webContents.mainFrame = old;
    }
    assert.deepEqual({ requests: requests.length, dialogs: dialogCalls }, beforeRefusal);
    for (const [name, mutate] of [
      ['navigation', () => current.webContents.emit('did-start-navigation', { isMainFrame: true, isSameDocument: false })],
      ['replacement', () => { current = windowFixture(); }],
      ['destroyed', () => { current.destroyed = true; }],
      ['frame-change', () => { current.webContents.mainFrame.routingId += 1; }],
    ]) {
      current = windowFixture();
      const count = requests.length;
      const result = await invoke('save-rendered-video', ['task_export', 'version_2'], `${name}.mp4`, async () => {
        mutate(); return { canceled: false, filePath: path.join(root, `${name}.mp4`) };
      });
      assert.equal(result.saved, false); assert.match(result.error, /保存来源已失效/);
      assert.equal(requests.length, count); assert.equal(fs.existsSync(path.join(root, `${name}.mp4`)), false); noPartials();
    }
    current = windowFixture();
    const count = requests.length;
    beforeFetch = () => { current.webContents.mainFrame.routingId += 1; };
    assert.equal((await invoke('save-rendered-video', ['task_export', 'version_2'], 'request-recheck.mp4')).saved, false);
    assert.equal(requests.length, count); beforeFetch = null; noPartials();
    current = windowFixture();
    afterDownload = () => current.webContents.emit('did-start-navigation', { isMainFrame: true, isSameDocument: false });
    assert.equal((await invoke('save-rendered-video', ['task_export', 'version_2'], 'commit-recheck.mp4')).saved, false);
    assert.equal(fs.existsSync(path.join(root, 'commit-recheck.mp4')), false); afterDownload = null; noPartials();
    current = windowFixture(); apiState = 'failed';
    assert.match((await invoke('save-rendered-video', ['task_export', 'version_2'], 'not-ready.mp4')).error, /尚未就绪/);
    assert.equal(requests.length, count+1); apiState = 'ready'; noPartials();
    current = windowFixture();
    afterDownload = () => { apiState = 'failed'; };
    assert.equal((await invoke('save-rendered-video', ['task_export', 'version_2'], 'completed-before-service-stop.mp4')).saved, true);
    afterDownload = null; apiState = 'ready'; noPartials();
    current = windowFixture();
    assert.equal((await invoke('save-rendered-video', ['task_export', 'version_2'], 'same-document.mp4', async () => {
      current.webContents.emit('did-start-navigation', { isMainFrame: true, isSameDocument: true });
      return { canceled: false, filePath: path.join(root, 'same-document.mp4') };
    })).saved, true);
    noPartials();
    console.log('native save focused boundary: passed (fake session/window/dialog, own loopback/files; no Electron/authentication acceptance)');
  } finally {
    for (const socket of sockets) socket.destroy();
    await new Promise((resolve) => server.close(resolve));
    fs.rmSync(root, { recursive: true, force: true });
  }
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
