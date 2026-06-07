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
const serviceManager = await readText("apps/desktop/src/service-manager.js");
const afterPack = await readText("apps/desktop/scripts/after-pack.cjs");
const pnpmShim = await readText("apps/desktop/scripts/with-pnpm-shim.mjs");
const nextConfig = await readText("apps/web/next.config.mjs");
const readmeBeta = await readText("README-beta.md");
const serviceManagerModule = require("../src/service-manager.js");

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
  "globalShortcut.register",
  "shell.openPath",
  "shutdownChildProcesses",
  "waitForHttp",
  "CommandOrControl+Shift+Space",
]) {
  assert(main.includes(required), `Desktop main process must include ${required}.`);
}

for (const required of [
  "createServiceConfig",
  "checkDesktopDependencies",
  "spawnManagedService",
  "shutdownChildProcesses",
  "waitForHttp",
  "PYTHONDONTWRITEBYTECODE",
  "NODE_OPTIONS",
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
    ELECTRON_RUN_AS_NODE: "1",
    NODE_OPTIONS: "--require ./unexpected",
    PATH: "/usr/bin:/bin",
  },
  homeDir: "/Users/example",
  isPackaged: true,
  resourcesPath: "/Applications/Agent Studio.app/Contents/Resources",
});
const apiService = serviceConfig.services.find((service) => service.name === "API");
const workerService = serviceConfig.services.find((service) => service.name === "Worker");
const webService = serviceConfig.services.find((service) => service.name === "Web");
assert(apiService, "Desktop service config must include API.");
assert(workerService, "Desktop service config must include Worker.");
assert(webService, "Desktop service config must include Web.");
for (const service of [apiService, workerService]) {
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
}
assert(
  webService.env.ELECTRON_RUN_AS_NODE === "1",
  "Desktop Web service must still run the packaged Electron executable as Node.",
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
