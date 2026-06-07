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

const extraResources = JSON.stringify(desktopPackage.build?.extraResources ?? []);
for (const required of [
  "apps/api",
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
  "uvicorn",
  "app.automation",
  "ELECTRON_RUN_AS_NODE",
]) {
  assert(
    serviceManager.includes(required),
    `Desktop service manager must include ${required}.`,
  );
}

const { checkDesktopDependencies } = serviceManagerModule;
assert(
  typeof checkDesktopDependencies === "function",
  "Desktop service manager must export checkDesktopDependencies.",
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

for (const required of ["corepack", ".desktop-bin", "pnpm"]) {
  assert(pnpmShim.includes(required), `Desktop pnpm shim must include ${required}.`);
}

assert(nextConfig.includes('output: "standalone"'), "Next.js must use standalone output.");

for (const required of [
  "AI视频日记-Agent-Studio.dmg",
  "未签名",
  "启动前检查",
  "uv",
  "ffmpeg",
  "API",
  "Worker",
]) {
  assert(readmeBeta.includes(required), `README-beta.md must document ${required}.`);
}

console.log("desktop delivery contract checks passed");
