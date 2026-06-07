# macOS Desktop DMG Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a macOS desktop shell and verified DMG artifact for AI视频日记 Agent Studio.

**Architecture:** Add `apps/desktop` as an Electron shell because the current machine has Node, `uv`, and `hdiutil`, but no Rust/Tauri toolchain. The shell keeps Web/API/Worker boundaries intact: Electron manages lifecycle and native macOS affordances, FastAPI remains in `apps/api`, and Next.js remains in `apps/web` with standalone output for packaged serving.

**Tech Stack:** Electron, electron-builder, Next.js standalone output, FastAPI via `uv`, macOS `hdiutil`, pnpm workspace scripts.

---

### Task 1: Desktop Delivery Contract

**Files:**
- Create: `apps/desktop/scripts/desktop-contract-check.mjs`
- Modify: `package.json`

- [x] **Step 1: Write the failing contract check**

Create `apps/desktop/scripts/desktop-contract-check.mjs` that checks:

```js
const rootPackage = await readJson("package.json");
const desktopPackage = await readJson("apps/desktop/package.json");
const main = await readText("apps/desktop/src/main.js");
const serviceManager = await readText("apps/desktop/src/service-manager.js");
const nextConfig = await readText("apps/web/next.config.mjs");

assert(rootPackage.scripts["test:desktop-contract"]);
assert(rootPackage.scripts["dist:mac"]);
assert(desktopPackage.main === "src/main.js");
assert(desktopPackage.build.mac.target.includes("dmg"));
assert(JSON.stringify(desktopPackage.build.extraResources).includes("apps/api"));
assert(JSON.stringify(desktopPackage.build.extraResources).includes(".next/standalone"));
assert(JSON.stringify(desktopPackage.build.extraResources).includes(".next/standalone/node_modules"));
assert(desktopPackage.scripts["dist:mac"].includes("with-pnpm-shim.mjs"));
assert(main.includes("BrowserWindow"));
assert(main.includes("Tray"));
assert(main.includes("globalShortcut.register"));
assert(main.includes("shell.openPath"));
assert(main.includes("shutdownChildProcesses"));
assert(serviceManager.includes("uvicorn"));
assert(serviceManager.includes("app.automation"));
assert(serviceManager.includes("ELECTRON_RUN_AS_NODE"));
assert(nextConfig.includes("output: \"standalone\""));
```

- [x] **Step 2: Run RED**

Run: `node apps/desktop/scripts/desktop-contract-check.mjs`
Expected: FAIL because `apps/desktop/package.json`, `src/main.js`, and `src/service-manager.js` do not exist.

- [x] **Step 3: Add root scripts**

Add root scripts:

```json
"test:desktop-contract": "node apps/desktop/scripts/desktop-contract-check.mjs",
"build:desktop": "corepack pnpm build:web && corepack pnpm --dir apps/desktop pack:mac",
"dist:mac": "corepack pnpm build:web && corepack pnpm --dir apps/desktop dist:mac"
```

### Task 2: Electron Desktop Shell

**Files:**
- Create: `apps/desktop/package.json`
- Create: `apps/desktop/src/main.js`
- Create: `apps/desktop/src/service-manager.js`
- Create: `apps/desktop/assets/icon.svg`
- Modify: `apps/web/next.config.mjs`

- [x] **Step 1: Implement service manager**

Create focused helpers in `service-manager.js`:

```js
function createServiceConfig({ appPath, isPackaged, resourcesPath }) { /* returns api/web/worker commands */ }
function spawnManagedService(service, logger) { /* spawns process, writes logs, tracks exit */ }
function shutdownChildProcesses(children) { /* terminate children on quit */ }
function waitForHttp(url, timeoutMs) { /* poll until ready */ }
function getDirectoryShortcuts(homeDir) { /* logs/video/output/jianying dirs */ }
```

- [x] **Step 2: Implement Electron main process**

`main.js` should:

```js
app.whenReady().then(async () => {
  const tray = createTray();
  const win = createWindow();
  const services = await startServices();
  await waitForHttp("http://127.0.0.1:3000", 90000);
  await win.loadURL("http://127.0.0.1:3000");
  globalShortcut.register("CommandOrControl+Shift+Space", () => win.show());
});
app.on("before-quit", () => shutdownChildProcesses(childProcesses));
```

It must expose menu actions for opening logs, video inbox, output, and Jianying draft directories.

- [x] **Step 3: Enable standalone web output**

Set `output: "standalone"` in `apps/web/next.config.mjs`.

- [x] **Step 4: Verify contract green**

Run: `corepack pnpm test:desktop-contract`
Expected: PASS.

### Task 3: DMG Build And Beta README

**Files:**
- Create: `README-beta.md`
- Modify: `package.json`
- Modify: `pnpm-lock.yaml`
- Modify: `docs/ai-video-diary-agent-2.0-prd.md`

- [x] **Step 1: Add desktop dependencies**

Run:

```bash
corepack pnpm --dir apps/desktop add -D electron electron-builder
```

- [x] **Step 2: Build web standalone**

Run: `corepack pnpm build:web`
Expected: `.next/standalone/apps/web/server.js` exists.

- [x] **Step 3: Build DMG**

Run: `corepack pnpm dist:mac`
Expected: `apps/desktop/dist/AI视频日记-Agent-Studio-*.dmg` exists and `hdiutil imageinfo` can read it.

- [x] **Step 4: Write README-beta**

Document:

```text
How to open the DMG
How to bypass unsigned-app Gatekeeper warning
Required local dependencies for this beta: uv, ffmpeg, optional Whisper/ASR
What the app starts automatically: API, Worker, WebView
Where logs and generated videos live
Known limitations: first beta uses local uv environment; notarization is not done
```

- [ ] **Step 5: Verify and commit**

Run:

```bash
corepack pnpm test:desktop-contract
corepack pnpm test:web-contract
corepack pnpm test:pixel-style
corepack pnpm typecheck:web
corepack pnpm lint:web
corepack pnpm build:web
corepack pnpm dist:mac
git diff --check
```

Then commit:

```bash
git add package.json pnpm-lock.yaml apps/desktop apps/web/next.config.mjs README-beta.md docs/ai-video-diary-agent-2.0-prd.md docs/superpowers/plans/2026-06-08-macos-desktop-dmg.md
git commit -m "feat: add macos desktop dmg packaging"
git push
```
