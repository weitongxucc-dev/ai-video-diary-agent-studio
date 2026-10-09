const { app, BrowserWindow, Menu, dialog, ipcMain, shell } = require('electron');
const path = require('path');
const os = require('os');
const fs = require('fs');
const { spawn } = require('child_process');
const http = require('http');
const crypto = require('crypto');
const { pipeline } = require('stream/promises');
const { autoUpdater } = require('electron-updater');
const { loadOrCreateIdentity } = require('./device-identity');

// Chromium's explicit profile directory must also govern Electron's own
// dotenv, device identity and logs before any of them are read or created.
const explicitUserDataPath = app.commandLine.getSwitchValue('user-data-dir');
if (explicitUserDataPath) {
  if (!path.isAbsolute(explicitUserDataPath)) {
    throw new Error('--user-data-dir must be an absolute path.');
  }
  fs.mkdirSync(explicitUserDataPath, { recursive: true });
  app.setPath('userData', explicitUserDataPath);
  app.setPath('sessionData', explicitUserDataPath);
  app.setAppLogsPath(path.join(explicitUserDataPath, 'logs'));
}

// ── State ──────────────────────────────────────────────
let mainWindow = null;
let apiProcess = null;
let nextProcess = null;
let apiPort = 0;
let nextPort = 0;
let apiStopping = false;
let nextStopping = false;
let quitInProgress = false;
let lifecycleLogFile = null;
const isDev = process.env.NODE_ENV === 'development';
const buildIdentityMetadataFile = path.join(__dirname, 'build-identity.generated.json');
// Supabase invite/recovery links must target a stable origin. The callback runs
// in the user's browser, sets the password, and can then be followed by a fresh
// sign-in inside Electron; a random port makes that link unreachable.
const authWebPort = 3000;
const authWebOrigin = `http://localhost:${authWebPort}`;
const serviceStates = {
  api: { label: '本地 API 服务', state: 'not-started', error: null },
  web: { label: '本地 Web 服务', state: 'not-started', error: null },
};
// A service that accepts the request and never ends the response must not hang
// the save promise forever.
const SUBTITLE_RESPONSE_TIMEOUT_MS = 30000;
// Reading the intake binding is a loopback metadata call; a slow answer is
// treated as a closed import rather than an open-ended wait.
const MATERIAL_BINDING_TIMEOUT_MS = 8000;
// Mirrors apps/api/app/video.py SUPPORTED_VIDEO_EXTENSIONS. Anything else the
// user picks cannot be registered by intake, so it is reported as skipped
// instead of being copied into the monitored directory.
const MATERIAL_IMPORT_EXTENSIONS = new Set(['.mp4', '.mov', '.m4v']);
// `_已处理` is the archive segment apps/api/app/production.py already skips in
// all three scan paths, so staged copies are invisible to intake.
const MATERIAL_STAGING_SEGMENTS = ['_已处理', '.import-staging'];
const MATERIAL_NAME_ATTEMPTS = 200;

function metadataManifestDigest(inputs) {
  return crypto.createHash('sha256').update(JSON.stringify(inputs)).digest('hex');
}

// Keep this locale-independent order identical to generate-build-identity.js.
function compareIdentityPaths(left, right) {
  if (left === right) return 0;
  return left < right ? -1 : 1;
}

function loadBuildIdentity() {
  let metadata;
  try {
    metadata = JSON.parse(fs.readFileSync(buildIdentityMetadataFile, 'utf8'));
  } catch (error) {
    if (isDev && !app.isPackaged && error && error.code === 'ENOENT') return null;
    throw new Error('Desktop package build identity metadata is missing or unreadable; rebuild the app before launching.');
  }
  const inputs = metadata && metadata.inputs;
  const validInputs = Array.isArray(inputs)
    && inputs.length > 0
    && inputs.every((input, index) => (
      input
      && typeof input.path === 'string'
      && typeof input.sha256 === 'string' && /^[a-f0-9]{64}$/.test(input.sha256)
      && Number.isSafeInteger(input.bytes) && input.bytes >= 0
      && (index === 0 || compareIdentityPaths(inputs[index - 1].path, input.path) < 0)
    ));
  if (
    !metadata
    || metadata.format !== 'ai-video-diary-candidate-inputs/v1'
    || typeof metadata.candidateInputDigest !== 'string' || !/^[a-f0-9]{64}$/.test(metadata.candidateInputDigest)
    || typeof metadata.manifestDigest !== 'string' || !/^[a-f0-9]{64}$/.test(metadata.manifestDigest)
    || !validInputs
    || metadata.manifestDigest !== metadataManifestDigest(inputs)
  ) {
    throw new Error('Desktop package build identity metadata is invalid; rebuild the app before launching.');
  }
  return metadata.candidateInputDigest;
}

const buildIdentity = loadBuildIdentity();

function runtimeLogPath() {
  if (lifecycleLogFile) return lifecycleLogFile;
  const directory = app.getPath('logs');
  fs.mkdirSync(directory, { recursive: true });
  lifecycleLogFile = path.join(directory, 'desktop-runtime.jsonl');
  return lifecycleLogFile;
}

function recordLifecycle(event, fields = {}) {
  fs.appendFileSync(runtimeLogPath(), `${JSON.stringify({
    timestamp: new Date().toISOString(),
    event,
    buildIdentity,
    ...fields,
  })}\n`, 'utf8');
}

function isBrokenPipe(error) {
  return error && error.code === 'EPIPE';
}

function stopAfterMainLogFailure(stream, error) {
  recordLifecycle('main-log-stream-failed', {
    stream,
    code: error.code || error.name || 'unknown',
  });
  if (quitInProgress) return;
  quitInProgress = true;
  dialog.showErrorBox(
    '日志输出失败',
    `桌面日志输出发生错误（${error.code || error.name || 'unknown'}）。应用将退出，避免继续显示无法追溯的核心服务状态。`,
  );
  void stopAllServices().finally(() => app.exit(1));
}

function handleMainLogStreamError(stream, error) {
  if (isBrokenPipe(error)) {
    recordLifecycle('main-log-sink-closed', { stream, code: error.code });
    return;
  }
  stopAfterMainLogFailure(stream, error);
}

function installMainLogStreamGuards() {
  process.stdout.on('error', (error) => handleMainLogStreamError('stdout', error));
  process.stderr.on('error', (error) => handleMainLogStreamError('stderr', error));
}

function attachChildOutput(service, stream, prefix, write) {
  stream.on('data', (data) => {
    write(`[${prefix}] ${data.toString().trim()}`);
  });
  stream.on('error', (error) => {
    if (isBrokenPipe(error)) {
      recordLifecycle('child-log-sink-closed', { service, stream: prefix, code: error.code });
      return;
    }
    reportServiceFailure(service, new Error(`${prefix} 日志管道失败（${error.code || error.name || 'unknown'}）`));
  });
}

function setServiceState(name, state, error = null) {
  serviceStates[name].state = state;
  serviceStates[name].error = error ? error.message : null;
  recordLifecycle('service-state', {
    service: name,
    state,
    errorCode: error && (error.code || error.name) ? (error.code || error.name) : null,
  });
  console.log(`[main] ${serviceStates[name].label}: ${state}${error ? ` (${error.message})` : ''}`);
}

function requiredPackagedFile(label, candidates, mode) {
  for (const candidate of candidates) {
    try {
      fs.accessSync(candidate, mode);
      return candidate;
    } catch (_) { /* try the next package location */ }
  }
  throw new Error(`${label} 缺失或不可用。请重新安装应用；不要从未完整解压的安装包中直接运行。`);
}

function reportServiceFailure(name, error) {
  setServiceState(name, 'failed', error);
  recordLifecycle('service-failure', {
    service: name,
    errorCode: error.code || error.name || 'unknown',
  });
  if (quitInProgress || !mainWindow) return;

  dialog.showErrorBox(
    '核心服务已停止',
    `${serviceStates[name].label}意外停止：${error.message}\n\n应用将退出，避免继续显示不可用的核心能力。请重新打开应用；若仍失败，请重新安装。`,
  );
  app.quit();
}

// ── Shared config path ─────────────────────────────────
// The packaged API reads one writable .env under userData (not inside the
// signed Resources bundle).
// Dev builds keep the default repo-root .env resolution.
function userDotenvPath() {
  return path.join(app.getPath('userData'), '.env');
}

function ensureUserDotenv() {
  const p = userDotenvPath();
  fs.mkdirSync(path.join(app.getPath('userData'), 'temporary'), { recursive: true });
  if (!fs.existsSync(p)) {
    fs.mkdirSync(path.dirname(p), { recursive: true });
    fs.writeFileSync(p, '', 'utf-8');
    console.log('[main] Created user config file');
  }
}

function localModelEnv() {
  const resourcePath = process.resourcesPath || path.join(__dirname, '..');
  return {
    PATH: ['/opt/homebrew/bin', '/usr/local/bin', process.env.PATH || '/usr/bin:/bin:/usr/sbin:/sbin'].join(path.delimiter),
    TMPDIR: path.join(app.getPath('userData'), 'temporary'),
    VISUAL_MODEL_WORKER_PATH: path.join(resourcePath, 'model-workers', 'chinese_clip_worker.py'),
    VOICE_MODEL_WORKER_PATH: path.join(resourcePath, 'model-workers', 'qwen3_tts_worker.py'),
    UNDERSTANDING_WORKER_PATH: path.join(resourcePath, 'model-workers', 'qwen3_vl_worker.py'),
    AVATAR_MODEL_WORKER_PATH: path.join(resourcePath, 'model-workers', 'musetalk_worker.py'),
    LATENTSYNC_WORKER_PATH: path.join(resourcePath, 'model-workers', 'latentsync_worker.py'),
    AI_VIDEO_DIARY_PI_EXTENSION: path.join(resourcePath, 'agent-tools', 'diary_tools.ts'),
  };
}

function distributionAuthEnv() {
  const configPath = path.join(__dirname, 'assets', 'distribution-config.generated.json');
  try {
    const config = JSON.parse(fs.readFileSync(configPath, 'utf-8'));
    if (config.authMode !== 'supabase') return {};
    if (!config.supabaseUrl || !config.supabasePublishableKey) {
      throw new Error('Supabase public distribution config is incomplete');
    }
    return {
      SUPABASE_URL: config.supabaseUrl,
      SUPABASE_PUBLISHABLE_KEY: config.supabasePublishableKey,
    };
  } catch (error) {
    if (error && error.code === 'ENOENT') return {};
    throw error;
  }
}

// First launch generates and persists the device identity; a malformed file is
// an operational fault (throws, never regenerated) and will surface as the API's
// 503 unavailable downstream.
function deviceIdentityEnv() {
  const identity = loadOrCreateIdentity(app.getPath('userData'));
  return {
    DEVICE_ID: identity.device_id,
    DEVICE_PROOF: identity.proof,
  };
}

// ── API Process Management ─────────────────────────────
function findApiBinary() {
  if (isDev) return null;
  return requiredPackagedFile('打包的 API 服务', [
    path.join(process.resourcesPath, 'api-server'),
  ], fs.constants.X_OK);
}

function getFreePort() {
  return new Promise((resolve, reject) => {
    const server = require('net').createServer();
    server.listen(0, '127.0.0.1', () => {
      const port = server.address().port;
      server.close(() => resolve(port));
    });
    server.on('error', reject);
  });
}

function ensurePortAvailable(port) {
  return new Promise((resolve, reject) => {
    const server = require('net').createServer();
    server.once('error', () => {
      reject(new Error(`本地登录端口 ${port} 已被占用，请关闭占用该端口的应用后重试`));
    });
    server.listen(port, '127.0.0.1', () => {
      server.close(resolve);
    });
  });
}

async function startApiServer() {
  if (apiProcess) return apiPort;

  setServiceState('api', 'starting');

  if (isDev) {
    apiPort = 8000;
    await waitForApi(apiPort);
    setServiceState('api', 'ready');
    return apiPort;
  }

  const binary = findApiBinary();
  apiPort = await getFreePort();
  console.log(`[main] Starting API server on port ${apiPort}...`);

  const env = {
    ...process.env,
    ...distributionAuthEnv(),
    ...deviceIdentityEnv(),
    ...localModelEnv(),
    API_PORT: String(apiPort),
    API_HOST: '127.0.0.1',
    AI_VIDEO_DIARY_DOTENV: userDotenvPath(),
  };

  apiProcess = spawn(binary, [], {
    env,
    stdio: ['ignore', 'pipe', 'pipe'],
    detached: process.platform !== 'win32',
  });

  apiProcess.on('error', (err) => {
    console.error(`[main] Failed to spawn API server: ${err.message}`);
    apiProcess = null;
    reportServiceFailure('api', new Error(`无法启动：${err.message}`));
  });

  attachChildOutput('api', apiProcess.stdout, 'api', (message) => console.log(message));
  attachChildOutput('api', apiProcess.stderr, 'api:err', (message) => console.error(message));

  apiProcess.on('exit', (code, signal) => {
    console.log(`[main] API server exited (code=${code}, signal=${signal})`);
    const pid = apiProcess ? apiProcess.pid : null;
    recordLifecycle('service-exit', { service: 'api', code, signal, expected: apiStopping });
    apiProcess = null;
    if (!apiStopping) {
      if (pid) terminateExitedServiceGroup(pid);
      reportServiceFailure('api', new Error(`进程已退出（code=${code}, signal=${signal}）`));
    }
  });

  await waitForApi(apiPort);
  setServiceState('api', 'ready');
  return apiPort;
}

function waitForApi(port, maxRetries = 60) {
  return waitForHttpService({
    service: 'api',
    port,
    path: '/api/runtime/health',
    maxRetries,
    ready: (statusCode) => statusCode === 200,
    label: 'API server',
  });
}

function waitForNext(port, maxRetries = 30) {
  return waitForHttpService({
    service: 'web',
    port,
    path: '/',
    maxRetries,
    ready: (statusCode) => statusCode < 500,
    label: 'Next.js server',
  });
}

function waitForHttpService({ service, port, path: requestPath, maxRetries, ready, label }) {
  return new Promise((resolve, reject) => {
    let attempts = 0;
    let settled = false;
    let retryTimer = null;
    let activeRequest = null;

    const finish = (error) => {
      if (settled) return;
      settled = true;
      if (retryTimer) clearTimeout(retryTimer);
      if (activeRequest) activeRequest.destroy();
      if (error) reject(error);
      else resolve();
    };

    const scheduleRetry = () => {
      if (settled || retryTimer) return;
      if (attempts >= maxRetries) {
        finish(new Error(`${label} did not become ready`));
        return;
      }
      retryTimer = setTimeout(() => {
        retryTimer = null;
        check();
      }, 1000);
    };

    const check = () => {
      if (settled) return;
      if (serviceStates[service].state === 'failed') {
        finish(new Error(serviceStates[service].error || `${label} 进程提前退出`));
        return;
      }
      attempts++;
      const req = http.get(`http://127.0.0.1:${port}${requestPath}`, (res) => {
        activeRequest = null;
        res.resume();
        if (settled) return;
        if (ready(res.statusCode)) {
          console.log(`[main] ${label} ready on port ${port}`);
          finish();
          return;
        }
        scheduleRetry();
      });
      activeRequest = req;
      req.once('error', () => {
        activeRequest = null;
        scheduleRetry();
      });
      req.setTimeout(2000, () => {
        req.destroy();
      });
    };
    check();
  });
}

// ── Next.js Server Management ───────────────────────────
function findNextServer() {
  if (isDev) return null;
  return requiredPackagedFile('打包的 Web 服务', [
    path.join(process.resourcesPath, 'web', 'server.js'),
  ], fs.constants.R_OK);
}

async function startNextServer() {
  if (nextProcess) return nextPort;

  setServiceState('web', 'starting');

  if (isDev) {
    nextPort = authWebPort;
    await waitForNext(nextPort);
    setServiceState('web', 'ready');
    return nextPort;
  }

  const serverJs = findNextServer();
  await ensurePortAvailable(authWebPort);
  nextPort = authWebPort;
  console.log(`[main] Starting Next.js server on port ${nextPort}...`);

  const env = {
    ...process.env,
    ...distributionAuthEnv(),
    PORT: String(nextPort),
    HOSTNAME: '127.0.0.1',
    API_PORT: String(apiPort),
    // A packaged app cannot rely on a system Node.js installation. Electron's
    // executable can run the bundled Next server in Node mode instead.
    ELECTRON_RUN_AS_NODE: '1',
  };

  nextProcess = spawn(process.execPath, [serverJs], {
    env,
    stdio: ['ignore', 'pipe', 'pipe'],
    detached: process.platform !== 'win32',
  });

  nextProcess.on('error', (err) => {
    console.error(`[main] Failed to spawn Next.js server: ${err.message}`);
    nextProcess = null;
    reportServiceFailure('web', new Error(`无法启动：${err.message}`));
  });

  attachChildOutput('web', nextProcess.stdout, 'next', (message) => console.log(message));
  attachChildOutput('web', nextProcess.stderr, 'next:err', (message) => console.error(message));

  nextProcess.on('exit', (code, signal) => {
    console.log(`[main] Next.js server exited (code=${code}, signal=${signal})`);
    const pid = nextProcess ? nextProcess.pid : null;
    recordLifecycle('service-exit', { service: 'web', code, signal, expected: nextStopping });
    nextProcess = null;
    if (!nextStopping) {
      if (pid) terminateExitedServiceGroup(pid);
      reportServiceFailure('web', new Error(`进程已退出（code=${code}, signal=${signal}）`));
    }
  });

  await waitForNext(nextPort);
  setServiceState('web', 'ready');
  return nextPort;
}

async function stopService(name, child, setStopping) {
  if (!child) return;
  if (child.exitCode !== null) {
    setServiceState(name, 'stopped');
    return;
  }
  setStopping(true);
  console.log(`[main] Stopping ${serviceStates[name].label} (pid=${child.pid})...`);

  const exited = new Promise((resolve) => child.once('exit', resolve));
  signalService(child, 'SIGTERM');
  let timeoutId;
  await Promise.race([
    exited,
    new Promise((resolve) => { timeoutId = setTimeout(resolve, 5000); }),
  ]);
  clearTimeout(timeoutId);
  if (child.exitCode === null) signalService(child, 'SIGKILL');
  let killTimeoutId;
  await Promise.race([
    exited,
    new Promise((resolve) => { killTimeoutId = setTimeout(resolve, 1000); }),
  ]);
  clearTimeout(killTimeoutId);
  setServiceState(name, 'stopped');
  setStopping(false);
}

function signalService(child, signal) {
  if (process.platform === 'win32') return child.kill(signal);
  return signalServiceGroup(child.pid, signal);
}

function terminateExitedServiceGroup(pid) {
  if (process.platform === 'win32') return;
  signalServiceGroup(pid, 'SIGTERM');
}

function signalServiceGroup(pid, signal) {
  try {
    return process.kill(-pid, signal);
  } catch (error) {
    if (error.code === 'ESRCH') return false;
    throw error;
  }
}

async function stopAllServices() {
  await Promise.all([
    stopService('web', nextProcess, (value) => { nextStopping = value; }),
    stopService('api', apiProcess, (value) => { apiStopping = value; }),
  ]);
}

async function downloadCreationTaskOutput(taskId, temporaryPath, coverId = null) {
  if (!Number.isInteger(apiPort) || apiPort <= 0 || serviceStates.api.state !== 'ready') {
    throw new Error('本地视频服务尚未就绪，请等待服务恢复后重试。');
  }
  const requestPath = coverId
    ? `/api/creation-tasks/${encodeURIComponent(taskId)}/covers/${encodeURIComponent(coverId)}/image`
    : `/api/creation-tasks/${encodeURIComponent(taskId)}/output`;
  const mediaType = coverId ? 'image/png' : 'video/mp4';
  const label = coverId ? 'PNG 封面' : 'MP4';
  await new Promise((resolve, reject) => {
    const request = http.get({
      hostname: '127.0.0.1',
      port: apiPort,
      path: requestPath,
      headers: { Accept: mediaType },
    }, (response) => {
      const contentType = String(response.headers['content-type'] || '');
      if (response.statusCode !== 200 || contentType.split(';')[0].trim() !== mediaType) {
        response.resume();
        reject(new Error(`本地服务没有返回可保存的 ${label}（HTTP ${response.statusCode || 0}）。`));
        return;
      }
      const output = fs.createWriteStream(temporaryPath, { flags: 'wx' });
      pipeline(response, output).then(resolve, reject);
    });
    request.on('error', reject);
  });
  const stats = await fs.promises.stat(temporaryPath);
  if (!stats.isFile() || stats.size <= 0) {
    throw new Error('本地服务返回了空的成片文件。');
  }
}

async function downloadSelectedSubtitles(selection, temporaryPath, timeoutMs = SUBTITLE_RESPONSE_TIMEOUT_MS) {
  if (!Number.isInteger(apiPort) || apiPort <= 0 || serviceStates.api.state !== 'ready') {
    throw new Error('本地视频服务尚未就绪，请等待服务恢复后重试。');
  }
  const params = new URLSearchParams({
    expected_version_id: selection.expectedVersionId,
    mode: selection.mode,
  });
  if (selection.zhTrackId) params.set('zh_track_id', selection.zhTrackId);
  if (selection.enTrackId) params.set('en_track_id', selection.enTrackId);
  const requestPath = `/api/creation-tasks/${encodeURIComponent(selection.taskId)}/output/selected-subtitles?${params}`;
  await new Promise((resolve, reject) => {
    let settled = false;
    const finish = (error) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      if (error) reject(error); else resolve();
    };
    // The API can accept the request and then never end the response. Without a
    // bound the save promise hangs forever and the .partial file stays beside
    // the destination the user chose, so the timeout destroys the request and
    // surfaces a retryable error instead.
    const timer = setTimeout(() => {
      request.destroy(new Error('本地字幕服务长时间没有返回，已取消本次保存，请重试。'));
      finish(new Error('本地字幕服务长时间没有返回，已取消本次保存，请重试。'));
    }, timeoutMs);
    const request = http.get({
      hostname: '127.0.0.1',
      port: apiPort,
      path: requestPath,
      headers: { Accept: 'application/x-subrip' },
    }, (response) => {
      const contentType = String(response.headers['content-type'] || '').split(';')[0].trim();
      if (response.statusCode !== 200 || contentType !== 'application/x-subrip') {
        const chunks = [];
        response.on('data', (chunk) => chunks.push(chunk));
        response.on('end', () => {
          let reason = '';
          try { reason = JSON.parse(Buffer.concat(chunks).toString('utf8')).detail || ''; } catch {}
          finish(new Error(reason || `本地服务没有返回可保存的字幕（HTTP ${response.statusCode || 0}）。`));
        });
        response.on('error', (error) => finish(error));
        return;
      }
      const output = fs.createWriteStream(temporaryPath, { flags: 'wx' });
      pipeline(response, output).then(() => finish(), (error) => finish(error));
    });
    request.on('error', (error) => finish(error));
  });
  const stats = await fs.promises.stat(temporaryPath);
  if (!stats.isFile() || stats.size <= 0) {
    throw new Error('本地服务返回了空的字幕文件。');
  }
}

// ── Material import ─────────────────────────────────────
// The renderer never supplies a destination. The shell reads the same
// controlled binding intake already uses, so an arbitrary path from the web
// layer can never become a write target (PRD §12, architecture §2.1/§7).

class MaterialImportError extends Error {}

function materialImportError(message) {
  return new MaterialImportError(message);
}

function httpMaterialJson(port, requestPath, timeoutMs, payload, identity = {}) {
  return new Promise((resolve, reject) => {
    let settled = false;
    const finish = (error, value) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      if (error) reject(error); else resolve(value);
    };
    const timer = setTimeout(() => {
      const error = materialImportError('本地服务没有及时返回素材接收结果，请重试。');
      request.destroy(error);
      finish(error);
    }, timeoutMs);
    const body = payload === undefined ? undefined : JSON.stringify(payload);
    const request = http.request({
      hostname: '127.0.0.1',
      port,
      path: requestPath,
      method: body === undefined ? 'GET' : 'POST',
      headers: {
        Accept: 'application/json',
        ...(identity.localMemberToken ? { 'X-Local-Member-Token': identity.localMemberToken } : {}),
        ...(identity.authorization ? { Authorization: identity.authorization } : {}),
        ...(body === undefined ? {} : { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(body) }),
      },
    }, (response) => {
      const chunks = [];
      response.on('data', (chunk) => chunks.push(chunk));
      response.on('end', () => finish(null, {
        status: response.statusCode || 0,
        body: Buffer.concat(chunks).toString('utf8'),
      }));
      response.on('error', (error) => finish(error));
    });
    request.on('error', (error) => finish(error));
    request.end(body);
  });
}

async function readMaterialBinding(port, timeoutMs = MATERIAL_BINDING_TIMEOUT_MS, identity = {}) {
  const response = await httpMaterialJson(port, '/api/production/materials/intake', timeoutMs, undefined, identity);
  if (response.status === 401 || response.status === 403) {
    throw materialImportError('本地服务要求登录，桌面端当前没有可用凭据，无法确认素材接入目录。本次未导入，源文件保持不变。');
  }
  if (response.status !== 200) {
    throw materialImportError(`无法确认素材接入目录（HTTP ${response.status}），已取消本次导入。`);
  }
  let binding;
  try {
    binding = JSON.parse(response.body);
  } catch {
    throw materialImportError('本地服务返回的素材接入目录状态无法解析，已取消本次导入。');
  }
  return binding;
}

async function confirmImportedMaterial(port, committedPath, timeoutMs, identity) {
  const stat = await fs.promises.stat(committedPath, { bigint: true });
  const payload = {
    relative_path: path.basename(committedPath),
    expected_revision: {
      size_bytes: stat.size.toString(), mtime_ns: stat.mtimeNs.toString(),
      ctime_ns: stat.ctimeNs.toString(), inode: stat.ino.toString(),
    },
    confirmation_source: 'app_import',
  };
  const response = await httpMaterialJson(
    port, '/api/production/materials/intake/copy-complete', timeoutMs, payload, identity,
  );
  if (response.status !== 200) {
    throw materialImportError(`接收确认失败（HTTP ${response.status}）。请在素材页确认接收。`);
  }
  let result;
  try {
    result = JSON.parse(response.body);
  } catch {
    throw materialImportError('接收确认结果无法解析，请在素材页确认接收。');
  }
  if (result.confirmed !== true) {
    throw materialImportError('服务端未确认接收，请在素材页确认接收。');
  }
}

// The API already resolves disk availability (`directory_state`), including the
// "a leftover /Volumes/<name> directory is not evidence that its disk is
// mounted" rule. The shell re-checks identity and containment so a stale answer
// cannot turn into a write outside the bound directory.
function resolveTrustedMaterialDirectory(binding) {
  if (!binding || binding.bound_to_current_workspace !== true) {
    throw materialImportError('当前工作区还没有接入素材目录，请先接入后再导入。');
  }
  if (binding.directory_state !== 'ready') {
    throw materialImportError(binding.directory_message || '素材目录当前不可用，已取消本次导入。');
  }
  const bound = typeof binding.bound_directory === 'string' ? binding.bound_directory : '';
  const configured = typeof binding.configured_directory === 'string' ? binding.configured_directory : '';
  const directory = bound || configured;
  if (typeof directory !== 'string' || directory.length === 0 || !path.isAbsolute(directory)) {
    throw materialImportError('素材接入目录无效，已取消本次导入。');
  }
  let real;
  try {
    if (fs.lstatSync(directory).isSymbolicLink()) {
      throw materialImportError('素材接入目录是符号链接，已取消本次导入。');
    }
    real = fs.realpathSync(directory);
    if (!fs.statSync(real).isDirectory()) {
      throw materialImportError('素材接入目录已不可用，已取消本次导入。');
    }
    // Every intake scan path in apps/api/app/production.py (scan_library at
    // :2611, discover_material_hash_candidates at :2719, scan_material_ingestion
    // at :3719) skips any path containing the `_已处理` segment. A directory
    // under such a segment is therefore never picked up: files published there
    // would be invisible to intake forever. Refuse at the entry point instead
    // of reporting a successful import that nothing will ever register.
    if (real.split(path.sep).includes('_已处理')) {
      throw materialImportError('素材接入目录位于「_已处理」归档目录内，本地服务不会扫描该目录，导入的素材不会被登记。请把接入目录改到「_已处理」之外再导入。');
    }
    // The API reports the bound directory and the configured directory as two
    // independent strings. Resolving both and comparing the two realpaths is a
    // genuine consistency check that they name one directory. Taking the
    // realpath of a single path and comparing it to itself would be an identity
    // and would prove nothing, so no such comparison is made here.
    if (bound.length > 0 && configured.length > 0) {
      const boundReal = fs.realpathSync(bound);
      const configuredReal = fs.realpathSync(configured);
      if (boundReal !== configuredReal) {
        throw materialImportError('素材接入目录与本地服务的绑定不一致，已取消本次导入。');
      }
    }
  } catch (error) {
    if (error instanceof MaterialImportError) throw error;
    throw materialImportError('素材接入目录已不可用，已取消本次导入。');
  }
  return real;
}

// Only a filesystem that genuinely cannot create hard links may be reported as
// "this disk does not support the required atomic commit". Every other failure
// is a different problem and must be described as such, otherwise a full disk or
// a permission error would be misreported as an unsupported filesystem.
// Codes that name the filesystem as the reason. EPERM is deliberately absent:
// it is what a permission problem looks like too, so it is reported separately
// rather than blamed on the volume.
const MATERIAL_HARD_LINK_UNSUPPORTED = new Set([
  'ENOSYS', 'ENOTSUP', 'EOPNOTSUPP', 'EXDEV',
]);

function isMissingPath(error) {
  return error && error.code === 'ENOENT';
}

// Removing a path this run created must never hide a real failure: ENOENT means
// the path is already gone, anything else is returned to the caller.
async function removeOwnedPath(target) {
  try {
    await fs.promises.unlink(target);
    return { removed: true };
  } catch (error) {
    if (isMissingPath(error)) return { removed: false, alreadyGone: true };
    return {
      removed: false,
      alreadyGone: false,
      code: error.code || 'unknown',
      error: error.message || '临时文件清理失败。',
    };
  }
}

async function assertHardLinkSupported(runDirectory) {
  const probeSource = path.join(runDirectory, '.probe-source');
  const probeTarget = path.join(runDirectory, '.probe-target');
  const cleanup = [];
  let failure = null;
  try {
    try {
      // 'wx' refuses to open an existing path, so the probe can never append to
      // or overwrite a file it does not own.
      await fs.promises.writeFile(probeSource, 'probe', { flag: 'wx' });
    } catch (error) {
      // The run directory was just created, so a failure to write here is a
      // volume or permission problem, not a hard-link limitation.
      throw materialImportError(
        `无法在已接入的素材目录中创建临时文件（${describeFileError(error)}），本次未导入，源文件保持不变。`,
      );
    }
    try {
      await fs.promises.link(probeSource, probeTarget);
    } catch (error) {
      if (MATERIAL_HARD_LINK_UNSUPPORTED.has(error.code)) {
        throw materialImportError('该素材盘的文件系统不支持本导入入口所需的原子提交方式，本次未导入，源文件保持不变。');
      }
      if (error.code === 'EPERM') {
        // EPERM is not evidence about the filesystem. It is what a permission
        // problem looks like just as often as what a link-hostile volume looks
        // like, and the two need different fixes. The message states what was
        // observed and what it might mean, and stops there.
        throw materialImportError('当前目录无法创建硬链接（EPERM，可能是权限或文件系统限制），本次未导入，源文件保持不变。');
      }
      if (error.code === 'ENOENT') {
        throw materialImportError('已接入的素材目录在导入过程中消失，本次未导入，源文件保持不变。');
      }
      throw materialImportError(
        `无法在已接入的素材目录中完成原子提交（${describeFileError(error)}），本次未导入，源文件保持不变。`,
      );
    }
  } catch (error) {
    failure = error;
  } finally {
    cleanup.push(await removeOwnedPath(probeTarget));
    cleanup.push(await removeOwnedPath(probeSource));
  }
  if (failure) throw failure;
  const cleanupFailed = cleanup.filter((entry) => entry.removed === false && !entry.alreadyGone);
  if (cleanupFailed.length > 0) {
    recordLifecycle('material-import-probe-cleanup-failed', {
      codes: cleanupFailed.map((entry) => entry.code).join(','),
    });
  }
  return { supported: true, cleanup };
}

function describeFileError(error) {
  if (!error) return '未知错误';
  const code = error.code || '';
  if (code === 'ENOSPC' || code === 'EDQUOT') return '磁盘空间不足';
  if (code === 'EACCES' || code === 'EPERM') return '没有写入权限';
  if (code === 'EROFS') return '磁盘是只读的';
  if (code === 'ENOENT') return '目录已不存在';
  if (code === 'EIO') return '磁盘读写出错';
  if (code === 'EBUSY') return '磁盘正忙';
  if (code === 'ENAMETOOLONG') return '文件名过长';
  if (code) return `${code}`;
  return error.message || '未知错误';
}

// Reading EOF only means the copy reached the source's current end. It says
// nothing about whether an external writer is still appending, so the source
// must be unchanged across the copy before anything is committed.
async function copyMaterialSourceToStaging(sourcePath, stagingPath) {
  let before;
  try {
    before = await fs.promises.stat(sourcePath, { bigint: true });
  } catch (error) {
    throw materialImportError(`源文件不可读（${describeFileError(error)}），本次未导入。`);
  }
  if (!before.isFile()) {
    throw materialImportError('源文件已不可读，本次未导入。');
  }
  try {
    await pipeline(
      fs.createReadStream(sourcePath, { flags: 'r' }),
      fs.createWriteStream(stagingPath, { flags: 'wx' }),
    );
  } catch (error) {
    throw materialImportError(`复制失败（${describeFileError(error)}），本次未导入，源文件保持不变。`);
  }
  const after = await fs.promises.stat(sourcePath, { bigint: true });
  if (before.size !== after.size || before.mtimeNs !== after.mtimeNs || before.ino !== after.ino) {
    throw materialImportError('源文件在复制期间发生变化，无法确认复制完成，本次未导入，源文件保持不变。');
  }
  const written = (await fs.promises.stat(stagingPath, { bigint: true })).size;
  if (written !== before.size) {
    throw materialImportError('源文件复制不完整，本次未导入，源文件保持不变。');
  }
}

// A hard link publishes the finished inode under the final name in one step,
// so the monitored directory never contains a partially written file under a
// scannable extension. Existence and creation are the same atomic call, so a
// name that is already taken fails instead of overwriting.
async function commitMaterialStaging(stagingPath, targetDirectory, fileName) {
  const extension = path.extname(fileName);
  const stem = fileName.slice(0, fileName.length - extension.length);
  for (let attempt = 0; attempt < MATERIAL_NAME_ATTEMPTS; attempt += 1) {
    const candidate = attempt === 0 ? fileName : `${stem} (${attempt})${extension}`;
    const candidatePath = path.join(targetDirectory, candidate);
    try {
      await fs.promises.link(stagingPath, candidatePath);
    } catch (error) {
      if (error.code === 'EEXIST') continue;
      if (MATERIAL_HARD_LINK_UNSUPPORTED.has(error.code)) {
        throw materialImportError('该素材盘的文件系统不支持本导入入口所需的原子提交方式，本次未导入，源文件保持不变。');
      }
      if (isMissingPath(error)) {
        throw materialImportError('已接入的素材目录在导入过程中消失，本次未导入，源文件保持不变。');
      }
      throw materialImportError(
        `无法在已接入的素材目录中提交文件（${describeFileError(error)}），本次未导入，源文件保持不变。`,
      );
    }
    // The file is already published under its final name. If the staging name
    // cannot be removed the import still succeeded, so the leftover is reported
    // rather than silently dropped and never turned into a failure message.
    const removal = await removeOwnedPath(stagingPath);
    if (removal.removed === false && !removal.alreadyGone) {
      recordLifecycle('material-import-staging-left-behind', { code: removal.code });
    }
    return candidatePath;
  }
  throw materialImportError('目标目录中同名文件过多，本次未导入，源文件保持不变。');
}

// Every segment between the trusted root and the staging root must already be a
// real directory inside the root. A pre-existing `_已处理/.import-staging`
// symlink would otherwise redirect both the write and the cleanup outside the
// authorized directory, so this is checked before any subpath is created, not
// only for the root itself.
async function assertStagingAncestorsContained(trustedRoot, stagingRoot) {
  const relative = path.relative(trustedRoot, stagingRoot);
  if (relative.length === 0 || relative.startsWith('..') || path.isAbsolute(relative)) {
    throw materialImportError('暂存位置不在已接入的素材目录内，已取消本次导入。');
  }
  let current = trustedRoot;
  for (const segment of relative.split(path.sep)) {
    current = path.join(current, segment);
    let entry;
    try {
      entry = await fs.promises.lstat(current);
    } catch (error) {
      // Anything past the first missing segment does not exist yet, so there is
      // nothing left to contain.
      if (isMissingPath(error)) return;
      throw materialImportError(`无法确认暂存位置（${describeFileError(error)}），已取消本次导入。`);
    }
    if (entry.isSymbolicLink()) {
      throw materialImportError('暂存位置存在符号链接，已取消本次导入，未写入任何文件。');
    }
    if (!entry.isDirectory()) {
      throw materialImportError('暂存位置不是目录，已取消本次导入，未写入任何文件。');
    }
    const resolved = await fs.promises.realpath(current);
    if (!isPathInside(trustedRoot, resolved)) {
      throw materialImportError('暂存位置不在已接入的素材目录内，已取消本次导入，未写入任何文件。');
    }
  }
}

function isPathInside(rootPath, candidatePath) {
  const relative = path.relative(rootPath, candidatePath);
  return relative.length > 0
    && !relative.startsWith('..')
    && !path.isAbsolute(relative);
}

async function createRunDirectory(trustedRoot, runId) {
  const stagingRoot = path.join(trustedRoot, ...MATERIAL_STAGING_SEGMENTS);
  await assertStagingAncestorsContained(trustedRoot, stagingRoot);
  const runDirectory = path.join(stagingRoot, runId);
  try {
    await fs.promises.mkdir(runDirectory, { recursive: true });
  } catch (error) {
    throw materialImportError(
      `无法在已接入的素材目录中创建暂存位置（${describeFileError(error)}），本次未导入，源文件保持不变。`,
    );
  }
  // Containment is confirmed against the real path after creation, so a
  // symlinked ancestor that appeared between the check and the mkdir is still
  // caught before any bytes are written into it.
  let resolvedRun;
  try {
    resolvedRun = await fs.promises.realpath(runDirectory);
  } catch (error) {
    throw materialImportError('暂存位置无法确认，已取消本次导入。');
  }
  if (!isPathInside(trustedRoot, resolvedRun)) {
    throw materialImportError('暂存位置不在已接入的素材目录内，已取消本次导入。');
  }
  return runDirectory;
}

// The binding is re-read and re-validated immediately before every commit. A
// long copy or a slow native picker can span a rebind, a volume swap, or a
// directory rename, and writing to the directory chosen at the start would then
// be a write the current binding never authorized.
async function revalidateTargetDirectory(port, bindingTimeoutMs, expected, identity) {
  let current;
  try {
    current = await readMaterialBinding(port, bindingTimeoutMs, identity);
  } catch (error) {
    throw materialImportError(
      `导入过程中无法再次确认素材接入目录（${error.message || '本地服务没有响应'}），已停止导入，源文件保持不变。`,
    );
  }
  let resolved;
  try {
    resolved = resolveTrustedMaterialDirectory(current);
  } catch (error) {
    throw materialImportError(
      `导入过程中素材接入目录已变化（${error.message || '目录状态异常'}），已停止导入，源文件保持不变。`,
    );
  }
  if (resolved !== expected) {
    throw materialImportError('导入过程中素材接入目录发生了变化，已停止导入。请确认目录后再试，源文件保持不变。');
  }
  return resolved;
}

// Only this run's own directory is removed. The shared `.import-staging` parent
// is not owned by this run and is always left in place, so removing it here
// would delete a directory other runs and earlier residue live in.
async function removeEmptyRunDirectory(runDirectory) {
  try {
    await fs.promises.rmdir(runDirectory);
    return { path: runDirectory, removed: true };
  } catch (error) {
    if (isMissingPath(error)) return { path: runDirectory, removed: false, alreadyGone: true };
    if (error.code === 'ENOTEMPTY') {
      // Another path landed in this run's directory; it is left for the next run
      // to find as unknown residue rather than deleted recursively.
      return { path: runDirectory, removed: false, code: 'ENOTEMPTY' };
    }
    return { path: runDirectory, removed: false, code: error.code || 'unknown' };
  }
}

async function importMaterials(options) {
  const { port, pickSources, bindingTimeoutMs = MATERIAL_BINDING_TIMEOUT_MS, identity = {} } = options;
  const binding = await readMaterialBinding(port, bindingTimeoutMs, identity);
  const targetDirectory = resolveTrustedMaterialDirectory(binding);
  const picked = await pickSources();
  if (!Array.isArray(picked) || picked.length === 0) {
    return { canceled: true, directory: targetDirectory, copied: [], skipped: [], failures: [], cleanup: [] };
  }
  const runId = crypto.randomUUID();
  const createdFiles = [];
  let runDirectory = null;
  const copied = [];
  const skipped = [];
  const failures = [];
  const cleanup = [];
  try {
    // The native picker is a modal the user may leave open for minutes, and it
    // is the longest uncontrolled window in this flow: the binding can be
    // rebound or the disk can be replaced while it is up. The directory read at
    // the top of this function is therefore stale the moment the picker returns.
    // Re-read and re-resolve it before anything is written — the run directory,
    // the hard-link probe, and the first staged copy all come after this point.
    // A changed target aborts the import with zero writes; the re-check before
    // each commit covers the rest of the run, not this window.
    await revalidateTargetDirectory(port, bindingTimeoutMs, targetDirectory, identity);
    runDirectory = await createRunDirectory(targetDirectory, runId);
    await assertHardLinkSupported(runDirectory);
    for (const sourcePath of picked) {
      const fileName = path.basename(sourcePath);
      if (!MATERIAL_IMPORT_EXTENSIONS.has(path.extname(fileName).toLowerCase())) {
        skipped.push(fileName);
        continue;
      }
      const stagingPath = path.join(runDirectory, `${runId}-${createdFiles.length}-${fileName}.partial`);
      createdFiles.push(stagingPath);
      let committed;
      try {
        await copyMaterialSourceToStaging(sourcePath, stagingPath);
        await revalidateTargetDirectory(port, bindingTimeoutMs, targetDirectory, identity);
        committed = await commitMaterialStaging(stagingPath, targetDirectory, fileName);
        await confirmImportedMaterial(port, committed, bindingTimeoutMs, identity);
        copied.push({ sourceName: fileName, fileName: path.basename(committed) });
      } catch (error) {
        failures.push({ sourceName: fileName, error: committed
          ? `文件已复制为“${path.basename(committed)}”，但接收确认未保存：${error.message}`
          : error.message || '导入失败。' });
      }
    }
  } finally {
    // Only this run's own paths are removed. Residue from an earlier run or
    // another process is never traversed, matched, or deleted. Removal failures
    // are collected as results instead of being swallowed.
    for (const owned of createdFiles) {
      cleanup.push(await removeOwnedPath(owned));
    }
    if (runDirectory) {
      cleanup.push(await removeEmptyRunDirectory(runDirectory));
    }
    const cleanupFailed = cleanup.filter((entry) => entry.removed === false && !entry.alreadyGone);
    if (cleanupFailed.length > 0) {
      recordLifecycle('material-import-cleanup-incomplete', {
        codes: cleanupFailed.map((entry) => entry.code || 'unknown').join(','),
        count: cleanupFailed.length,
      });
    }
  }
  return {
    canceled: false,
    directory: targetDirectory,
    copied,
    skipped,
    failures,
    cleanup: cleanup.filter((entry) => entry.removed === false && !entry.alreadyGone),
  };
}

async function pickMaterialSources() {
  const result = await dialog.showOpenDialog(mainWindow, {
    properties: ['openFile', 'multiSelections'],
    title: '选择要导入的本地素材',
    filters: [{ name: '视频素材', extensions: ['mp4', 'mov', 'm4v'] }],
  });
  if (result.canceled || result.filePaths.length === 0) return [];
  return result.filePaths;
}

// ── IPC Handlers ───────────────────────────────────────
function registerIpcHandlers() {
  ipcMain.handle('get-api-port', () => {
    return apiPort;
  });

  ipcMain.handle('open-directory', async () => {
    const result = await dialog.showOpenDialog(mainWindow, {
      properties: ['openDirectory'],
      title: '选择目录',
    });
    if (result.canceled || result.filePaths.length === 0) return null;
    return result.filePaths[0];
  });

  ipcMain.handle('open-file', async () => {
    const result = await dialog.showOpenDialog(mainWindow, {
      properties: ['openFile'],
      title: '选择文件',
    });
    if (result.canceled || result.filePaths.length === 0) return null;
    return result.filePaths[0];
  });

  ipcMain.handle('open-wav-file', async () => {
    const result = await dialog.showOpenDialog(mainWindow, {
      properties: ['openFile'],
      title: '选择授权声音参考音频',
      filters: [{ name: 'WAV 音频', extensions: ['wav'] }],
    });
    if (result.canceled || result.filePaths.length === 0) return null;
    return result.filePaths[0];
  });

  ipcMain.handle('save-rendered-video', async (_event, taskId) => {
    if (typeof taskId !== 'string' || !/^task_[a-zA-Z0-9_-]{1,80}$/.test(taskId)) {
      return { saved: false, error: '创作任务标识无效。' };
    }
    const result = await dialog.showSaveDialog(mainWindow, {
      title: '保存初版成片',
      defaultPath: path.join(app.getPath('videos'), `${taskId}.mp4`),
      filters: [{ name: 'MP4 视频', extensions: ['mp4'] }],
    });
    if (result.canceled || !result.filePath) return { saved: false, canceled: true };
    const temporaryPath = `${result.filePath}.partial-${crypto.randomUUID()}`;
    try {
      await downloadCreationTaskOutput(taskId, temporaryPath);
      await fs.promises.rename(temporaryPath, result.filePath);
      recordLifecycle('rendered-video-saved', { taskId, fileName: path.basename(result.filePath) });
      return { saved: true, fileName: path.basename(result.filePath) };
    } catch (error) {
      await fs.promises.unlink(temporaryPath).catch(() => undefined);
      recordLifecycle('rendered-video-save-failed', {
        taskId,
        errorCode: error.code || error.name || 'unknown',
      });
      return { saved: false, error: error.message || '成片保存失败。' };
    }
  });

  ipcMain.handle('save-cover-image', async (_event, taskId, coverId) => {
    if (typeof taskId !== 'string' || !/^task_[a-zA-Z0-9_-]{1,80}$/.test(taskId)
        || typeof coverId !== 'string' || !/^cover_[a-zA-Z0-9_-]{1,80}$/.test(coverId)) {
      return { saved: false, error: '创作任务或封面标识无效。' };
    }
    const result = await dialog.showSaveDialog(mainWindow, {
      title: '保存封面',
      defaultPath: path.join(app.getPath('pictures'), `${taskId}-cover.png`),
      filters: [{ name: 'PNG 图片', extensions: ['png'] }],
    });
    if (result.canceled || !result.filePath) return { saved: false, canceled: true };
    const temporaryPath = `${result.filePath}.partial-${crypto.randomUUID()}`;
    try {
      await downloadCreationTaskOutput(taskId, temporaryPath, coverId);
      await fs.promises.rename(temporaryPath, result.filePath);
      recordLifecycle('cover-image-saved', { taskId, coverId, fileName: path.basename(result.filePath) });
      return { saved: true, fileName: path.basename(result.filePath) };
    } catch (error) {
      await fs.promises.unlink(temporaryPath).catch(() => undefined);
      recordLifecycle('cover-image-save-failed', { taskId, coverId, errorCode: error.code || error.name || 'unknown' });
      return { saved: false, error: error.message || '封面保存失败。' };
    }
  });

  ipcMain.handle('save-subtitle-file', async (_event, selection) => {
    const idPattern = /^[a-zA-Z0-9_-]{1,120}$/;
    const taskPattern = /^task_[a-zA-Z0-9_-]{1,80}$/;
    if (!selection || typeof selection !== 'object'
        || !taskPattern.test(selection.taskId)
        || typeof selection.expectedVersionId !== 'string' || !idPattern.test(selection.expectedVersionId)
        || !['zh', 'en', 'zh-en'].includes(selection.mode)
        || (selection.mode !== 'en' && (typeof selection.zhTrackId !== 'string' || !idPattern.test(selection.zhTrackId)))
        || (selection.mode !== 'zh' && (typeof selection.enTrackId !== 'string' || !idPattern.test(selection.enTrackId)))
        || (selection.mode === 'zh' && selection.enTrackId)
        || (selection.mode === 'en' && selection.zhTrackId)) {
      return { saved: false, error: '字幕保存选择无效，请重新选择语言和当前字幕轨。' };
    }
    const suffix = { zh: 'zh', en: 'en', 'zh-en': 'zh-en' }[selection.mode];
    const result = await dialog.showSaveDialog(mainWindow, {
      title: '保存字幕',
      defaultPath: path.join(app.getPath('videos'), `${selection.taskId}-${suffix}.srt`),
      filters: [{ name: 'SRT 字幕', extensions: ['srt'] }],
    });
    if (result.canceled || !result.filePath) return { saved: false, canceled: true };
    const temporaryPath = `${result.filePath}.partial-${crypto.randomUUID()}`;
    try {
      await downloadSelectedSubtitles(selection, temporaryPath, SUBTITLE_RESPONSE_TIMEOUT_MS);
      await fs.promises.rename(temporaryPath, result.filePath);
      recordLifecycle('subtitle-file-saved', { taskId: selection.taskId, mode: selection.mode, fileName: path.basename(result.filePath) });
      return { saved: true, fileName: path.basename(result.filePath) };
    } catch (error) {
      await fs.promises.unlink(temporaryPath).catch(() => undefined);
      recordLifecycle('subtitle-file-save-failed', { taskId: selection.taskId, mode: selection.mode, errorCode: error.code || error.name || 'unknown' });
      return { saved: false, error: error.message || '字幕保存失败。' };
    }
  });

  // Takes no path argument on purpose: the destination comes from the
  // controlled intake binding, never from the renderer.
  ipcMain.handle('import-materials', async (_event, identity = {}) => {
    if (!Number.isInteger(apiPort) || apiPort <= 0 || serviceStates.api.state !== 'ready') {
      return { canceled: false, copied: [], skipped: [], failures: [], error: '本地视频服务尚未就绪，请等待服务恢复后重试。' };
    }
    try {
      const result = await importMaterials({ port: apiPort, pickSources: pickMaterialSources, identity });
      recordLifecycle('material-import-finished', {
        copied: result.copied.length,
        skipped: result.skipped.length,
        failed: result.failures.length,
        canceled: result.canceled,
        cleanupPending: Array.isArray(result.cleanup) ? result.cleanup.length : 0,
      });
      if (Array.isArray(result.cleanup) && result.cleanup.length > 0) {
        // A run where every file failed leaves cleanup residue too, and that
        // residue is still worth telling the user about — it is not hidden just
        // because nothing was imported. What the wording must not do is claim
        // the import succeeded, which would contradict the failure list beside
        // it, so it states only the residue and how to deal with it.
        result.warning = '部分临时文件未能自动清理，残留文件对素材接入不可见，可稍后重试导入或联系支持。';
      }
      return result;
    } catch (error) {
      recordLifecycle('material-import-failed', { errorCode: error.code || error.name || 'unknown' });
      return {
        canceled: false,
        copied: [],
        skipped: [],
        failures: [],
        error: error.message || '导入失败，源文件保持不变。',
      };
    }
  });

  ipcMain.handle('open-path', async (_event, filePath) => {
    if (typeof filePath !== 'string' || filePath.length === 0) {
      throw new Error('invalid path');
    }
    if (!fs.existsSync(filePath)) {
      throw new Error(`path not found: ${filePath}`);
    }
    return shell.openPath(filePath);
  });

  ipcMain.handle('get-version', () => {
    return app.getVersion();
  });

  ipcMain.handle('quit', () => {
    app.quit();
  });

  ipcMain.handle('check-for-updates', () => {
    checkForUpdates(false);
  });

  ipcMain.handle('get-service-status', () => ({
    api: { ...serviceStates.api, pid: apiProcess ? apiProcess.pid : null, port: apiPort },
    web: { ...serviceStates.web, pid: nextProcess ? nextProcess.pid : null, port: nextPort },
    buildIdentity,
  }));

  ipcMain.handle('get-runtime-info', () => ({ buildIdentity, logFileName: 'desktop-runtime.jsonl' }));
}

// ── Window Management ──────────────────────────────────
function createWindow() {
  const isMac = process.platform === 'darwin';
  const window = new BrowserWindow({
    width: 1360,
    height: 840,
    minWidth: 1024,
    minHeight: 700,
    title: 'AI 视频日记',
    backgroundColor: '#f7f7f5',
    show: true,
    ...(isMac ? {
      titleBarStyle: 'hiddenInset',
      trafficLightPosition: { x: 14, y: 15 },
      vibrancy: 'sidebar',
      visualEffectState: 'active',
    } : {}),
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  mainWindow = window;

  window.on('closed', () => {
    if (mainWindow === window) mainWindow = null;
  });

  if (isDev) {
    window.loadURL(authWebOrigin);
    window.webContents.openDevTools({ mode: 'detach' });
  } else {
    window.loadURL(authWebOrigin);
  }

  // Open external links in browser
  window.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: 'deny' };
  });
}

// ── Menu ───────────────────────────────────────────────
function buildMenu() {
  const isMac = process.platform === 'darwin';

  const template = [
    ...(isMac ? [{
      label: app.name,
      submenu: [
        { role: 'about' },
        { type: 'separator' },
        { role: 'services' },
        { type: 'separator' },
        { role: 'hide' },
        { role: 'hideOthers' },
        { role: 'unhide' },
        { type: 'separator' },
        { role: 'quit' },
      ],
    }] : []),
    {
      label: '文件',
      submenu: [
        {
          label: '打开视频目录',
          click: () => {
            const videoPath = path.join(os.homedir(), 'Movies', 'AI视频日记', '01_待处理视频');
            shell.openPath(videoPath);
          },
        },
        {
          label: '打开知识库',
          click: () => {
            dialog.showOpenDialog(mainWindow, {
              properties: ['openDirectory'],
              title: '选择 Obsidian Vault',
            });
          },
        },
        { type: 'separator' },
        isMac ? { role: 'close' } : { role: 'quit' },
      ],
    },
    {
      label: '编辑',
      submenu: [
        { role: 'undo' },
        { role: 'redo' },
        { type: 'separator' },
        { role: 'cut' },
        { role: 'copy' },
        { role: 'paste' },
        { role: 'selectAll' },
      ],
    },
    {
      label: '视图',
      submenu: [
        { role: 'reload' },
        { role: 'forceReload' },
        { role: 'toggleDevTools' },
        { type: 'separator' },
        { role: 'resetZoom' },
        { role: 'zoomIn' },
        { role: 'zoomOut' },
        { type: 'separator' },
        { role: 'togglefullscreen' },
      ],
    },
    {
      label: '帮助',
      submenu: [
        {
          label: 'AI视频日记 帮助',
          click: () => {
            mainWindow?.webContents.send('open-help');
          },
        },
        { type: 'separator' },
        {
          label: '检查更新...',
          click: () => {
            checkForUpdates(false);
          },
        },
        { type: 'separator' },
        {
          label: '关于 AI视频日记',
          click: () => {
            dialog.showMessageBox(mainWindow, {
              type: 'info',
              title: '关于 AI视频日记 Agent Studio',
              message: `AI视频日记 Agent Studio v${app.getVersion()}`,
              detail: `构建身份：${buildIdentity}\n\n本地优先的 macOS 内容生产 Agent。\n\n将素材、想法和语音指令推进成可验证、可回滚、可导入剪映的完整视频草稿。\n\n© 2026 AI视频日记 Contributors\nMIT License`,
            });
          },
        },
      ],
    },
  ];

  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

// ── App Lifecycle ──────────────────────────────────────
app.whenReady().then(async () => {
  installMainLogStreamGuards();
  recordLifecycle('app-start', { mode: isDev ? 'development' : 'packaged' });
  buildMenu();
  registerIpcHandlers();
  configureAutoUpdater();

  // Check for updates in background only in a packaged app.
  if (app.isPackaged) {
    setTimeout(() => checkForUpdates(true), 10_000);
  }

  // Ensure a writable .env exists under userData before spawning the API.
  if (!isDev) {
    ensureUserDotenv();
  }

  let startupService = 'api';
  try {
    await startApiServer();
    startupService = 'web';
    await startNextServer();
    createWindow();
  } catch (err) {
    const error = err instanceof Error ? err : new Error(String(err));
    if (serviceStates[startupService].state !== 'failed') {
      setServiceState(startupService, 'failed', error);
    }
    console.error(`[main] Failed to start ${serviceStates[startupService].label}:`, error.message);
    dialog.showErrorBox(
      '启动失败',
      `${serviceStates[startupService].label}无法就绪：${error.message}\n\n请重新打开应用；若仍失败，请重新安装。开发模式请按 README 分别启动 API 和 Web 服务。`,
    );
    quitInProgress = true;
    await stopAllServices();
    app.quit();
    return;
  }

  app.on('activate', () => {
    if (mainWindow && !mainWindow.isDestroyed()) {
      if (!mainWindow.isVisible()) mainWindow.show();
      return;
    }
    if (BrowserWindow.getAllWindows().length === 0 && serviceStates.web.state === 'ready') {
      createWindow();
    }
  });
});

app.on('window-all-closed', () => {
  // On macOS the app stays alive with its services (API and Next)
  // so re-activate can create a fresh window instantly.  Services are
  // only torn down on before-quit / quit (actual app termination).
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

app.on('before-quit', (event) => {
  if (quitInProgress) return;
  event.preventDefault();
  quitInProgress = true;
  void stopAllServices().finally(() => app.exit());
});

// ── Auto Updater ──────────────────────────────────────
function configureAutoUpdater() {
  autoUpdater.autoDownload = false;
  autoUpdater.autoInstallOnAppQuit = true;

  autoUpdater.on('checking-for-update', () => {
    console.log('[updater] Checking for updates...');
  });

  autoUpdater.on('update-available', (info) => {
    console.log('[updater] Update available:', info.version);
    dialog.showMessageBox(mainWindow, {
      type: 'info',
      title: '发现新版本',
      message: `新版本 ${info.version} 可用`,
      detail: '是否立即下载更新？',
      buttons: ['下载更新', '稍后提醒'],
      defaultId: 0,
      cancelId: 1,
    }).then(({ response }) => {
      if (response === 0) {
        autoUpdater.downloadUpdate();
      }
    });
  });

  autoUpdater.on('update-not-available', () => {
    console.log('[updater] No updates available.');
  });

  autoUpdater.on('download-progress', (progress) => {
    console.log(`[updater] Download: ${Math.round(progress.percent)}%`);
  });

  autoUpdater.on('update-downloaded', (info) => {
    console.log('[updater] Update downloaded:', info.version);
    dialog.showMessageBox(mainWindow, {
      type: 'info',
      title: '更新已就绪',
      message: `版本 ${info.version} 已下载完成`,
      detail: '重启应用以完成更新，或稍后在菜单中选择"检查更新"再重启。',
      buttons: ['立即重启', '稍后'],
      defaultId: 0,
      cancelId: 1,
    }).then(({ response }) => {
      if (response === 0) {
        autoUpdater.quitAndInstall();
      }
    });
  });

  autoUpdater.on('error', (err) => {
    console.error('[updater] Error:', err.message);
  });
}

function checkForUpdates(silent = true) {
  if (!app.isPackaged || !fs.existsSync(path.join(process.resourcesPath, 'app-update.yml'))) {
    if (!silent) {
      dialog.showMessageBox(mainWindow, {
        type: 'info',
        title: '本地测试版本',
        message: '此版本没有配置自动更新。',
        detail: '请使用新的测试包手动更新应用。',
      });
    }
    return;
  }
  autoUpdater.checkForUpdates().catch((err) => {
    if (!silent) {
      dialog.showErrorBox(
        '检查更新失败',
        `无法检查更新：${err.message}`
      );
    }
  });
}

// Prevent multiple instances
const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
} else {
  app.on('second-instance', () => {
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.focus();
    }
  });
}
