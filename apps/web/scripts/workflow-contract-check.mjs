import { readFile } from "node:fs/promises";

const files = {
  topics: new URL("../src/app/topics/page.tsx", import.meta.url),
  script: new URL("../src/app/script/page.tsx", import.meta.url),
  draft: new URL("../src/app/draft/page.tsx", import.meta.url),
  oldMontage: new URL(
    "../src/app/production/[batchId]/episodes/[episodeId]/montage/page.tsx",
    import.meta.url,
  ),
  homeComposer: new URL(
    "../src/components/HomeCommandComposer.tsx",
    import.meta.url,
  ),
  appShell: new URL("../src/components/AppShell.tsx", import.meta.url),
  api: new URL("../src/lib/api.ts", import.meta.url),
  home: new URL("../src/app/page.tsx", import.meta.url),
  production: new URL("../src/app/production/page.tsx", import.meta.url),
  taskPage: new URL("../src/app/tasks/[taskId]/page.tsx", import.meta.url),
  taskWorkspace: new URL(
    "../src/components/CreationTaskWorkspace.tsx",
    import.meta.url,
  ),
  actions: new URL("../src/app/actions.ts", import.meta.url),
  settings: new URL("../src/app/settings/page.tsx", import.meta.url),
};

async function readOptional(file) {
  try {
    return await readFile(file, "utf8");
  } catch {
    return "";
  }
}

const sources = Object.fromEntries(
  await Promise.all(
    Object.entries(files).map(async ([name, file]) => [
      name,
      await readOptional(file),
    ]),
  ),
);
const failures = [];

for (const retiredRoute of ["topics", "script", "draft", "oldMontage"]) {
  if (sources[retiredRoute]) {
    failures.push(`Retired web route still exists: ${retiredRoute}`);
  }
}

for (const required of [
  "HomeCommandComposer",
  "listCreationTasks",
  "/production",
  "/tasks/",
]) {
  if (!`${sources.home}\n${sources.homeComposer}\n${sources.production}`.includes(required)) {
    failures.push(`CreationTask entry path is missing: ${required}`);
  }
}

for (const required of [
  "getProductionFoundation",
  "listCreationTasks",
  "CreateProductionTaskForm",
  "defaultInspector",
  "已创建任务",
  "等待开始",
  'href={`/tasks/${encodeURIComponent(task.id)}`}',
]) {
  if (!sources.production.includes(required)) {
    failures.push(`CreationTask list or preflight is missing: ${required}`);
  }
}

for (const retired of [
  "ProductionBatch",
  "recent_batches",
  "eligible_episodes",
  "recent_montage_plans",
  "params.batch",
  "params.plan",
  "ProductionTaskControls",
  "CreationTaskTopicWorkspace",
]) {
  if (sources.production.includes(retired)) {
    failures.push(`/production still activates legacy behavior: ${retired}`);
  }
}

for (const required of [
  "CreationTaskWorkspace",
  "getCreationTaskWorkspace",
  "getCreationTaskTopics",
  "listCreationTasks",
]) {
  if (!`${sources.taskPage}\n${sources.taskWorkspace}`.includes(required)) {
    failures.push(`Canonical /tasks/{taskId} workspace is missing: ${required}`);
  }
}

for (const retiredPath of [
  "/api/episodes",
  "/api/drafts/package",
  "/api/drafts/import",
  "/api/production/batches",
  "/api/production/montage-plans",
  "/api/production/episodes",
]) {
  if (`${sources.api}\n${sources.actions}`.includes(retiredPath)) {
    failures.push(`Retired API writer still has an active web client: ${retiredPath}`);
  }
}

for (const retiredRoute of ["/topics", "/script", "/draft"]) {
  if (`${sources.appShell}\n${sources.production}\n${sources.actions}`.includes(retiredRoute)) {
    failures.push(`Retired route is still linked by the active shell: ${retiredRoute}`);
  }
}

if (!sources.api.includes("previewCreationPlan") || !sources.api.includes("confirmCreationPlan")) {
  failures.push("CreationPlan preflight and confirmation clients must remain available.");
}
if (!sources.actions.includes("updateRuntimeSettingsAction")) {
  failures.push("Server actions must expose a runtime settings update action.");
}
if (!sources.settings.includes("updateRuntimeSettingsAction")) {
  failures.push("/settings must submit editable runtime settings.");
}

if (failures.length > 0) {
  console.error(failures.map((failure) => `- ${failure}`).join("\n"));
  process.exit(1);
}

console.log("workflow contract checks passed");
