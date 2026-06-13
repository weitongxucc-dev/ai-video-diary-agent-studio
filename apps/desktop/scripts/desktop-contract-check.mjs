import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);

async function readText(path) {
  try {
    return await readFile(new URL(`../../../${path}`, import.meta.url), "utf8");
  } catch (error) {
    throw new Error(`Missing required desktop delivery file: ${path}`, {
      cause: error,
    });
  }
}

async function readJson(path) {
  return JSON.parse(await readText(path));
}

function assert(condition, message) {
  if (!condition) {
    throw new Error(message);
  }
}

const rootPackage = await readJson("package.json");
const desktopPackage = await readJson("apps/desktop/package.json");
const main = await readText("apps/desktop/src/main.js");
const episodeJobNotifier = await readText("apps/desktop/src/episode-job-notifier.js");
const desktopDependencies = await readText("apps/desktop/src/desktop-dependencies.js");
const serviceManager = await readText("apps/desktop/src/service-manager.js");
const afterPack = await readText("apps/desktop/scripts/after-pack.cjs");
const pnpmShim = await readText("apps/desktop/scripts/with-pnpm-shim.mjs");
const nextConfig = await readText("apps/web/next.config.mjs");
const readmeBeta = await readText("README-beta.md");
const serviceManagerModule = require("../src/service-manager.js");
const episodeJobNotifierModule = require("../src/episode-job-notifier.js");
const desktopDependenciesModule = require("../src/desktop-dependencies.js");

assert(
  rootPackage.scripts["test:desktop-contract"],
  "Root package must expose test:desktop-contract.",
);
assert(rootPackage.scripts["build:desktop"], "Root package must expose build:desktop.");
assert(rootPackage.scripts["dist:mac"], "Root package must expose dist:mac.");

assert(desktopPackage.main === "src/main.js", "Desktop package must point at src/main.js.");
assert(
  desktopPackage.scripts?.["dist:mac"]?.includes("electron-builder"),
  "Desktop package must build macOS artifacts with electron-builder.",
);
assert(
  desktopPackage.scripts?.["dist:mac"]?.includes("with-pnpm-shim.mjs"),
  "Desktop dist:mac must install a pnpm shim before electron-builder runs.",
);
assert(
  desktopPackage.build?.productName === "AI视频日记 Agent Studio",
  "Desktop productName must match the beta app name.",
);
assert(
  desktopPackage.build?.mac?.target?.includes("dmg"),
  "Desktop mac target must include dmg.",
);
assert(
  desktopPackage.build?.afterPack === "scripts/after-pack.cjs",
  "Desktop mac packaging must run the afterPack signing hook.",
);

const extraResources = JSON.stringify(desktopPackage.build?.extraResources ?? []);
for (const required of [
  "apps/api",
  "!app/**/__pycache__/**",
  "!app/**/*.pyc",
  ".next/standalone",
  ".next/standalone/node_modules",
  ".next/static",
  "public",
]) {
  assert(
    extraResources.includes(required),
    `Desktop build extraResources must include ${required}.`,
  );
}

for (const required of [
  "BrowserWindow",
  "Notification",
  "Tray",
  "Menu",
  "main.log",
  "uncaughtException",
  "unhandledRejection",
  "Content-Security-Policy",
  "setWindowOpenHandler",
  "will-navigate",
  "isTrustedNavigationUrl",
  "checkDesktopDependencies",
  "启动前检查未通过",
  "依赖缺失",
  "apiReadinessUrl",
  "globalShortcut.register",
  "shell.openPath",
  "shutdownChildProcesses",
  "waitForHttp",
  "CommandOrControl+Shift+Space",
  "requestSingleInstanceLock",
  "second-instance",
  'app.on("activate"',
  "app.focus({ steal: true })",
  "编辑",
  'role: "copy"',
  'role: "paste"',
  'role: "cut"',
  'role: "selectAll"',
]) {
  assert(main.includes(required), `Desktop main process must include ${required}.`);
}

assert(
  main.includes("function notify(") && main.includes("Notification.isSupported()"),
  "Desktop main process must wrap Electron Notification behind a support check.",
);

for (const required of [
  "startEpisodeJobNotifier",
  "stopEpisodeJobNotifier",
  "episodeJobNotifier",
]) {
  assert(
    main.includes(required),
    `Desktop main process must delegate background job notifications with ${required}.`,
  );
}

for (const required of [
  "startEpisodeJobNotifier",
  "/api/episodes/today",
  "topic_job_status",
  "video_job_status",
  "completed",
  "failed",
  "interrupted",
  "finished_at",
  "clearInterval",
]) {
  assert(
    episodeJobNotifier.includes(required),
    `Desktop episode job notifier must handle background job transitions with ${required}.`,
  );
}

for (const required of [
  "createServiceConfig",
  "spawnManagedService",
  "shutdownChildProcesses",
  "waitForHttp",
  "PYTHON_APP_ENV_KEYS",
  "PYTHONDONTWRITEBYTECODE",
  "/api/runtime/ping",
  "uvicorn",
  "app.automation",
  "ELECTRON_RUN_AS_NODE",
]) {
  assert(
    serviceManager.includes(required),
    `Desktop service manager must include ${required}.`,
  );
}

for (const required of [
  "checkDesktopDependencies",
  "REQUIRED_DESKTOP_DEPENDENCIES",
  "commandSearchPaths",
  "defaultCommandExists",
]) {
  assert(
    desktopDependencies.includes(required),
    `Desktop dependency preflight module must include ${required}.`,
  );
}

assert(
  !serviceManager.includes("const REQUIRED_DESKTOP_DEPENDENCIES"),
  "Desktop service manager must not own dependency preflight constants.",
);

for (const required of [
  "codesign",
  "--deep",
  "--sign",
  "Ad-hoc signing",
  "electronPlatformName",
]) {
  assert(afterPack.includes(required), `Desktop afterPack hook must include ${required}.`);
}

const { checkDesktopDependencies } = serviceManagerModule;
const { createServiceConfig } = serviceManagerModule;
const { waitForHttp } = serviceManagerModule;
assert(
  typeof episodeJobNotifierModule.startEpisodeJobNotifier === "function",
  "Desktop episode job notifier must export startEpisodeJobNotifier.",
);
assert(
  typeof desktopDependenciesModule.checkDesktopDependencies === "function",
  "Desktop dependency preflight module must export checkDesktopDependencies.",
);
assert(
  typeof checkDesktopDependencies === "function",
  "Desktop service manager must export checkDesktopDependencies.",
);
assert(
  typeof createServiceConfig === "function",
  "Desktop service manager must export createServiceConfig.",
);
assert(
  typeof waitForHttp === "function",
  "Desktop service manager must export waitForHttp.",
);
const missingAllDependencies = checkDesktopDependencies({ commandExists: () => false });
assert(
  missingAllDependencies.ready === false,
  "Desktop dependency preflight must fail when required commands are unavailable.",
);
assert(
  missingAllDependencies.missing.some((item) => item.id === "uv"),
  "Desktop dependency preflight must report missing uv.",
);
assert(
  missingAllDependencies.missing.some((item) => item.id === "ffmpeg"),
  "Desktop dependency preflight must report missing ffmpeg.",
);
const completeDependencies = checkDesktopDependencies({ commandExists: () => true });
assert(
  completeDependencies.ready === true && completeDependencies.missing.length === 0,
  "Desktop dependency preflight must pass when all required commands are available.",
);

const serviceConfig = createServiceConfig({
  appPath: "/Applications/Agent Studio.app/Contents/MacOS/Agent Studio",
  env: {
    CODEX_THREAD_ID: "unexpected",
    ELECTRON_RUN_AS_NODE: "1",
    NODE_OPTIONS: "--require ./unexpected",
    PATH: "/usr/bin:/bin",
    UV_RUN_RECURSION_DEPTH: "1",
    VIRTUAL_ENV: "/tmp/unexpected-venv",
    VSCODE_PID: "123",
  },
  homeDir: "/Users/example",
  isPackaged: true,
  resourcesPath: "/Applications/Agent Studio.app/Contents/Resources",
});
const apiService = serviceConfig.services.find((service) => service.name === "API");
const workerService = serviceConfig.services.find((service) => service.name === "Worker");
const webService = serviceConfig.services.find((service) => service.name === "Web");
assert(
  serviceConfig.apiReadinessUrl === "http://127.0.0.1:8000/api/runtime/ping",
  "Desktop startup readiness must use lightweight runtime ping.",
);
assert(apiService, "Desktop service config must include API.");
assert(!workerService, "Desktop service config must not autostart Worker by default.");
assert(webService, "Desktop service config must include Web.");
assert(
  apiService.env.DATABASE_PATH ===
    "/Users/example/Library/Application Support/AI视频日记 Agent Studio/agent.sqlite3",
  "Desktop API must use an Application Support database path by default.",
);
assert(
  apiService.env.AI_VIDEO_DIARY_RUNTIME_ENV_PATH ===
    "/Users/example/Library/Application Support/AI视频日记 Agent Studio/.env",
  "Desktop API must use an Application Support runtime env path by default.",
);
assert(
  apiService.env.UV_PROJECT_ENVIRONMENT ===
    "/Users/example/Library/Application Support/AI视频日记 Agent Studio/api-venv",
  "Desktop API must keep its uv virtual environment outside the signed app bundle.",
);
for (const service of [apiService]) {
  assert(
    service.env.PYTHONDONTWRITEBYTECODE === "1",
    `${service.name} must disable Python bytecode writes inside the packaged app.`,
  );
  assert(
    service.env.ELECTRON_RUN_AS_NODE === undefined,
    `${service.name} must not inherit ELECTRON_RUN_AS_NODE from the desktop shell.`,
  );
  assert(
    service.env.NODE_OPTIONS === undefined,
    `${service.name} must not inherit NODE_OPTIONS from the desktop shell.`,
  );
  assert(
    service.env.CODEX_THREAD_ID === undefined,
    `${service.name} must not inherit IDE agent environment from the desktop shell.`,
  );
  assert(
    service.env.UV_RUN_RECURSION_DEPTH === undefined,
    `${service.name} must not inherit uv recursion environment from the desktop shell.`,
  );
  assert(
    service.env.VIRTUAL_ENV === undefined,
    `${service.name} must let uv choose the packaged virtual environment.`,
  );
  assert(
    service.env.VSCODE_PID === undefined,
    `${service.name} must not inherit VS Code extension host environment.`,
  );
  assert(
    service.env.PATH.includes("/opt/homebrew/bin"),
    `${service.name} must include Homebrew commands in PATH when launched from Finder.`,
  );
  assert(
    service.env.PATH.includes("/Users/example/.local/bin"),
    `${service.name} must include user-local commands in PATH when launched from Finder.`,
  );
}
assert(
  webService.env.ELECTRON_RUN_AS_NODE === "1",
  "Desktop Web service must still run the packaged Electron executable as Node.",
);

const sourceServiceConfig = createServiceConfig({
  appPath: "/repo/node_modules/electron/dist/Electron.app/Contents/MacOS/Electron",
  env: {
    ELECTRON_RUN_AS_NODE: "1",
    PATH: "/usr/bin:/bin",
  },
  homeDir: "/Users/example",
  isPackaged: false,
  resourcesPath: "/repo/node_modules/electron/dist/Electron.app/Contents/Resources",
});
const sourceWebService = sourceServiceConfig.services.find((service) => service.name === "Web");
assert(sourceWebService, "Desktop source service config must include Web.");
assert(
  sourceWebService.command === "corepack",
  "Desktop source mode must use the Next dev server instead of stale standalone output.",
);
assert(
  sourceWebService.args.join(" ") === "pnpm dev",
  "Desktop source mode must start web with pnpm dev.",
);
assert(
  sourceWebService.env.ELECTRON_RUN_AS_NODE === undefined,
  "Desktop source Web service must not inherit ELECTRON_RUN_AS_NODE.",
);

const workerEnabledServiceConfig = createServiceConfig({
  appPath: "/Applications/Agent Studio.app/Contents/MacOS/Agent Studio",
  env: {
    AI_VIDEO_DIARY_DESKTOP_AUTOSTART_WORKER: "1",
    PATH: "/usr/bin:/bin",
  },
  homeDir: "/Users/example",
  isPackaged: true,
  resourcesPath: "/Applications/Agent Studio.app/Contents/Resources",
});
const enabledWorkerService = workerEnabledServiceConfig.services.find(
  (service) => service.name === "Worker",
);
assert(
  enabledWorkerService?.args?.join(" ").includes("app.automation"),
  "Desktop service config must support opt-in Worker autostart.",
);
assert(
  enabledWorkerService.env.DATABASE_PATH ===
    "/Users/example/Library/Application Support/AI视频日记 Agent Studio/agent.sqlite3",
  "Desktop opt-in Worker must use the Application Support database path by default.",
);
assert(
  enabledWorkerService.env.AI_VIDEO_DIARY_RUNTIME_ENV_PATH ===
    "/Users/example/Library/Application Support/AI视频日记 Agent Studio/.env",
  "Desktop opt-in Worker must use the Application Support runtime env path by default.",
);
assert(
  enabledWorkerService.env.UV_PROJECT_ENVIRONMENT ===
    "/Users/example/Library/Application Support/AI视频日记 Agent Studio/api-venv",
  "Desktop opt-in Worker must keep its uv virtual environment outside the signed app bundle.",
);
assert(
  enabledWorkerService.env.PATH.includes("/opt/homebrew/bin"),
  "Desktop opt-in Worker must include Homebrew commands in PATH.",
);

let earlyExitError = null;
try {
  await waitForHttp("http://127.0.0.1:9", 5, {
    children: [{ serviceName: "API", exitCode: 1, signalCode: null }],
  });
} catch (error) {
  earlyExitError = error;
}
assert(
  earlyExitError?.message?.includes("API exited early code=1"),
  "Desktop HTTP readiness errors must identify child services that exited early.",
);

for (const required of ["corepack", ".desktop-bin", "pnpm"]) {
  assert(pnpmShim.includes(required), `Desktop pnpm shim must include ${required}.`);
}

assert(nextConfig.includes('output: "standalone"'), "Next.js must use standalone output.");

for (const required of [
  "AI视频日记-Agent-Studio.dmg",
  "ad-hoc 签名",
  "未 notarize",
  "启动前检查",
  "uv",
  "ffmpeg",
  "API",
  "Worker",
  "main.log",
]) {
  assert(readmeBeta.includes(required), `README-beta.md must document ${required}.`);
}

console.log("desktop delivery contract checks passed");
