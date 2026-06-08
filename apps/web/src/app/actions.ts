"use server";

import { redirect } from "next/navigation";
import {
  analyzeCommentInsights,
  approveScript,
  approveTopic,
  confirmPreference,
  createEpisodeDraft,
  createDraftPackage,
  deletePreference,
  disablePreference,
  generateTopics,
  generateScript,
  ingestManualLink,
  importDraftPackage,
  markEpisodePublished,
  processAdHocEpisodeVideo,
  processAdHocEpisodeVideoBatch,
  processEpisodeVideo,
  processVideo,
  regenerateEpisodeScript,
  regenerateEpisodeScriptSection,
  regenerateTopics,
  revertEditPlanSegmentDecision,
  revertEpisodeScriptVersion,
  revertFeedbackEvent,
  restorePreference,
  sendFeedback,
  selectEpisodeContentTemplate,
  syncSourceItems,
  updateEpisodeCover,
  updateCreatorProfile,
  updateEditPlanSegmentDecision,
  updateEpisodeScript,
  uploadEpisodeVideo,
  updateRuntimeSettings,
} from "@/lib/api";
import type {
  EditPlanSegmentDecisionAction,
  FeedbackValue,
  PreferenceKind,
  RuntimeSettingsUpdate,
  ScriptPackage,
} from "@/lib/types";

function requiredString(formData: FormData, key: string): string {
  const value = formData.get(key);

  if (typeof value !== "string" || value.length === 0) {
    throw new Error(`Missing form field: ${key}`);
  }

  return value;
}

function feedbackValue(formData: FormData): FeedbackValue {
  const value = requiredString(formData, "value");

  if (value !== "up" && value !== "down") {
    throw new Error("Invalid feedback value");
  }

  return value;
}

function optionalFeedbackValue(value: FormDataEntryValue | null): FeedbackValue | null {
  if (value === "up" || value === "down") {
    return value;
  }

  return null;
}

function optionalString(formData: FormData, key: string): string | undefined {
  const value = formData.get(key);

  return typeof value === "string" && value.trim() ? value.trim() : undefined;
}

function scriptActionFailureRedirect(error: "section_rewrite_failed"): never {
  redirect(`/script?script_error=${error}`);
}

function topicActionFailureRedirect(error: unknown): never {
  const message = error instanceof Error ? error.message : "";
  const code = message.includes("DeepSeek and at least one topic source must be configured")
    ? "configuration"
    : "generation_failed";

  if (code === "configuration") {
    redirect("/topics?topics_error=configuration");
  }
  redirect("/topics?topics_error=generation_failed");
}

function textField(formData: FormData, key: string): string {
  const value = formData.get(key);

  return typeof value === "string" ? value.trim() : "";
}

function multilineField(formData: FormData, key: string): string[] {
  const value = formData.get(key);
  if (typeof value !== "string") {
    return [];
  }

  return value
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
}

function optionalNonNegativeInteger(
  formData: FormData,
  key: string,
): number | undefined {
  const value = optionalString(formData, key);
  if (value === undefined) {
    return undefined;
  }

  const parsed = Number(value);
  if (!Number.isInteger(parsed) || parsed < 0) {
    throw new Error(`Invalid number field: ${key}`);
  }

  return parsed;
}

function optionalFeedbackValueField(
  formData: FormData,
  key: string,
): FeedbackValue | undefined {
  const value = optionalString(formData, key);
  if (value === undefined) {
    return undefined;
  }

  if (value !== "up" && value !== "down") {
    throw new Error(`Invalid feedback field: ${key}`);
  }

  return value;
}

function preferenceKind(formData: FormData): PreferenceKind {
  const kind = requiredString(formData, "kind");
  if (kind !== "prefer" && kind !== "avoid") {
    throw new Error("Invalid preference kind");
  }
  return kind;
}

function editPlanSegmentDecisionAction(
  formData: FormData,
): EditPlanSegmentDecisionAction {
  const action = requiredString(formData, "action");
  if (action !== "keep" && action !== "remove" && action !== "review") {
    throw new Error("Invalid edit plan segment decision");
  }
  return action;
}

function scriptFromFormData(formData: FormData): ScriptPackage {
  const sectionIds = formData
    .getAll("section_id")
    .filter((value): value is string => typeof value === "string");
  const titleOptions = formData
    .getAll("title_option")
    .filter((value): value is string => typeof value === "string")
    .map((value) => value.trim())
    .filter(Boolean);

  if (titleOptions.length === 0) {
    throw new Error("Missing form field: title_option");
  }

  return {
    id: requiredString(formData, "script_id"),
    hook: requiredString(formData, "hook"),
    sections: sectionIds.map((sectionId) => ({
      id: sectionId,
      title: requiredString(formData, `section_title:${sectionId}`),
      estimated_seconds: Number(
        requiredString(formData, `section_seconds:${sectionId}`),
      ),
      content: requiredString(formData, `section_content:${sectionId}`),
      feedback: optionalFeedbackValue(formData.get(`section_feedback:${sectionId}`)),
    })),
    title_options: titleOptions,
    cover_copy: requiredString(formData, "cover_copy"),
    publish_copy: requiredString(formData, "publish_copy"),
    comment_prompt: requiredString(formData, "comment_prompt"),
  };
}

export async function approveTopicAndGenerateScript(formData: FormData) {
  const episodeId = requiredString(formData, "episode_id");
  const topicId = requiredString(formData, "topic_id");

  await approveTopic(episodeId, topicId);
  await generateScript(episodeId);
  redirect("/script");
}

export async function selectContentTemplateAction(formData: FormData) {
  const episodeId = requiredString(formData, "episode_id");
  const templateId = requiredString(formData, "template_id");

  await selectEpisodeContentTemplate(episodeId, templateId);
  redirect("/templates");
}

export async function generateTopicsAction(formData: FormData) {
  const episodeId = requiredString(formData, "episode_id");

  try {
    await generateTopics(episodeId);
  } catch (error) {
    topicActionFailureRedirect(error);
  }
  redirect("/topics");
}

export async function sendTopicFeedbackAction(formData: FormData) {
  const episodeId = requiredString(formData, "episode_id");
  const topicId = requiredString(formData, "topic_id");
  const reason = formData.get("reason");

  await sendFeedback(
    episodeId,
    "topic",
    topicId,
    feedbackValue(formData),
    typeof reason === "string" ? reason : undefined,
  );
  redirect("/topics");
}

export async function regenerateTopicsAction(formData: FormData) {
  const episodeId = requiredString(formData, "episode_id");

  try {
    await regenerateTopics(episodeId);
  } catch (error) {
    topicActionFailureRedirect(error);
  }
  redirect("/topics");
}

export async function sendScriptSectionFeedbackAction(formData: FormData) {
  const episodeId = requiredString(formData, "episode_id");
  const sectionId = requiredString(formData, "section_id");
  const reason = formData.get("reason");

  await sendFeedback(
    episodeId,
    "script_section",
    sectionId,
    feedbackValue(formData),
    typeof reason === "string" ? reason : undefined,
  );
  redirect("/script");
}

export async function saveScriptAction(formData: FormData) {
  const episodeId = requiredString(formData, "episode_id");

  await updateEpisodeScript(
    episodeId,
    scriptFromFormData(formData),
    optionalString(formData, "reason"),
  );
  redirect("/script");
}

export async function regenerateScriptAction(formData: FormData) {
  const episodeId = requiredString(formData, "episode_id");

  await regenerateEpisodeScript(episodeId, optionalString(formData, "reason"));
  redirect("/script");
}

export async function regenerateScriptSectionAction(formData: FormData) {
  const episodeId = requiredString(formData, "episode_id");
  const sectionId = requiredString(formData, "section_id");

  try {
    await regenerateEpisodeScriptSection(
      episodeId,
      sectionId,
      {
        reason: optionalString(formData, "reason"),
        rewrite_intent_id: optionalString(formData, "rewrite_intent_id"),
        rewrite_section_title: optionalString(formData, "rewrite_section_title"),
        rewrite_user_note: optionalString(formData, "rewrite_user_note"),
      },
    );
  } catch {
    scriptActionFailureRedirect("section_rewrite_failed");
  }
  redirect("/script");
}

export async function revertScriptVersionAction(formData: FormData) {
  const episodeId = requiredString(formData, "episode_id");
  const versionId = requiredString(formData, "version_id");

  await revertEpisodeScriptVersion(
    episodeId,
    versionId,
    optionalString(formData, "reason"),
  );
  redirect("/script");
}

export async function updateCoverAction(formData: FormData) {
  const episodeId = requiredString(formData, "episode_id");

  await updateEpisodeCover(episodeId, {
    headline: requiredString(formData, "headline"),
    subline: textField(formData, "subline"),
    tag: textField(formData, "tag"),
    template_id: requiredString(formData, "template_id"),
  });
  redirect("/cover");
}

export async function revertFeedbackAction(formData: FormData) {
  const episodeId = requiredString(formData, "episode_id");
  const feedbackEventId = requiredString(formData, "feedback_event_id");

  await revertFeedbackEvent(
    episodeId,
    feedbackEventId,
    optionalString(formData, "reason"),
  );
  redirect("/learning");
}

export async function confirmPreferenceAction(formData: FormData) {
  await confirmPreference({
    kind: preferenceKind(formData),
    content: requiredString(formData, "content"),
    source_event_ids: formData
      .getAll("source_event_id")
      .filter((value): value is string => typeof value === "string"),
    confidence: Number(requiredString(formData, "confidence")),
    scope: requiredString(formData, "scope"),
  });
  redirect("/learning");
}

export async function disablePreferenceAction(formData: FormData) {
  await disablePreference(requiredString(formData, "preference_id"));
  redirect("/learning");
}

export async function restorePreferenceAction(formData: FormData) {
  await restorePreference(requiredString(formData, "preference_id"));
  redirect("/learning");
}

export async function deletePreferenceAction(formData: FormData) {
  await deletePreference(requiredString(formData, "preference_id"));
  redirect("/learning");
}

export async function updateCreatorProfileAction(formData: FormData) {
  await updateCreatorProfile({
    identity: textField(formData, "identity"),
    content_domains: multilineField(formData, "content_domains"),
    tone_keywords: multilineField(formData, "tone_keywords"),
    preferred_phrases: multilineField(formData, "preferred_phrases"),
    banned_phrases: multilineField(formData, "banned_phrases"),
    good_examples: multilineField(formData, "good_examples"),
    bad_examples: multilineField(formData, "bad_examples"),
  });
  redirect("/profile");
}

export async function approveScriptAction(formData: FormData) {
  const episodeId = requiredString(formData, "episode_id");

  await approveScript(episodeId);
  redirect("/video");
}

export async function processPendingVideoAction(formData: FormData) {
  const fileName = requiredString(formData, "file_name");
  const episodeId = formData.get("episode_id");

  if (typeof episodeId === "string" && episodeId.length > 0) {
    await uploadEpisodeVideo(episodeId, fileName);
    await processEpisodeVideo(episodeId);
  } else {
    await processVideo(fileName);
  }
  redirect("/video");
}

export async function processVideoAndPackageAction(formData: FormData) {
  const fileName = requiredString(formData, "file_name");
  const episodeId = requiredString(formData, "episode_id");

  await uploadEpisodeVideo(episodeId, fileName);
  await processEpisodeVideo(episodeId);
  await createEpisodeDraft(episodeId);
  redirect("/draft");
}

export async function processAdHocVideoAction(formData: FormData) {
  const fileName = requiredString(formData, "file_name");
  const episodeId = requiredString(formData, "episode_id");

  await processAdHocEpisodeVideo(episodeId, fileName);
  redirect("/video");
}

export async function processAdHocVideoBatchAction(formData: FormData) {
  const episodeId = requiredString(formData, "episode_id");
  const fileNames = formData
    .getAll("file_names")
    .filter((value): value is string => typeof value === "string")
    .filter((value, index, values) => values.indexOf(value) === index)
    .sort((left, right) => {
      const leftOrder = Number(formData.get(`order:${left}`));
      const rightOrder = Number(formData.get(`order:${right}`));
      return leftOrder - rightOrder;
    });

  if (fileNames.length < 2) {
    throw new Error("至少选择两条短视频");
  }

  await processAdHocEpisodeVideoBatch(episodeId, fileNames);
  redirect("/video");
}

export async function createDraftPackageAction(formData: FormData) {
  const value = formData.get("video_file_name");
  const fileName = typeof value === "string" && value.length > 0 ? value : undefined;

  await createDraftPackage(fileName);
  redirect("/draft");
}

export async function confirmEditPlanAction(formData: FormData) {
  const episodeId = requiredString(formData, "episode_id");

  await createEpisodeDraft(episodeId);
  redirect("/draft");
}

export async function markPublishedAction(formData: FormData) {
  const episodeId = requiredString(formData, "episode_id");

  await markEpisodePublished(episodeId, {
    publish_url: optionalString(formData, "publish_url"),
    play_count: optionalNonNegativeInteger(formData, "play_count"),
    like_count: optionalNonNegativeInteger(formData, "like_count"),
    comment_count: optionalNonNegativeInteger(formData, "comment_count"),
    feedback_value: optionalFeedbackValueField(formData, "feedback_value"),
    feedback_reason: optionalString(formData, "feedback_reason"),
  });
  redirect("/learning");
}

export async function analyzeCommentInsightsAction(formData: FormData) {
  const episodeId = requiredString(formData, "episode_id");
  const commentsText = textField(formData, "comments_text");

  await analyzeCommentInsights(episodeId, commentsText);
  redirect("/learning");
}

export async function updateEditPlanSegmentDecisionAction(formData: FormData) {
  const episodeId = requiredString(formData, "episode_id");
  const segmentId = requiredString(formData, "segment_id");

  await updateEditPlanSegmentDecision(
    episodeId,
    segmentId,
    editPlanSegmentDecisionAction(formData),
    optionalString(formData, "reason"),
  );
  redirect("/edit-plan");
}

export async function revertEditPlanSegmentDecisionAction(formData: FormData) {
  const episodeId = requiredString(formData, "episode_id");
  const segmentId = requiredString(formData, "segment_id");

  await revertEditPlanSegmentDecision(episodeId, segmentId);
  redirect("/edit-plan");
}

export async function importDraftPackageAction(formData: FormData) {
  const value = formData.get("package_name");
  const packageName = typeof value === "string" && value.length > 0 ? value : undefined;

  await importDraftPackage(packageName);
  redirect("/draft");
}

export async function syncSourcesAction() {
  await syncSourceItems();
  redirect("/sources");
}

export async function ingestManualLinkAction(formData: FormData) {
  const url = requiredString(formData, "url");

  await ingestManualLink(url);
  redirect("/sources");
}

export async function updateRuntimeSettingsAction(formData: FormData) {
  type RuntimeTextField = Exclude<keyof RuntimeSettingsUpdate, "topic_source_urls">;
  const textFieldNames: RuntimeTextField[] = [
    "deepseek_api_key",
    "deepseek_model",
    "product_hunt_token",
    "tencentcloud_secret_id",
    "tencentcloud_secret_key",
    "tencentcloud_asr_region",
    "tencentcloud_asr_engine_model_type",
    "local_whisper_python_path",
    "local_whisper_model_path",
    "local_whisper_language",
    "obsidian_vault_path",
    "hermes_radar_path",
    "video_inbox_path",
    "video_output_path",
    "draft_output_path",
    "jianying_draft_root_path",
    "database_path",
  ];
  const payload: RuntimeSettingsUpdate = {};

  for (const fieldName of textFieldNames) {
    const value = formData.get(fieldName);
    if (typeof value === "string" && value.trim()) {
      payload[fieldName] = value.trim();
    }
  }

  const sourceUrls = formData.get("topic_source_urls");
  if (typeof sourceUrls === "string") {
    payload.topic_source_urls = sourceUrls
      .split(/[\n,]/)
      .map((url) => url.trim())
      .filter(Boolean);
  }

  await updateRuntimeSettings(payload);
  const redirectTo = formData.get("redirect_to");
  redirect(redirectTo === "/setup" ? "/setup" : "/settings");
}
