import { readFile } from "node:fs/promises";
import assert from "node:assert/strict";

const source = new URL("../src/", import.meta.url);
const read = path => readFile(new URL(path, source), "utf8");
const [shell, workspace, navigation, host, controls] = await Promise.all([
  read("components/AppShell.tsx"), read("components/CreationTaskWorkspace.tsx"),
  read("components/creation-task/TaskViewNavigation.tsx"), read("components/workbench/Workbench.tsx"),
  read("components/ui/workbench-controls.module.css"),
]);
const primaryNav = shell.match(/const primaryNav: NavItem\[\] = \[([\s\S]*?)\n\];/)?.[1];
assert(primaryNav, "the existing primary navigation must remain explicit");
const entries = [...primaryNav.matchAll(/href: "([^"]+)", label: "([^"]+)"/g)].map(match => [match[1], match[2]]);
assert.deepEqual(entries, [["/", "首页"], ["/library", "素材库"], ["/knowledge", "知识库"], ["/profiles", "人物与声音"], ["/templates", "视频模板"], ["/history", "发布与数据"]]);
for (const marker of ["desktop-nav-section--creation-tasks", 'aria-label="创作者与设备设置"', 'href="/settings"']) assert(shell.includes(marker), `protected navigation entry missing: ${marker}`);
assert(!workspace.includes('aria-label="制作阶段"'), "the nine-stage primary rail must be retired");
assert(navigation.includes('aria-label="作品视图"') && navigation.includes('aria-label="制作进度与恢复"'));
assert(host.includes('content: null, open: false'), "closed views must release their component tree");
assert(host.includes("<HomeCommandComposer"), "the host must own one persistent composer");
assert(!host.includes("cancelCreationExecution"), "closing a view must never cancel production");
assert(controls.includes("var(--focus)") && controls.includes("var(--surface-1)"));
for (const file of ["tabs", "button", "scroll-area", "dropdown-menu", "input-group"]) {
  const text = await read(`components/ui/${file}.tsx`);
  assert(text.includes("https://ui.shadcn.com/r/styles/base-nova/"), `${file}: official component source is not recorded`);
  assert(text.includes("MIT"), `${file}: source license attribution is missing`);
}
console.log("Workbench static contracts passed. Real interaction, media and desktop acceptance are separate.");
