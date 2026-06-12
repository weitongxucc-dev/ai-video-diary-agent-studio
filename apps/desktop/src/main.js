const fs = require("node:fs");
const path = require("node:path");
const {
  app,
  BrowserWindow,
  Menu,
  Notification,
  Tray,
  dialog,
  globalShortcut,
  nativeImage,
  shell,
} = require("electron");
const {
  checkDesktopDependencies,
  createServiceConfig,
  getDirectoryShortcuts,
  shutdownChildProcesses,
  spawnManagedService,
  WEB_PORT,
  waitForHttp,
} = require("./service-manager");

let mainWindow = null;
let tray = null;
let agentStatus = "启动中";
let isQuitting = false;
const childProcesses = [];
let directoryShortcuts = getDirectoryShortcuts();
let mainLogPath = path.join(directoryShortcuts.logs, "main.log");
let serviceReadyNotificationSent = false;

writeMainLog("main process boot");
process.on("uncaughtException", (error) => {
  writeMainLog(`uncaughtException: ${formatError(error)}`);
  showStartupFailure(`桌面主进程异常：${error.message}`);
});
process.on("unhandledRejection", (reason) => {
  writeMainLog(`unhandledRejection: ${formatError(reason)}`);
  showStartupFailure(`桌面主进程异步异常：${formatError(reason)}`);
});

function createWindow() {
  const window = new BrowserWindow({
    backgroundColor: "#f4ecd6",
    height: 820,
    minHeight: 680,
    minWidth: 1120,
    show: false,
    title: "AI视频日记 Agent Studio",
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
    width: 1280,
  });

  installNavigationGuards(window);
  window.once("ready-to-show", () => showMainWindow({ stealFocus: true }));
  window.on("close", (event) => {
    if (!isQuitting) {
      event.preventDefault();
      window.hide();
    }
  });
  window.loadURL(startupDataUrl("正在启动本地 Agent 服务"));
  return window;
}

function startupDataUrl(message) {
  return `data:text/html;charset=utf-8,${encodeURIComponent(`
    <!doctype html>
    <html lang="zh-CN">
      <head>
        <meta charset="utf-8" />
        <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline';" />
        <title>AI视频日记 Agent Studio</title>
        <style>
          body {
            margin: 0;
            display: grid;
            place-items: center;
            min-height: 100vh;
            background: #f4ecd6;
            color: #202020;
            font: 14px/1.6 -apple-system, BlinkMacSystemFont, "PingFang SC", sans-serif;
          }
          main {
            width: min(520px, calc(100vw - 48px));
            border: 2px solid #202020;
            background: #e1dccb;
            padding: 22px;
            box-shadow: 6px 6px 0 #202020;
          }
          h1 { margin: 0 0 10px; font-size: 22px; }
          p { margin: 0; color: #4c4c4c; }
        </style>
      </head>
      <body>
        <main>
          <h1>AI视频日记 Agent Studio</h1>
          <p>${escapeHtml(message)}</p>
        </main>
      </body>
    </html>
  `)}`;
}

async function startServices() {
  writeMainLog("running dependency preflight");
  const dependencyStatus = checkDesktopDependencies();
  if (!dependencyStatus.ready) {
    setAgentStatus("依赖缺失");
    throw createDependencyError(dependencyStatus);
  }

  const serviceConfig = createServiceConfig({
    appPath: process.execPath,
    isPackaged: app.isPackaged,
    resourcesPath: process.resourcesPath,
  });
  directoryShortcuts = getDirectoryShortcuts();
  mainLogPath = path.join(directoryShortcuts.logs, "main.log");
  fs.mkdirSync(serviceConfig.appSupportDir, { recursive: true });
  fs.mkdirSync(serviceConfig.logsDir, { recursive: true });

  for (const service of serviceConfig.services) {
    writeMainLog(`starting ${service.name}: ${service.command} ${service.args.join(" ")}`);
    const child = spawnManagedService(service, console);
    child.on("exit", () => {
      if (!isQuitting) {
        setAgentStatus(`${service.name} 已退出`);
      }
    });
    childProcesses.push(child);
  }

  setAgentStatus("等待 API");
  await waitForHttp(serviceConfig.apiReadinessUrl, 90000, {
    children: childProcesses,
  });
  setAgentStatus("等待 WebView");
  await waitForHttp(serviceConfig.webUrl, 90000, { children: childProcesses });
  setAgentStatus("空闲");
  notifyServiceReady();
  return serviceConfig;
}

function createDependencyError(dependencyStatus) {
  const missingNames = dependencyStatus.missing.map((dependency) => dependency.label).join("、");
  const hints = dependencyStatus.missing
    .map((dependency) => `${dependency.label}：${dependency.installHint}`)
    .join(" ");
  const error = new Error(
    `启动前检查未通过：本机缺少 ${missingNames}。${hints}安装完成后请重启 App。`,
  );
  error.code = "DESKTOP_DEPENDENCIES_MISSING";
  return error;
}

function createTray() {
  const nextTray = new Tray(createTrayImage());
  nextTray.setToolTip(`AI视频日记 Agent Studio · ${agentStatus}`);
  nextTray.setContextMenu(buildTrayMenu());
  nextTray.on("click", () => showMainWindow());
  return nextTray;
}

function createTrayImage() {
  const svg = `
    <svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 18 18">
      <rect x="1" y="1" width="16" height="16" fill="black"/>
      <rect x="4" y="4" width="10" height="10" fill="white"/>
      <rect x="6" y="6" width="6" height="6" fill="black"/>
    </svg>
  `;
  const image = nativeImage.createFromDataURL(
    `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`,
  );
  image.setTemplateImage(true);
  return image;
}

function buildApplicationMenu() {
  return Menu.buildFromTemplate([
    {
      label: "AI视频日记",
      submenu: [
        { enabled: false, label: `Agent 状态：${agentStatus}` },
        { type: "separator" },
        { click: () => showMainWindow({ stealFocus: true }), label: "打开工作台" },
        { click: () => openDirectory(directoryShortcuts.logs), label: "打开日志目录" },
        { type: "separator" },
        { role: "quit", label: "退出并关闭本地服务" },
      ],
    },
    {
      label: "编辑",
      submenu: [
        { role: "undo", label: "撤销" },
        { role: "redo", label: "重做" },
        { type: "separator" },
        { role: "cut", label: "剪切" },
        { role: "copy", label: "复制" },
        { role: "paste", label: "粘贴" },
        { role: "pasteAndMatchStyle", label: "粘贴并匹配样式" },
        { role: "delete", label: "删除" },
        { type: "separator" },
        { role: "selectAll", label: "全选" },
      ],
    },
    {
      label: "目录",
      submenu: directoryMenuItems(),
    },
    {
      label: "窗口",
      submenu: [
        { role: "minimize", label: "最小化" },
        { role: "zoom", label: "缩放" },
        { click: () => showMainWindow({ stealFocus: true }), label: "显示主窗口" },
      ],
    },
  ]);
}

function buildTrayMenu() {
  return Menu.buildFromTemplate([
    { enabled: false, label: `状态：${agentStatus}` },
    { type: "separator" },
    { click: () => showMainWindow({ stealFocus: true }), label: "打开工作台" },
    ...directoryMenuItems(),
    { type: "separator" },
    { role: "quit", label: "退出并关闭本地服务" },
  ]);
}

function directoryMenuItems() {
  return [
    { click: () => openDirectory(directoryShortcuts.logs), label: "日志目录" },
    { click: () => openDirectory(directoryShortcuts.videoInbox), label: "待处理视频目录" },
    { click: () => openDirectory(directoryShortcuts.videoOutput), label: "待发布成片目录" },
    { click: () => openDirectory(directoryShortcuts.draftOutput), label: "草稿输出目录" },
    { click: () => openDirectory(directoryShortcuts.jianyingDraftRoot), label: "剪映草稿目录" },
  ];
}

function setAgentStatus(status) {
  writeMainLog(`status: ${status}`);
  agentStatus = status;
  if (tray) {
    tray.setToolTip(`AI视频日记 Agent Studio · ${agentStatus}`);
    tray.setContextMenu(buildTrayMenu());
  }
  Menu.setApplicationMenu(buildApplicationMenu());
}

function notify(title, body) {
  if (!Notification.isSupported()) {
    return;
  }
  new Notification({ title, body }).show();
}

function notifyServiceReady() {
  if (serviceReadyNotificationSent) {
    return;
  }
  serviceReadyNotificationSent = true;
  notify("AI视频日记 Agent Studio", "本地 API 和 WebView 已就绪，可以继续今天的流程。");
}

function installNavigationGuards(window) {
  window.webContents.setWindowOpenHandler(({ url }) => {
    openExternalIfNeeded(url);
    return { action: "deny" };
  });
  window.webContents.on("will-navigate", (event, url) => {
    if (isTrustedNavigationUrl(url)) {
      return;
    }
    event.preventDefault();
    openExternalIfNeeded(url);
  });
}

function isTrustedNavigationUrl(url) {
  try {
    const parsedUrl = new URL(url);
    return (
      parsedUrl.protocol === "data:"
      || (
        parsedUrl.protocol === "http:"
        && parsedUrl.hostname === "127.0.0.1"
        && parsedUrl.port === WEB_PORT
      )
    );
  } catch {
    return false;
  }
}

function openExternalIfNeeded(url) {
  try {
    const parsedUrl = new URL(url);
    if (
      ["http:", "https:"].includes(parsedUrl.protocol)
      && !isTrustedNavigationUrl(url)
    ) {
      shell.openExternal(url);
    }
  } catch {
    // Invalid URLs are denied by the navigation guard.
  }
}

function showStartupFailure(message) {
  setAgentStatus("启动失败");
  if (!mainWindow) {
    dialog.showErrorBox("AI视频日记 Agent Studio 启动失败", message);
    return;
  }
  mainWindow.loadURL(
    startupDataUrl(`${message}。请打开日志目录查看 main.log、api.log、worker.log 和 web.log。`),
  );
  showMainWindow();
}

function writeMainLog(message) {
  try {
    fs.mkdirSync(path.dirname(mainLogPath), { recursive: true });
    fs.appendFileSync(
      mainLogPath,
      `[${new Date().toISOString()}] ${message}\n`,
      "utf8",
    );
  } catch {
    // Logging must never prevent the app from starting.
  }
}

function formatError(error) {
  if (error instanceof Error) {
    return error.stack || error.message;
  }
  return String(error);
}

function showMainWindow({ stealFocus = false } = {}) {
  if (!mainWindow) {
    return;
  }
  if (mainWindow.isMinimized()) {
    mainWindow.restore();
  }
  mainWindow.show();
  if (stealFocus) {
    app.focus({ steal: true });
  }
  mainWindow.focus();
}

async function openDirectory(directoryPath) {
  fs.mkdirSync(directoryPath, { recursive: true });
  const error = await shell.openPath(directoryPath);
  if (error) {
    dialog.showErrorBox("无法打开目录", `${directoryPath}\n${error}`);
  }
}

function registerGlobalShortcut() {
  globalShortcut.register("CommandOrControl+Shift+Space", () => {
    showMainWindow({ stealFocus: true });
    if (mainWindow) {
      mainWindow.webContents.send("desktop:focus-voice-command");
    }
  });
}

function escapeHtml(value) {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on("second-instance", () => {
    showMainWindow({ stealFocus: true });
  });

  app.on("activate", () => {
    showMainWindow({ stealFocus: true });
  });

  app.on("before-quit", async (event) => {
    if (isQuitting) {
      return;
    }
    event.preventDefault();
    isQuitting = true;
    setAgentStatus("正在退出");
    globalShortcut.unregisterAll();
    await shutdownChildProcesses(childProcesses);
    app.exit(0);
  });

  app.on("window-all-closed", () => {});

  app.whenReady().then(async () => {
    Menu.setApplicationMenu(buildApplicationMenu());
    tray = createTray();
    mainWindow = createWindow();
    registerGlobalShortcut();

    try {
      const serviceConfig = await startServices();
      await mainWindow.loadURL(serviceConfig.webUrl);
      showMainWindow({ stealFocus: true });
    } catch (error) {
      const dependencyMissing = error?.code === "DESKTOP_DEPENDENCIES_MISSING";
      const errorMessage = error instanceof Error ? error.message : String(error);
      setAgentStatus(dependencyMissing ? "依赖缺失" : "启动失败");
      await mainWindow.loadURL(
        startupDataUrl(
          dependencyMissing
            ? errorMessage
            : `本地服务启动失败：${errorMessage}。请打开日志目录查看 main.log、api.log、worker.log 和 web.log。`,
        ),
      );
      showMainWindow({ stealFocus: true });
    }
  });
}
