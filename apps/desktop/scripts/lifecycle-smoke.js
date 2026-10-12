#!/usr/bin/env node
/*
 * Exercises Electron main-process lifecycle behavior without building an app.
 * Each case runs main.js with a narrow Electron mock and real, disposable
 * API/Web child processes in a temporary packaged-resources directory.
 */

const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const { EventEmitter } = require('node:events');
const fs = require('node:fs');
const net = require('node:net');
const os = require('node:os');
const path = require('node:path');
const Module = require('node:module');

const MAIN_SOURCE = path.resolve(__dirname, '..', 'main.js');
const DEVICE_IDENTITY_SOURCE = path.resolve(__dirname, '..', 'device-identity.js');
const { createMetadata } = require('./generate-build-identity');
const POLL_MS = 25;
const TIMEOUT_MS = 8_000;

const serverFixture = `
const http = require('node:http');
const port = Number(process.env.FIXTURE_PORT || process.env.PORT || process.env.API_PORT);
const server = http.createServer((request, response) => {
  if (request.url === '/api/creation-tasks/task_fixture/covers/cover_fixture/image') {
    response.writeHead(200, { 'content-type': 'image/png' });
    response.end('fixture-png');
    return;
  }
  if (request.url === '/api/creation-tasks/task_missing/output') {
    response.writeHead(404, { 'content-type': 'application/json' });
    response.end(JSON.stringify({ detail: 'creation task output not found' }));
    return;
  }
  if (/^\\/api\\/creation-tasks\\/task_[a-zA-Z0-9_-]+\\/output$/.test(request.url)) {
    response.writeHead(200, { 'content-type': 'video/mp4' });
    response.end('fixture-mp4');
    return;
  }
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

function createElectronMock(logsPath, packaged = false, windowLoadMode = 'no-navigation-events', userDataPath = '', { localWebPort, autoStart = true, singleInstanceLock = () => true } = {}) {
  const events = new Map();
  const handlers = new Map();
  const dialogs = [];
  const windows = [];
  let saveDialogResult = { canceled: true };
  const paths = { logs: logsPath, userData: path.join(path.dirname(logsPath), 'user-data') };
  let resolveReady;
  const readyPromise = new Promise(resolve => { resolveReady = resolve; });
  if (autoStart) resolveReady();
  const app = {
    isPackaged: packaged,
    commandLine: {
      getSwitchValue: (name) => name === 'user-data-dir' ? userDataPath : name === 'local-web-port' ? (localWebPort ?? '') : '',
      hasSwitch: (name) => name === 'local-web-port' && localWebPort !== undefined,
    },
    setPath: (name, value) => { paths[name] = value; },
    setAppLogsPath: (value) => { paths.logs = value; },
    whenReady: () => readyPromise,
    getPath: (name) => paths[name] || os.tmpdir(),
    getVersion: () => 'test',
    requestSingleInstanceLock: singleInstanceLock,
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
    exit(code = 0) { this.exited = true; this.exitCode = code; },
  };
  class BrowserWindow {
    constructor(options) {
      windows.push(this);
      this.options = options;
      this.visible = Boolean(options.show);
      this.webContents = {
        on() {},
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
    loadURL(url) {
      this.loadedURL = url;
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
        async showSaveDialog() { return saveDialogResult; },
      },
      ipcMain: { handle(name, callback) { handlers.set(name, callback); }, on() {}, removeListener() {} },
      shell: { openExternal() {}, openPath() { return ''; } },
    },
    updater: { autoUpdater: { on() {}, checkForUpdates() { return Promise.resolve(); } } },
    dialogs,
    windows,
    handlers,
    app,
    markReady() { resolveReady(); },
    setSaveDialogResult(value) { saveDialogResult = value; },
  };
}

async function loadMain({ resourcesPath, development, packaged = false, windowLoadMode = 'no-navigation-events', userDataPath = '', localWebPort, autoStart = true, singleInstanceLock, lifecycleStreamFactory }) {
  const mainPath = fs.realpathSync(path.join(resourcesPath, 'app', 'main.js'));
  const originalLoad = Module._load;
  const originalResourcesPath = process.resourcesPath;
  const originalNodeEnv = process.env.NODE_ENV;
  const stdoutErrorListeners = new Set(process.stdout.listeners('error'));
  const stderrErrorListeners = new Set(process.stderr.listeners('error'));
  const mock = createElectronMock(path.join(resourcesPath, 'logs'), packaged, windowLoadMode, userDataPath, { localWebPort, autoStart, singleInstanceLock });
  let mainExports;
  const children = [];
  mock.electron.utilityProcess = {
    fork(modulePath, args, options) {
      assert.equal(options.serviceName, 'Yaohe Web');
      assert.equal(options.stdio, 'pipe');
      assert.equal(options.env.ELECTRON_RUN_AS_NODE, undefined);
      const child = spawn(process.execPath, [modulePath, ...args], { env: options.env, stdio: ['ignore', 'pipe', 'pipe'] });
      children.push(child);
      let pid = child.pid;
      const service = new EventEmitter();
      Object.defineProperties(service, {
        pid: { get: () => pid },
        stdout: { get: () => pid === undefined ? null : child.stdout },
        stderr: { get: () => pid === undefined ? null : child.stderr },
      });
      service.kill = () => child.kill('SIGTERM');
      child.on('spawn', () => service.emit('spawn'));
      child.on('error', () => service.emit('error', { type: 'FatalError' }));
      child.on('exit', (code) => { pid = undefined; service.emit('exit', code); });
      return service;
    },
  };
  const restoreProcessErrorListeners = () => {
    for (const listener of process.stdout.listeners('error')) {
      if (!stdoutErrorListeners.has(listener)) process.stdout.removeListener('error', listener);
    }
    for (const listener of process.stderr.listeners('error')) {
      if (!stderrErrorListeners.has(listener)) process.stderr.removeListener('error', listener);
    }
  };
  process.resourcesPath = resourcesPath;
  if (development) process.env.NODE_ENV = 'development';
  else delete process.env.NODE_ENV;
  delete require.cache[mainPath];
  Module._load = function load(request, parent, isMain) {
    if (request === 'electron') return mock.electron;
    if (request === 'electron-updater') return mock.updater;
    if (request === 'fs' && parent.filename === mainPath && lifecycleStreamFactory) return { ...fs, createWriteStream: lifecycleStreamFactory };
    if (request === 'child_process' && parent.filename === mainPath) {
      const childProcess = originalLoad.call(this, request, parent, isMain);
      return { ...childProcess, spawn(...args) { const child = childProcess.spawn(...args); children.push(child); return child; } };
    }
    return originalLoad.call(this, request, parent, isMain);
  };
  try {
    mainExports = require(mainPath);
    await new Promise((resolve) => setImmediate(resolve));
  } catch (error) {
    Module._load = originalLoad;
    process.resourcesPath = originalResourcesPath;
    restoreProcessErrorListeners();
    if (originalNodeEnv === undefined) delete process.env.NODE_ENV;
    else process.env.NODE_ENV = originalNodeEnv;
    delete require.cache[mainPath];
    throw error;
  }
  return {
    ...mock,
    mainExports,
    children,
    restore() {
      Module._load = originalLoad;
      process.resourcesPath = originalResourcesPath;
      restoreProcessErrorListeners();
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

function createResources({ api = true, web = true, legacyWorker = false, buildIdentity = 'valid', identityRecords = null, distributionConfig, portProbe = false, logProbe = false } = {}) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'ai-video-diary-lifecycle-'));
  const appRoot = path.join(root, 'app');
  fs.mkdirSync(appRoot, { recursive: true });
  const mainPath = path.join(appRoot, 'main.js');
  fs.copyFileSync(MAIN_SOURCE, mainPath);
  // Test-only access to the actual selected constants; readiness is held so
  // default/Supabase origin probes never start services or bind real 3000.
  if (portProbe) fs.appendFileSync(mainPath, '\nmodule.exports = { webPort, webOrigin };\n');
  if (logProbe) fs.appendFileSync(mainPath, '\nmodule.exports = { recordLifecycle, closeLifecycleLog };\n');
  if (distributionConfig !== undefined) {
    fs.mkdirSync(path.join(appRoot, 'assets'), { recursive: true });
    writeFixture(path.join(appRoot, 'assets', 'distribution-config.generated.json'), JSON.stringify(distributionConfig));
  }
  // main.js requires this sibling at load time; without it every fixture dies on
  // MODULE_NOT_FOUND before reaching the behaviour under test.
  fs.copyFileSync(DEVICE_IDENTITY_SOURCE, path.join(appRoot, 'device-identity.js'));
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
  try {
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
  } catch (error) {
    const status = await runtime.handlers.get('get-service-status')();
    await cleanupRuntime({ resources, runtime, status, previousWorkerMarkerPath, previousVaultPath });
    throw error;
  }
}

async function cleanupRuntime({ resources, runtime, status, previousWorkerMarkerPath, previousVaultPath }) {
  runtime.app.quit();
  try {
    await waitFor(
      () => [status.api.pid, status.web.pid]
        .every((pid) => !isAlive(pid)),
      'child-process cleanup',
    );
    fs.rmSync(resources, { recursive: true, force: true });
  } finally {
    runtime.restore();
    if (previousWorkerMarkerPath === undefined) delete process.env.AI_VIDEO_DIARY_LIFECYCLE_WORKER_MARKER_PATH;
    else process.env.AI_VIDEO_DIARY_LIFECYCLE_WORKER_MARKER_PATH = previousWorkerMarkerPath;
    if (previousVaultPath === undefined) delete process.env.OBSIDIAN_VAULT_PATH;
    else process.env.OBSIDIAN_VAULT_PATH = previousVaultPath;
  }
}

async function assertIsolatedUserData() {
  const resources = createResources();
  await assert.rejects(
    loadMain({ resourcesPath: resources, development: false, userDataPath: 'relative-profile' }),
    /--user-data-dir must be an absolute path/,
  );
  const userDataPath = path.join(resources, 'isolated-profile');
  const runtime = await loadMain({ resourcesPath: resources, development: false, userDataPath });
  try {
    await waitFor(() => runtime.windows.length === 1, 'isolated BrowserWindow creation');
    const status = await runtime.handlers.get('get-service-status')();
    assert.equal(runtime.app.getPath('userData'), userDataPath);
    assert.equal(runtime.app.getPath('sessionData'), userDataPath);
    assert.equal(runtime.app.getPath('logs'), path.join(userDataPath, 'logs'));
    assert.equal(fs.readFileSync(path.join(userDataPath, '.env'), 'utf8'), '');
    assert.equal(fs.existsSync(path.join(userDataPath, 'device-identity.generated.json')), true);
    assert.equal(fs.existsSync(path.join(userDataPath, 'logs', 'desktop-runtime.jsonl')), true);
    assert.equal(fs.existsSync(path.join(resources, 'user-data')), false, 'default userData must remain untouched');
    assert.equal(fs.existsSync(path.join(resources, 'logs')), false, 'default logs must remain untouched');
    await cleanupRuntime({ resources, runtime, status });
  } finally {
    if (fs.existsSync(resources)) {
      runtime.app.quit();
      runtime.restore();
    }
  }
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

async function assertNativeRenderedVideoSaveBoundary() {
  const test = await startPackaged();
  try {
    const handler = test.runtime.handlers.get('save-rendered-video');
    assert.equal(typeof handler, 'function', 'native rendered-video save handler must be registered');
    const invalid = await handler({}, '/tmp/untrusted.mp4');
    assert.equal(invalid.saved, false, 'renderer paths must not be accepted as task ids');

    test.runtime.setSaveDialogResult({ canceled: true });
    const canceled = await handler({}, 'task_fixture');
    assert.deepEqual(canceled, { saved: false, canceled: true }, 'canceling the native dialog must be a normal result');

    const missingDestination = path.join(test.resources, 'missing-output.mp4');
    test.runtime.setSaveDialogResult({ canceled: false, filePath: missingDestination });
    const missing = await handler({}, 'task_missing');
    assert.deepEqual(missing, {
      saved: false,
      error: '本地服务没有返回可保存的 MP4（HTTP 404）。',
    });
    assert.equal(fs.existsSync(missingDestination), false, 'a failed download must not create the destination');
    assert.deepEqual(
      fs.readdirSync(test.resources).filter((name) => name.startsWith('missing-output.mp4.partial-')),
      [],
      'a failed download must remove its partial file',
    );

    const destination = path.join(test.resources, 'chosen-output.mp4');
    test.runtime.setSaveDialogResult({ canceled: false, filePath: destination });
    const saved = await handler({}, 'task_fixture');
    assert.deepEqual(saved, { saved: true, fileName: 'chosen-output.mp4' });
    assert.equal(fs.readFileSync(destination, 'utf8'), 'fixture-mp4');

    const saveCover = test.runtime.handlers.get('save-cover-image');
    assert.equal(typeof saveCover, 'function');
    assert.equal((await saveCover({}, 'task_fixture', '/tmp/source.png')).saved, false);
    test.runtime.setSaveDialogResult({ canceled: true });
    assert.deepEqual(await saveCover({}, 'task_fixture', 'cover_fixture'), { saved: false, canceled: true });
    const coverDestination = path.join(test.resources, 'chosen-cover.png');
    test.runtime.setSaveDialogResult({ canceled: false, filePath: coverDestination });
    const wrongType = await saveCover({}, 'task_fixture', 'cover_unknown');
    assert.equal(wrongType.saved, false, 'non-image response must not be saved');
    assert.equal(fs.existsSync(coverDestination), false);
    assert.deepEqual(await saveCover({}, 'task_fixture', 'cover_fixture'), { saved: true, fileName: 'chosen-cover.png' });
    assert.equal(fs.readFileSync(coverDestination, 'utf8'), 'fixture-png');
    assert.deepEqual(fs.readdirSync(test.resources).filter((name) => name.includes('.partial-')), []);
  } finally {
    await cleanupRuntime(test);
  }
}

async function assertSingleInstanceLockCheckpoints() {
  for (const outcome of [true, false, 'throw']) {
    const resources = createResources({ api: false, web: false });
    const logPath = path.join(resources, 'logs', 'desktop-runtime.jsonl');
    const metadata = JSON.parse(fs.readFileSync(path.join(resources, 'app', 'build-identity.generated.json'), 'utf8'));
    const readLog = () => fs.readFileSync(logPath, 'utf8').trim().split('\n').map(line => JSON.parse(line));
    let runtime;
    let lockCalls = 0;
    const checkpoints = [];
    const originalStderrWrite = process.stderr.write;
    process.stderr.write = function captureStartupCheckpoint(chunk, ...args) {
      if (typeof chunk === 'string' && chunk.startsWith('[desktop-startup] ')) {
        const checkpoint = JSON.parse(chunk.slice('[desktop-startup] '.length));
        assert.deepEqual(Object.keys(checkpoint).sort(), ['buildIdentity', 'pid', 'ppid', 'stage']);
        assert.equal(checkpoint.pid, process.pid);
        assert.equal(checkpoint.ppid, process.ppid);
        if (checkpoint.stage === 'main-entry') assert.equal(fs.existsSync(path.join(resources, 'logs')), false, 'the entry probe must not depend on a log directory');
        if (checkpoint.stage === 'lifecycle-log-append-start') assert.equal(fs.existsSync(logPath), false, 'stderr must mark the first append before its file is opened');
        checkpoints.push(checkpoint);
        return true;
      }
      return originalStderrWrite.call(this, chunk, ...args);
    };
    try {
      const load = () => loadMain({ resourcesPath: resources, development: false, autoStart: outcome === 'throw',
        singleInstanceLock() {
          lockCalls++;
          assert.deepEqual(checkpoints.map(item => item.stage), [
            'main-entry', 'electron-require-start', 'electron-require-end',
            'updater-require-start', 'updater-require-end',
            'device-identity-module-require-start', 'device-identity-module-require-end',
            'path-initialization-start', 'path-initialization-end',
            'web-port-resolve-start', 'web-port-resolve-end',
            'build-identity-load-start', 'build-identity-load-end',
            'lifecycle-log-path-start', 'lifecycle-log-path-end',
            'lifecycle-log-directory-start', 'lifecycle-log-directory-end',
            'lifecycle-log-append-start', 'lifecycle-log-append-end', 'single-instance-lock-call-start',
          ]);
          const identityEnd = checkpoints.findIndex(item => item.stage === 'build-identity-load-end');
          assert.equal(checkpoints.slice(0, identityEnd).every(item => item.buildIdentity === null), true, 'unread build metadata must not be presented as a verified candidate');
          assert.equal(checkpoints.slice(identityEnd).every(item => item.buildIdentity === metadata.candidateInputDigest), true);
          assert.doesNotThrow(() => process.stderr.emit('error', Object.assign(new Error('fixture closed sink'), { code: 'EPIPE' })));
          const unknownSinkError = Object.assign(new Error('fixture stderr IO failure'), { code: 'EIO' });
          assert.throws(() => process.stderr.emit('error', unknownSinkError), error => error === unknownSinkError, 'the early diagnostic must not swallow unknown sink errors');
          const rows = readLog();
          assert.equal(rows.length, 1, 'the start checkpoint must persist before entering the native lock');
          assert.deepEqual(Object.keys(rows[0]).sort(), ['buildIdentity', 'event', 'pid', 'ppid', 'timestamp']);
          assert.equal(rows[0].event, 'single-instance-lock-start');
          assert.equal(rows[0].buildIdentity, metadata.candidateInputDigest);
          assert.equal(rows[0].pid, process.pid);
          assert.equal(rows[0].ppid, process.ppid);
          if (outcome === 'throw') throw new Error('fixture lock did not return');
          return outcome;
        },
      });
      runtime = await load();
      await waitFor(() => lockCalls === 1, 'first completed write before lock');
      if (outcome === 'throw') {
        await waitFor(() => runtime.app.exited, 'lock exception ending its instance');
        assert.equal(readLog().length, 1, 'a lock exception must not receive a fabricated end checkpoint');
      } else {
        await waitFor(() => readLog().length === 2, 'lock result persisted');
        if (outcome) runtime.app.quit();
        await waitFor(() => runtime.app.exited, 'checkpoint log closed before exit');
        const rows = readLog();
        assert.deepEqual(rows.map(row => row.event), ['single-instance-lock-start', 'single-instance-lock-end']);
        assert.deepEqual(Object.keys(rows[1]).sort(), ['buildIdentity', 'elapsedMs', 'event', 'gotLock', 'pid', 'ppid', 'timestamp']);
        assert.equal(rows[1].buildIdentity, metadata.candidateInputDigest);
        assert.equal(rows[1].pid, process.pid);
        assert.equal(rows[1].ppid, process.ppid);
        assert.equal(rows[1].gotLock, outcome);
        assert.equal(Number.isFinite(rows[1].elapsedMs) && rows[1].elapsedMs >= 0, true);
        assert.equal(runtime.app.exited, true, 'quit must finish after the ordered log closes');
        assert.equal(runtime.children.length, 0, 'checkpoint probes must not start services');
        assert.equal(runtime.windows.length, 0, 'checkpoint probes must not open windows');
      }
      assert.equal(lockCalls, 1, 'instrumentation must not retry or bypass the lock');
      assert.equal(fs.existsSync(path.join(resources, 'user-data')), false, 'checkpoint probes must not create a profile or identity');
      assert.equal(fs.existsSync(path.join(resources, 'api-server')), false);
      assert.equal(fs.existsSync(path.join(resources, 'web')), false);
    } finally {
      process.stderr.write = originalStderrWrite;
      runtime?.restore();
      fs.rmSync(resources, { recursive: true, force: true });
    }
  }
  console.log('desktop single-instance checkpoints: PASS (3 isolated cases; stderr stages/privacy/closed sink; no app/services/profile)');
}

async function assertAsyncLifecycleLogGate() {
  for (const outcome of ['delayed', 'failed', 'stalled', 'close-failed']) {
    const resources = createResources({ api: false, web: false, logProbe: true });
    const writes = [];
    const stages = [];
    let lockCalls = 0;
    let streamCount = 0;
    let runtime;
    const stream = new EventEmitter();
    stream.writableFinished = false;
    stream.write = (line, callback) => { writes.push({ line, callback, completed: false }); return true; };
    stream.destroy = () => {
      stream.destroyed = true;
      for (const write of writes) {
        if (!write.completed) { write.completed = true; write.callback(Object.assign(new Error('fixture destroyed'), { code: 'ERR_STREAM_DESTROYED' })); }
      }
      setImmediate(() => stream.emit('close'));
    };
    stream.end = () => {
      if (outcome === 'close-failed') stream.emit('error', Object.assign(new Error('fixture close failure'), { code: 'EIO' }));
      else { stream.writableFinished = true; setImmediate(() => stream.emit('close')); }
    };
    const complete = index => { const write = writes[index]; write.completed = true; write.callback(); };
    const timers = [];
    const originalSetTimeout = global.setTimeout;
    const originalStderrWrite = process.stderr.write;
    global.setTimeout = (callback, milliseconds, ...args) => {
      const timer = originalSetTimeout(callback, milliseconds, ...args);
      if (milliseconds === 60_000) timers.push({ timer, callback });
      return timer;
    };
    process.stderr.write = function capture(chunk, ...args) {
      if (typeof chunk === 'string' && chunk.startsWith('[desktop-startup] ')) { stages.push(JSON.parse(chunk.slice('[desktop-startup] '.length)).stage); return true; }
      return originalStderrWrite.call(this, chunk, ...args);
    };
    try {
      runtime = await loadMain({ resourcesPath: resources, development: false,
        autoStart: outcome === 'failed' || outcome === 'stalled',
        singleInstanceLock() { lockCalls++; assert.equal(writes[0].completed, true, 'the native lock must wait for the actual write callback'); return true; },
        lifecycleStreamFactory(logFile, options) {
          streamCount++;
          assert.equal(logFile, path.join(resources, 'logs', 'desktop-runtime.jsonl'));
          assert.deepEqual(options, { flags: 'a', encoding: 'utf8' });
          return stream;
        },
      });
      assert.equal(lockCalls, 0);
      assert.equal(writes.length, 1);
      assert.equal(stages.includes('lifecycle-log-append-end'), false, 'opening or queueing the stream is not a completed write');
      assert.equal(runtime.children.length, 0);
      assert.equal(runtime.handlers.size, 0);
      assert.equal(runtime.windows.length, 0);
      if (outcome === 'failed' || outcome === 'stalled') {
        const lateCallback = writes[0].callback;
        if (outcome === 'failed') { writes[0].completed = true; writes[0].callback(Object.assign(new Error('fixture append denied'), { code: 'EACCES' })); }
        else { assert.equal(timers.length, 1); clearTimeout(timers[0].timer); timers[0].callback(); }
        await waitFor(() => runtime.app.exited, 'explicit log failure ending the instance');
        lateCallback();
        await new Promise(resolve => setImmediate(resolve));
        assert.equal(lockCalls, 0, 'failure or a late success must never bypass the startup gate');
        assert.equal(runtime.app.exitCode, 1);
        assert.equal(runtime.dialogs.length, 1);
        assert.equal(runtime.dialogs[0].title, '日志写入失败');
        assert.match(runtime.dialogs[0].message, /恢复后重新打开/);
        assert.equal(writes.length, 1, 'fault reporting must not write recursively into the failed logger');
        assert.equal(stages.filter(stage => stage === 'lifecycle-log-failed').length, 1);
        assert.deepEqual(stages.filter(stage => [
          'lifecycle-log-dialog-returned', 'lifecycle-log-services-stopped', 'lifecycle-log-exit-call-start',
        ].includes(stage)), [
          'lifecycle-log-dialog-returned', 'lifecycle-log-services-stopped', 'lifecycle-log-exit-call-start',
        ], 'diagnostics distinguish dialog return, completed service cleanup and exit invocation; they do not prove native process death');
      } else {
        complete(0);
        await waitFor(() => lockCalls === 1 && writes.length === 2, 'completed first write admitting the original lock');
        complete(1);
        if (outcome === 'delayed') {
          const before = runtime.mainExports.recordLifecycle('fixture-before');
          const after = runtime.mainExports.recordLifecycle('fixture-after');
          assert.deepEqual(writes.map(write => JSON.parse(write.line).event), ['single-instance-lock-start', 'single-instance-lock-end', 'fixture-before', 'fixture-after']);
          complete(2); complete(3); await Promise.all([before, after]);
          runtime.app.quit();
          await waitFor(() => runtime.app.exited, 'ordered log close before normal exit');
          assert.equal(runtime.app.exitCode, 0);
        } else {
          runtime.app.quit();
          runtime.markReady();
          await waitFor(() => runtime.app.exited, 'close failure ending the instance');
          assert.equal(runtime.app.exitCode, 1);
          assert.equal(runtime.dialogs.length, 1);
          assert.equal(writes.length, 2, 'a close failure must not recursively enqueue a fault log');
          assert.equal(stages.filter(stage => stage === 'lifecycle-log-failed').length, 1);
        }
      }
      assert.equal(streamCount, 1, 'all records must use one ordered append stream');
      assert.equal(runtime.children.length, 0);
      assert.equal(runtime.handlers.size, 0);
      assert.equal(runtime.windows.length, 0);
      assert.equal(fs.existsSync(path.join(resources, 'user-data')), false);
    } finally {
      for (const { timer } of timers) clearTimeout(timer);
      global.setTimeout = originalSetTimeout;
      process.stderr.write = originalStderrWrite;
      runtime?.restore();
      fs.rmSync(resources, { recursive: true, force: true });
    }
  }
  // Verify actual on-disk append order and preservation of an existing log.
  const resources = createResources({ api: false, web: false, logProbe: true });
  const logFile = path.join(resources, 'logs', 'desktop-runtime.jsonl');
  fs.mkdirSync(path.dirname(logFile)); fs.writeFileSync(logFile, '{"event":"existing-record"}\n');
  let runtime;
  let lockCalls = 0;
  try {
    runtime = await loadMain({ resourcesPath: resources, development: false, autoStart: false, singleInstanceLock: () => { lockCalls++; return true; } });
    await waitFor(() => lockCalls === 1, 'real append callback admitting the lock');
    await Promise.all([runtime.mainExports.recordLifecycle('fixture-one'), runtime.mainExports.recordLifecycle('fixture-two')]);
    runtime.app.quit(); await waitFor(() => runtime.app.exited, 'real log closed');
    const rows = fs.readFileSync(logFile, 'utf8').trim().split('\n').map(line => JSON.parse(line));
    assert.deepEqual(rows.map(row => row.event), ['existing-record', 'single-instance-lock-start', 'single-instance-lock-end', 'fixture-one', 'fixture-two']);
    assert.equal(runtime.children.length, 0); assert.equal(runtime.windows.length, 0);
    assert.equal(runtime.app.exitCode, 0);
    assert.equal(fs.existsSync(path.join(resources, 'user-data')), false);
  } finally { runtime?.restore(); fs.rmSync(resources, { recursive: true, force: true }); }
  console.log('desktop async lifecycle log: PASS (5 isolated cases; callback gate/failure/deadline/FIFO/close; no app/services/profile)');
}

async function assertLocalWebPort() {
  let checks = 0;
  const configLocal = { authMode: 'local' };
  const configSupabase = { authMode: 'supabase', supabaseUrl: 'https://fixture.invalid', supabasePublishableKey: 'public-fixture' };
  // No startup promise resolves in these probes: neither 3000 nor any API port
  // is bound, even when verifying the default Supabase callback origin.
  for (const distributionConfig of [undefined, configLocal, configSupabase]) {
    const resources = createResources({ distributionConfig, portProbe: true });
    let runtime;
    try {
      runtime = await loadMain({ resourcesPath: resources, development: false, autoStart: false });
      assert.deepEqual(runtime.mainExports, { webPort: 3000, webOrigin: 'http://localhost:3000' });
      assert.equal(runtime.windows.length, 0);
      checks++;
    } finally {
      runtime?.restore();
      fs.rmSync(resources, { recursive: true, force: true });
    }
  }
  for (const localWebPort of ['1', '32123', '65535']) {
    const resources = createResources({ distributionConfig: configLocal, portProbe: true });
    let runtime;
    try {
      runtime = await loadMain({ resourcesPath: resources, development: false, autoStart: false, userDataPath: path.join(resources, 'profile'), localWebPort });
      assert.equal(runtime.mainExports.webPort, Number(localWebPort));
      assert.equal(runtime.mainExports.webOrigin, `http://localhost:${Number(localWebPort)}`);
      checks++;
    } finally {
      runtime?.restore();
      fs.rmSync(resources, { recursive: true, force: true });
    }
  }
  const refused = [
    ...['', '0', '-1', '65536', '1.5', '3e3', ' 32123', 'abc'].map(localWebPort => ({ localWebPort, expected: /integer from 1 to 65535/ })),
    { localWebPort: '32123', noProfile: true, expected: /requires an explicit absolute --user-data-dir/ },
    { localWebPort: '32123', relativeProfile: true, expected: /--user-data-dir must be an absolute path/ },
    { localWebPort: '32123', distributionConfig: configSupabase, expected: /Supabase callbacks must remain on port 3000/ },
    { localWebPort: '3000', distributionConfig: configSupabase, expected: /Supabase callbacks must remain on port 3000/ },
    { localWebPort: '32123', distributionConfig: undefined, expected: /requires readable local auth build configuration/ },
    { localWebPort: '32123', distributionConfig: null, expected: /only allowed for local auth builds/ },
    { localWebPort: '32123', malformed: true, expected: /requires readable local auth build configuration/ },
    { localWebPort: '32123', development: true, expected: /not development mode/ },
  ];
  for (const test of refused) {
    const distributionConfig = Object.hasOwn(test, 'distributionConfig') ? test.distributionConfig : configLocal;
    const resources = createResources({ distributionConfig });
    try {
      if (test.malformed) fs.writeFileSync(path.join(resources, 'app', 'assets', 'distribution-config.generated.json'), '{invalid');
      await assert.rejects(loadMain({ resourcesPath: resources, development: Boolean(test.development), autoStart: false,
        userDataPath: test.noProfile ? '' : test.relativeProfile ? 'relative-profile' : path.join(resources, 'profile'), localWebPort: test.localWebPort }), test.expected);
      assert.equal(fs.existsSync(path.join(resources, 'profile', 'device-identity.generated.json')), false, 'refused override must not launch an API or create device credentials');
      checks++;
    } finally {
      fs.rmSync(resources, { recursive: true, force: true });
    }
  }

  async function ownPort() {
    const server = net.createServer();
    await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); });
    const port = server.address().port;
    assert.notEqual(port, 3000, 'only disposable, non-3000 ports may be bound');
    await new Promise(resolve => server.close(resolve));
    return port;
  }
  async function stopAndVerify(runtime) {
    const status = await runtime.handlers.get('get-service-status')();
    runtime.app.quit();
    await waitFor(() => runtime.children.every(child => !isAlive(child.pid)), 'owned local-port fixtures exiting');
    for (const port of [status.api.port, status.web.port].filter(Boolean)) {
      assert.notEqual(port, 3000);
      assert.equal(await portIsAvailable(port), true, `owned fixture port ${port} must be released`);
    }
    console.log(`local-web-port cleanup: ${JSON.stringify({ childPids: runtime.children.map(child => child.pid), apiPort: status.api.port, webPort: status.web.port, released: true })}`);
    runtime.restore();
  }

  const port = await ownPort();
  const resources = createResources({ distributionConfig: configLocal });
  const userDataPath = path.join(resources, 'profile');
  let runtime;
  try {
    let identity;
    for (let restart = 0; restart < 2; restart++) {
      runtime = await loadMain({ resourcesPath: resources, development: false, userDataPath, localWebPort: String(port) });
      await waitFor(() => runtime.windows.length === 1, 'isolated local Web fixture startup');
      const status = await runtime.handlers.get('get-service-status')();
      assert.equal(status.web.port, port);
      assert.equal(runtime.windows[0].loadedURL, `http://localhost:${port}`);
      assert.equal(runtime.app.getPath('userData'), userDataPath);
      assert.equal(runtime.app.getPath('sessionData'), userDataPath);
      assert.equal(fs.existsSync(path.join(resources, 'user-data')), false);
      const current = fs.readFileSync(path.join(userDataPath, 'device-identity.generated.json'), 'utf8');
      if (restart) assert.equal(current, identity, 'the same isolated profile must retain its identity');
      else identity = current;
      await stopAndVerify(runtime);
      runtime = null;
      checks++;
    }
  } finally {
    if (runtime) await stopAndVerify(runtime);
    fs.rmSync(resources, { recursive: true, force: true });
  }

  const occupied = net.createServer();
  await new Promise((resolve, reject) => { occupied.once('error', reject); occupied.listen(0, '127.0.0.1', resolve); });
  const occupiedPort = occupied.address().port;
  assert.notEqual(occupiedPort, 3000);
  const failedResources = createResources({ distributionConfig: configLocal });
  let failedRuntime;
  try {
    failedRuntime = await loadMain({ resourcesPath: failedResources, development: false, userDataPath: path.join(failedResources, 'profile'), localWebPort: String(occupiedPort) });
    await waitFor(() => failedRuntime.dialogs.length === 1, 'occupied explicit Web port failure');
    assert.match(failedRuntime.dialogs[0].message, new RegExp(`端口 ${occupiedPort} 已被占用`));
    assert.equal(failedRuntime.windows.length, 0, 'occupied explicit origin must never open a window or use an old service');
    await waitFor(async () => (await failedRuntime.handlers.get('get-service-status')()).api.pid === null, 'API fixture cleanup after occupied Web port');
    assert.equal(occupied.listening, true, 'the port holder must remain untouched');
    const status = await failedRuntime.handlers.get('get-service-status')();
    assert.equal(status.web.state, 'failed');
    assert.equal(status.web.pid, null);
    assert.equal(status.api.state, 'stopped');
    assert.equal(await portIsAvailable(status.api.port), true);
    checks++;
  } finally {
    if (failedRuntime) await stopAndVerify(failedRuntime);
    fs.rmSync(failedResources, { recursive: true, force: true });
    await new Promise(resolve => occupied.close(resolve));
    assert.equal(await portIsAvailable(occupiedPort), true, 'owned occupied-port fixture must release its port');
  }

  const exitPort = await ownPort();
  const exitResources = createResources({ distributionConfig: configLocal });
  writeFixture(path.join(exitResources, 'web', 'server.js'), 'process.exit(86);\n');
  let exitRuntime;
  try {
    exitRuntime = await loadMain({ resourcesPath: exitResources, development: false, userDataPath: path.join(exitResources, 'profile'), localWebPort: String(exitPort) });
    await waitFor(() => exitRuntime.dialogs.length === 1, 'owned Web child startup failure');
    assert.equal(exitRuntime.windows.length, 0, 'failed Web child must not fall back to another origin');
    await waitFor(async () => (await exitRuntime.handlers.get('get-service-status')()).api.pid === null, 'API fixture exit after Web failure');
    const status = await exitRuntime.handlers.get('get-service-status')();
    assert.equal(status.web.port, exitPort);
    assert.equal(status.web.state, 'failed');
    assert.equal(status.web.pid, null);
    assert.equal(await portIsAvailable(exitPort), true);
    checks++;
  } finally {
    if (exitRuntime) await stopAndVerify(exitRuntime);
    fs.rmSync(exitResources, { recursive: true, force: true });
  }
  console.log(`desktop local-web-port: PASS (${checks} checks; no real 3000 binding)`);
}

async function main() {
  await assertAsyncLifecycleLogGate();
  await assertSingleInstanceLockCheckpoints();
  await assertIsolatedUserData();
  await assertBuildIdentityMetadataFailure('missing', 'build identity metadata is missing or unreadable');
  await assertBuildIdentityMetadataFailure('invalid', 'build identity metadata is invalid');
  await assertBuildIdentityMetadataFailure('missing', 'build identity metadata is missing or unreadable', { development: true, packaged: true });
  await assertRuntimeAcceptsCodeUnitIdentityOrder();
  await assertVisibleByConstruction('no-navigation-events');
  await assertVisibleByConstruction('sync-later-events');
  await assertVisibleWindowLifecycleBoundary();
  for (const resource of ['api', 'web']) await assertMissingResource(resource);
  for (const service of ['api', 'web']) await assertRuntimeExit(service);
  await assertNativeRenderedVideoSaveBoundary();
  const normal = await startPackaged();
  await cleanupRuntime(normal);
  await assertDevelopmentExternalServices();
  await assertClosedPipe();
  console.log('desktop lifecycle smoke: PASS');
}

const runner = process.argv.includes('--async-lifecycle-log') ? assertAsyncLifecycleLogGate
  : process.argv.includes('--single-instance-checkpoints') ? assertSingleInstanceLockCheckpoints
  : process.argv.includes('--closed-pipe-probe') ? closedPipeProbe
  : process.argv.includes('--save-boundary') ? assertNativeRenderedVideoSaveBoundary
    : process.argv.includes('--local-web-port') ? assertLocalWebPort
      : process.argv.includes('--isolated-user-data') ? assertIsolatedUserData : main;

runner().catch((error) => {
  console.error('desktop lifecycle smoke: FAIL');
  console.error(error.stack || error.message);
  process.exitCode = 1;
});
