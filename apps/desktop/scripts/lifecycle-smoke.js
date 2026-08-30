#!/usr/bin/env node
/*
 * Exercises Electron main-process lifecycle behavior without building an app.
 * Each case runs main.js with a narrow Electron mock and real, disposable
 * API/Web child processes in a temporary packaged-resources directory.
 */

const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const net = require('node:net');
const os = require('node:os');
const path = require('node:path');
const Module = require('node:module');

const MAIN_SOURCE = path.resolve(__dirname, '..', 'main.js');
const { createMetadata } = require('./generate-build-identity');
const POLL_MS = 25;
const TIMEOUT_MS = 8_000;

const serverFixture = `
const http = require('node:http');
const port = Number(process.env.FIXTURE_PORT || process.env.PORT || process.env.API_PORT);
const server = http.createServer((request, response) => {
  response.writeHead(200, { 'content-type': 'text/plain' });
  response.end(request.url === '/api/runtime/health' ? 'ok' : 'web');
});
server.listen(port, '127.0.0.1');
process.on('SIGTERM', () => server.close(() => { process.exitCode = 0; }));
`;
const legacyWorkerFixture = `
const fs = require('node:fs');
fs.writeFileSync(process.env.AI_VIDEO_DIARY_LIFECYCLE_WORKER_MARKER_PATH, 'started');
process.exit(91);
`;

function waitFor(predicate, description) {
  const deadline = Date.now() + TIMEOUT_MS;
  return new Promise((resolve, reject) => {
    const check = async () => {
      if (await predicate()) return resolve();
      if (Date.now() >= deadline) return reject(new Error(`Timed out waiting for ${description}`));
      setTimeout(check, POLL_MS);
    };
    check();
  });
}

function writeFixture(filePath, source, executable = false) {
  fs.writeFileSync(filePath, source, { mode: executable ? 0o755 : 0o644 });
  if (executable) fs.chmodSync(filePath, 0o755);
}

function isAlive(pid) {
  if (!Number.isInteger(pid) || pid <= 0) return false;
  try {
    process.kill(pid, 0);
    return true;
  } catch (error) {
    return error.code !== 'ESRCH';
  }
}

function portIsAvailable(port) {
  return new Promise((resolve) => {
    const server = net.createServer();
    server.once('error', () => resolve(false));
    server.listen(port, '127.0.0.1', () => server.close(() => resolve(true)));
  });
}

async function startExternalFixture(port) {
  if (!await portIsAvailable(port)) return null;
  const child = spawn(process.execPath, ['-e', serverFixture], {
    env: { ...process.env, FIXTURE_PORT: String(port) },
    stdio: 'ignore',
    detached: true,
  });
  await waitFor(() => isAlive(child.pid), `external fixture on ${port}`);
  return child;
}

function createElectronMock(logsPath, packaged = false, windowLoadMode = 'no-navigation-events') {
  const events = new Map();
  const handlers = new Map();
  const dialogs = [];
  const windows = [];
  const app = {
    isPackaged: packaged,
    whenReady: () => Promise.resolve(),
    getPath: (name) => name === 'logs' ? logsPath : os.tmpdir(),
    getVersion: () => 'test',
    requestSingleInstanceLock: () => true,
    on(event, callback) {
      const callbacks = events.get(event) || [];
      callbacks.push(callback);
      events.set(event, callbacks);
    },
    emit(event) {
      for (const callback of events.get(event) || []) callback();
    },
    quit() {
      const event = { prevented: false, preventDefault() { this.prevented = true; } };
      for (const callback of events.get('before-quit') || []) callback(event);
      if (!event.prevented) this.exit();
    },
    exit() {},
  };
  class BrowserWindow {
    constructor(options) {
      windows.push(this);
      this.options = options;
      this.visible = Boolean(options.show);
      this.webContents = {
        openDevTools() {},
        setWindowOpenHandler() {},
        send() {},
        once(event, callback) {
          if (event === 'dom-ready') this.domReady = callback;
          if (event === 'did-finish-load') this.didFinishLoad = callback;
        },
        emitDomReady() {
          const callback = this.domReady;
          this.domReady = null;
          if (callback) callback();
        },
        emitDidFinishLoad() {
          const callback = this.didFinishLoad;
          this.didFinishLoad = null;
          if (callback) callback();
        },
      };
    }
    loadURL() {
      this.loadURLCount = (this.loadURLCount || 0) + 1;
      this.visibleWhenNavigationStarted = this.visible;
      if (windowLoadMode === 'sync-later-events') {
        this.webContents.emitDomReady();
        this.webContents.emitDidFinishLoad();
        this.emitReadyToShow();
      }
      if (windowLoadMode === 'async-later-events') {
        setImmediate(() => {
          this.webContents.emitDomReady();
          this.webContents.emitDidFinishLoad();
          this.emitReadyToShow();
        });
      }
    }
    show() {
      this.showCount = (this.showCount || 0) + 1;
      this.visible = true;
    }
    once(event, callback) { if (event === 'ready-to-show') this.readyToShow = callback; }
    on(event, callback) { if (event === 'closed') this.closed = callback; }
    emitReadyToShow() { if (this.readyToShow) this.readyToShow(); }
    close() {
      this.destroyed = true;
      const index = windows.indexOf(this);
      if (index !== -1) windows.splice(index, 1);
      if (this.closed) this.closed();
    }
    isDestroyed() { return Boolean(this.destroyed); }
    isVisible() { return Boolean(this.visible); }
    isMinimized() { return false; }
    restore() {}
    focus() {}
  }
  BrowserWindow.getAllWindows = () => windows;
  return {
    electron: {
      app,
      BrowserWindow,
      Menu: { setApplicationMenu() {}, buildFromTemplate: () => ({}) },
      dialog: {
        showErrorBox(title, message) { dialogs.push({ title, message }); },
        async showMessageBox() { return { response: 1 }; },
        async showOpenDialog() { return { canceled: true, filePaths: [] }; },
      },
      ipcMain: { handle(name, callback) { handlers.set(name, callback); } },
      shell: { openExternal() {}, openPath() { return ''; } },
    },
    updater: { autoUpdater: { on() {}, checkForUpdates() { return Promise.resolve(); } } },
    dialogs,
    windows,
    handlers,
    app,
  };
}

async function loadMain({ resourcesPath, development, packaged = false, windowLoadMode = 'no-navigation-events' }) {
  const mainPath = path.join(resourcesPath, 'app', 'main.js');
  const originalLoad = Module._load;
  const originalResourcesPath = process.resourcesPath;
  const originalNodeEnv = process.env.NODE_ENV;
  const mock = createElectronMock(path.join(resourcesPath, 'logs'), packaged, windowLoadMode);
  process.resourcesPath = resourcesPath;
  if (development) process.env.NODE_ENV = 'development';
  else delete process.env.NODE_ENV;
  delete require.cache[mainPath];
  Module._load = function load(request, parent, isMain) {
    if (request === 'electron') return mock.electron;
    if (request === 'electron-updater') return mock.updater;
    return originalLoad.call(this, request, parent, isMain);
  };
  try {
    require(mainPath);
    await new Promise((resolve) => setImmediate(resolve));
  } catch (error) {
    Module._load = originalLoad;
    process.resourcesPath = originalResourcesPath;
    if (originalNodeEnv === undefined) delete process.env.NODE_ENV;
    else process.env.NODE_ENV = originalNodeEnv;
    delete require.cache[mainPath];
    throw error;
  }
  return {
    ...mock,
    restore() {
      Module._load = originalLoad;
      process.resourcesPath = originalResourcesPath;
      if (originalNodeEnv === undefined) delete process.env.NODE_ENV;
      else process.env.NODE_ENV = originalNodeEnv;
      delete require.cache[mainPath];
    },
  };
}

async function assertVisibleByConstruction(windowLoadMode) {
  const resources = createResources();
  const runtime = await loadMain({ resourcesPath: resources, development: false, windowLoadMode });
  try {
    await waitFor(() => runtime.windows.length === 1, `${windowLoadMode} initial BrowserWindow`);
    const window = runtime.windows[0];
    assert.equal(window.options.show, true, 'the BrowserWindow must be visible by construction');
    assert.equal(window.visible, true, 'the first window must be visible without a navigation event');
    assert.equal(window.visibleWhenNavigationStarted, true, 'navigation must begin after the visible window is constructed');
    assert.equal(window.loadURLCount, 1, 'the initial navigation must still occur exactly once');
    assert.equal(window.showCount || 0, 0, 'startup must not call show()');
    assert.equal(window.domReady, undefined, 'dom-ready must not remain an initial reveal authority');
    assert.equal(window.didFinishLoad, undefined, 'did-finish-load must not remain an initial reveal authority');
    assert.equal(window.readyToShow, undefined, 'ready-to-show must not remain an initial reveal authority');
    const status = await runtime.handlers.get('get-service-status')();
    await cleanupRuntime({ resources, runtime, status });
  } finally {
    if (fs.existsSync(resources)) {
      runtime.restore();
      fs.rmSync(resources, { recursive: true, force: true });
    }
  }
}

async function assertVisibleWindowLifecycleBoundary() {
  const resources = createResources();
  const runtime = await loadMain({ resourcesPath: resources, development: false, windowLoadMode: 'manual' });
  try {
    await waitFor(() => runtime.windows.length === 1, 'manual BrowserWindow creation');
    const firstWindow = runtime.windows[0];
    assert.equal(firstWindow.visible, true, 'the manual first window starts visible');
    assert.equal(firstWindow.showCount || 0, 0, 'the manual first window has no startup show call');

    runtime.app.emit('activate');
    assert.equal(firstWindow.showCount || 0, 0, 'activation must not re-show an already visible window');

    firstWindow.visible = false;
    runtime.app.emit('activate');
    assert.equal(firstWindow.showCount, 1, 'activation must show an actually hidden current window');
    runtime.app.emit('activate');
    assert.equal(firstWindow.showCount, 1, 'activation must not duplicate-show an already visible window');

    firstWindow.close();
    runtime.app.emit('activate');
    await waitFor(() => runtime.windows.length === 1 && runtime.windows[0] !== firstWindow, 'second BrowserWindow creation after close');
    const secondWindow = runtime.windows[0];

    firstWindow.webContents.emitDomReady();
    firstWindow.webContents.emitDidFinishLoad();
    firstWindow.emitReadyToShow();
    assert.equal(firstWindow.showCount, 1, 'a stale closed window must not show again');
    assert.equal(secondWindow.visible, true, 'the second window must be visible by construction');
    assert.equal(secondWindow.showCount || 0, 0, 'a stale window must not show the later window');

    secondWindow.webContents.emitDomReady();
    secondWindow.webContents.emitDidFinishLoad();
    secondWindow.emitReadyToShow();
    assert.equal(secondWindow.showCount || 0, 0, 'later navigation events must not create a startup show call');
    runtime.app.emit('activate');
    assert.equal(secondWindow.showCount || 0, 0, 'activation must not duplicate-show the second visible window');
    const status = await runtime.handlers.get('get-service-status')();
    await cleanupRuntime({ resources, runtime, status });
  } finally {
    if (fs.existsSync(resources)) {
      runtime.restore();
      fs.rmSync(resources, { recursive: true, force: true });
    }
  }
}

function createResources({ api = true, web = true, legacyWorker = false, buildIdentity = 'valid', identityRecords = null } = {}) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'ai-video-diary-lifecycle-'));
  const appRoot = path.join(root, 'app');
  fs.mkdirSync(appRoot, { recursive: true });
  const mainPath = path.join(appRoot, 'main.js');
  fs.copyFileSync(MAIN_SOURCE, mainPath);
  if (buildIdentity === 'valid') {
    const metadata = createMetadata(identityRecords || [{ path: 'desktop/main.js', content: fs.readFileSync(mainPath) }]);
    writeFixture(path.join(appRoot, 'build-identity.generated.json'), `${JSON.stringify(metadata, null, 2)}\n`);
  } else if (buildIdentity === 'invalid') {
    writeFixture(path.join(appRoot, 'build-identity.generated.json'), '{"format":"invalid"}\n');
  }
  if (api) writeFixture(path.join(root, 'api-server'), `#!/usr/bin/env node\n${serverFixture}`, true);
  if (legacyWorker) writeFixture(path.join(root, 'worker-server'), `#!/usr/bin/env node\n${legacyWorkerFixture}`, true);
  if (web) {
    const webRoot = path.join(root, 'web');
    fs.mkdirSync(webRoot, { recursive: true });
    writeFixture(path.join(webRoot, 'server.js'), serverFixture);
  }
  return root;
}

async function assertRuntimeAcceptsCodeUnitIdentityOrder() {
  const records = [
    { path: 'web/.next/app-path-routes-manifest.json', content: 'routes' },
    { path: 'web/.next/BUILD_ID', content: 'build' },
    { path: 'web/.next/server/app/production/page_client-reference-manifest.js', content: 'references' },
    { path: 'web/.next/server/app/production/page.js', content: 'page' },
  ];
  const resources = createResources({ identityRecords: records });
  const runtime = await loadMain({ resourcesPath: resources, development: false });
  try {
    await waitFor(() => runtime.windows.length === 1, 'code-unit identity startup');
    const status = await runtime.handlers.get('get-service-status')();
    assert.equal(status.buildIdentity, createMetadata(records).candidateInputDigest);
    await cleanupRuntime({ resources, runtime, status });
  } finally {
    if (fs.existsSync(resources)) {
      runtime.restore();
      fs.rmSync(resources, { recursive: true, force: true });
    }
  }
}

async function assertBuildIdentityMetadataFailure(buildIdentity, expectedMessage, { development = false, packaged = false } = {}) {
  const resources = createResources({ buildIdentity });
  try {
    await assert.rejects(
      loadMain({ resourcesPath: resources, development, packaged }),
      new RegExp(expectedMessage),
    );
  } finally {
    fs.rmSync(resources, { recursive: true, force: true });
  }
}

async function assertMissingResource(name) {
  const resources = createResources({ [name]: false });
  const runtime = await loadMain({ resourcesPath: resources, development: false });
  try {
    await waitFor(() => runtime.dialogs.length === 1, `${name} startup failure`);
    assert.equal(runtime.windows.length, 0, `${name} must fail before BrowserWindow creation`);
    const status = await runtime.handlers.get('get-service-status')();
    assert.equal(status[name].state, 'failed');
    await waitFor(async () => {
      const current = await runtime.handlers.get('get-service-status')();
      return current.api.state === 'not-started' || current.api.state === 'stopped' || current.api.pid === null;
    }, `${name} startup child cleanup`);
  } finally {
    runtime.restore();
    fs.rmSync(resources, { recursive: true, force: true });
  }
}

async function startPackaged() {
  const resources = createResources({ legacyWorker: true });
  const workerMarkerPath = path.join(resources, 'worker-started');
  const vaultPath = path.join(resources, 'vault-must-not-be-scanned');
  const previousWorkerMarkerPath = process.env.AI_VIDEO_DIARY_LIFECYCLE_WORKER_MARKER_PATH;
  const previousVaultPath = process.env.OBSIDIAN_VAULT_PATH;
  process.env.AI_VIDEO_DIARY_LIFECYCLE_WORKER_MARKER_PATH = workerMarkerPath;
  process.env.OBSIDIAN_VAULT_PATH = vaultPath;
  const runtime = await loadMain({ resourcesPath: resources, development: false });
  await waitFor(() => runtime.windows.length === 1, 'packaged BrowserWindow creation');
  const status = await runtime.handlers.get('get-service-status')();
  assert.deepEqual([status.api.state, status.web.state], ['ready', 'ready']);
  const metadata = JSON.parse(fs.readFileSync(path.join(resources, 'app', 'build-identity.generated.json'), 'utf8'));
  assert.equal(status.buildIdentity, metadata.candidateInputDigest);
  assert.equal((await runtime.handlers.get('get-runtime-info')()).buildIdentity, metadata.candidateInputDigest);
  assert.equal('worker' in status, false, 'legacy Worker must not be a desktop startup service');
  assert.equal(fs.existsSync(workerMarkerPath), false, 'legacy Worker must not be launched');
  return {
    resources,
    runtime,
    status,
    lifecycleLog: path.join(resources, 'logs', 'desktop-runtime.jsonl'),
    previousWorkerMarkerPath,
    previousVaultPath,
  };
}

async function cleanupRuntime({ resources, runtime, status, previousWorkerMarkerPath, previousVaultPath }) {
  runtime.app.quit();
  await waitFor(
    () => [status.api.pid, status.web.pid]
      .every((pid) => !isAlive(pid)),
    'child-process cleanup',
  );
  runtime.restore();
  if (previousWorkerMarkerPath === undefined) delete process.env.AI_VIDEO_DIARY_LIFECYCLE_WORKER_MARKER_PATH;
  else process.env.AI_VIDEO_DIARY_LIFECYCLE_WORKER_MARKER_PATH = previousWorkerMarkerPath;
  if (previousVaultPath === undefined) delete process.env.OBSIDIAN_VAULT_PATH;
  else process.env.OBSIDIAN_VAULT_PATH = previousVaultPath;
  fs.rmSync(resources, { recursive: true, force: true });
}

async function assertRuntimeExit(name) {
  const test = await startPackaged();
  try {
    process.kill(test.status[name].pid, 'SIGTERM');
    await waitFor(async () => {
      const current = await test.runtime.handlers.get('get-service-status')();
      return current[name].state === 'failed';
    }, `${name} failed state`);
    assert.equal(test.runtime.dialogs.length, 1, `${name} exit must be visible`);
    await waitFor(() => fs.existsSync(test.lifecycleLog), `${name} lifecycle log`);
    const log = fs.readFileSync(test.lifecycleLog, 'utf8');
    assert.match(log, new RegExp(`"event":"service-exit"[^\\n]*"service":"${name}"`));
    assert.match(log, new RegExp(`"buildIdentity":"${test.status.buildIdentity}"`));
    await waitFor(
      () => Object.entries(test.status)
        .filter(([service]) => service !== name)
        .every(([, state]) => !isAlive(state.pid)),
      `${name} sibling cleanup`,
    );
  } finally {
    await cleanupRuntime(test);
  }
}

async function closedPipeProbe() {
  const test = await startPackaged();
  const resultPath = process.env.AI_VIDEO_DIARY_CLOSED_PIPE_RESULT;
  try {
    console.log('closed-pipe probe ready');
    await new Promise((resolve) => setTimeout(resolve, 100));
    console.log('closed-pipe probe after sink closure');
    await waitFor(() => fs.existsSync(test.lifecycleLog) && fs.readFileSync(test.lifecycleLog, 'utf8').includes('main-log-sink-closed'), 'EPIPE lifecycle record');
    await cleanupRuntime(test);
    fs.writeFileSync(resultPath, JSON.stringify({ pass: true }));
  } catch (error) {
    fs.writeFileSync(resultPath, JSON.stringify({ pass: false, error: error.message }));
    throw error;
  }
}

async function assertClosedPipe() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'ai-video-diary-closed-pipe-'));
  const resultPath = path.join(root, 'result.json');
  const child = spawn(process.execPath, [__filename, '--closed-pipe-probe'], {
    env: { ...process.env, AI_VIDEO_DIARY_CLOSED_PIPE_RESULT: resultPath },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  const childExit = new Promise((resolve) => child.once('exit', (exitCode, exitSignal) => resolve([exitCode, exitSignal])));
  let stdout = '';
  let stderr = '';
  child.stdout.on('data', (data) => { stdout += data; });
  child.stderr.on('data', (data) => { stderr += data; });
  try {
    await waitFor(() => stdout.includes('closed-pipe probe ready'), 'closed-pipe probe readiness');
    child.stdout.destroy();
    await waitFor(() => fs.existsSync(resultPath), 'closed-pipe probe result');
    const outcome = JSON.parse(fs.readFileSync(resultPath, 'utf8'));
    assert.equal(outcome.pass, true, outcome.error || 'closed-pipe probe failed');
    const [code, signal] = await childExit;
    assert.equal(code, 0, `closed-pipe probe exited ${code}/${signal}; stderr=${stderr}`);
    assert.equal(signal, null, `closed-pipe probe signal=${signal}; stderr=${stderr}`);
  } finally {
    if (isAlive(child.pid)) child.kill('SIGTERM');
    fs.rmSync(root, { recursive: true, force: true });
  }
}

async function assertDevelopmentExternalServices() {
  const resources = createResources({ api: false, web: false });
  const api = await startExternalFixture(8000);
  const web = await startExternalFixture(3000);
  const sentinel = spawn(process.execPath, ['-e', 'setInterval(() => {}, 1000)'], {
    stdio: 'ignore',
    detached: true,
  });
  const runtime = await loadMain({ resourcesPath: resources, development: true });
  try {
    await waitFor(() => runtime.windows.length === 1, 'development BrowserWindow creation');
    const status = await runtime.handlers.get('get-service-status')();
    assert.equal(status.api.state, 'ready');
    assert.equal(status.web.state, 'ready');
    assert.equal('worker' in status, false);
    runtime.app.quit();
    await waitFor(() => isAlive(sentinel.pid), 'non-owned process survives app quit');
    if (api) assert.equal(isAlive(api.pid), true, 'external API fixture must survive app quit');
    if (web) assert.equal(isAlive(web.pid), true, 'external Web fixture must survive app quit');
  } finally {
    if (api) api.kill('SIGTERM');
    if (web) web.kill('SIGTERM');
    sentinel.kill('SIGTERM');
    await waitFor(
      () => !isAlive(sentinel.pid) && (!api || !isAlive(api.pid)) && (!web || !isAlive(web.pid)),
      'external fixture cleanup',
    );
    runtime.restore();
    fs.rmSync(resources, { recursive: true, force: true });
  }
}

async function main() {
  await assertBuildIdentityMetadataFailure('missing', 'build identity metadata is missing or unreadable');
  await assertBuildIdentityMetadataFailure('invalid', 'build identity metadata is invalid');
  await assertBuildIdentityMetadataFailure('missing', 'build identity metadata is missing or unreadable', { development: true, packaged: true });
  await assertRuntimeAcceptsCodeUnitIdentityOrder();
  await assertVisibleByConstruction('no-navigation-events');
  await assertVisibleByConstruction('sync-later-events');
  await assertVisibleWindowLifecycleBoundary();
  for (const resource of ['api', 'web']) await assertMissingResource(resource);
  for (const service of ['api', 'web']) await assertRuntimeExit(service);
  const normal = await startPackaged();
  await cleanupRuntime(normal);
  await assertDevelopmentExternalServices();
  await assertClosedPipe();
  console.log('desktop lifecycle smoke: PASS');
}

const runner = process.argv.includes('--closed-pipe-probe') ? closedPipeProbe : main;

runner().catch((error) => {
  console.error('desktop lifecycle smoke: FAIL');
  console.error(error.stack || error.message);
  process.exitCode = 1;
});
