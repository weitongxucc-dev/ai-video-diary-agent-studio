const fs = require("node:fs");
const http = require("node:http");
const https = require("node:https");
const os = require("node:os");
const path = require("node:path");
const { spawn } = require("node:child_process");

const API_PORT = "8000";
const WEB_PORT = "3000";
const REQUIRED_DESKTOP_DEPENDENCIES = [
  {
    id: "uv",
    command: "uv",
    label: "uv",
    purpose: "启动 FastAPI API 和 Worker",
    installHint: "建议使用 Homebrew 或 uv 官方安装脚本安装。",
  },
  {
    id: "ffmpeg",
    command: "ffmpeg",
    label: "ffmpeg",
    purpose: "视频处理",
    installHint: "建议使用 Homebrew 安装。",
  },
];

function desktopRoot() {
  return path.resolve(__dirname, "..");
}

function repoRoot() {
  return path.resolve(desktopRoot(), "..", "..");
}

function checkDesktopDependencies({
  commandExists = defaultCommandExists,
  env = process.env,
  homeDir = os.homedir(),
} = {}) {
  const required = REQUIRED_DESKTOP_DEPENDENCIES.map((dependency) => ({ ...dependency }));
  const missing = required.filter(
    (dependency) => !safeCommandExists(commandExists, dependency.command, env, homeDir),
  );
  return {
    ready: missing.length === 0,
    required,
    missing,
  };
}

function safeCommandExists(commandExists, command, env, homeDir) {
  try {
    return Boolean(commandExists(command, { env, homeDir }));
  } catch {
    return false;
  }
}

function defaultCommandExists(command, { env = process.env, homeDir = os.homedir() } = {}) {
  return commandCandidates(command, env, homeDir).some(isExecutableFile);
}

function commandCandidates(command, env, homeDir) {
  if (path.isAbsolute(command)) {
    return [command];
  }
  return commandSearchPaths(env, homeDir).map((directory) => path.join(directory, command));
}

function commandSearchPaths(env = process.env, homeDir = os.homedir()) {
  const paths = new Set();
  for (const entry of String(env.PATH ?? "").split(path.delimiter)) {
    if (entry) {
      paths.add(entry);
    }
  }
  for (const entry of [
    path.join(homeDir, ".local", "bin"),
    path.join(homeDir, ".cargo", "bin"),
    "/opt/homebrew/bin",
    "/usr/local/bin",
    "/usr/bin",
    "/bin",
    "/usr/sbin",
    "/sbin",
  ]) {
    paths.add(entry);
  }
  return [...paths];
}

function isExecutableFile(filePath) {
  try {
    const stat = fs.statSync(filePath);
    if (!stat.isFile()) {
      return false;
    }
    fs.accessSync(filePath, fs.constants.X_OK);
    return true;
  } catch {
    return false;
  }
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
  const logsDir = getDirectoryShortcuts(homeDir).logs;
  const apiBase = `http://127.0.0.1:${API_PORT}`;

  return {
    apiUrl: apiBase,
    webUrl: `http://127.0.0.1:${WEB_PORT}`,
    logsDir,
    services: [
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
        env: {
          ...env,
          PYTHONUNBUFFERED: "1",
        },
        logPath: path.join(logsDir, "api.log"),
      },
      {
        name: "Worker",
        command: "uv",
        args: ["run", "python", "-m", "app.automation"],
        cwd: apiCwd,
        env: {
          ...env,
          PYTHONUNBUFFERED: "1",
        },
        logPath: path.join(logsDir, "worker.log"),
      },
      webServiceConfig({
        apiBase,
        appPath,
        env,
        isPackaged,
        logPath: path.join(logsDir, "web.log"),
        webDevCwd,
        webStandalone,
      }),
    ],
  };
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
  if (isPackaged || fs.existsSync(webStandalone)) {
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
      ...env,
      NEXT_PUBLIC_API_BASE: apiBase,
      PORT: WEB_PORT,
    },
    logPath,
  };
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

async function waitForHttp(url, timeoutMs = 90000) {
  const startedAt = Date.now();
  while (Date.now() - startedAt < timeoutMs) {
    if (await canConnect(url)) {
      return true;
    }
    await new Promise((resolve) => setTimeout(resolve, 750));
  }
  throw new Error(`Timed out waiting for ${url}`);
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
