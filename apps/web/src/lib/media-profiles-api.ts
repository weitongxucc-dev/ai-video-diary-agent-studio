import { localMemberTokenHeader } from "./local-member-auth";

export type MediaAuthorization = {
  id: string; workspace_id: string; version: string; rights_holder: string;
  content_types: string[]; usage_scopes: string[]; effective_from: string;
  expires_at: string | null; evidence_reference: string;
  status: "active" | "pending" | "expired" | "revoked";
};

export type PersonProfile = {
  id: string; workspace_id: string; display_name: string; version: string;
  avatar_reference: string; background_capabilities: string[]; shot_capabilities: string[];
  authorization: MediaAuthorization; current_authorization: MediaAuthorization;
  runnable: boolean; runnable_reason: string | null;
};

export type VoiceProfile = {
  id: string; workspace_id: string; person_profile_id: string; person_version: string;
  version: string; language: "zh" | "en" | "zh-en"; reference_audio_path: string;
  reference_text: string; model_id: string; authorization: MediaAuthorization;
  current_authorization: MediaAuthorization; runnable: boolean; runnable_reason: string | null;
};

export type PronunciationEntry = {
  id: string; workspace_id: string; term: string; language: "zh" | "en" | "zh-en";
  target_pronunciation: string; scope: "workspace" | "person" | "task";
  scope_reference: string | null; version: string;
};

export type MediaCapability = {
  capability_id: string; available: boolean; model_id: string | null; message: string;
  impact: string | null; repair_action: string | null;
};

export type ApprovedMaterial = { id: string; file_name: string; duration_seconds: number | null; review_status: "approved"; workspace_reference: string | null };
export type MediaPreview = {
  id: string; workspace_id: string; preview_type: "voice" | "avatar_lipsync"; identity: string;
  attempt: number; status: "running" | "succeeded" | "unavailable" | "blocked" | "failed";
  capability_id: string; model_id: string; language: string;
  person_profile_id: string | null; person_version: string | null;
  voice_profile_id: string | null; voice_version: string | null;
  authorization_id: string | null; authorization_version: string | null;
  output_url: string | null; reason: string | null; impact: string | null;
  repair_action: string | null; started_at: string; finished_at: string | null;
};
export type AuthorizationInput = Omit<MediaAuthorization, "workspace_id">;
export type PersonInput = Pick<PersonProfile, "id" | "display_name" | "version" | "avatar_reference" | "background_capabilities" | "shot_capabilities"> & { authorization: AuthorizationInput };
export type VoiceInput = Pick<VoiceProfile, "id" | "person_profile_id" | "person_version" | "version" | "language" | "reference_audio_path" | "reference_text" | "model_id"> & { authorization: AuthorizationInput };
export type PronunciationInput = Omit<PronunciationEntry, "workspace_id">;
export type VoiceReferenceImport = {
  reference: string; file_name: string; size_bytes: number; duration_seconds: number;
};

export class MediaProfilesApiError extends Error {
  constructor(public readonly status: number, message: string, public readonly code: string | null = null, public readonly impact: string | null = null, public readonly repairAction: string | null = null) {
    super(message); this.name = "MediaProfilesApiError";
  }
}

function json(value: string): unknown { try { return JSON.parse(value); } catch { return value; } }

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  const formUpload = typeof FormData !== "undefined" && init?.body instanceof FormData;
  try { response = await fetch(path, { ...init, headers: { ...(init?.body && !formUpload ? { "Content-Type": "application/json" } : {}), ...init?.headers, ...localMemberTokenHeader() }, cache: "no-store" }); }
  catch { throw new MediaProfilesApiError(0, "本地服务未接通。"); }
  const text = await response.text(); const payload = text ? json(text) : null;
  if (!response.ok) {
    const detail = payload && typeof payload === "object" && "detail" in payload ? (payload as { detail: unknown }).detail : payload;
    const object = detail && typeof detail === "object" ? detail as Record<string, unknown> : {};
    throw new MediaProfilesApiError(response.status, typeof detail === "string" ? detail : typeof object.detail === "string" ? object.detail : `请求失败：${response.status}`, typeof object.error === "string" ? object.error : null, typeof object.impact === "string" ? object.impact : null, typeof object.repair_action === "string" ? object.repair_action : null);
  }
  return payload as T;
}

export const listPersonProfiles = () => request<{ items: PersonProfile[]; total_count: number }>("/api/media/person-profiles");
export const savePersonProfile = (input: PersonInput) => request<PersonProfile>("/api/media/person-profiles", { method: "POST", body: JSON.stringify(input) });
export const listVoiceProfiles = () => request<{ items: VoiceProfile[]; total_count: number }>("/api/media/voice-profiles");
export const saveVoiceProfile = (input: VoiceInput) => request<VoiceProfile>("/api/media/voice-profiles", { method: "POST", body: JSON.stringify(input) });
export const importVoiceReference = (file: File) => {
  const body = new FormData();
  body.append("audio", file);
  return request<VoiceReferenceImport>("/api/media/voice-references/import", { method: "POST", body });
};
export const listPronunciationEntries = () => request<{ items: PronunciationEntry[]; total_count: number }>("/api/media/pronunciation-entries");
export const savePronunciationEntry = (input: PronunciationInput) => request<PronunciationEntry>("/api/media/pronunciation-entries", { method: "POST", body: JSON.stringify(input) });
export const listAuthorizations = () => request<{ items: MediaAuthorization[]; total_count: number }>("/api/media/authorizations");
export const getMediaCapability = (name: "voice-clone" | "avatar-lipsync") => request<MediaCapability>(`/api/media/capabilities/${name}`);
export const listApprovedMaterials = () => request<{ assets: ApprovedMaterial[]; total_count: number }>("/api/production/materials?review_status=approved&limit=24");
export const createVoicePreview = (input: { voice_profile_id: string; voice_version: string; preview_text: string; retry: boolean }) => request<MediaPreview>("/api/media/previews/voice", { method: "POST", body: JSON.stringify(input) });
export const createAvatarLipsyncPreview = (input: {
  person_profile_id: string; person_version: string; voice_profile_id: string; voice_version: string;
  voice_preview_identity: string; motion: string; expression: string;
  authorization_id: string; authorization_version: string; model_id: string; retry: boolean;
}) => request<MediaPreview>("/api/media/previews/avatar-lipsync", { method: "POST", body: JSON.stringify(input) });
export const listMediaPreviews = (previewType?: MediaPreview["preview_type"]) => request<{ items: MediaPreview[]; total_count: number }>(`/api/media/previews${previewType ? `?preview_type=${previewType}` : ""}`);
export const listMediaPreviewAttempts = (previewType: MediaPreview["preview_type"], identity: string) => request<{ items: MediaPreview[]; total_count: number }>(`/api/media/previews/${previewType}/${encodeURIComponent(identity)}/attempts`);

export function mediaProfilesErrorMessage(error: unknown) {
  if (!(error instanceof MediaProfilesApiError)) return "读取人物与声音信息失败，请重试。";
  if (error.status === 502) return "本机服务暂时连不上，请重试。";
  if (error.status === 0) return "本地服务未接通。";
  if (error.status === 404) return "本地服务版本未接通人物与声音接口。";
  if (error.status === 401) return "需要登录后才能管理人物与声音。";
  if (error.status === 403) return "当前账号无权限管理这些档案。";
  if (error.status === 501 || error.code === "capability_unavailable") return error.message || "此能力尚未配置。";
  return error.message || "读取人物与声音信息失败，请重试。";
}
