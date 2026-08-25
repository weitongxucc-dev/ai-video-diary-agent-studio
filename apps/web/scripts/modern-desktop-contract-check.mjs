import { readFile } from "node:fs/promises";

const root = new URL("../", import.meta.url);
const read = (path) => readFile(new URL(path, root), "utf8");
const [design, globals, overrides, shell, home, composer, library, montage, production, desktop, layout, signin, routeIcon, mascot] = await Promise.all([
  read("../../DESIGN.md"),
  read("src/app/globals.css"),
  read("src/app/modern-overrides.css"),
  read("src/components/AppShell.tsx"),
  read("src/app/page.tsx"),
  read("src/components/HomeCommandComposer.tsx"),
  read("src/components/MaterialLibraryWorkspace.tsx"),
  read("src/components/MontageReviewWorkspace.tsx"),
  read("src/app/production/page.tsx"),
  read("../desktop/main.js"),
  read("src/app/layout.tsx"),
  read("src/app/signin/page.tsx"),
  read("src/components/PixelIcon.tsx"),
  read("src/components/PixelMascot.tsx"),
]);

const failures = [];
const css = `${globals}\n${overrides}`;

for (const token of [
  "--app-bg",
  "--sidebar-bg",
  "--surface-1",
  "--surface-2",
  "--text-1",
  "--text-2",
  "--border-1",
  "--accent",
  "--focus",
]) {
  if (!css.includes(`${token}:`)) failures.push(`modern theme token is missing: ${token}`);
}

for (const required of [
  ':root[data-theme="dark"]',
  "prefers-reduced-motion",
  "--sidebar-width: 232px",
  "--inspector-width: 320px",
  "min-width: 1024px",
]) {
  if (!css.includes(required)) failures.push(`modern desktop CSS is missing: ${required}`);
}

for (const required of [
  "desktop-sidebar",
  "desktop-toolbar",
  "desktop-inspector",
  'href: "/"',
  'href: "/production"',
  'href: "/library"',
  'href: "/settings"',
  "ai-video-diary-theme",
  'window.matchMedia("(min-width: 1280px)")',
]) {
  if (!shell.includes(required)) failures.push(`AppShell is missing: ${required}`);
}

for (const forbidden of ["RetroChrome", "PixelIcon", "PixelPrimitives", "retro-desktop-shell"] ) {
  if (shell.includes(forbidden)) failures.push(`AppShell still depends on legacy chrome: ${forbidden}`);
}

for (const [name, source] of [["home", home], ["composer", composer], ["library", library], ["montage", montage], ["production", production]]) {
  if (/Pixel(?:Icon|Panel|Button|Status|Mascot|Agent)/.test(source)) failures.push(`${name} north-star UI imports Pixel components`);
}

for (const required of ["HomeCommandComposer", "想制作什么", "recent_batches", "waiting_for_user"]) {
  if (!home.includes(required)) failures.push(`modern home is missing: ${required}`);
}

for (const required of ["previewVoiceCommand", "executeVoiceCommand", "确认执行"]) {
  if (!composer.includes(required)) failures.push(`home command composer is missing: ${required}`);
}

for (const required of ["scanProductionMaterials", "reviewProductionMaterial", "素材库", "/media"]) {
  if (!library.includes(required)) failures.push(`material library is missing: ${required}`);
}

for (const required of ["getMontageClipCandidates", "updateMontageClip", "reorderMontageClips", "滑移裁剪", "锁定镜头"]) {
  if (!montage.includes(required)) failures.push(`montage review is missing: ${required}`);
}

for (const required of ["titleBarStyle: 'hiddenInset'", "minWidth: 1024", "minHeight: 700", "backgroundColor: '#f7f7f5'"]) {
  if (!desktop.includes(required)) failures.push(`Electron chrome is missing: ${required}`);
}

if (!layout.includes("modern-overrides.css") || !layout.includes("ai-video-diary-theme")) {
  failures.push("root layout must initialize and load the modern theme before paint");
}

if (!signin.includes('name="account"') || !signin.includes('autoComplete="username"')) {
  failures.push("sign-in must use a direct account and password");
}
if (/邮箱|邀请|邮件链接|type="email"|name="email"/.test(signin)) {
  failures.push("sign-in must not expose email or invitation-link concepts");
}
if (!signin.includes('pattern="[A-Za-z0-9._-]{3,32}"')) {
  failures.push("sign-in must reject email-shaped identifiers and enforce account names");
}

if (!design.includes("Modern Desktop System") || design.includes('visual_direction: "A precise local AI content production workstation: Windows')) {
  failures.push("DESIGN.md is not the modern desktop source of truth");
}

if (!routeIcon.includes("LucideIcon") || routeIcon.includes("pixel-sprite")) {
  failures.push("compatibility route icons must render the modern line-icon system");
}
if (!mascot.includes("modern-mascot") || mascot.includes("pixel-mascot-head")) {
  failures.push("compatibility mascot must not render pixel artwork");
}

if (failures.length) {
  console.error(failures.map((failure) => `- ${failure}`).join("\n"));
  process.exit(1);
}

console.log("modern desktop design contract checks passed");
