import { getProductionFoundation } from "@/lib/api";
import { fetchLocalMemberIdentity } from "@/lib/local-member-auth";
import { isLocalAuthMode } from "@/lib/auth/routing";
import { createClient } from "@/lib/supabase/client";
import type { CreationPlanInput } from "@/lib/types";

export type DraftScope = { memberId: string; workspaceId: string };
export type PreflightReplyOrigin = { conversation_id: string; turn_id: string; request_id: string };
export type PreflightKnowledgePin = { knowledge_item_id: string; version: string };
export type PreflightDraftFields = {
  goal: string; name: string; targetCount: string; duration: string;
  language: CreationPlanInput["language"]; aspectRatio: CreationPlanInput["aspect_ratio"];
  presenterShare: 0 | 20 | 40 | 60 | 80 | 100; lipsyncQuality: "high" | "fast";
  voicePin: NonNullable<CreationPlanInput["selected_voice"]> | null;
  personPin: NonNullable<CreationPlanInput["selected_person"]> | null;
  voiceEdited: boolean; template: { id: string; version: number } | null;
  replyOrigin?: PreflightReplyOrigin | null;
  knowledgePin?: PreflightKnowledgePin | null;
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
function replyOrigin(value: unknown): value is PreflightReplyOrigin | null {
  if (value === null) return true;
  const uuid = (id: unknown) => typeof id === "string" && UUID.test(id);
  return record(value) && Object.keys(value).sort().join() === "conversation_id,request_id,turn_id"
    && uuid(value.conversation_id) && uuid(value.turn_id) && uuid(value.request_id);
}
function fields(value: unknown): value is PreflightDraftFields {
  if (!record(value)) return false;
  const keys = Object.keys(value).filter(key => key !== "knowledgePin").sort().join();
  if (keys !== "aspectRatio,duration,goal,language,lipsyncQuality,name,personPin,presenterShare,targetCount,template,voiceEdited,voicePin"
    && keys !== "aspectRatio,duration,goal,language,lipsyncQuality,name,personPin,presenterShare,replyOrigin,targetCount,template,voiceEdited,voicePin") return false;
  const template = value.template;
  return typeof value.goal === "string" && value.goal.length <= 1000 && typeof value.name === "string" && value.name.length <= 200
    && typeof value.targetCount === "string" && value.targetCount.length <= 10 && typeof value.duration === "string" && value.duration.length <= 10
    && ["zh-CN", "en-US", "zh-CN/en-US"].includes(value.language as string) && ["9:16", "16:9", "1:1"].includes(value.aspectRatio as string)
    && [0, 20, 40, 60, 80, 100].includes(value.presenterShare as number) && ["high", "fast"].includes(value.lipsyncQuality as string)
    && pin(value.voicePin) && pin(value.personPin) && typeof value.voiceEdited === "boolean"
    && (value.replyOrigin === undefined || replyOrigin(value.replyOrigin))
    && (value.knowledgePin === undefined || value.knowledgePin === null || (record(value.knowledgePin) && Object.keys(value.knowledgePin).sort().join() === "knowledge_item_id,version" && typeof value.knowledgePin.knowledge_item_id === "string" && value.knowledgePin.knowledge_item_id.length > 0 && value.knowledgePin.knowledge_item_id.length <= 120 && typeof value.knowledgePin.version === "string" && value.knowledgePin.version.length > 0 && value.knowledgePin.version.length <= 100))
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
export function clearPreflightDraft(storage: Storage, scope: DraftScope, id: string): void {
  const markerKey = repairKey(scope), raw = storage.getItem(markerKey);
  const ownMarker = raw !== null && parseRepairMarker(raw, scope).draftId.toLowerCase() === id.toLowerCase();
  if (ownMarker) storage.removeItem(markerKey);
  try { storage.removeItem(key(scope, id)); }
  catch (cause) {
    if (ownMarker) {
      try { storage.setItem(markerKey, raw!); }
      catch { throw new Error("草稿未删除，修复定位恢复也失败；当前输入仍保留，请勿离开表单。"); }
    }
    throw cause;
  }
}
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


export type PreflightRepairContext = { scope: DraftScope; target: NonNullable<ReturnType<typeof boundedPreflightReturn>>; draft: PreflightDraftFields };
function repairKey(scope: DraftScope): string {
  return `ai-video-diary.preflight-repair.v1:${encodeURIComponent(scope.memberId)}:${encodeURIComponent(scope.workspaceId)}`;
}
function parseRepairMarker(raw: string, scope: DraftScope): { draftId: string; returnTo: string } {
  if (raw.length > 2000) throw new Error("修复定位超出范围，没有读取草稿。");
  const value: unknown = JSON.parse(raw);
  if (!record(value) || Object.keys(value).sort().join() !== "draftId,memberId,returnTo,version,workspaceId" || value.version !== 1
    || value.memberId !== scope.memberId || value.workspaceId !== scope.workspaceId || typeof value.draftId !== "string" || !validDraftId(value.draftId) || typeof value.returnTo !== "string") throw new Error("修复定位无效，没有读取其他草稿。");
  return { draftId: value.draftId, returnTo: value.returnTo };
}
export function sameDraftTemplate(a: PreflightDraftFields["template"], b: PreflightDraftFields["template"]): boolean {
  return a?.id === b?.id && a?.version === b?.version;
}
export function savePreflightRepairContext(storage: Storage, scope: DraftScope, id: string, template: PreflightDraftFields["template"]): void {
  const draft = loadPreflightDraft(storage, scope, id);
  if (!draft || !sameDraftTemplate(draft.template, template)) throw new Error("本成员与工作区的草稿或固定模板版本失联，没有建立修复定位。");
  storage.setItem(repairKey(scope), JSON.stringify({ version: 1, memberId: scope.memberId, workspaceId: scope.workspaceId, draftId: id, returnTo: preflightReturnUrl(id, template) }));
}
// An explicit URL always wins, including when invalid. Only absent URLs may use the scoped marker.
export function resolvePreflightRepairContext(storage: Storage, scope: DraftScope, raw: string | null, origin: string): PreflightRepairContext | null {
  let target;
  if (raw !== null) {
    target = boundedPreflightReturn(raw, origin);
    if (!target) throw new Error("返回创作地址无效，没有回退其他草稿。");
  } else {
    const saved = storage.getItem(repairKey(scope));
    if (saved === null) return null;
    const marker = parseRepairMarker(saved, scope);
    target = boundedPreflightReturn(marker.returnTo, origin);
    if (!target || target.draftId.toLowerCase() !== marker.draftId.toLowerCase()) throw new Error("修复定位返回地址无效，没有读取其他草稿。");
  }
  const draft = loadPreflightDraft(storage, scope, target.draftId);
  if (!draft || !sameDraftTemplate(draft.template, target.template)) throw new Error("当前成员与工作区没有这份创作草稿或固定版本已失联，不能返回。");
  return { scope, target, draft };
}
export function replacePreflightDraftTemplate(storage: Storage, context: PreflightRepairContext, template: NonNullable<PreflightDraftFields["template"]>, origin: string): string {
  const current = resolvePreflightRepairContext(storage, context.scope, null, origin);
  if (!current || current.target.href !== context.target.href) throw new Error("当前修复定位已变化，没有修改其他草稿。");
  const original = storage.getItem(key(context.scope, context.target.draftId))!;
  const updated = { ...current.draft, template };
  savePreflightDraft(storage, context.scope, context.target.draftId, updated);
  try { savePreflightRepairContext(storage, context.scope, context.target.draftId, template); }
  catch (cause) {
    try { storage.setItem(key(context.scope, context.target.draftId), original); }
    catch { throw new Error("模板换版未完成，原草稿恢复也失败；没有离开模板页，请保留当前输入。"); }
    throw cause;
  }
  return preflightReturnUrl(context.target.draftId, template);
}
