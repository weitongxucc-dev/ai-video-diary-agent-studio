import { globSync } from "node:fs";
import { readFile } from "node:fs/promises";

const root = new URL("../", import.meta.url);
const read = (path) => readFile(new URL(path, root), "utf8");
const routeErrorCallers = [
  "src/app/error.tsx",
  "src/app/edit-plan/error.tsx",
  "src/app/video/error.tsx",
  "src/app/script/error.tsx",
];
const [
  css,
  appShell,
  retroChrome,
  primitives,
  commandDock,
  browserCheck,
  ...routeErrors
] =
  await Promise.all([
    read("src/app/globals.css"),
    read("src/components/AppShell.tsx"),
    read("src/components/RetroChrome.tsx"),
    read("src/components/PixelPrimitives.tsx"),
    read("src/components/VoiceCommandConsole.tsx"),
    read("scripts/phase-a-browser-check.js"),
    ...routeErrorCallers.map(read),
  ]);

const failures = [];
const phaseAMarker = "/* Phase A — approved DESIGN.md shared workstation system */";
const [legacyCss, phaseACss = ""] = css.split(phaseAMarker);
const emojiPattern = /[\u{1F000}-\u{1FAFF}\u{2600}-\u{27BF}]/u;
const tsxFiles = globSync("src/**/*.{ts,tsx}", { cwd: root });

for (const file of tsxFiles) {
  const source = await read(file);
  if (emojiPattern.test(source)) {
    failures.push(`${file} contains emoji; use the shared PixelIcon grid.`);
  }
}

if (css.includes("linear-gradient(")) {
  failures.push("globals.css uses gradients; the approved workstation uses solid surfaces.");
}

const requiredTokens = {
  "--retro-canvas": "#c9c3b4",
  "--retro-surface": "#f4efd9",
  "--retro-surface-subtle": "#ece6d0",
  "--retro-inset": "#fffdf0",
  "--retro-ink": "#171717",
  "--retro-muted": "#60656f",
  "--retro-border": "#111111",
  "--retro-border-soft": "#6f6a5b",
  "--retro-selection": "#064a9b",
  "--retro-selection-dark": "#003f8f",
  "--retro-success": "#1f8a4c",
  "--retro-warning": "#c28a18",
  "--retro-danger": "#b7332f",
  "--retro-focus": "#f0c14b",
};

for (const [token, value] of Object.entries(requiredTokens)) {
  if (!css.includes(`${token}: ${value}`)) {
    failures.push(`globals.css must map approved token ${token}: ${value}.`);
  }
}

for (const required of [
  "font-family: var(--font-readable)",
  "font-size: 14px",
  "line-height: 1.6",
  "outline: 2px solid var(--retro-focus)",
  "@media (max-width: 1179px)",
  "@media (min-width: 1440px)",
  'data-scroll-region="main-y"',
  'data-scroll-region="inspector-y"',
  'data-scroll-region="command-details-y"',
]) {
  const source = required.startsWith("data-") ? `${appShell}\n${commandDock}` : css;
  if (!source.includes(required)) {
    failures.push(`Phase A shared system is missing: ${required}`);
  }
}

if (!/\.pixel-button\s*\{[^}]*min-height:\s*34px[^}]*background:\s*var\(--blue\)/s.test(legacyCss)) {
  failures.push("Legacy .pixel-button must retain its 34px, legacy-token compatibility contract.");
}

for (const exportedType of [
  "PixelPanelProps",
  "PixelButtonProps",
  "PixelInputProps",
  "PixelStatusProps",
  "PixelStateAction",
  "PixelStateProps",
  "PixelEmptyStateProps",
  "PixelLoadingStateProps",
  "PixelBlockedStateProps",
  "PixelErrorStateProps",
  "PixelScrollableRegionProps",
]) {
  if (!primitives.includes(`export type ${exportedType}`)) {
    failures.push(`PixelPrimitives must export stable ${exportedType}.`);
  }
}

if (primitives.includes("secondaryAction?: ReactNode")) {
  failures.push("PixelState actions must use the controlled button/link union, not arbitrary ReactNode.");
}

for (const required of [
  'kind: "button"',
  'kind: "link"',
  "ResizeObserver",
  'event.key === "Home"',
  'event.key === "End"',
  "showControls",
]) {
  if (!primitives.includes(required)) {
    failures.push(`PixelPrimitives is missing approved API behavior: ${required}`);
  }
}

if (/body\s*\{[^}]*overflow-x:\s*(?:hidden|clip)/s.test(css)) {
  failures.push("body must not hide or clip horizontal overflow.");
}

for (const required of [
  ".pixel-button {",
  ".pixel-button.secondary {",
  ".pixel-button.danger {",
  ".pixel-button.compact {",
]) {
  if (!legacyCss.includes(required)) {
    failures.push(`Legacy business-page button compatibility is missing: ${required}`);
  }
}

if (/\.pixel-button(?![\w-])/.test(phaseACss)) {
  failures.push("Phase A CSS must not override the legacy .pixel-button namespace.");
}

for (const required of [
  "pixel-control-button pixel-control-button--",
  ".pixel-control-button {",
  ".pixel-control-button--primary",
  ".pixel-control-button--secondary",
  ".pixel-control-button--danger",
]) {
  const source = required.startsWith(".") ? phaseACss : primitives;
  if (!source.includes(required)) {
    failures.push(`Isolated PixelButton contract is missing: ${required}`);
  }
}

if (/className=\{`pixel-button(?:\s|`)/.test(primitives)) {
  failures.push("PixelButton must not emit the legacy .pixel-button class.");
}

const navBlock = appShell.match(/const navItems:[\s\S]*?=\s*\[([\s\S]*?)\n\];/);
const navRoutes = navBlock?.[1].match(/href:\s*"[^"]+"/g) ?? [];
if (navRoutes.length !== 8) {
  failures.push(`AppShell production navigation must contain exactly 8 items; found ${navRoutes.length}.`);
}

for (const required of [
  "PixelPanel",
  "PixelButton",
  "PixelInput",
  "PixelStatus",
  "PixelEmptyState",
  "PixelErrorState",
  "PixelLoadingState",
  "PixelBlockedState",
  "PixelScrollableRegion",
]) {
  if (!primitives.includes(`export function ${required}`) && !primitives.includes(`export const ${required}`)) {
    failures.push(`PixelPrimitives must export ${required}.`);
  }
}

if (!commandDock.includes("disabled={!canPreview}")) {
  failures.push("Command dock preview must be disabled until trimmed input can be previewed.");
}

if (!/<PixelButton[\s\S]*?onClick=\{onPreview\}[\s\S]*?variant="secondary"[\s\S]*?>\s*预览指令/.test(commandDock)) {
  failures.push("Command dock preview must remain a neutral secondary action.");
}

if ((commandDock.match(/variant="primary"/g) ?? []).length !== 1) {
  failures.push("Command dock may use primary only for the waiting-for-user confirm action.");
}

routeErrors.forEach((source, index) => {
  const caller = routeErrorCallers[index];
  if (!source.includes("errorCategory={classifyRouteError(error)}")) {
    failures.push(`${caller} must classify the actual error before rendering RouteError.`);
  }
  if (/\b(?:hint|detail)=/.test(source)) {
    failures.push(`${caller} must not override error classification with hard-coded hint/detail copy.`);
  }
});

if (/localhost|127\.0\.0\.1|disabled/.test(retroChrome)) {
  failures.push("RetroChrome must not expose development addresses or decorative disabled controls.");
}

for (const required of [
  "router.back()",
  "toggleHelpPanel()",
  "workspaceStatus",
  "工作区状态未知",
  "当前工作区",
]) {
  if (!retroChrome.includes(required)) {
    failures.push(`RetroChrome must provide actionable workstation chrome: ${required}`);
  }
}

if (retroChrome.includes("本地工作区已连接")) {
  failures.push("RetroChrome must not report a connected workspace without controlled evidence.");
}

for (const required of [
  "aria-controls=\"agent-inspector\"",
  "window.matchMedia(\"(min-width: 1180px)\")",
  "inspector-backdrop",
  "shell-main-scroll",
  "inspectorTriggerRef.current?.focus()",
]) {
  if (!appShell.includes(required)) {
    failures.push(`AppShell is missing compact Inspector/shell contract: ${required}`);
  }
}

for (const required of [
  "export function VoiceCommandConsoleView",
  "export function VoiceCommandConsole",
  "aria-controls=\"voice-command-details\"",
  "预览指令",
  "确认并执行",
  "setExpanded(true)",
]) {
  if (!commandDock.includes(required)) {
    failures.push(`Command dock is missing preview/confirm/collapse behavior: ${required}`);
  }
}

for (const required of [
  "1440, height: 900",
  "1180, height: 800",
  "1024, height: 768",
  "undersized targets",
  "inaccessible clipped content",
  "primary focus is not visibly rendered",
  "last stage is not reachable",
  "inspector drawer shrank main task",
  "inspector focus was not returned",
  "horizontal controls are missing",
  "PixelButton namespaces are not isolated",
  "empty command preview is not neutral",
]) {
  if (!browserCheck.includes(required)) {
    failures.push(`Phase A browser check is missing gate: ${required}`);
  }
}

if (failures.length > 0) {
  console.error(failures.map((failure) => `- ${failure}`).join("\n"));
  process.exit(1);
}

console.log("approved pixel workstation contract checks passed");
