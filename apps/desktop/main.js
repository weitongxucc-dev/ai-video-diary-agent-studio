// Isolated frame workers do not start studio services, identity or UI lifecycle.
if (process.argv.includes('--yaohe-template-renderer')) {
  require(process.defaultApp ? './runtime/template-renderer.js'
    : require('path').join(process.resourcesPath, 'template-renderer/template-renderer.js'));
  return;
}
// Startup probes use the already-open stderr descriptor, never a log file.
// The candidate is unknown until the existing build-identity validation finishes.
let startupBuildIdentity = null;
function handleStartupStderrError(error) {
  if (error.code !== 'EPIPE') throw error;
}
process.stderr.on('error', handleStartupStderrError);
function startupCheckpoint(stage) {
  process.stderr.write(`[desktop-startup] ${JSON.stringify({
    stage, buildIdentity: startupBuildIdentity, pid: process.pid, ppid: process.ppid,
  })}\n`);
}
startupCheckpoint('main-entry');
startupCheckpoint('electron-require-start');
const { app, BrowserWindow, Menu, dialog, ipcMain, shell, utilityProcess } = require('electron');
startupCheckpoint('electron-require-end');
const path = require('path');
const fs = require('fs');
const { spawn } = require('child_process');
startupCheckpoint('updater-require-start');
const { autoUpdater } = require('electron-updater');
startupCheckpoint('updater-require-end');
startupCheckpoint('device-identity-module-require-start');
const { createServiceConfiguration, resolveWebPort } = require('./runtime/configuration');
startupCheckpoint('device-identity-module-require-end');

// Chromium's explicit profile directory must also govern Electron's own
// dotenv, device identity and logs before any of them are read or created.
startupCheckpoint('path-initialization-start');
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

startupCheckpoint('path-initialization-end');

const { loadBuildIdentity } = require('./runtime/build-identity');
const { createLifecycleLog } = require('./runtime/lifecycle-log');
const { createOutputLogging } = require('./runtime/output-logging');
const { createServiceSupervisor } = require('./runtime/service-supervisor');
const { createMaterialImporter } = require('./runtime/material-import');
const { createNativeFiles } = require('./runtime/native-files');
const { createNativeMedia } = require('./runtime/native-media');
const { registerDesktopIpc } = require('./runtime/ipc-handlers');
const { createDesktopWindow } = require('./runtime/desktop-window');
const { buildMenu } = require('./runtime/menu');
const { createAutoUpdater } = require('./runtime/auto-updater');

// main owns application/window lifetime; modules own their own narrower state.
let mainWindow = null;
let quitInProgress = false;
const isDev = process.env.NODE_ENV === 'development';
startupCheckpoint('web-port-resolve-start');
const webPort = resolveWebPort({
  hasLocalWebPort: app.commandLine.hasSwitch('local-web-port'),
  localWebPort: app.commandLine.getSwitchValue('local-web-port'),
  explicitUserDataPath, isDev, desktopDirectory: __dirname,
});
startupCheckpoint('web-port-resolve-end');
const webOrigin = `http://localhost:${webPort}`;
startupCheckpoint('build-identity-load-start');
const buildIdentity = loadBuildIdentity({
  metadataFile: path.join(__dirname, 'build-identity.generated.json'), isDev, isPackaged: app.isPackaged,
});
startupBuildIdentity = buildIdentity;
startupCheckpoint('build-identity-load-end');

const { recordLifecycle, closeLifecycleLog } = createLifecycleLog({
  getLogsDirectory: () => app.getPath('logs'), buildIdentity, startupCheckpoint,
  onFailure: handleLifecycleLogFailure,
});
const { installMainLogStreamGuards, attachChildOutput } = createOutputLogging({
  stdout: process.stdout, stderr: process.stderr,
  recordLifecycle, onMainFailure: stopAfterMainLogFailure,
  onServiceFailure: (name, error) => services.reportServiceFailure(name, error),
  handleStartupStderrError,
});
const serviceConfiguration = createServiceConfiguration({
  userDataPath: app.getPath('userData'), resourcesPath: process.resourcesPath,
  desktopDirectory: __dirname, environment: process.env,
});
const services = createServiceSupervisor({
  isDev, webPort, resourcesPath: process.resourcesPath,
  apiEnvironment: serviceConfiguration.apiEnvironment, webEnvironment: serviceConfiguration.webEnvironment,
  spawnApi: spawn, forkWeb: (...args) => utilityProcess.fork(...args),
  recordLifecycle, attachChildOutput, onFailure: reportServiceFailure,
});
const { createCompanionGateway } = require('./runtime/companion-gateway');
const { loadOrCreateIdentity } = require('./device-identity');
const companion = createCompanionGateway({
  userDataPath: app.getPath('userData'), getApiEndpoint: services.getApiEndpoint,
  getDeviceId: () => loadOrCreateIdentity(app.getPath('userData')).device_id,
  getAuthConfig: () => {
    const env = serviceConfiguration.apiEnvironment(0);
    return { supabase_url: env.SUPABASE_URL, publishable_key: env.SUPABASE_PUBLISHABLE_KEY };
  },
});
function stopAllServices() {
  return services.stopAllServices({ stopCompanion: () => companion.stop() });
}
const getWindow = () => mainWindow;
const files = createNativeFiles({ dialog, shell, getWindow });
const { importMaterials } = createMaterialImporter({ recordLifecycle });
const media = createNativeMedia({
  showSaveDialog: (options) => dialog.showSaveDialog(getWindow(), options),
  getWindow, webOrigin,
  getMediaDirectory: (name) => app.getPath(name), getApiEndpoint: services.getApiEndpoint,
  importMaterials, pickMaterialSources: files.pickMaterialSources, recordLifecycle,
});
const { configureAutoUpdater, checkForUpdates } = createAutoUpdater({
  autoUpdater, dialog, getWindow, isPackaged: app.isPackaged, resourcesPath: process.resourcesPath,
});

function showLifecycleLogFailure(error) {
  dialog.showErrorBox(
    '日志写入失败',
    `运行日志未能完成写入（${error.code || error.name || 'unknown'}）。应用将结束本实例，不继续制作。\n\n请检查外接数据盘连接与日志目录访问，恢复后重新打开应用。不会自动修改文件权限或用户数据。`,
  );
}

function handleLifecycleLogFailure(error) {
  if (quitInProgress) return;
  quitInProgress = true;
  void app.whenReady().then(async () => {
    showLifecycleLogFailure(error);
    startupCheckpoint('lifecycle-log-dialog-returned');
    await stopAllServices();
    startupCheckpoint('lifecycle-log-services-stopped');
  }).finally(() => {
    startupCheckpoint('lifecycle-log-exit-call-start');
    app.exit(1);
  });
}

async function exitAfterLifecycleLog(code = 0) {
  try { await closeLifecycleLog(); }
  catch (error) {
    handleLifecycleLogFailure(error);
    await app.whenReady();
    showLifecycleLogFailure(error);
    code = 1;
  }
  app.exit(code);
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
  void stopAllServices().finally(() => exitAfterLifecycleLog(1));
}

function reportServiceFailure(name, error, label) {
  if (quitInProgress || !mainWindow) return;

  dialog.showErrorBox(
    '核心服务已停止',
    `${label}意外停止：${error.message}\n\n应用将退出，避免继续显示不可用的核心能力。请重新打开应用；若仍失败，请重新安装。`,
  );
  app.quit();
}

function createWindow() {
  createDesktopWindow({
    BrowserWindow, ipcMain, shell, preloadPath: path.join(__dirname, 'preload.js'), webOrigin, isDev,
    onCreated: (window) => { mainWindow = window; },
    onClosed: (window) => { if (mainWindow === window) mainWindow = null; },
  });
}

async function startReadyServices() {
  installMainLogStreamGuards();
  await recordLifecycle('app-start', { mode: isDev ? 'development' : 'packaged' });
  if (quitInProgress) return;
  buildMenu({ app, Menu, dialog, shell, getWindow, checkForUpdates, buildIdentity });
  registerDesktopIpc({ ipcMain, services, files, media, buildIdentity,
    getVersion: () => app.getVersion(), quit: () => app.quit(), checkForUpdates, companion, getWindow, webOrigin,
  });
  configureAutoUpdater();

  // Check for updates in background only in a packaged app.
  if (app.isPackaged) {
    setTimeout(() => checkForUpdates(true), 10_000);
  }

  // Ensure a writable .env exists under userData before spawning the API.
  if (!isDev) {
    serviceConfiguration.ensureUserDotenv();
  }

  let startupService = 'api';
  try {
    await services.startApiServer();
    startupService = 'web';
    await services.startNextServer();
    try { await companion.restore(); }
    catch (error) { dialog.showErrorBox('手机连接未恢复', error.message); }
    createWindow();
  } catch (err) {
    const error = err instanceof Error ? err : new Error(String(err));
    if (services.getServiceStatus()[startupService].state !== 'failed') {
      services.setServiceState(startupService, 'failed', error);
    }
    console.error(`[main] Failed to start ${services.getServiceStatus()[startupService].label}:`, error.message);
    dialog.showErrorBox(
      '启动失败',
      `${services.getServiceStatus()[startupService].label}无法就绪：${error.message}\n\n请重新打开应用；若仍失败，请重新安装。开发模式请按 README 分别启动 API 和 Web 服务。`,
    );
    quitInProgress = true;
    await stopAllServices();
    await exitAfterLifecycleLog(1);
    return;
  }

  app.on('activate', () => {
    if (mainWindow && !mainWindow.isDestroyed()) {
      if (!mainWindow.isVisible()) mainWindow.show();
      return;
    }
    if (BrowserWindow.getAllWindows().length === 0 && services.getServiceStatus().web.state === 'ready') {
      createWindow();
    }
  });
}

app.on('window-all-closed', () => {
  // On macOS the app stays alive with its services (API and Next)
  // so re-activate can create a fresh window instantly.  Services are
  // only torn down on before-quit / quit (actual app termination).
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

app.on('before-quit', (event) => {
  event.preventDefault();
  if (quitInProgress) return;
  quitInProgress = true;
  void stopAllServices().then(
    result => exitAfterLifecycleLog(result.clean ? 0 : 1),
    error => {
      recordLifecycle('desktop-shutdown-failed', { errorCode: error.code || 'unknown' });
      return exitAfterLifecycleLog(1);
    },
  );
});

// The first completed file write gates the original lock and all services.
async function startDesktop() {
  try {
    await recordLifecycle('single-instance-lock-start', { pid: process.pid, ppid: process.ppid });
  } catch (error) { handleLifecycleLogFailure(error); return; }
  if (quitInProgress) return;
  startupCheckpoint('single-instance-lock-call-start');
  const singleInstanceLockStartedAt = process.hrtime.bigint();
  const gotLock = app.requestSingleInstanceLock();
  startupCheckpoint('single-instance-lock-call-end');
  recordLifecycle('single-instance-lock-end', {
    pid: process.pid, ppid: process.ppid, gotLock,
    elapsedMs: Number(process.hrtime.bigint() - singleInstanceLockStartedAt) / 1_000_000,
  });
  if (!gotLock) { app.quit(); return; }
  app.on('second-instance', () => {
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.focus();
    }
  });
  await app.whenReady();
  if (!quitInProgress) await startReadyServices();
}
void startDesktop().catch(error => {
  // Preserve a visible, explicit failure for lock/initialization exceptions.
  startupCheckpoint('desktop-startup-failed');
  if (quitInProgress) return;
  quitInProgress = true;
  void app.whenReady().then(() => {
    dialog.showErrorBox('启动失败', `桌面初始化未完成（${error.code || error.name || 'unknown'}），请查看本次启动诊断并在恢复后重新打开应用。`);
    return stopAllServices();
  }).finally(() => exitAfterLifecycleLog(1));
});
