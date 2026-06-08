import { readFile } from "node:fs/promises";

const files = {
  css: new URL("../src/app/globals.css", import.meta.url),
  topics: new URL("../src/app/topics/page.tsx", import.meta.url),
  script: new URL("../src/app/script/page.tsx", import.meta.url),
  sectionEditor: new URL("../src/components/ScriptSectionEditor.tsx", import.meta.url),
  scriptQuickRewritePanel: new URL("../src/components/ScriptQuickRewritePanel.tsx", import.meta.url),
  scriptQuickRewriteButton: new URL("../src/components/ScriptQuickRewriteButton.tsx", import.meta.url),
  topicCard: new URL("../src/components/TopicCandidateCard.tsx", import.meta.url),
  agentPanel: new URL("../src/components/PixelAgentPanel.tsx", import.meta.url),
  appShell: new URL("../src/components/AppShell.tsx", import.meta.url),
  retroChrome: new URL("../src/components/RetroChrome.tsx", import.meta.url),
  api: new URL("../src/lib/api.ts", import.meta.url),
  types: new URL("../src/lib/types.ts", import.meta.url),
  home: new URL("../src/app/page.tsx", import.meta.url),
  templates: new URL("../src/app/templates/page.tsx", import.meta.url),
  health: new URL("../src/app/health/page.tsx", import.meta.url),
  learning: new URL("../src/app/learning/page.tsx", import.meta.url),
  video: new URL("../src/app/video/page.tsx", import.meta.url),
  actions: new URL("../src/app/actions.ts", import.meta.url),
  settings: new URL("../src/app/settings/page.tsx", import.meta.url),
  profile: new URL("../src/app/profile/page.tsx", import.meta.url),
  draft: new URL("../src/app/draft/page.tsx", import.meta.url),
  cover: new URL("../src/app/cover/page.tsx", import.meta.url),
  editPlan: new URL("../src/app/edit-plan/page.tsx", import.meta.url),
  logs: new URL("../src/app/logs/page.tsx", import.meta.url),
  setup: new URL("../src/app/setup/page.tsx", import.meta.url),
  voiceCommandConsole: new URL("../src/components/VoiceCommandConsole.tsx", import.meta.url),
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
  scriptQuickRewritePanel,
  scriptQuickRewriteButton,
  topicCard,
  agentPanel,
  appShell,
  retroChrome,
  apiFile,
  typesFile,
  homePage,
  templatesPage,
  healthPage,
  learningPage,
  videoPage,
  actionsFile,
  settingsPage,
  profilePage,
  draftPage,
  coverPage,
  editPlanPage,
  logsPage,
  setupPage,
  voiceCommandConsole,
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

if (!scriptPage.includes("getScriptRewriteIntents")) {
  failures.push("/script must load script quick rewrite intents.");
}

if (!scriptPage.includes("ScriptQuickRewritePanel")) {
  failures.push("/script must render ScriptQuickRewritePanel for each script section.");
}

for (const required of ["searchParams", "script_error", "script-error-banner"]) {
  if (!scriptPage.includes(required)) {
    failures.push(`/script must show recoverable script action errors with ${required}.`);
  }
}

for (const required of [
  "ScriptQuickRewritePanel",
  "rewrite_section_title",
  "rewrite_user_note",
  "regenerateScriptSectionAction",
  "ScriptQuickRewriteButton",
]) {
  if (!scriptQuickRewritePanel.includes(required)) {
    failures.push(`ScriptQuickRewritePanel must include ${required}.`);
  }
}

if (
  !scriptQuickRewritePanel.includes("rewrite_intent_id")
  && !scriptQuickRewriteButton.includes("rewrite_intent_id")
) {
  failures.push("ScriptQuickRewritePanel must submit rewrite_intent_id through its button.");
}

for (const required of ["useFormStatus", "pending", "disabled={pending}", "rewrite_intent_id"]) {
  if (!scriptQuickRewriteButton.includes(required)) {
    failures.push(`ScriptQuickRewriteButton must prevent duplicate submits with ${required}.`);
  }
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

for (const route of ["/templates", "/profile", "/draft", "/knowledge", "/sources", "/learning", "/health", "/logs", "/setup", "/settings"]) {
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

const editPlanRoute = 'href: "/edit-plan"';
const editPlanRouteIndex = appShell.indexOf(editPlanRoute);
if (editPlanRouteIndex === -1) {
  failures.push("AppShell must expose /edit-plan as the 2.0 visual edit plan route.");
} else {
  const nextRouteIndex = appShell.indexOf("},", editPlanRouteIndex);
  const navSlice = appShell.slice(
    editPlanRouteIndex,
    nextRouteIndex === -1 ? editPlanRouteIndex + 120 : nextRouteIndex,
  );
  if (navSlice.includes("disabled: true")) {
    failures.push("/edit-plan must not be disabled in the sidebar.");
  }
}

const coverRoute = 'href: "/cover"';
const coverRouteIndex = appShell.indexOf(coverRoute);
if (coverRouteIndex === -1) {
  failures.push("AppShell must expose /cover as the 2.1 cover editor route.");
} else {
  const nextRouteIndex = appShell.indexOf("},", coverRouteIndex);
  const navSlice = appShell.slice(
    coverRouteIndex,
    nextRouteIndex === -1 ? coverRouteIndex + 120 : nextRouteIndex,
  );
  if (navSlice.includes("disabled: true")) {
    failures.push("/cover must not be disabled in the sidebar.");
  }
}

if (!appShell.includes("VoiceCommandConsole") || !appShell.includes("<VoiceCommandConsole")) {
  failures.push("AppShell must render the reusable VoiceCommandConsole.");
}

for (const fakeMenu of ["文件(F)", "编辑(E)", "视图(V)", "工具(T)", "帮助(H)"]) {
  if (retroChrome.includes(fakeMenu)) {
    failures.push(`RetroChrome must not keep fake disabled menu item ${fakeMenu}.`);
  }
}

for (const required of [
  "chromeMenuGroups",
  "内容",
  "画像",
  "运行",
  "设置",
  "system-menu-popover",
  "menu-panel-edit",
  "menu-panel-link",
  'href: "/templates"',
  'href: "/profile"',
  'href: "/video"',
  'href: "/logs"',
  'href: "/setup"',
  'href: "/settings"',
]) {
  if (!retroChrome.includes(required)) {
    failures.push(`RetroChrome must expose real top menu surface ${required}.`);
  }
}

for (const required of ["语音指令", "COMMAND", "previewVoiceCommand", "executeVoiceCommand", "等待确认"]) {
  if (!voiceCommandConsole.includes(required)) {
    failures.push(`VoiceCommandConsole must include ${required}.`);
  }
}

for (const required of ["MediaRecorder", "transcribeVoiceAudio", "开始录音", "停止录音", "recording"]) {
  if (!voiceCommandConsole.includes(required)) {
    failures.push(`VoiceCommandConsole must implement microphone recording with ${required}.`);
  }
}

for (const required of ["剪辑计划", "视频浏览窗口", "原片标记", "剪辑计划时间轴", "语音定位结果", "剪映轨道预览"]) {
  if (!editPlanPage.includes(required)) {
    failures.push(`/edit-plan must include ${required}.`);
  }
}

if (!editPlanPage.includes("timeline-action-chip")) {
  failures.push("/edit-plan timeline segments must use compact Win95 action chips instead of full-color blocks.");
}

for (const required of [".voice-command-console", ".edit-plan-page", ".video-preview-window", ".source-marker-strip", ".edit-plan-timeline", ".timeline-action-chip"]) {
  if (!css.includes(required)) {
    failures.push(`globals.css must define ${required}.`);
  }
}

for (const required of [".script-quick-rewrite-panel", ".script-quick-rewrite-grid", ".script-quick-rewrite-note"]) {
  if (!css.includes(required)) {
    failures.push(`globals.css must define script quick rewrite surface ${required}.`);
  }
}

if (!css.includes(".script-error-banner")) {
  failures.push("globals.css must define .script-error-banner.");
}

if (!css.includes(".voice-recording-indicator")) {
  failures.push("globals.css must define .voice-recording-indicator.");
}

for (const required of [
  ".system-menu-popover",
  ".menu-panel-edit",
  ".bounded-list",
  ".video-list.bounded-list",
  ".edit-decision-grid.bounded-list",
  ".source-marker-list.bounded-list",
  ".video-file-row code",
  ".edit-decision-card p",
]) {
  if (!css.includes(required)) {
    failures.push(`globals.css must define compact overflow guard ${required}.`);
  }
}

for (const required of [
  "bounded-list",
  "visibleVideos",
  "MAX_VISIBLE_PENDING_VIDEOS",
  "还有",
]) {
  if (!videoPage.includes(required)) {
    failures.push(`/video must cap visible pending videos and use bounded-list guard ${required}.`);
  }
}

for (const required of [
  "bounded-list",
  "visibleSegments",
  "visibleDecisions",
  "visibleMarkers",
  "MAX_VISIBLE_EDIT_SEGMENTS",
  "MAX_VISIBLE_EDIT_DECISIONS",
  "MAX_VISIBLE_MARKERS",
]) {
  if (!editPlanPage.includes(required)) {
    failures.push(`/edit-plan must cap timeline/decision/marker surfaces with ${required}.`);
  }
}

if (!homePage.includes("primaryFlowHref") || !homePage.includes("href={primaryFlowHref}")) {
  failures.push("The workbench primary CTA must route by approved/unapproved state.");
}

if (!actionsFile.includes("processVideoAndPackageAction")) {
  failures.push("Server actions must expose one command that processes a video and creates a draft package.");
}

if (!apiFile.includes("getContentTemplates")) {
  failures.push("API client must load 2.0 content templates.");
}

if (!apiFile.includes("selectEpisodeContentTemplate")) {
  failures.push("API client must select an episode content template.");
}

if (!apiFile.includes("getScriptRewriteIntents")) {
  failures.push("API client must load script quick rewrite intents.");
}

for (const required of ["getEpisodeCover", "updateEpisodeCover", "/api/episodes/${episodeId}/cover"]) {
  if (!apiFile.includes(required)) {
    failures.push(`API client must expose cover editor API ${required}.`);
  }
}

if (!apiFile.includes("/api/script/rewrite-intents")) {
  failures.push("API client must call the script quick rewrite intents route.");
}

for (const required of ["ScriptSectionRegenerateInput", "rewrite_intent_id", "rewrite_section_title", "rewrite_user_note"]) {
  if (!apiFile.includes(required)) {
    failures.push(`API client must submit structured script rewrite field ${required}.`);
  }
}

if (!apiFile.includes("getRuntimeHealth")) {
  failures.push("API client must load runtime health diagnostics.");
}

if (!apiFile.includes("getDiagnosticsStatus")) {
  failures.push("API client must load diagnostics status.");
}

if (!apiFile.includes("diagnosticsReportUrl")) {
  failures.push("API client must expose a diagnostics report download URL.");
}

if (!apiFile.includes("transcribeVoiceAudio")) {
  failures.push("API client must upload recorded voice audio for ASR.");
}

for (const required of ["confirmPreference", "disablePreference", "restorePreference", "deletePreference"]) {
  if (!apiFile.includes(required)) {
    failures.push(`API client must expose long-term preference action ${required}.`);
  }
}

if (!typesFile.includes("DiagnosticsStatus")) {
  failures.push("Types must expose DiagnosticsStatus.");
}

if (!typesFile.includes("VoiceTranscription")) {
  failures.push("Types must expose VoiceTranscription.");
}

for (const required of ["PreferenceCandidate", "LongTermPreference"]) {
  if (!typesFile.includes(required)) {
    failures.push(`Types must expose ${required}.`);
  }
}

for (const required of ["ScriptRewriteIntent", "ScriptRewriteIntents"]) {
  if (!typesFile.includes(required)) {
    failures.push(`Types must expose ${required}.`);
  }
}

for (const required of ["CoverDraft", "CoverTemplate"]) {
  if (!typesFile.includes(required)) {
    failures.push(`Types must expose ${required}.`);
  }
}

if (!actionsFile.includes("selectContentTemplateAction")) {
  failures.push("Server actions must submit content template selection.");
}

if (!actionsFile.includes("updateCoverAction")) {
  failures.push("Server actions must expose a cover update action.");
}

if (!actionsFile.includes("redirect_to")) {
  failures.push("Server actions must let setup wizard return to /setup after saving.");
}

for (const required of ["confirmPreferenceAction", "disablePreferenceAction", "restorePreferenceAction", "deletePreferenceAction"]) {
  if (!actionsFile.includes(required)) {
    failures.push(`Server actions must expose long-term preference action ${required}.`);
  }
}

for (const required of ["rewrite_intent_id", "rewrite_section_title", "rewrite_user_note"]) {
  if (!actionsFile.includes(required)) {
    failures.push(`Server actions must accept structured script rewrite field ${required}.`);
  }
}

for (const required of ["script_error", "section_rewrite_failed", "catch"]) {
  if (!actionsFile.includes(required)) {
    failures.push(`Server actions must redirect script rewrite failures with ${required}.`);
  }
}

for (const required of ["getContentTemplates", "selectContentTemplateAction", "tool_review", "tutorial_demo", "video_diary"]) {
  if (!templatesPage.includes(required)) {
    failures.push(`/templates must include ${required}.`);
  }
}

for (const required of [
  "getEpisodeCover",
  "updateCoverAction",
  "cover-preview-frame",
  "cover-editor-form",
  "cover-template-grid",
  "headline",
  "subline",
  "tag",
  "template_id",
]) {
  if (!coverPage.includes(required)) {
    failures.push(`/cover must implement cover editor field/surface ${required}.`);
  }
}

if (!draftPage.includes('href="/cover"')) {
  failures.push("/draft must link to the cover editor before package generation.");
}

for (const required of ["getRuntimeHealth", "运行健康", "修复动作", "DeepSeek", "ffmpeg"]) {
  if (!healthPage.includes(required)) {
    failures.push(`/health must include ${required}.`);
  }
}

for (const required of [
  "getRuntimeSettings",
  "getRuntimeHealth",
  "updateRuntimeSettingsAction",
  "首次配置向导",
  "DeepSeek API Key",
  "Obsidian Vault",
  "Video Inbox",
  "Local Whisper Python",
  "redirect_to",
]) {
  if (!setupPage.includes(required)) {
    failures.push(`/setup must implement first-run wizard surface ${required}.`);
  }
}

if (!homePage.includes("setupRequired") || !homePage.includes("/setup")) {
  failures.push("The workbench must route first-run users to /setup before production flow.");
}

for (const required of [".setup-page", ".setup-step-grid", ".setup-step-card", ".setup-health-list"]) {
  if (!css.includes(required)) {
    failures.push(`globals.css must define setup wizard surface ${required}.`);
  }
}

for (const required of ["long_term_preferences", "candidates", "确认进入长期记忆", "禁用", "恢复", "删除"]) {
  if (!learningPage.includes(required)) {
    failures.push(`/learning must expose long-term preference control ${required}.`);
  }
}

for (const required of [".preference-control-grid", ".preference-memory-card", ".preference-candidate-grid"]) {
  if (!css.includes(required)) {
    failures.push(`globals.css must define long-term preference surface ${required}.`);
  }
}

for (const required of ["getDiagnosticsStatus", "diagnosticsReportUrl", "内测日志", "错误报告包", "脱敏配置", "最近错误"]) {
  if (!logsPage.includes(required)) {
    failures.push(`/logs must include ${required}.`);
  }
}

for (const required of [".logs-page", ".log-category-grid", ".diagnostic-file-list", ".redacted-settings-grid"]) {
  if (!css.includes(required)) {
    failures.push(`globals.css must define diagnostics surface ${required}.`);
  }
}

if (!actionsFile.includes("confirmEditPlanAction")) {
  failures.push("Server actions must expose a real edit-plan confirmation action.");
}

if (!apiFile.includes("updateEditPlanSegmentDecision")) {
  failures.push("API client must expose edit-plan segment decision updates.");
}

if (!apiFile.includes("revertEditPlanSegmentDecision")) {
  failures.push("API client must expose edit-plan segment decision revert.");
}

if (!actionsFile.includes("updateEditPlanSegmentDecisionAction")) {
  failures.push("Server actions must submit edit-plan segment decisions.");
}

if (!actionsFile.includes("revertEditPlanSegmentDecisionAction")) {
  failures.push("Server actions must submit edit-plan segment decision reverts.");
}

if (!videoPage.includes("processVideoAndPackageAction")) {
  failures.push("/video must let the user process a video into a complete draft artifact package.");
}

if (!editPlanPage.includes("getEpisodeEditPlan")) {
  failures.push("/edit-plan must load the real episode edit plan from the API.");
}

if (!editPlanPage.includes("<video")) {
  failures.push("/edit-plan video browser must render a real video element, not only a mock preview.");
}

if (!editPlanPage.includes("confirmEditPlanAction")) {
  failures.push("/edit-plan must submit confirmation to the draft creation flow.");
}

if (!editPlanPage.includes("updateEditPlanSegmentDecisionAction")) {
  failures.push("/edit-plan must render actionable segment decision controls.");
}

if (!editPlanPage.includes("revertEditPlanSegmentDecisionAction")) {
  failures.push("/edit-plan must render segment decision rollback controls.");
}

if (!editPlanPage.includes("user_decision")) {
  failures.push("/edit-plan must show persisted user segment decisions.");
}

if (!typesFile.includes("subtitle_path") || !typesFile.includes("cover_path")) {
  failures.push("DraftPackage types must expose generated subtitle and cover paths.");
}

for (const required of ["template_id", "template_name", "track_preview", "effect_preview"]) {
  if (!typesFile.includes(required)) {
    failures.push(`DraftAsset types must expose Jianying template preview field ${required}.`);
  }
}

for (const required of ["剪映轨道结构", "基础模板元素", "track_preview", "effect_preview", "标题卡", "章节字幕条", "关键词高亮", "背景音乐"]) {
  if (!draftPage.includes(required)) {
    failures.push(`/draft must show Jianying template preview ${required}.`);
  }
}

for (const required of [".draft-template-preview", ".draft-track-preview", ".draft-effect-chip"]) {
  if (!css.includes(required)) {
    failures.push(`globals.css must define Jianying template preview surface ${required}.`);
  }
}

for (const required of [".cover-page", ".cover-preview-frame", ".cover-editor-form", ".cover-template-grid", ".cover-template-option"]) {
  if (!css.includes(required)) {
    failures.push(`globals.css must define cover editor surface ${required}.`);
  }
}

const draftTrackPreviewBlock = css.slice(
  css.indexOf(".draft-track-preview {"),
  css.indexOf(".draft-track-preview li"),
);
if (draftTrackPreviewBlock.includes("max-height") || draftTrackPreviewBlock.includes("overflow: hidden")) {
  failures.push("Draft track preview must not crop the required six Jianying tracks.");
}

if (!actionsFile.includes("updateRuntimeSettingsAction")) {
  failures.push("Server actions must expose a runtime settings update action.");
}

for (const required of ["getCreatorProfile", "updateCreatorProfile"]) {
  if (!apiFile.includes(required)) {
    failures.push(`API client must expose creator profile function ${required}.`);
  }
}

for (const required of ["CreatorProfile", "CreatorProfileUpdate"]) {
  if (!typesFile.includes(required)) {
    failures.push(`Types must expose ${required}.`);
  }
}

if (!actionsFile.includes("updateCreatorProfileAction")) {
  failures.push("Server actions must expose a creator profile update action.");
}

for (const required of [
  "AppShell",
  "workspace-page",
  "settings-form",
  "settings-field",
  "创作者画像",
  "identity",
  "content_domains",
  "tone_keywords",
  "preferred_phrases",
  "banned_phrases",
  "good_examples",
  "bad_examples",
]) {
  if (!profilePage.includes(required)) {
    failures.push(`/profile must include creator profile field/surface ${required}.`);
  }
}

if (!actionsFile.includes("markPublishedAction")) {
  failures.push("Server actions must expose a publish feedback action.");
}

for (const required of ["markPublishedAction", "publish_url", "play_count", "feedback_reason"]) {
  if (!draftPage.includes(required)) {
    failures.push(`/draft must include publish feedback field/action ${required}.`);
  }
}

for (const required of ["analyzeCommentInsights", "CommentInsightsResponse"]) {
  if (!apiFile.includes(required)) {
    failures.push(`API client must expose publish comment insight function/type ${required}.`);
  }
}

if (!actionsFile.includes("analyzeCommentInsightsAction")) {
  failures.push("Server actions must expose a publish comment insight action.");
}

for (const required of ["评论洞察", "comments_text", "analyzeCommentInsightsAction"]) {
  if (!draftPage.includes(required)) {
    failures.push(`/draft must include publish comment insight field/action ${required}.`);
  }
}

if (!learningPage.includes("published_comment_insight") || !learningPage.includes("评论洞察")) {
  failures.push("/learning must label publish comment insight feedback separately.");
}

if (
  draftPage.includes("生成并导入") ||
  draftPage.includes("2 自动导入剪映") ||
  draftPage.includes("已自动导入")
) {
  failures.push("/draft must show template structure before import, not a create-and-import flow.");
}

for (const required of ["生成素材包", "确认轨道结构", "确认导入剪映"]) {
  if (!draftPage.includes(required)) {
    failures.push(`/draft must expose pre-import confirmation copy ${required}.`);
  }
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
