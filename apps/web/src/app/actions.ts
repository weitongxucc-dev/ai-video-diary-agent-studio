"use server";

import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";
import {
  approveScript,
  approveTopic,
  archiveSources,
  confirmMontageClips,
  createMontagePlan,
  createEpisodeDraft,
  createProductionBatch,
  createDraftPackage,
  disablePreferenceCard,
  generateTopics,
  generateScript,
  ingestManualLink,
  importDraftPackage,
  markEpisodePublished,
  processAdHocEpisodeVideo,
  processAdHocEpisodeVideoBatch,
  processEpisodeVideo,
  processVideo,
  analyzeProductionMaterial,
  regenerateEpisodeScript,
  regenerateEpisodeScriptSection,
  regenerateTopics,
  restorePreferenceCard,
  revertEditPlanSegmentDecision,
  revertEpisodeScriptVersion,
  revertFeedbackEvent,
  sendFeedback,
  selectEpisodeContentTemplate,
  scanProductionMaterials,
  syncSourceItems,
  updateEditPlanSegmentDecision,
  updateEpisodeScript,
  updateProductionProfile,
  updateProductionMaterialSegment,
  reviewProductionMaterial,
  renderMontagePlan,
  runProductionBatch,
  uploadEpisodeVideo,
  updatePublishMetrics,
  updateRuntimeSettings,
} from "@/lib/api.server";
import type {
  EditPlanSegmentDecisionAction,
  FeedbackValue,
  RuntimeSettingsUpdate,
  ScriptPackage,
  ToastKind,
} from "@/lib/types";
import { validateAbsolutePath } from "@/lib/absolutePath";

const DIRECTORY_FIELD_NAMES: ReadonlySet<string> = new Set([
  "obsidian_vault_path",
  "hermes_radar_path",
  "video_inbox_path",
  "video_output_path",
  "draft_output_path",
  "jianying_draft_root_path",
  "database_path",
  "model_root_path",
]);

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

function editPlanSegmentDecisionAction(
  formData: FormData,
): EditPlanSegmentDecisionAction {
  const action = requiredString(formData, "action");
  if (action !== "keep" && action !== "remove" && action !== "review") {
    throw new Error("Invalid edit plan segment decision");
  }
  return action;
}

function redirectWithToast(
  path: string,
  kind: ToastKind,
  message: string,
): never {
  const separator = path.includes("?") ? "&" : "?";
  const params = new URLSearchParams({
    toast: kind,
    toast_message: message,
  });
  redirect(`${path}${separator}${params.toString()}`);
}

function safeErrorMessage(err: unknown, fallback: string): string {
  if (err instanceof Error && err.message) {
    return err.message;
  }
  return fallback;
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

  try {
    await approveTopic(episodeId, topicId);
    await generateScript(episodeId);
    revalidatePath("/script");
    revalidatePath("/topics");
    redirectWithToast("/script", "success", "选题已通过，口播稿生成中");
  } catch (err) {
    redirectWithToast(
      "/topics",
      "error",
      `选题已通过但生成口播稿失败：${safeErrorMessage(err, "未知错误")}（选题已确认，可重试生成）`,
    );
  }
}

export async function selectContentTemplateAction(formData: FormData) {
  const episodeId = requiredString(formData, "episode_id");
  const templateId = requiredString(formData, "template_id");

  try {
    await selectEpisodeContentTemplate(episodeId, templateId);
    revalidatePath("/templates");
    redirectWithToast("/templates", "success", "内容模板已切换");
  } catch (err) {
    redirectWithToast(
      "/templates",
      "error",
      `切换内容模板失败：${safeErrorMessage(err, "未知错误")}`,
    );
  }
}

export async function generateTopicsAction(formData: FormData) {
  const episodeId = requiredString(formData, "episode_id");

  try {
    await generateTopics(episodeId);
    revalidatePath("/topics");
    redirectWithToast("/topics", "success", "今日选题已生成");
  } catch (err) {
    redirectWithToast(
      "/topics",
      "error",
      `生成选题失败：${safeErrorMessage(err, "未知错误")}`,
    );
  }
}

export async function sendTopicFeedbackAction(formData: FormData) {
  const episodeId = requiredString(formData, "episode_id");
  const topicId = requiredString(formData, "topic_id");
  const reason = formData.get("reason");

  try {
    await sendFeedback(
      episodeId,
      "topic",
      topicId,
      feedbackValue(formData),
      typeof reason === "string" ? reason : undefined,
    );
    revalidatePath("/topics");
    revalidatePath("/learning");
    redirectWithToast("/topics", "success", "选题反馈已记录");
  } catch (err) {
    redirectWithToast(
      "/topics",
      "error",
      `记录选题反馈失败：${safeErrorMessage(err, "未知错误")}`,
    );
  }
}

export async function regenerateTopicsAction(formData: FormData) {
  const episodeId = requiredString(formData, "episode_id");

  try {
    await regenerateTopics(episodeId);
    revalidatePath("/topics");
    redirectWithToast("/topics", "success", "选题已重新生成");
  } catch (err) {
    redirectWithToast(
      "/topics",
      "error",
      `重新生成选题失败：${safeErrorMessage(err, "未知错误")}`,
    );
  }
}

export async function sendScriptSectionFeedbackAction(formData: FormData) {
  const episodeId = requiredString(formData, "episode_id");
  const sectionId = requiredString(formData, "section_id");
  const reason = formData.get("reason");

  try {
    await sendFeedback(
      episodeId,
      "script_section",
      sectionId,
      feedbackValue(formData),
      typeof reason === "string" ? reason : undefined,
    );
    revalidatePath("/script");
    revalidatePath("/learning");
    redirectWithToast("/script", "success", "口播段落反馈已记录");
  } catch (err) {
    redirectWithToast(
      "/script",
      "error",
      `记录口播段落反馈失败：${safeErrorMessage(err, "未知错误")}`,
    );
  }
}

export async function saveScriptAction(formData: FormData) {
  const episodeId = requiredString(formData, "episode_id");

  try {
    await updateEpisodeScript(
      episodeId,
      scriptFromFormData(formData),
      optionalString(formData, "reason"),
    );
    revalidatePath("/script");
    redirectWithToast("/script", "success", "口播稿已保存");
  } catch (err) {
    redirectWithToast(
      "/script",
      "error",
      `保存口播稿失败：${safeErrorMessage(err, "未知错误")}`,
    );
  }
}

export async function regenerateScriptAction(formData: FormData) {
  const episodeId = requiredString(formData, "episode_id");

  try {
    await regenerateEpisodeScript(episodeId, optionalString(formData, "reason"));
    revalidatePath("/script");
    redirectWithToast("/script", "success", "口播稿已整体重新生成");
  } catch (err) {
    redirectWithToast(
      "/script",
      "error",
      `重新生成口播稿失败：${safeErrorMessage(err, "未知错误")}`,
    );
  }
}

export async function regenerateScriptSectionAction(formData: FormData) {
  const episodeId = requiredString(formData, "episode_id");
  const sectionId = requiredString(formData, "section_id");

  try {
    await regenerateEpisodeScriptSection(
      episodeId,
      sectionId,
      optionalString(formData, "reason"),
    );
    revalidatePath("/script");
    redirectWithToast("/script", "success", "段落已重新生成");
  } catch (err) {
    redirectWithToast(
      "/script",
      "error",
      `重新生成段落失败：${safeErrorMessage(err, "未知错误")}`,
    );
  }
}

export async function revertScriptVersionAction(formData: FormData) {
  const episodeId = requiredString(formData, "episode_id");
  const versionId = requiredString(formData, "version_id");

  try {
    await revertEpisodeScriptVersion(
      episodeId,
      versionId,
      optionalString(formData, "reason"),
    );
    revalidatePath("/script");
    redirectWithToast("/script", "success", "已回滚到指定版本");
  } catch (err) {
    redirectWithToast(
      "/script",
      "error",
      `回滚口播稿版本失败：${safeErrorMessage(err, "未知错误")}`,
    );
  }
}

export async function revertFeedbackAction(formData: FormData) {
  const episodeId = requiredString(formData, "episode_id");
  const feedbackEventId = requiredString(formData, "feedback_event_id");

  try {
    await revertFeedbackEvent(
      episodeId,
      feedbackEventId,
      optionalString(formData, "reason"),
    );
    revalidatePath("/learning");
    revalidatePath("/topics");
    revalidatePath("/script");
    redirectWithToast("/learning", "success", "反馈已撤销");
  } catch (err) {
    redirectWithToast(
      "/learning",
      "error",
      `撤销反馈失败：${safeErrorMessage(err, "未知错误")}`,
    );
  }
}

export async function approveScriptAction(formData: FormData) {
  const episodeId = requiredString(formData, "episode_id");

  try {
    await approveScript(episodeId);
    revalidatePath("/video");
    revalidatePath("/script");
    redirectWithToast("/video", "success", "口播稿已确认通过");
  } catch (err) {
    redirectWithToast(
      "/script",
      "error",
      `确认口播稿失败：${safeErrorMessage(err, "未知错误")}`,
    );
  }
}

export async function processPendingVideoAction(formData: FormData) {
  const fileName = requiredString(formData, "file_name");
  const episodeId = formData.get("episode_id");

  try {
    if (typeof episodeId === "string" && episodeId.length > 0) {
      await uploadEpisodeVideo(episodeId, fileName);
      await processEpisodeVideo(episodeId);
    } else {
      await processVideo(fileName);
    }
    revalidatePath("/video");
    revalidatePath("/");
    redirectWithToast("/video", "success", "视频已预处理");
  } catch (err) {
    redirectWithToast(
      "/video",
      "error",
      `视频预处理失败：${safeErrorMessage(err, "未知错误")}`,
    );
  }
}

export async function processVideoAndPackageAction(formData: FormData) {
  const fileName = requiredString(formData, "file_name");
  const episodeId = requiredString(formData, "episode_id");

  let uploadOk = false;
  let processedOk = false;
  try {
    await uploadEpisodeVideo(episodeId, fileName);
    uploadOk = true;
    await processEpisodeVideo(episodeId);
    processedOk = true;
    await createEpisodeDraft(episodeId);
    revalidatePath("/draft");
    revalidatePath("/video");
    redirectWithToast("/draft", "success", "素材包已生成并导入");
  } catch (err) {
    const stage = !uploadOk
      ? "上传视频"
      : !processedOk
        ? "预处理视频"
        : "生成草稿素材包";
    redirectWithToast(
      "/video",
      "error",
      `${stage}失败：${safeErrorMessage(err, "未知错误")}（之前步骤已生效，请重试）`,
    );
  }
}

export async function processAdHocVideoAction(formData: FormData) {
  const fileName = requiredString(formData, "file_name");
  const episodeId = requiredString(formData, "episode_id");

  try {
    await processAdHocEpisodeVideo(episodeId, fileName);
    revalidatePath("/video");
    redirectWithToast("/video", "success", "临时视频已处理");
  } catch (err) {
    redirectWithToast(
      "/video",
      "error",
      `临时视频处理失败：${safeErrorMessage(err, "未知错误")}`,
    );
  }
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
    redirectWithToast("/video", "error", "至少选择两条短视频");
  }

  try {
    await processAdHocEpisodeVideoBatch(episodeId, fileNames);
    revalidatePath("/video");
    redirectWithToast(
      "/video",
      "success",
      `已合并 ${fileNames.length} 段视频`,
    );
  } catch (err) {
    redirectWithToast(
      "/video",
      "error",
      `合并临时视频失败：${safeErrorMessage(err, "未知错误")}`,
    );
  }
}

export async function archiveSourcesAction(formData: FormData) {
  const episodeId = requiredString(formData, "episode_id");
  const fileNames = formData
    .getAll("file_names")
    .filter((value): value is string => typeof value === "string")
    .filter((value, index, values) => values.indexOf(value) === index);

  if (fileNames.length === 0) {
    redirectWithToast("/video", "error", "请选择要归档的源文件");
  }

  try {
    await archiveSources(episodeId, fileNames);
    revalidatePath("/video");
    redirectWithToast(
      "/video",
      "success",
      `已归档 ${fileNames.length} 个源文件`,
    );
  } catch (err) {
    redirectWithToast(
      "/video",
      "error",
      `归档源文件失败：${safeErrorMessage(err, "未知错误")}`,
    );
  }
}

function optionalPublishUrl(formData: FormData): string | null {
  const value = optionalString(formData, "publish_url");
  return value ?? null;
}

function optionalPlatform(formData: FormData): string | null {
  return optionalString(formData, "platform") ?? null;
}

function optionalPublishNotes(formData: FormData): string | null {
  return optionalString(formData, "notes") ?? null;
}

function optionalPublishedAt(formData: FormData): string | null {
  const value = optionalString(formData, "published_at");
  return value ?? null;
}

function optionalMetric(formData: FormData, key: string): number | null {
  const raw = formData.get(key);
  if (typeof raw !== "string" || raw.trim().length === 0) {
    return null;
  }
  const parsed = Number(raw);
  return Number.isFinite(parsed) ? Math.max(0, Math.round(parsed)) : null;
}

export async function markEpisodePublishedAction(formData: FormData) {
  const episodeId = requiredString(formData, "episode_id");

  try {
    await markEpisodePublished(episodeId, {
      publish_url: optionalPublishUrl(formData),
      platform: optionalPlatform(formData),
      play_count: optionalMetric(formData, "play_count"),
      like_count: optionalMetric(formData, "like_count"),
      comment_count: optionalMetric(formData, "comment_count"),
      notes: optionalPublishNotes(formData),
      published_at: optionalPublishedAt(formData),
    });
    revalidatePath("/history");
    revalidatePath("/draft");
    redirectWithToast("/history", "success", "已标记为已发布");
  } catch (err) {
    redirectWithToast(
      "/draft",
      "error",
      `标记发布失败：${safeErrorMessage(err, "未知错误")}`,
    );
  }
}

export async function updatePublishMetricsAction(formData: FormData) {
  const episodeId = requiredString(formData, "episode_id");

  try {
    await updatePublishMetrics(episodeId, {
      play_count: optionalMetric(formData, "play_count"),
      like_count: optionalMetric(formData, "like_count"),
      comment_count: optionalMetric(formData, "comment_count"),
    });
    revalidatePath("/history");
    revalidatePath("/draft");
    redirectWithToast("/history", "success", "发布数据已更新");
  } catch (err) {
    redirectWithToast(
      "/history",
      "error",
      `更新发布数据失败：${safeErrorMessage(err, "未知错误")}`,
    );
  }
}

export async function retrySourceSyncAction(formData: FormData) {
  const sourceKey = requiredString(formData, "source_key");
  // Backend 不支持单来源重试：使用全量同步 + 提示用户
  void sourceKey;
  try {
    await syncSourceItems();
    revalidatePath("/sources");
    redirectWithToast(
      "/sources",
      "error",
      "单来源重试接口暂未实现，已改为全量同步",
    );
  } catch (err) {
    redirectWithToast(
      "/sources",
      "error",
      `全量同步失败：${safeErrorMessage(err, "未知错误")}`,
    );
  }
}

export async function disablePreferenceCardAction(formData: FormData) {
  const preferenceId = requiredString(formData, "preference_id");
  try {
    await disablePreferenceCard(preferenceId);
    revalidatePath("/learning");
    redirectWithToast("/learning", "success", "偏好卡已禁用");
  } catch (err) {
    redirectWithToast(
      "/learning",
      "error",
      `禁用偏好卡失败：${safeErrorMessage(err, "未知错误")}`,
    );
  }
}

export async function restorePreferenceCardAction(formData: FormData) {
  const preferenceId = requiredString(formData, "preference_id");
  try {
    await restorePreferenceCard(preferenceId);
    revalidatePath("/learning");
    redirectWithToast("/learning", "success", "偏好卡已恢复");
  } catch (err) {
    redirectWithToast(
      "/learning",
      "error",
      `恢复偏好卡失败：${safeErrorMessage(err, "未知错误")}`,
    );
  }
}

export async function createDraftPackageAction(formData: FormData) {
  const value = formData.get("video_file_name");
  const fileName = typeof value === "string" && value.length > 0 ? value : undefined;

  try {
    await createDraftPackage(fileName);
    revalidatePath("/draft");
    redirectWithToast("/draft", "success", "剪映素材包已生成");
  } catch (err) {
    redirectWithToast(
      "/draft",
      "error",
      `生成剪映素材包失败：${safeErrorMessage(err, "未知错误")}`,
    );
  }
}

export async function confirmEditPlanAction(formData: FormData) {
  const episodeId = requiredString(formData, "episode_id");

  try {
    await createEpisodeDraft(episodeId);
    revalidatePath("/draft");
    revalidatePath("/edit-plan");
    redirectWithToast("/draft", "success", "剪辑计划已确认，素材包已生成");
  } catch (err) {
    redirectWithToast(
      "/edit-plan",
      "error",
      `确认剪辑计划失败：${safeErrorMessage(err, "未知错误")}`,
    );
  }
}

export async function updateEditPlanSegmentDecisionAction(formData: FormData) {
  const episodeId = requiredString(formData, "episode_id");
  const segmentId = requiredString(formData, "segment_id");

  try {
    await updateEditPlanSegmentDecision(
      episodeId,
      segmentId,
      editPlanSegmentDecisionAction(formData),
      optionalString(formData, "reason"),
    );
    revalidatePath("/edit-plan");
    redirectWithToast("/edit-plan", "success", "片段决策已保存");
  } catch (err) {
    redirectWithToast(
      "/edit-plan",
      "error",
      `保存片段决策失败：${safeErrorMessage(err, "未知错误")}`,
    );
  }
}

export async function revertEditPlanSegmentDecisionAction(formData: FormData) {
  const episodeId = requiredString(formData, "episode_id");
  const segmentId = requiredString(formData, "segment_id");

  try {
    await revertEditPlanSegmentDecision(episodeId, segmentId);
    revalidatePath("/edit-plan");
    redirectWithToast("/edit-plan", "success", "片段决策已撤销");
  } catch (err) {
    redirectWithToast(
      "/edit-plan",
      "error",
      `撤销片段决策失败：${safeErrorMessage(err, "未知错误")}`,
    );
  }
}

export async function importDraftPackageAction(formData: FormData) {
  const value = formData.get("package_name");
  const packageName = typeof value === "string" && value.length > 0 ? value : undefined;

  try {
    await importDraftPackage(packageName);
    revalidatePath("/draft");
    redirectWithToast("/draft", "success", "已重新导入剪映");
  } catch (err) {
    redirectWithToast(
      "/draft",
      "error",
      `导入剪映失败：${safeErrorMessage(err, "未知错误")}`,
    );
  }
}

export async function syncSourcesAction() {
  try {
    await syncSourceItems();
    revalidatePath("/sources");
    redirectWithToast("/sources", "success", "信息源已同步");
  } catch (err) {
    redirectWithToast(
      "/sources",
      "error",
      `信息源同步失败：${safeErrorMessage(err, "未知错误")}`,
    );
  }
}

export async function refreshTemplatesAction() {
  try {
    revalidatePath("/templates");
    redirectWithToast("/templates", "success", "模板列表已刷新");
  } catch (err) {
    redirectWithToast(
      "/templates",
      "error",
      `刷新模板失败：${safeErrorMessage(err, "未知错误")}`,
    );
  }
}

export async function ingestManualLinkAction(formData: FormData) {
  const url = requiredString(formData, "url");

  try {
    await ingestManualLink(url);
    revalidatePath("/sources");
    redirectWithToast("/sources", "success", "手动链接已识别");
  } catch (err) {
    redirectWithToast(
      "/sources",
      "error",
      `识别链接失败：${safeErrorMessage(err, "未知错误")}`,
    );
  }
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
    "model_root_path",
  ];
  const payload: RuntimeSettingsUpdate = {};

  try {
    for (const fieldName of textFieldNames) {
      const value = formData.get(fieldName);
      if (typeof value !== "string") {
        continue;
      }
      const trimmed = value.trim();
      if (trimmed.length === 0) {
        continue;
      }
      if (DIRECTORY_FIELD_NAMES.has(fieldName)) {
        const validation = validateAbsolutePath(trimmed);
        if (validation.kind === "relative") {
          throw new Error(
            `目录字段 ${fieldName} 必须是绝对路径（POSIX 或 Windows 格式）`,
          );
        }
      }
      payload[fieldName] = trimmed;
    }

    const sourceUrls = formData.get("topic_source_urls");
    if (typeof sourceUrls === "string") {
      payload.topic_source_urls = sourceUrls
        .split(/[\n,]/)
        .map((url) => url.trim())
        .filter(Boolean);
    }

    await updateRuntimeSettings(payload);
    revalidatePath("/settings");
    redirectWithToast("/settings", "success", "配置已保存");
  } catch (err) {
    redirectWithToast(
      "/settings",
      "error",
      `保存配置失败：${safeErrorMessage(err, "未知错误")}`,
    );
  }
}

export async function scanProductionMaterialsAction() {
  try {
    await scanProductionMaterials();
  } catch (err) {
    redirectWithToast(
      "/production",
      "error",
      `扫描素材失败：${safeErrorMessage(err, "未知错误")}`,
    );
  }
  revalidatePath("/production");
  redirectWithToast("/production", "success", "素材库已扫描");
}

export async function analyzeProductionMaterialAction(formData: FormData) {
  const assetId = requiredString(formData, "asset_id");
  try {
    await analyzeProductionMaterial(assetId);
  } catch (err) {
    redirectWithToast(
      "/production",
      "error",
      `镜头分析失败：${safeErrorMessage(err, "未知错误")}`,
    );
  }
  revalidatePath("/production");
  redirectWithToast("/production", "success", "镜头分析已完成");
}

export async function reviewProductionMaterialAction(formData: FormData) {
  const assetId = requiredString(formData, "asset_id");
  const status = requiredString(formData, "review_status");
  if (status !== "raw" && status !== "approved" && status !== "rejected") {
    throw new Error("Invalid material review status");
  }
  const usageRole = requiredString(formData, "usage_role");
  if (!["unassigned", "a_roll", "b_roll", "exclude"].includes(usageRole)) {
    throw new Error("Invalid material usage role");
  }
  try {
    await reviewProductionMaterial(assetId, {
      review_status: usageRole === "exclude" ? "rejected" : status,
      usage_role: usageRole as "unassigned" | "a_roll" | "b_roll" | "exclude",
      rights_confirmed: formData.get("rights_confirmed") === "on",
      brand_safe: formData.get("brand_safe") === "on",
    });
  } catch (err) {
    redirectWithToast(
      "/production",
      "error",
      `素材审核失败：${safeErrorMessage(err, "未知错误")}`,
    );
  }
  revalidatePath("/production");
  redirectWithToast("/production", "success", "素材审核状态已更新");
}

export async function updateProductionMaterialSegmentAction(formData: FormData) {
  const assetId = requiredString(formData, "asset_id");
  const segmentId = requiredString(formData, "segment_id");
  const status = requiredString(formData, "review_status");
  if (status !== "raw" && status !== "approved" && status !== "rejected") {
    throw new Error("Invalid material segment status");
  }
  const usageRole = requiredString(formData, "usage_role");
  if (!["unassigned", "a_roll", "b_roll", "exclude"].includes(usageRole)) {
    throw new Error("Invalid material segment usage role");
  }
  try {
    await updateProductionMaterialSegment(assetId, segmentId, {
      description: String(formData.get("description") ?? "").trim(),
      transcript: String(formData.get("transcript") ?? "").trim(),
      review_status: status,
      usage_role: usageRole as "unassigned" | "a_roll" | "b_roll" | "exclude",
    });
  } catch (err) {
    redirectWithToast(
      "/production",
      "error",
      `保存镜头信息失败：${safeErrorMessage(err, "未知错误")}`,
    );
  }
  revalidatePath("/production");
  redirectWithToast("/production", "success", "镜头语义和审核状态已保存");
}

export async function updateProductionProfileAction(formData: FormData) {
  const sourceUrls = String(formData.get("source_urls") ?? "")
    .split(/[\n,]/)
    .map((value) => value.trim())
    .filter(Boolean);
  try {
    await updateProductionProfile({
      workspace_name: requiredString(formData, "workspace_name"),
      brand_name: String(formData.get("brand_name") ?? "").trim(),
      brand_template_name: String(formData.get("brand_template_name") ?? "").trim(),
      brand_colors: String(formData.get("brand_colors") ?? "")
        .split(/[\n,]/)
        .map((value) => value.trim())
        .filter(Boolean),
      subtitle_style: String(formData.get("subtitle_style") ?? "").trim(),
      intro_text: String(formData.get("intro_text") ?? "").trim(),
      outro_text: String(formData.get("outro_text") ?? "").trim(),
      background_music_path: optionalString(formData, "background_music_path") ?? null,
      voice_name: String(formData.get("voice_name") ?? "").trim(),
      voice_reference_path: optionalString(formData, "voice_reference_path") ?? null,
      voice_reference_text: String(formData.get("voice_reference_text") ?? "").trim(),
      voice_consent_confirmed: formData.get("voice_consent_confirmed") === "on",
      source_urls: sourceUrls,
      use_obsidian: formData.get("use_obsidian") === "on",
      use_hermes: formData.get("use_hermes") === "on",
      factuality_gate_enabled: true,
      cloud_enhancement_enabled: formData.get("cloud_enhancement_enabled") === "on",
    });
  } catch (err) {
    redirectWithToast(
      "/production",
      "error",
      `保存生产配置失败：${safeErrorMessage(err, "未知错误")}`,
    );
  }
  revalidatePath("/production");
  redirectWithToast("/production", "success", "生产基础配置已保存");
}

export async function createProductionBatchAction(formData: FormData) {
  const requestedCount = Number(requiredString(formData, "target_count"));
  let successKind: ToastKind = "success";
  let successMessage = "生产批次已创建";
  try {
    const batch = await createProductionBatch({
      name: String(formData.get("name") ?? "").trim(),
      target_count: Number.isFinite(requestedCount) ? requestedCount : 10,
    });
    successKind = batch.status === "blocked" ? "info" : "success";
    successMessage = batch.message;
  } catch (err) {
    redirectWithToast(
      "/production",
      "error",
      `创建批次失败：${safeErrorMessage(err, "未知错误")}`,
    );
  }
  revalidatePath("/production");
  redirectWithToast("/production", successKind, successMessage);
}

export async function runProductionBatchAction(formData: FormData) {
  const batchId = requiredString(formData, "batch_id");
  try {
    const batch = await runProductionBatch(batchId);
    revalidatePath("/production");
    redirectWithToast("/production", "success", batch.message);
  } catch (err) {
    redirectWithToast(
      "/production",
      "error",
      `启动批量生产失败：${safeErrorMessage(err, "未知错误")}`,
    );
  }
}

export async function createMontagePlanAction(formData: FormData) {
  const batchId = requiredString(formData, "batch_id");
  let successKind: ToastKind = "success";
  let successMessage = "混剪时间线已生成";
  try {
    const plan = await createMontagePlan(batchId, {
      episode_id: requiredString(formData, "episode_id"),
      narration_audio_path: optionalString(formData, "narration_audio_path") ?? null,
    });
    successKind = plan.status === "waiting_for_user" ? "info" : "success";
    successMessage = plan.message;
  } catch (err) {
    redirectWithToast(
      "/production",
      "error",
      `生成混剪计划失败：${safeErrorMessage(err, "未知错误")}`,
    );
  }
  revalidatePath("/production");
  redirectWithToast("/production", successKind, successMessage);
}

export async function confirmMontageClipsAction(formData: FormData) {
  const planId = requiredString(formData, "plan_id");
  const clipIds = requiredString(formData, "clip_ids").split(",").filter(Boolean);
  let successMessage = "镜头已确认";
  try {
    const plan = await confirmMontageClips(planId, clipIds);
    successMessage = plan.message;
  } catch (err) {
    redirectWithToast(
      "/production",
      "error",
      `确认镜头失败：${safeErrorMessage(err, "未知错误")}`,
    );
  }
  revalidatePath("/production");
  redirectWithToast("/production", "success", successMessage);
}

export async function renderMontagePlanAction(formData: FormData) {
  const planId = requiredString(formData, "plan_id");
  let successMessage = "初版成片已生成";
  try {
    const plan = await renderMontagePlan(
      planId,
      requiredString(formData, "narration_audio_path"),
    );
    successMessage = plan.message;
  } catch (err) {
    redirectWithToast(
      "/production",
      "error",
      `渲染初版成片失败：${safeErrorMessage(err, "未知错误")}`,
    );
  }
  revalidatePath("/production");
  redirectWithToast("/production", "success", successMessage);
}
