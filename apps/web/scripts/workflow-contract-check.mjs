import { readFile } from "node:fs/promises";

const files = {
  topics: new URL("../src/app/topics/page.tsx", import.meta.url),
  script: new URL("../src/app/script/page.tsx", import.meta.url),
  sectionEditor: new URL("../src/components/ScriptSectionEditor.tsx", import.meta.url),
  topicCard: new URL("../src/components/TopicCandidateCard.tsx", import.meta.url),
  agentPanel: new URL("../src/components/PixelAgentPanel.tsx", import.meta.url),
  appShell: new URL("../src/components/AppShell.tsx", import.meta.url),
  types: new URL("../src/lib/types.ts", import.meta.url),
  home: new URL("../src/app/page.tsx", import.meta.url),
  video: new URL("../src/app/video/page.tsx", import.meta.url),
  actions: new URL("../src/app/actions.ts", import.meta.url),
  settings: new URL("../src/app/settings/page.tsx", import.meta.url),
};

const [topicsPage, scriptPage, sectionEditor, topicCard, agentPanel, appShell, typesFile, homePage, videoPage, actionsFile, settingsPage] = await Promise.all(
  Object.values(files).map((file) => readFile(file, "utf8")),
);

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

if (!homePage.includes("selected_topic_id")) {
  failures.push("The workbench must distinguish unapproved topics from approved scripts.");
}

if (!typesFile.includes("hermes_radar")) {
  failures.push("SystemStatus must expose Hermes radar source status.");
}

if (!agentPanel.includes("Hermes 雷达") || !agentPanel.includes("hermes_radar")) {
  failures.push("Agent panel must show real Hermes radar ingestion counts.");
}

for (const route of ["/draft", "/knowledge", "/sources", "/learning", "/settings"]) {
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

if (!homePage.includes("primaryFlowHref") || !homePage.includes("href={primaryFlowHref}")) {
  failures.push("The workbench primary CTA must route by approved/unapproved state.");
}

if (!actionsFile.includes("processVideoAndPackageAction")) {
  failures.push("Server actions must expose one command that processes a video and creates a draft package.");
}

if (!videoPage.includes("processVideoAndPackageAction")) {
  failures.push("/video must let the user process a video into a complete draft artifact package.");
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

if (homePage.includes('href="/script"')) {
  failures.push("The workbench must not link directly to /script without checking approval state.");
}

if (failures.length > 0) {
  console.error(failures.map((failure) => `- ${failure}`).join("\n"));
  process.exit(1);
}

console.log("workflow contract checks passed");
