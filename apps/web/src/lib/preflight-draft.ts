import { getProductionFoundation } from "@/lib/api";
import { fetchLocalMemberIdentity } from "@/lib/local-member-auth";
import { isLocalAuthMode } from "@/lib/auth/routing";
import { createClient } from "@/lib/supabase/client";
import type { CreationPlanInput } from "@/lib/types";

export type DraftScope = { memberId: string; workspaceId: string };
export type PreflightDraftFields = {
  goal: string; name: string; targetCount: string; duration: string;
  language: CreationPlanInput["language"]; aspectRatio: CreationPlanInput["aspect_ratio"];
  presenterShare: 0 | 20 | 40 | 60 | 80 | 100; lipsyncQuality: "high" | "fast";
  voicePin: NonNullable<CreationPlanInput["selected_voice"]> | null;
  personPin: NonNullable<CreationPlanInput["selected_person"]> | null;
  voiceEdited: boolean; template: { id: string; version: number } | null;
};
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
export function validDraftId(value: string): boolean { return UUID.test(value); }
export function sameDraftScope(a: DraftScope, b: DraftScope): boolean { return a.memberId === b.memberId && a.workspaceId === b.workspaceId; }

export async function readPreflightDraftScope(): Promise<DraftScope> {
  async function verifiedMemberId(): Promise<string> {
    if (isLocalAuthMode()) {
      const identity = await fetchLocalMemberIdentity();
      if (!identity.member_id) throw new Error("当前成员身份未确认，不能保存或恢复草稿。");
      return `local:${identity.member_id}`;
    }
    const { data, error } = await createClient().auth.getUser();
    if (error || !data.user) throw new Error("当前登录身份未确认，不能保存或恢复草稿。");
    return `supabase:${data.user.id}`;
  }
  const memberId = await verifiedMemberId();
  const foundation = await getProductionFoundation();
  if (!foundation.profile.workspace_id) throw new Error("当前工作区未确认，不能保存或恢复草稿。");
  // The identity may change while the authenticated workspace request is in flight.
  if (memberId !== await verifiedMemberId()) throw new Error("成员身份在工作区读取期间变化，没有保存或恢复草稿。");
  return { memberId, workspaceId: foundation.profile.workspace_id };
}

function key(scope: DraftScope, id: string): string {
  if (!validDraftId(id)) throw new Error("草稿编号无效，没有读取或写入草稿。");
  return `ai-video-diary.preflight-draft.v1:${encodeURIComponent(scope.memberId)}:${encodeURIComponent(scope.workspaceId)}:${id.toLowerCase()}`;
}
function record(value: unknown): value is Record<string, unknown> { return typeof value === "object" && value !== null && !Array.isArray(value); }
function pin(value: unknown): boolean { return value === null || (record(value) && Object.keys(value).length === 2 && typeof value.profile_id === "string" && value.profile_id.length > 0 && value.profile_id.length <= 120 && typeof value.version === "string" && value.version.length > 0 && value.version.length <= 120); }
function fields(value: unknown): value is PreflightDraftFields {
  if (!record(value) || Object.keys(value).sort().join() !== "aspectRatio,duration,goal,language,lipsyncQuality,name,personPin,presenterShare,targetCount,template,voiceEdited,voicePin") return false;
  const template = value.template;
  return typeof value.goal === "string" && value.goal.length <= 1000 && typeof value.name === "string" && value.name.length <= 200
    && typeof value.targetCount === "string" && value.targetCount.length <= 10 && typeof value.duration === "string" && value.duration.length <= 10
    && ["zh-CN", "en-US", "zh-CN/en-US"].includes(value.language as string) && ["9:16", "16:9", "1:1"].includes(value.aspectRatio as string)
    && [0, 20, 40, 60, 80, 100].includes(value.presenterShare as number) && ["high", "fast"].includes(value.lipsyncQuality as string)
    && pin(value.voicePin) && pin(value.personPin) && typeof value.voiceEdited === "boolean"
    && (template === null || (record(template) && Object.keys(template).length === 2 && typeof template.id === "string" && template.id.length > 0 && template.id.length <= 120 && Number.isSafeInteger(template.version) && Number(template.version) > 0));
}
export function savePreflightDraft(storage: Storage, scope: DraftScope, id: string, value: PreflightDraftFields): void {
  if (!fields(value)) throw new Error("草稿输入超出可保存范围；当前输入仍保留，没有离开表单。");
  storage.setItem(key(scope, id), JSON.stringify({ version: 1, id: id.toLowerCase(), memberId: scope.memberId, workspaceId: scope.workspaceId, fields: value }));
}
export function loadPreflightDraft(storage: Storage, scope: DraftScope, id: string): PreflightDraftFields | null {
  const raw = storage.getItem(key(scope, id));
  if (raw === null) return null;
  if (raw.length > 10000) throw new Error("临时草稿超出可读取范围，当前输入没有改变。");
  const saved: unknown = JSON.parse(raw);
  if (!record(saved) || Object.keys(saved).sort().join() !== "fields,id,memberId,version,workspaceId" || saved.version !== 1 || saved.id !== id.toLowerCase()
    || saved.memberId !== scope.memberId || saved.workspaceId !== scope.workspaceId || !fields(saved.fields)) throw new Error("临时草稿内容无效，当前输入没有改变。");
  return saved.fields;
}
export function clearPreflightDraft(storage: Storage, scope: DraftScope, id: string): void { storage.removeItem(key(scope, id)); }
export function preflightReturnUrl(id: string, template: PreflightDraftFields["template"]): string {
  if (!validDraftId(id)) throw new Error("草稿编号无效。");
  const params = new URLSearchParams({ new: "1", draft_id: id });
  if (template) { params.set("template_id", template.id); params.set("template_version", String(template.version)); }
  return `/production?${params.toString()}`;
}
export function boundedPreflightReturn(raw: string, origin: string): { href: string; draftId: string; template: PreflightDraftFields["template"] } | null {
  if (raw.length > 800 || /[\u0000-\u001f\\]/.test(raw)) return null;
  let url: URL;
  try { url = new URL(raw, origin); } catch { return null; }
  if (url.origin !== origin || url.username || url.password || url.hash || url.pathname !== "/production") return null;
  const allowed = ["new", "draft_id", "template_id", "template_version"];
  if ([...url.searchParams.keys()].some(item => !allowed.includes(item) || url.searchParams.getAll(item).length !== 1) || url.searchParams.get("new") !== "1") return null;
  const draftId = url.searchParams.get("draft_id") ?? "";
  if (!validDraftId(draftId)) return null;
  const id = url.searchParams.get("template_id"), version = url.searchParams.get("template_version");
  if ((id === null) !== (version === null) || (id !== null && (!id.length || id.length > 120 || /[\u0000-\u001f]/.test(id))) || (version !== null && (!/^[1-9]\d*$/.test(version) || !Number.isSafeInteger(Number(version))))) return null;
  const template = id !== null ? { id, version: Number(version) } : null;
  return { href: preflightReturnUrl(draftId, template), draftId, template };
}
export const PREFLIGHT_RESOURCE_PATHS = new Set(["/knowledge", "/sources", "/profiles", "/library", "/settings", "/health", "/templates"]);
