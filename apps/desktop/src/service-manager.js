const fs = require("node:fs");
const http = require("node:http");
const https = require("node:https");
const os = require("node:os");
const path = require("node:path");
const { spawn } = require("node:child_process");
const {
  checkDesktopDependencies,
  commandSearchPaths,
} = require("./desktop-dependencies");

const API_PORT = "8000";
const WEB_PORT = "3000";
const PYTHON_BASE_ENV_KEYS = [
  "HOME",
  "TMPDIR",
  "TEMP",
  "TMP",
  "LANG",
  "LC_ALL",
  "LC_CTYPE",
  "SSL_CERT_FILE",
  "REQUESTS_CA_BUNDLE",
  "HTTP_PROXY",
  "HTTPS_PROXY",
  "ALL_PROXY",
  "NO_PROXY",
  "http_proxy",
  "https_proxy",
  "all_proxy",
  "no_proxy",
];
const PYTHON_APP_ENV_KEYS = [
  "DEEPSEEK_API_KEY",
  "DEEPSEEK_BASE_URL",
  "DEEPSEEK_MODEL",
  "AI_VIDEO_DIARY_RUNTIME_ENV_PATH",
  "OBSIDIAN_VAULT_PATH",
  "PRODUCT_HUNT_TOKEN",
  "PRODUCT_HUNT_API_URL",
  "TOPIC_SOURCE_URLS",
  "HERMES_RADAR_PATH",
  "VIDEO_INBOX_PATH",
  "VIDEO_OUTPUT_PATH",
  "DRAFT_OUTPUT_PATH",
  "JIANYING_DRAFT_ROOT_PATH",
  "DATABASE_PATH",
  "TENCENTCLOUD_SECRET_ID",
  "TENCENTCLOUD_SECRET_KEY",
  "TENCENTCLOUD_ASR_REGION",
  "TENCENTCLOUD_ASR_ENGINE_MODEL_TYPE",
  "TENCENTCLOUD_ASR_MAX_WAIT_SECONDS",
  "TENCENTCLOUD_ASR_POLL_INTERVAL_SECONDS",
  "TENCENTCLOUD_ASR_MAX_LOCAL_AUDIO_BYTES",
  "LOCAL_WHISPER_PYTHON_PATH",
  "LOCAL_WHISPER_MODEL_PATH",
  "LOCAL_WHISPER_LANGUAGE",
];
function desktopRoot() {
  return path.resolve(__dirname, "..");
}

function repoRoot() {
  return path.resolve(desktopRoot(), "..", "..");
}

function createServiceConfig({
  appPath = process.execPath,
  env = process.env,
  homeDir = os.homedir(),
  isPackaged = false,
  resourcesPath = process.resourcesPath,
} = {}) {
  const apiCwd = isPackaged
    ? path.join(resourcesPath, "api")
    : path.join(repoRoot(), "apps", "api");
  const webStandalone = isPackaged
    ? path.join(resourcesPath, "web-standalone", "apps", "web", "server.js")
    : path.join(repoRoot(), "apps", "web", ".next", "standalone", "apps", "web", "server.js");
  const webDevCwd = path.join(repoRoot(), "apps", "web");
  const directoryShortcuts = getDirectoryShortcuts(homeDir);
  const logsDir = directoryShortcuts.logs;
  const databasePath = path.join(directoryShortcuts.appSupport, "agent.sqlite3");
  const runtimeEnvPath = path.join(directoryShortcuts.appSupport, ".env");
  const apiVirtualEnvPath = path.join(directoryShortcuts.appSupport, "api-venv");
  const apiBase = `http://127.0.0.1:${API_PORT}`;

  const services = [
    {
      name: "API",
      command: "uv",
      args: [
        "run",
        "uvicorn",
        "app.main:app",
        "--host",
        "127.0.0.1",
        "--port",
        API_PORT,
      ],
      cwd: apiCwd,
      env: pythonServiceEnv(env, homeDir, {
        databasePath,
        runtimeEnvPath,
        virtualEnvPath: apiVirtualEnvPath,
      }),
      logPath: path.join(logsDir, "api.log"),
    },
  ];
  if (shouldAutostartWorker(env)) {
    services.push({
      name: "Worker",
      command: "uv",
      args: ["run", "python", "-m", "app.automation"],
      cwd: apiCwd,
      env: pythonServiceEnv(env, homeDir, {
        databasePath,
        runtimeEnvPath,
        virtualEnvPath: apiVirtualEnvPath,
      }),
      logPath: path.join(logsDir, "worker.log"),
    });
  }
  services.push(
    webServiceConfig({
      apiBase,
      appPath,
      env,
      isPackaged,
      logPath: path.join(logsDir, "web.log"),
      webDevCwd,
      webStandalone,
    }),
  );

  return {
    apiUrl: apiBase,
    apiReadinessUrl: `${apiBase}/api/runtime/ping`,
    appSupportDir: directoryShortcuts.appSupport,
    webUrl: `http://127.0.0.1:${WEB_PORT}`,
    logsDir,
    services,
  };
}

function shouldAutostartWorker(env = process.env) {
  return env.AI_VIDEO_DIARY_DESKTOP_AUTOSTART_WORKER === "1";
}

function pythonServiceEnv(
  env = process.env,
  homeDir = os.homedir(),
  { databasePath = null, runtimeEnvPath = null, virtualEnvPath = null } = {},
) {
  const serviceEnv = {};
  for (const key of [...PYTHON_BASE_ENV_KEYS, ...PYTHON_APP_ENV_KEYS]) {
    if (env[key] !== undefined && env[key] !== null && env[key] !== "") {
      serviceEnv[key] = env[key];
    }
  }
  if (!serviceEnv.DATABASE_PATH && databasePath) {
    serviceEnv.DATABASE_PATH = databasePath;
  }
  if (!serviceEnv.AI_VIDEO_DIARY_RUNTIME_ENV_PATH && runtimeEnvPath) {
    serviceEnv.AI_VIDEO_DIARY_RUNTIME_ENV_PATH = runtimeEnvPath;
  }
  if (virtualEnvPath) {
    serviceEnv.UV_PROJECT_ENVIRONMENT = virtualEnvPath;
  }
  return {
    ...serviceEnv,
    HOME: serviceEnv.HOME ?? homeDir,
    LANG: serviceEnv.LANG ?? "C.UTF-8",
    PATH: serviceSearchPath(env, homeDir),
    PYTHONDONTWRITEBYTECODE: "1",
    PYTHONUNBUFFERED: "1",
  };
}

function serviceSearchPath(env = process.env, homeDir = os.homedir()) {
  return commandSearchPaths(env, homeDir).join(path.delimiter);
}

function webServiceConfig({
  apiBase,
  appPath,
  env,
  isPackaged,
  logPath,
  webDevCwd,
  webStandalone,
}) {
  if (isPackaged) {
    return {
      name: "Web",
      command: appPath,
      args: [webStandalone],
      cwd: path.dirname(webStandalone),
      env: {
        ...env,
        ELECTRON_RUN_AS_NODE: "1",
        HOSTNAME: "127.0.0.1",
        NEXT_PUBLIC_API_BASE: apiBase,
        PORT: WEB_PORT,
      },
      logPath,
    };
  }

  return {
    name: "Web",
    command: "corepack",
    args: ["pnpm", "dev"],
    cwd: webDevCwd,
    env: {
      ...webServiceEnv(env),
      NEXT_PUBLIC_API_BASE: apiBase,
      PORT: WEB_PORT,
    },
    logPath,
  };
}

function webServiceEnv(env = process.env) {
  const serviceEnv = { ...env };
  delete serviceEnv.ELECTRON_RUN_AS_NODE;
  delete serviceEnv.NODE_OPTIONS;
  delete serviceEnv.CODEX_THREAD_ID;
  delete serviceEnv.VSCODE_PID;
  return serviceEnv;
}

function spawnManagedService(service, logger = console) {
  fs.mkdirSync(path.dirname(service.logPath), { recursive: true });
  const logStream = fs.createWriteStream(service.logPath, { flags: "a" });
  logStream.write(`\n[${new Date().toISOString()}] Starting ${service.name}\n`);
  logStream.write(`$ ${service.command} ${service.args.join(" ")}\n`);

  const child = spawn(service.command, service.args, {
    cwd: service.cwd,
    env: service.env,
    stdio: ["ignore", "pipe", "pipe"],
  });

  child.stdout.on("data", (chunk) => logStream.write(chunk));
  child.stderr.on("data", (chunk) => logStream.write(chunk));
  child.on("error", (error) => {
    const message = `${service.name} failed to start: ${error.message}`;
    logStream.write(`[error] ${message}\n`);
    logger.error(message);
  });
  child.on("exit", (code, signal) => {
    logStream.write(
      `[${new Date().toISOString()}] ${service.name} exited code=${code} signal=${signal}\n`,
    );
    logStream.end();
  });

  child.serviceName = service.name;
  child.serviceLogPath = service.logPath;
  return child;
}

async function shutdownChildProcesses(children, timeoutMs = 5000) {
  await Promise.all(
    children.map(
      (child) =>
        new Promise((resolve) => {
          if (!child || child.killed || child.exitCode !== null) {
            resolve();
            return;
          }

          const timer = setTimeout(() => {
            try {
              child.kill("SIGKILL");
            } catch {
              // Process may have exited between timeout scheduling and kill.
            }
            resolve();
          }, timeoutMs);

          child.once("exit", () => {
            clearTimeout(timer);
            resolve();
          });

          try {
            child.kill("SIGTERM");
          } catch {
            clearTimeout(timer);
            resolve();
          }
        }),
    ),
  );
}

async function waitForHttp(url, timeoutMs = 90000, { children = [] } = {}) {
  const startedAt = Date.now();
  while (Date.now() - startedAt < timeoutMs) {
    const exitedChildren = exitedChildSummaries(children);
    if (exitedChildren.length > 0) {
      throw new Error(
        `Timed out waiting for ${url}; ${exitedChildren.join("; ")}`,
      );
    }
    if (await canConnect(url)) {
      return true;
    }
    await new Promise((resolve) => setTimeout(resolve, 750));
  }
  const exitedChildren = exitedChildSummaries(children);
  const exitDetail = exitedChildren.length > 0
    ? `; ${exitedChildren.join("; ")}`
    : "";
  throw new Error(`Timed out waiting for ${url}${exitDetail}`);
}

function exitedChildSummaries(children) {
  return children
    .filter((child) => {
      if (!child) {
        return false;
      }
      const exitCode = child.exitCode ?? null;
      const signalCode = child.signalCode ?? null;
      return exitCode !== null || signalCode !== null;
    })
    .map((child) => {
      const name = child.serviceName || "Service";
      const code = child.exitCode ?? "null";
      const signal = child.signalCode ?? "null";
      const logPath = child.serviceLogPath ? ` log=${child.serviceLogPath}` : "";
      return `${name} exited early code=${code} signal=${signal}${logPath}`;
    });
}

function canConnect(url) {
  const transport = url.startsWith("https:") ? https : http;
  return new Promise((resolve) => {
    const request = transport.get(url, (response) => {
      response.resume();
      resolve(response.statusCode >= 200 && response.statusCode < 500);
    });
    request.on("error", () => resolve(false));
    request.setTimeout(1500, () => {
      request.destroy();
      resolve(false);
    });
  });
}

function getDirectoryShortcuts(homeDir = os.homedir()) {
  return {
    appSupport: path.join(homeDir, "Library", "Application Support", "AI视频日记 Agent Studio"),
    logs: path.join(homeDir, "Library", "Logs", "AI视频日记 Agent Studio"),
    videoInbox: path.join(homeDir, "Movies", "AI视频日记", "01_待处理视频"),
    videoOutput: path.join(homeDir, "Movies", "AI视频日记", "06_待发布成片"),
    draftOutput: path.join(homeDir, "Movies", "AI视频日记", "05_剪映草稿"),
    jianyingDraftRoot: path.join(
      homeDir,
      "Movies",
      "JianyingPro",
      "User Data",
      "Projects",
      "com.lveditor.draft",
    ),
  };
}

module.exports = {
  API_PORT,
  WEB_PORT,
  checkDesktopDependencies,
  createServiceConfig,
  getDirectoryShortcuts,
  spawnManagedService,
  shutdownChildProcesses,
  waitForHttp,
};
