import { readFile } from "node:fs/promises";

const root = new URL("../", import.meta.url);
const read = (path) => readFile(new URL(path, root), "utf8");
const [design, globals, overrides, shell, home, composer, api, library, production, people, templates, publishing, knowledge, sources, desktop, apiSpec, desktopPackage, layout, signin, routeIcon, mascot] = await Promise.all([
  read("../../DESIGN.md"),
  read("src/app/globals.css"),
  read("src/app/modern-overrides.css"),
  read("src/components/AppShell.tsx"),
  read("src/app/page.tsx"),
  read("src/components/HomeCommandComposer.tsx"),
  read("src/lib/api.ts"),
  read("src/components/MaterialLibraryWorkspace.tsx"),
  read("src/app/production/page.tsx"),
  read("src/components/PeopleAndVoiceWorkspace.tsx"),
  read("src/components/TemplateLibraryWorkspace.tsx"),
  read("src/components/PublishingWorkspace.tsx"),
  read("src/app/knowledge/page.tsx"),
  read("src/app/sources/page.tsx"),
  read("../desktop/main.js"),
  read("../api/pyinstaller.spec"),
  read("../desktop/package.json"),
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
  "--sidebar-width: 220px",
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

for (const [name, source] of [["home", home], ["composer", composer], ["library", library], ["production", production]]) {
  if (/Pixel(?:Icon|Panel|Button|Status|Mascot|Agent)/.test(source)) failures.push(`${name} north-star UI imports Pixel components`);
}

for (const required of ["HomeCommandComposer", "想制作什么", "listCreationTasks", "/tasks/"]) {
  if (!home.includes(required)) failures.push(`modern home is missing: ${required}`);
}
for (const retired of ["recent_batches", "ProductionBatch", "production?batch="]) {
  if (home.includes(retired)) failures.push(`home still projects legacy Batch tasks: ${retired}`);
}

for (const retired of ["?batch=", "rendered_count", "target_count"]) {
  if (shell.includes(retired)) failures.push(`AppShell still projects legacy Batch task fields: ${retired}`);
}

for (const retired of ["legacyTaskProjection", "foundation?.recent_batches", "params.batch", "ProductionBatch", "recent_batches", "ProductionTaskControls"]) {
  if (production.includes(retired)) failures.push(`production still projects a legacy Batch path: ${retired}`);
}
for (const required of ["defaultInspector", "已创建任务", "等待开始", "/tasks/", "CreateProductionTaskForm"]) {
  if (!production.includes(required)) failures.push(`production is missing the CreationTask boundary: ${required}`);
}

for (const required of ["/production?", 'new: "1"', "goal: normalizedGoal", "检查可用内容", "下一步只检查可用内容，不会直接开始生成"]) {
  if (!composer.includes(required)) failures.push(`home command composer is missing the CreationPlan entry: ${required}`);
}
for (const retired of ["previewVoiceCommand", "executeVoiceCommand", "VoiceCommandResponse", "createProductionBatch", "添加素材", "语音输入"]) {
  if (`${composer}\n${api}`.includes(retired)) failures.push(`home CreationPlan path still consumes a retired helper: ${retired}`);
}

for (const retired of [".modern-home__eyebrow", ".home-plan-readiness", ".home-composer__availability", ".composer-icon-button", ".home-run-board__notifications"]) {
  if (overrides.includes(retired)) failures.push(`modern desktop CSS still contains a retired empty-space selector: ${retired}`);
}

for (const retired of [".modern-library__header", ".source-workspace__header", ".source-workspace__actions"]) {
  if (overrides.includes(retired)) failures.push(`modern desktop CSS still contains a retired duplicate-heading selector: ${retired}`);
}
for (const [name, source, retired] of [
  ["shell inspector", shell, "<span>详情</span>"],
  ["library", library, "<h1>素材库</h1>"],
  ["people", people, "<h1>人物与声音</h1>"],
  ["templates", templates, "<h1>视频模板</h1>"],
  ["publishing", publishing, "<h1>发布与数据</h1>"],
  ["knowledge", knowledge, "内容资料 · 我的知识库"],
  ["sources", sources, "内容资料 · 外部信息源"],
  ["production", production, "<div><span>创作任务</span><h1>创作任务</h1>"],
]) {
  if (source.includes(retired)) failures.push(`${name} still renders a retired duplicate heading: ${retired}`);
}

for (const required of ["scanProductionMaterials", "reviewProductionMaterial", "素材库", "/media"]) {
  if (!library.includes(required)) failures.push(`material library is missing: ${required}`);
}

for (const required of ["titleBarStyle: 'hiddenInset'", "minWidth: 1024", "minHeight: 700", "backgroundColor: '#f7f7f5'"]) {
  if (!desktop.includes(required)) failures.push(`Electron chrome is missing: ${required}`);
}

if (!apiSpec.includes("console=False") || apiSpec.includes("console=True")) {
  failures.push("api-server must use the background PyInstaller bootloader");
}
for (const retired of ["build:worker", "worker-server"]) {
  if (desktopPackage.includes(retired)) failures.push(`desktop package still includes retired automation worker: ${retired}`);
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
