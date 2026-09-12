"use server";

import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";
import {
  createVoiceProfile,
  disablePreferenceCard,
  restorePreferenceCard,
  updateProductionProfile,
  updateRuntimeSettings,
  updateVoiceProfile,
} from "@/lib/api.server";
import type { RuntimeSettingsUpdate, ToastKind } from "@/lib/types";
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
  "temporary_root_path",
]);

function requiredString(formData: FormData, key: string): string {
  const value = formData.get(key);
  if (typeof value !== "string" || value.length === 0) {
    throw new Error(`Missing form field: ${key}`);
  }
  return value;
}

function optionalString(formData: FormData, key: string): string | undefined {
  const value = formData.get(key);
  return typeof value === "string" && value.trim() ? value.trim() : undefined;
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

export async function updateRuntimeSettingsAction(formData: FormData) {
  const requestedSection = formData.get("settings_section");
  const section =
    typeof requestedSection === "string" &&
    ["model", "paths", "asr", "sources"].includes(requestedSection)
      ? requestedSection
      : "model";
  const destination = `/settings?section=${section}`;
  type RuntimeTextField = Exclude<
    keyof RuntimeSettingsUpdate,
    "topic_source_urls"
  >;
  const textFieldNames: RuntimeTextField[] = [
    "asr_provider",
    "local_whisper_backend",
    "local_whisper_cli_path",
    "llm_provider",
    "llm_api_key",
    "llm_base_url",
    "llm_model",
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
    "temporary_root_path",
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
    redirectWithToast(destination, "success", "配置已保存");
  } catch (err) {
    redirectWithToast(
      destination,
      "error",
      `保存配置失败：${safeErrorMessage(err, "未知错误")}`,
    );
  }
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
      target_duration_min_seconds: Number(
        requiredString(formData, "target_duration_min_seconds"),
      ),
      target_duration_max_seconds: Number(
        requiredString(formData, "target_duration_max_seconds"),
      ),
      brand_template_name: String(
        formData.get("brand_template_name") ?? "",
      ).trim(),
      brand_colors: String(formData.get("brand_colors") ?? "")
        .split(/[\n,]/)
        .map((value) => value.trim())
        .filter(Boolean),
      subtitle_style: String(formData.get("subtitle_style") ?? "").trim(),
      intro_text: String(formData.get("intro_text") ?? "").trim(),
      outro_text: String(formData.get("outro_text") ?? "").trim(),
      background_music_path:
        optionalString(formData, "background_music_path") ?? null,
      voice_name: String(formData.get("voice_name") ?? "").trim(),
      voice_reference_path:
        optionalString(formData, "voice_reference_path") ?? null,
      voice_reference_text: String(
        formData.get("voice_reference_text") ?? "",
      ).trim(),
      voice_consent_confirmed:
        formData.get("voice_consent_confirmed") === "on",
      source_urls: sourceUrls,
      use_obsidian: formData.get("use_obsidian") === "on",
      use_hermes: formData.get("use_hermes") === "on",
      factuality_gate_enabled: true,
      cloud_enhancement_enabled:
        formData.get("cloud_enhancement_enabled") === "on",
    });
  } catch (err) {
    redirectWithToast(
      "/settings",
      "error",
      `保存生产配置失败：${safeErrorMessage(err, "未知错误")}`,
    );
  }
  revalidatePath("/settings");
  revalidatePath("/production");
  redirectWithToast("/settings", "success", "生产基础配置已保存");
}

export async function createVoiceProfileAction(formData: FormData) {
  try {
    await createVoiceProfile({
      name: requiredString(formData, "name").trim(),
      reference_audio_path: requiredString(
        formData,
        "reference_audio_path",
      ).trim(),
      reference_text: requiredString(formData, "reference_text").trim(),
      consent_confirmed: formData.get("consent_confirmed") === "on",
      is_default: formData.get("is_default") === "on",
    });
  } catch (err) {
    redirectWithToast(
      "/settings",
      "error",
      `添加声音失败：${safeErrorMessage(err, "未知错误")}`,
    );
  }
  revalidatePath("/settings");
  redirectWithToast("/settings", "success", "授权声音已加入声音库");
}

export async function setDefaultVoiceProfileAction(formData: FormData) {
  try {
    await updateVoiceProfile(requiredString(formData, "voice_profile_id"), {
      name: requiredString(formData, "name"),
      reference_audio_path: requiredString(formData, "reference_audio_path"),
      reference_text: requiredString(formData, "reference_text"),
      consent_confirmed: formData.get("consent_confirmed") === "true",
      is_default: true,
    });
  } catch (err) {
    redirectWithToast(
      "/settings",
      "error",
      `切换默认声音失败：${safeErrorMessage(err, "未知错误")}`,
    );
  }
  revalidatePath("/settings");
  redirectWithToast("/settings", "success", "默认声音已切换");
}
