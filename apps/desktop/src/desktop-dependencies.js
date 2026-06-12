const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

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

module.exports = {
  REQUIRED_DESKTOP_DEPENDENCIES,
  checkDesktopDependencies,
  commandSearchPaths,
  defaultCommandExists,
};
