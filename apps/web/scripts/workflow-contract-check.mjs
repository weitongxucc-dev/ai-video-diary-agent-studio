import { readFile } from "node:fs/promises";

const files = {
  css: new URL("../src/app/globals.css", import.meta.url),
  topics: new URL("../src/app/topics/page.tsx", import.meta.url),
  script: new URL("../src/app/script/page.tsx", import.meta.url),
  sectionEditor: new URL("../src/components/ScriptSectionEditor.tsx", import.meta.url),
  topicCard: new URL("../src/components/TopicCandidateCard.tsx", import.meta.url),
  homeComposer: new URL("../src/components/HomeCommandComposer.tsx", import.meta.url),
  appShell: new URL("../src/components/AppShell.tsx", import.meta.url),
  api: new URL("../src/lib/api.ts", import.meta.url),
  types: new URL("../src/lib/types.ts", import.meta.url),
  home: new URL("../src/app/page.tsx", import.meta.url),
  templates: new URL("../src/app/templates/page.tsx", import.meta.url),
  health: new URL("../src/app/health/page.tsx", import.meta.url),
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

const [
  css,
  topicsPage,
  scriptPage,
  sectionEditor,
  topicCard,
  homeComposer,
  appShell,
  apiFile,
  typesFile,
  homePage,
  templatesPage,
  healthPage,
  actionsFile,
  settingsPage,
] = await Promise.all(Object.values(files).map((file) => readOptional(file)));

const failures = [];

if (scriptPage.includes("approveTopic(") || scriptPage.includes("approveTopic,")) {
  failures.push("/script must not approve a topic automatically.");
}

if (!scriptPage.includes("needs_topic_review")) {
  failures.push("/script must expose an explicit needs_topic_review state.");
}

if (!topicsPage.includes("approveTopicAndGenerateScript")) {
  failures.push("/topics must persist approval before navigating to the script page.");
}

if (!topicsPage.includes("regenerateTopicsAction")) {
  failures.push("/topics must expose an explicit regenerate topics action.");
}

if (!topicsPage.includes("generateTopicsAction")) {
  failures.push("/topics must expose an explicit generate topics action.");
}

if (topicsPage.includes("await generateTopics(")) {
  failures.push("/topics must not call the model automatically during page load.");
}

if (topicsPage.includes('href="/script"')) {
  failures.push("/topics must not bypass approval with a direct /script link.");
}

if (!topicsPage.includes("sendTopicFeedbackAction")) {
  failures.push("/topics must submit topic feedback to the API.");
}

if (!topicsPage.includes("searchParams") || !topicsPage.includes("topic_id")) {
  failures.push("/topics must let the user inspect any candidate via a topic_id route state.");
}

if (!topicsPage.includes("activeTopicId")) {
  failures.push("/topics must pass the active topic id into candidate cards.");
}

if (!topicCard.includes("topic_id=${encodeURIComponent(topic.id)}")) {
  failures.push("Topic candidate cards must link to their own topic_id.");
}

if (
  !sectionEditor.includes("sendScriptSectionFeedbackAction")
  && !scriptPage.includes("sendScriptSectionFeedbackAction")
) {
  failures.push("Script sections must submit feedback to the API.");
}

if (!typesFile.includes("hermes_radar")) {
  failures.push("SystemStatus must expose Hermes radar source status.");
}

if (
  !homePage.includes("HomeCommandComposer")
  || !homeComposer.includes("检查可用内容")
  || !homeComposer.includes('new: "1"')
  || !apiFile.includes("previewCreationPlan")
  || !apiFile.includes("confirmCreationPlan")
  || !typesFile.includes("CreationPlanPreview")
) {
  failures.push("The home workbench must carry a goal into the reviewed creation-plan preflight.");
}

for (const retiredConsumer of ["previewVoiceCommand", "executeVoiceCommand", "VoiceCommandResponse", "result_episode", "result_edit_plan"]) {
  if (homeComposer.includes(retiredConsumer) || apiFile.includes(retiredConsumer) || typesFile.includes(retiredConsumer)) {
    failures.push(`Retired Episode command consumer must be absent: ${retiredConsumer}`);
  }
}

for (const route of ["/", "/production", "/library", "/templates", "/knowledge", "/sources", "/health", "/settings"]) {
  const routeBlock = `href: "${route}"`;
  const routeIndex = appShell.indexOf(routeBlock);
  if (routeIndex === -1) {
    failures.push(`AppShell must expose ${route} as a real navigation route.`);
    continue;
  }
  const nextRouteIndex = appShell.indexOf("},", routeIndex);
  const navSlice = appShell.slice(routeIndex, nextRouteIndex === -1 ? routeIndex + 120 : nextRouteIndex);
  if (navSlice.includes("disabled: true")) {
    failures.push(`${route} must not be disabled in the sidebar.`);
  }
}

if (
  !homePage.includes("listCreationTasks")
  || !homePage.includes("CreationTask")
  || !homePage.includes("production?plan=")
  || homePage.includes("recent_batches")
  || homePage.includes("ProductionBatch")
) {
  failures.push("The workbench must prioritize workspace-scoped CreationTasks instead of legacy Batch projections.");
}

if (!apiFile.includes("getContentTemplates")) {
  failures.push("API client must load 2.0 content templates.");
}

if (!apiFile.includes("selectEpisodeContentTemplate")) {
  failures.push("API client must select an episode content template.");
}

if (!apiFile.includes("getRuntimeHealth")) {
  failures.push("API client must load runtime health diagnostics.");
}

if (!actionsFile.includes("selectContentTemplateAction")) {
  failures.push("Server actions must submit content template selection.");
}

for (const required of ["getContentTemplates", "selectContentTemplateAction", "tool_review", "tutorial_demo", "video_diary"]) {
  if (!templatesPage.includes(required)) {
    failures.push(`/templates must include ${required}.`);
  }
}

for (const required of ["getRuntimeHealth", "运行健康", "修复动作", "DeepSeek", "ffmpeg"]) {
  if (!healthPage.includes(required)) {
    failures.push(`/health must include ${required}.`);
  }
}

if (!typesFile.includes("subtitle_path") || !typesFile.includes("cover_path")) {
  failures.push("DraftPackage types must expose generated subtitle and cover paths.");
}

if (!actionsFile.includes("updateRuntimeSettingsAction")) {
  failures.push("Server actions must expose a runtime settings update action.");
}

if (!settingsPage.includes("updateRuntimeSettingsAction")) {
  failures.push("/settings must submit editable runtime settings, not just render a read-only snapshot.");
}

if (failures.length > 0) {
  console.error(failures.map((failure) => `- ${failure}`).join("\n"));
  process.exit(1);
}

console.log("workflow contract checks passed");
