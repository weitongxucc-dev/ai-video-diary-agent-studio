const { app, BrowserWindow, Menu, dialog, ipcMain, shell } = require('electron');
const path = require('path');
const os = require('os');
const fs = require('fs');
const { spawn } = require('child_process');
const http = require('http');
const { autoUpdater } = require('electron-updater');

// ── State ──────────────────────────────────────────────
let mainWindow = null;
let apiProcess = null;
let nextProcess = null;
let workerProcess = null;
let apiPort = 0;
let nextPort = 0;
let apiStopping = false;
let nextStopping = false;
let workerStopping = false;
const isDev = process.env.NODE_ENV === 'development';

// ── API Process Management ─────────────────────────────
function findApiBinary() {
  if (isDev) return null; // dev mode: user runs API separately

  // Production: look in extraResources
  const resourcePath = process.resourcesPath || path.join(__dirname, '..');
  const candidates = [
    path.join(resourcePath, 'api-server'),
    path.join(__dirname, 'dist', 'api-server'),
  ];
  for (const c of candidates) {
    try {
      fs.accessSync(c, fs.constants.X_OK);
      return c;
    } catch (_) { /* continue */ }
  }
  return null;
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

async function startApiServer() {
  const binary = findApiBinary();
  if (!binary) {
    console.log('[main] API server binary not found — assuming external API');
    return null;
  }

  apiPort = await getFreePort();
  console.log(`[main] Starting API server on port ${apiPort}...`);

  const env = {
    ...process.env,
    PORT: String(apiPort),
    HOST: '127.0.0.1',
  };

  apiProcess = spawn(binary, [], {
    env,
    stdio: ['ignore', 'pipe', 'pipe'],
  });

  apiProcess.on('error', (err) => {
    console.error(`[main] Failed to spawn API server: ${err.message}`);
    apiProcess = null;
  });

  apiProcess.stdout.on('data', (data) => {
    console.log(`[api] ${data.toString().trim()}`);
  });

  apiProcess.stderr.on('data', (data) => {
    console.error(`[api:err] ${data.toString().trim()}`);
  });

  apiProcess.on('exit', (code, signal) => {
    console.log(`[main] API server exited (code=${code}, signal=${signal})`);
    apiProcess = null;
  });

  // Wait for API to be ready
  await waitForApi(apiPort);
  return apiPort;
}

function waitForApi(port, maxRetries = 30) {
  return new Promise((resolve, reject) => {
    let attempts = 0;
    const check = () => {
      attempts++;
      const req = http.get(`http://127.0.0.1:${port}/api/health`, (res) => {
        if (res.statusCode === 200) {
          console.log(`[main] API server ready on port ${port}`);
          resolve();
        } else if (attempts < maxRetries) {
          setTimeout(check, 1000);
        } else {
          reject(new Error('API server did not become ready'));
        }
      });
      req.on('error', () => {
        if (attempts < maxRetries) {
          setTimeout(check, 1000);
        } else {
          reject(new Error('API server did not become ready'));
        }
      });
      req.setTimeout(2000, () => {
        req.destroy();
        if (attempts < maxRetries) {
          setTimeout(check, 1000);
        } else {
          reject(new Error('API server did not become ready'));
        }
      });
    };
    check();
  });
}

function stopApiServer() {
  if (!apiProcess || apiStopping) return;
  apiStopping = true;
  console.log('[main] Stopping API server...');
  apiProcess.kill('SIGTERM');
  const pid = apiProcess.pid;
  setTimeout(() => {
    try {
      process.kill(pid, 'SIGKILL');
    } catch (_) { /* already dead */ }
    apiStopping = false;
  }, 5000);
}

// ── Next.js Server Management ───────────────────────────
function findNextServer() {
  if (isDev) return null;

  const resourcePath = process.resourcesPath || path.join(__dirname, '..');
  const candidate = path.join(resourcePath, 'web', 'server.js');
  try {
    fs.accessSync(candidate, fs.constants.R_OK);
    return candidate;
  } catch (_) {
    return null;
  }
}

async function startNextServer() {
  const serverJs = findNextServer();
  if (!serverJs) {
    console.log('[main] Next.js standalone server not found — assuming external Next.js');
    return null;
  }

  nextPort = await getFreePort();
  console.log(`[main] Starting Next.js server on port ${nextPort}...`);

  const env = {
    ...process.env,
    PORT: String(nextPort),
    HOSTNAME: '127.0.0.1',
    API_PORT: String(apiPort),
  };

  nextProcess = spawn('node', [serverJs], {
    env,
    stdio: ['ignore', 'pipe', 'pipe'],
  });

  nextProcess.on('error', (err) => {
    console.error(`[main] Failed to spawn Next.js server: ${err.message}`);
    nextProcess = null;
  });

  nextProcess.stdout.on('data', (data) => {
    console.log(`[next] ${data.toString().trim()}`);
  });

  nextProcess.stderr.on('data', (data) => {
    console.error(`[next:err] ${data.toString().trim()}`);
  });

  nextProcess.on('exit', (code, signal) => {
    console.log(`[main] Next.js server exited (code=${code}, signal=${signal})`);
    nextProcess = null;
  });

  // Wait for Next.js to be ready
  await waitForNext(nextPort);
  return nextPort;
}

function waitForNext(port, maxRetries = 30) {
  return new Promise((resolve, reject) => {
    let attempts = 0;
    const check = () => {
      attempts++;
      const req = http.get(`http://127.0.0.1:${port}`, (res) => {
        if (res.statusCode < 500) {
          console.log(`[main] Next.js server ready on port ${port}`);
          resolve();
        } else if (attempts < maxRetries) {
          setTimeout(check, 1000);
        } else {
          reject(new Error('Next.js server did not become ready'));
        }
      });
      req.on('error', () => {
        if (attempts < maxRetries) {
          setTimeout(check, 1000);
        } else {
          reject(new Error('Next.js server did not become ready'));
        }
      });
      req.setTimeout(2000, () => {
        req.destroy();
        if (attempts < maxRetries) {
          setTimeout(check, 1000);
        } else {
          reject(new Error('Next.js server did not become ready'));
        }
      });
    };
    check();
  });
}

function stopNextServer() {
  if (!nextProcess || nextStopping) return;
  nextStopping = true;
  console.log('[main] Stopping Next.js server...');
  nextProcess.kill('SIGTERM');
  const pid = nextProcess.pid;
  setTimeout(() => {
    try {
      process.kill(pid, 'SIGKILL');
    } catch (_) { /* already dead */ }
    nextStopping = false;
  }, 5000);
}

// ── Worker Process Management ──────────────────────────
function findWorkerBinary() {
  if (isDev) return null; // dev mode: user runs worker separately

  const resourcePath = process.resourcesPath || path.join(__dirname, '..');
  const candidates = [
    path.join(resourcePath, 'worker-server'),
    path.join(__dirname, 'dist', 'worker-server'),
  ];
  for (const c of candidates) {
    try {
      fs.accessSync(c, fs.constants.X_OK);
      return c;
    } catch (_) { /* continue */ }
  }
  return null;
}

function startWorker() {
  const binary = findWorkerBinary();
  if (!binary) {
    console.log('[main] Worker binary not found — automation disabled');
    return;
  }

  // Duplicate guard: prevent two workers
  if (workerProcess) {
    console.log('[main] Worker already running (pid=%d) — skipping', workerProcess.pid);
    return;
  }

  console.log('[main] Starting automation worker...');

  const env = {
    ...process.env,
    API_PORT: String(apiPort),
    HOST: '127.0.0.1',
  };

  workerProcess = spawn(binary, [], {
    env,
    stdio: ['ignore', 'pipe', 'pipe'],
  });

  workerProcess.on('error', (err) => {
    console.error('[main] Failed to spawn worker: %s', err.message);
    workerProcess = null;
  });

  workerProcess.stdout.on('data', (data) => {
    console.log('[worker] %s', data.toString().trim());
  });

  workerProcess.stderr.on('data', (data) => {
    console.error('[worker:err] %s', data.toString().trim());
  });

  // No hidden auto-restart — surface failure in logs, let user decide
  workerProcess.on('exit', (code, signal) => {
    console.log('[main] Worker exited (code=%d, signal=%s) — will not auto-restart', code, signal);
    workerProcess = null;
  });
}

function stopWorker() {
  if (!workerProcess || workerStopping) return;
  workerStopping = true;
  console.log('[main] Stopping worker (pid=%d)...', workerProcess.pid);
  workerProcess.kill('SIGTERM');
  const pid = workerProcess.pid;
  setTimeout(() => {
    try {
      process.kill(pid, 'SIGKILL');
    } catch (_) { /* already dead */ }
    workerStopping = false;
  }, 5000);
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

  ipcMain.handle('get-worker-status', () => {
    return {
      running: workerProcess !== null && !workerStopping,
      pid: workerProcess ? workerProcess.pid : null,
    };
  });
}

// ── Window Management ──────────────────────────────────
function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 800,
    minWidth: 900,
    minHeight: 600,
    title: 'AI视频日记 Agent Studio',
    backgroundColor: '#c0c0c0',
    show: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  // Load content
  if (isDev) {
    mainWindow.loadURL('http://localhost:3000');
    mainWindow.webContents.openDevTools({ mode: 'detach' });
  } else {
    // Production: load from Next.js standalone server
    if (nextPort) {
      mainWindow.loadURL(`http://127.0.0.1:${nextPort}`);
    } else {
      mainWindow.loadURL(`http://127.0.0.1:${apiPort}`);
    }
  }

  mainWindow.once('ready-to-show', () => {
    mainWindow.show();
  });

  mainWindow.on('closed', () => {
    mainWindow = null;
  });

  // Open external links in browser
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
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
              detail: '本地优先的 macOS 内容生产 Agent。\n\n将素材、想法和语音指令推进成可验证、可回滚、可导入剪映的完整视频草稿。\n\n© 2026 AI视频日记 Contributors\nMIT License',
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
  buildMenu();
  registerIpcHandlers();
  configureAutoUpdater();

  // Check for updates in background 10s after startup
  setTimeout(() => checkForUpdates(true), 10_000);

  let apiReady = true;
  try {
    await startApiServer();
  } catch (err) {
    apiReady = false;
    console.error('[main] Failed to start API server:', err.message);
    dialog.showErrorBox(
      '启动失败',
      `无法启动本地 API 服务：${err.message}\n\n请确认：\n1. api-server 可执行文件存在\n2. .env 配置文件已正确设置`
    );
  }

  if (apiReady) {
    // Start Next.js server after API is ready (middleware proxies /api to API)
    try {
      await startNextServer();
    } catch (err) {
      console.error('[main] Failed to start Next.js server:', err.message);
    }

    // Start automation worker after API is ready (non-blocking —
    // worker failures are logged but do not prevent the main window)
    startWorker();

    createWindow();
  }

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0 && apiReady) {
      createWindow();
    }
  });
});

app.on('window-all-closed', () => {
  stopWorker();
  stopNextServer();
  stopApiServer();
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

app.on('before-quit', () => {
  stopWorker();
  stopNextServer();
  stopApiServer();
});

app.on('quit', () => {
  stopWorker();
  stopNextServer();
  stopApiServer();
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
  if (!app.isPackaged) {
    if (!silent) {
      dialog.showMessageBox(mainWindow, {
        type: 'info',
        title: '未打包环境',
        message: '当前为未打包环境，不检查更新。',
        detail: '更新检查仅在打包版本中生效。',
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
