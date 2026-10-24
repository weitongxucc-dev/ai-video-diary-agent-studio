"use client";

import { AppSelect, AppSelectOption } from "@/components/AppSelect";
import { AlertCircle, CheckCircle2, ChevronRight, LoaderCircle, SearchCheck, ShieldCheck } from "lucide-react";
import { type FormEvent, useEffect, useId, useRef, useState } from "react";
import {
  confirmCreationPlan,
  CreationPlanApiError,
  previewCreationPlan,
} from "@/lib/api";
import { getDefaultVoice, listVoiceProfiles, listPersonProfiles, type VoiceProfile, type PersonProfile } from "@/lib/media-profiles-api";
import type { CreationPlanInput, CreationPlanPreview, VideoTemplate } from "@/lib/types";
import { PresenterShareSlider, type PresenterShare } from "@/components/PresenterShareSlider";
import { LipSyncQualitySelect, type LipSyncQuality } from "@/components/LipSyncQualitySelect";
import { getKnowledgeItem } from "@/lib/source-api";
import { createCreationRequest } from "@/lib/creation-request-api";
import { CreationRequestPanel } from "./CreationRequestPanel";
import ui from "./CreateProductionTaskForm.module.css";
import { clearPreflightDraft, loadPreflightDraft, PREFLIGHT_RESOURCE_PATHS, preflightReturnUrl, readPreflightDraftScope, sameDraftScope, savePreflightDraft, savePreflightRepairContext, validDraftId, type DraftScope, type PreflightDraftFields, type PreflightReplyOrigin, type PreflightKnowledgePin } from "@/lib/preflight-draft";

// These fields are returned by MaterialSegmentMatchResponse; the shared Web type omits them.
type PreflightMatch = CreationPlanPreview["material_matches"][number] & { match_tier?: string; asset_file_name?: string };
function matchTierLabel(tier: string | undefined) {
  if (tier === "text") return "主题候选";
  if (tier === "partial") return "词语候选";
  if (tier === "structural") return "结构候选";
  return "候选层级未提供";
}
function stageLabel(stage: string) {
  return ({ preparation: "资料准备", topic: "选题", script: "文案", narration: "配音", person_lipsync: "人物与口型", avatar_lipsync: "人物与口型", montage: "视频剪辑", video_editing: "视频剪辑", subtitle_cover: "字幕与封面", review_output: "检查与输出", publish_analytics: "发布与数据" } as Record<string, string>)[stage] ?? "后续阶段（未提供名称）";
}

type CreateProductionTaskFormProps = {
  template?: VideoTemplate | null;
  autoFocus?: boolean;
  initialGoal?: string;
  assignLocation?: (url: string) => void;
  draftId?: string;
  replyOrigin?: PreflightReplyOrigin | null;
  initialKnowledgePin?: PreflightKnowledgePin | null;
};

type FormError = {
  message: string;
  impact: string | null;
  repairAction: string | null;
};

function assignDocumentLocation(url: string) {
  window.location.assign(url);
}

function messageForError(cause: unknown): FormError {
  if (cause instanceof CreationPlanApiError) {
    return { message: cause.message, impact: cause.impact, repairAction: cause.repairAction };
  }
  return {
    message: cause instanceof Error && cause.message ? cause.message : "当前无法完成此操作。",
    impact: null,
    repairAction: null,
  };
}

const languageLabels: Record<string, string> = { zh: "中文", en: "英文", "zh-en": "中英混读" };

function materialSummary(value: string) {
  return value.replace(/\ba[-_]?roll\b/gi, "主镜头").replace(/\bb[-_]?roll\b/gi, "补充画面");
}

function scopeLabel(scope: string) {
  if (scope === "workspace") return "工作区";
  if (scope === "person") return "人物";
  return "当前任务";
}

function capabilityLabel(capability: string) {
  if (capability.includes("voice")) return "声音生成";
  if (capability.includes("lipsync") || capability.includes("avatar")) return "人物口型";
  if (capability.includes("text")) return "文字生成";
  return "模型能力";
}

function profileOptionLabel(profile: VoiceProfile | PersonProfile) {
  return /^\d{1,4}$/.test(profile.version) ? `${profile.display_name} · ${profile.version}` : profile.display_name;
}

function stateLabel(ready: boolean) {
  return ready ? "安装检查通过" : "不可用";
}

export function PreflightFacts({ preview, voices = [], people = [], onRepair, disabled = false }: { preview: CreationPlanPreview; voices?: VoiceProfile[]; people?: PersonProfile[]; onRepair?: (href: string) => void; disabled?: boolean }) {
  const matches = preview.material_matches as PreflightMatch[];
  return (
    <div className="creation-plan-facts" aria-label="可用内容检查结果">
      <section>
        <header><SearchCheck size={16} /><div><span>资料与信息源</span><strong>{preview.knowledge_references.length + preview.source_references.length} 条已纳入检查</strong></div></header>
        {preview.knowledge_references.length ? <ul>{preview.knowledge_references.map((item) => <li key={`${item.id}-${item.version}`}><strong>{item.title}</strong><span>已纳入本次资料检查</span><details className={ui.diagnostics}><summary>资料引用诊断</summary><p>{item.id} · {item.version}</p><p>{item.authorization || "未返回证明字段"} · {item.ownership || "未返回归属字段"}</p></details></li>)}</ul> : null}
        {preview.source_references.length ? <ul>{preview.source_references.map((item) => <li key={item.id}><strong>{item.title}</strong><span>{item.source_label}</span><details className={ui.diagnostics}><summary>来源引用诊断</summary><p>{item.id} · {item.license || "未返回证明字段"}</p></details></li>)}</ul> : null}
        {!preview.knowledge_references.length && !preview.source_references.length ? <p>本次检查没有返回资料或信息源。</p> : null}
      </section>
      <section>
        <header><ShieldCheck size={16} /><div><span>素材候选</span><strong>{matches.length} 个检索候选</strong></div></header>
        <p>候选按主题、词语或结构条件检索，需在选片时核对内容与使用范围。</p>
        {matches.length ? <ul>{matches.slice(0, 4).map((item) => <li key={`${item.asset_ref}-${item.segment_ref}`}><strong>{matchTierLabel(item.match_tier)}</strong><span>{item.reasons.map(materialSummary).join("；")}</span><details className={ui.diagnostics}><summary>候选诊断</summary><p>原始理由：{item.reasons.join("；")}</p><p>引用：{item.asset_ref} · {item.segment_ref}</p><p>层级：{item.match_tier ?? "未返回"} · 启发分值：{item.score}（用于排序，不是适配概率）</p>{item.asset_file_name ? <p>文件：{item.asset_file_name}</p> : null}</details></li>)}</ul> : <p>当前目标没有返回检索候选。{onRepair ? <button type="button" disabled={disabled} onClick={() => onRepair("/library")}>前往素材库补充内容</button> : <a href="/library">前往素材库补充内容</a>}</p>}
        {matches.length > 4 ? <p>当前展示前 4 个，检查共返回 {matches.length} 个候选。</p> : null}
        <details className={ui.diagnostics}><summary>素材计数与筛选口径</summary><p>本次可自动选用：{preview.approved_asset_count} 个素材 · {preview.approved_clip_count} 个补充片段；检索候选与可用计数分别核验。</p><p>{materialSummary(preview.material_rule_summary)}</p></details>
        {preview.selected_voice ? <><p>本次声音：{voices.find(item => item.id === preview.selected_voice?.profile_id && item.version === preview.selected_voice?.version)?.display_name ?? "已固定声音版本"}，适用语言：{languageLabels[preview.selected_voice.language]}。配音及人物阶段是否可进入，见待处理事项。</p><details className={ui.diagnostics}><summary>声音引用诊断</summary><p>声音：{preview.selected_voice.profile_id} · {preview.selected_voice.version}</p><p>模型：{preview.selected_voice.model_id}</p><p>语言：{preview.selected_voice.language}</p></details></> : <p>本次检查未选定声音，配音阶段的影响见待处理事项。</p>}
        {preview.selected_person ? <><p>本次人物：{people.find(item => item.id === preview.selected_person?.profile_id && item.version === preview.selected_person?.version)?.display_name ?? "已固定人物版本"}（与声音独立选择）</p><details className={ui.diagnostics}><summary>人物引用诊断</summary><p>{preview.selected_person.profile_id} · {preview.selected_person.version}</p></details></> : null}
        {preview.pronunciation_references.length ? <ul>{preview.pronunciation_references.map((item) => <li key={`${item.id}-${item.version}`}><strong>发音词典</strong><span>{scopeLabel(item.scope)} · {languageLabels[item.language]}</span></li>)}</ul> : null}
      </section>
      <section>
        <header><CheckCircle2 size={16} /><div><span>模型与人工确认</span><strong>{preview.model_snapshot.length} 项安装状态已检查</strong></div></header>
        <p>安装检查核对运行程序与模型文件，不代表生成实测通过或设备资源许可。</p>
        {preview.model_snapshot.length ? <ul>{preview.model_snapshot.map((item) => <li key={item.id}><strong>{capabilityLabel(item.capability)} · {stateLabel(item.ready)}</strong><details className={ui.diagnostics}><summary>模型诊断</summary><p>{item.message}</p><p>{item.id} · {item.capability} · {item.state}</p></details></li>)}</ul> : <p>本地服务没有返回模型状态。</p>}
        {preview.confirmation_points.length ? <ul>{preview.confirmation_points.map((item) => <li key={item}><strong>需要你确认</strong><span>{item}</span></li>)}</ul> : null}
      </section>
    </div>
  );
}

function repairEntries(reason: string, stage: string): { href: string; label: string }[] {
  if (reason === "template_voice_unavailable") return [{ href: "/profiles", label: "检查声音版本" }, { href: "/templates", label: "重新选择模板版本" }];
  if (reason === "content_sources_missing") return [{ href: "/knowledge", label: "补充知识资料" }, { href: "/sources", label: "管理信息源" }];
  if (["approved_material_clips_missing", "template_material_matches_missing"].includes(reason)) return [{ href: "/library", label: "补充素材" }];
  if (["voice_model_unavailable", "person_render_unavailable"].includes(reason)) return [{ href: "/health", label: "检查本地能力" }, { href: "/settings", label: "检查连接设置" }];
  if (stage === "narration" || stage === "avatar_lipsync" || stage === "person_lipsync") return [{ href: "/profiles", label: stage === "narration" ? "检查声音版本" : "检查人物参考" }];
  return [];
}
export function Blockers({ preview, onRepair, disabled = false }: { preview: CreationPlanPreview; onRepair?: (href: string) => void; disabled?: boolean }) {
  if (!preview.blockers.length) return null;
  return <section className="creation-plan-blockers" aria-label="待处理事项"><header><AlertCircle size={17} /><div><span>需要处理</span><h3>{preview.blockers.length} 项待处理</h3></div></header><div>{preview.blockers.map((item, index) => <article key={`${item.reason}-${index}`}><strong>{item.reason === "template_voice_unavailable" ? "模板指定的固定声音版本不可用，未替换为其他声音。" : item.impact}</strong><p>受影响阶段：{stageLabel(item.stage)}</p><p>怎么处理：{item.repair_action}</p><span className={item.blocks_confirmation ? "is-blocking" : ""}>{item.blocks_confirmation ? "阻止创建任务" : `不阻止创建；进入${stageLabel(item.stage)}前需处理`}</span>{onRepair ? <div className={ui.repairActions}>{repairEntries(item.reason, item.stage).map(entry => <button key={entry.href} type="button" disabled={disabled} onClick={() => onRepair(entry.href)}>{entry.label}</button>)}</div> : null}<details className={ui.diagnostics}><summary>阻断诊断</summary><p>{item.reason} · {item.stage}</p>{item.reason === "template_voice_unavailable" ? <p>{item.impact}</p> : null}</details></article>)}</div></section>;
}

export function CreateProductionTaskForm({
  template = null,
  autoFocus = false,
  initialGoal = "",
  assignLocation = assignDocumentLocation,
  draftId,
  replyOrigin: initialReplyOrigin = null,
  initialKnowledgePin = null,
}: CreateProductionTaskFormProps) {
  const [voices, setVoices] = useState<VoiceProfile[]>([]);
  const [people, setPeople] = useState<PersonProfile[]>([]);
  const [knowledgePin, setKnowledgePin] = useState<PreflightKnowledgePin | null>(initialKnowledgePin);
  const [knowledgeName, setKnowledgeName] = useState<string | null>(null);
  const [knowledgeError, setKnowledgeError] = useState<string | null>(null);
  useEffect(() => {
    let active = true; setKnowledgeName(null); setKnowledgeError(null);
    if (knowledgePin) void getKnowledgeItem(knowledgePin.knowledge_item_id).then(item => {
      if (!active) return;
      const selected = item.version === knowledgePin.version ? item : item.versions.find(version => version.version === knowledgePin.version);
      if (!selected) { setKnowledgeError("所选资料的固定版本无法读取，请返回知识库重新选择，或取消本次引用。"); return; }
      setKnowledgeName(selected.title);
    }).catch(cause => { if (active) setKnowledgeError(cause instanceof Error ? cause.message : "所选资料无法读取。"); });
    return () => { active = false; };
  }, [knowledgePin?.knowledge_item_id, knowledgePin?.version]);
  const [voicePin, setVoicePin] = useState<CreationPlanInput["selected_voice"]>(undefined);
  const [personPin, setPersonPin] = useState<CreationPlanInput["selected_person"]>(undefined);
  const voiceSelectionEdited = useRef(false);
  const draftScope = useRef<DraftScope | null>(null);
  const draftUuid = useRef<string | null>(null);
  const previousDraftId = useRef(draftId);
  const userEdited = useRef(false);
  const [draftError, setDraftError] = useState<string | null>(null);
  const [draftReady, setDraftReady] = useState(false);
  const [restored, setRestored] = useState(false);
  const [replyOrigin, setReplyOrigin] = useState<PreflightReplyOrigin | null>(initialReplyOrigin);
  const replyOriginRef = useRef<PreflightReplyOrigin | null>(initialReplyOrigin);
  const alive = useRef(true);
  const operation = useRef(0);
  const [profilesError, setProfilesError] = useState<string | null>(null);
  useEffect(() => {
    let active = true;
    void verifiedScope().then(async scope => {
      const [v, p, d] = await Promise.all([listVoiceProfiles(), listPersonProfiles(), getDefaultVoice()]);
      const current = await verifiedScope();
      if (!sameDraftScope(scope, current)) throw new Error("成员或工作区在档案读取期间变化，没有使用旧档案。");
      if (!active) return;
      setVoices(v.items); setPeople(p.items);
      if (d && !voiceSelectionEdited.current && !submittingRef.current) { setVoicePin({ profile_id: d.id, version: d.version }); ++inputRevision.current; setPreview(null); }
    }).catch(cause => { if (active) setProfilesError(cause instanceof Error ? cause.message : "档案读取失败"); });
    return () => { active = false; };
  }, []);
  const [goal, setGoal] = useState(initialGoal);
  const [name, setName] = useState("");
  const [targetCount, setTargetCount] = useState("1");
  const [language, setLanguage] = useState<CreationPlanInput["language"]>(template?.settings.language ?? "zh-CN");
  const [aspectRatio, setAspectRatio] = useState<CreationPlanInput["aspect_ratio"]>(template?.settings.aspect_ratio ?? "9:16");
  const [duration, setDuration] = useState(String(template?.settings.duration_target_seconds ?? 60));
  const [presenterShare, setPresenterShare] = useState<PresenterShare>(template?.settings.presenter_share_target ?? 20);
  const [lipsyncQuality, setLipsyncQuality] = useState<LipSyncQuality>(template?.settings.lipsync_quality ?? (template ? "fast" : "high"));
  const [preview, setPreview] = useState<CreationPlanPreview | null>(null);
  const [creationRequestId, setCreationRequestId] = useState<string | null>(null);
  const creationRequestKey = useRef<string | null>(null);
  const [created, setCreated] = useState(false);
  const [pending, setPending] = useState<"preview" | "confirm" | null>(null);
  const [error, setError] = useState<FormError | null>(null);
  const submittingRef = useRef(false);
  const inputRevision = useRef(0);
  const formId = useId();

  useEffect(() => {
    ++inputRevision.current;
    setPreview(null);
    setCreationRequestId(null);
    creationRequestKey.current = null;
    setError(null);
    setDuration(String(template?.settings.duration_target_seconds ?? 60));
    setLanguage(template?.settings.language ?? "zh-CN"); setAspectRatio(template?.settings.aspect_ratio ?? "9:16");
  }, [template?.id, template?.version]);

  useEffect(() => {
    let active = true; alive.current = true;
    ++inputRevision.current; ++operation.current; submittingRef.current = false; setPending(null); setCreated(false); setPreview(null);
    setCreationRequestId(null); creationRequestKey.current = null;
    userEdited.current = false; setDraftReady(false); setRestored(false); setDraftError(null); draftScope.current = null;
    draftUuid.current = draftId ?? crypto.randomUUID();
    const hasInitialOrigin = initialReplyOrigin !== null;
    if (!draftId || previousDraftId.current !== draftId) {
      replyOriginRef.current = hasInitialOrigin ? initialReplyOrigin : null;
      setReplyOrigin(replyOriginRef.current);
    }
    if (draftId || previousDraftId.current !== draftId) {
      setGoal(draftId ? "" : initialGoal); setName(""); setTargetCount("1");
      setDuration(String(template?.settings.duration_target_seconds ?? 60));
      setLanguage(template?.settings.language ?? "zh-CN"); setAspectRatio(template?.settings.aspect_ratio ?? "9:16");
      setPresenterShare(template?.settings.presenter_share_target ?? 20); setLipsyncQuality(template?.settings.lipsync_quality ?? (template ? "fast" : "high"));
      voiceSelectionEdited.current = false; setVoicePin(undefined); setPersonPin(undefined); setKnowledgePin(initialKnowledgePin);
    }
    previousDraftId.current = draftId;
    void readPreflightDraftScope().then(scope => {
      if (!active) return;
      draftScope.current = scope;
      if (draftId) {
        const saved = loadPreflightDraft(window.sessionStorage, scope, draftId);
        if (!saved) throw new Error("当前成员与工作区没有这份临时草稿。");
        if (JSON.stringify(saved.template) !== JSON.stringify(template ? { id: template.id, version: template.version } : null)) throw new Error("草稿模板固定版本与入口不符，没有恢复旧输入。");
        if (userEdited.current) throw new Error("你已更改输入，晚到的草稿没有覆盖当前内容。");
        setGoal(saved.goal); setName(saved.name); setTargetCount(saved.targetCount); setDuration(saved.duration);
        setLanguage(saved.language); setAspectRatio(saved.aspectRatio); setPresenterShare(saved.presenterShare); setLipsyncQuality(saved.lipsyncQuality);
        voiceSelectionEdited.current = saved.voiceEdited || saved.voicePin !== null;
        setVoicePin(saved.voicePin ?? undefined); setPersonPin(saved.personPin ?? undefined); setKnowledgePin(saved.knowledgePin ?? null); setRestored(true);
        replyOriginRef.current = saved.replyOrigin ?? null;
        setReplyOrigin(replyOriginRef.current);
        ++inputRevision.current;
        void runPreview(saved);
      }
      setDraftReady(true);
    }).catch(cause => { if (active) setDraftError(`临时草稿未恢复：${cause instanceof Error ? cause.message : "身份或存储读取失败"}`); });
    return () => { active = false; alive.current = false; ++inputRevision.current; ++operation.current; };
  }, [draftId, template?.id, template?.version, initialReplyOrigin?.conversation_id, initialReplyOrigin?.turn_id, initialReplyOrigin?.request_id, initialKnowledgePin?.knowledge_item_id, initialKnowledgePin?.version]);

  function draftFields(): PreflightDraftFields {
    return { goal, name, targetCount, duration, language, aspectRatio, presenterShare, lipsyncQuality,
      voicePin: voicePin ?? null, personPin: personPin ?? null, voiceEdited: voiceSelectionEdited.current,
      template: template ? { id: template.id, version: template.version } : null,
      replyOrigin: replyOriginRef.current, knowledgePin };
  }
  async function verifiedScope(): Promise<DraftScope> {
    const scope = await readPreflightDraftScope();
    if (draftScope.current && !sameDraftScope(scope, draftScope.current)) throw new Error("成员或工作区已切换，请重新打开新建表单；没有发送原草稿。");
    return scope;
  }
  async function repair(href: string) {
    if (!PREFLIGHT_RESOURCE_PATHS.has(href) || !draftReady || pending) return;
    const revision = inputRevision.current;
    try {
      const scope = await verifiedScope();
      if (!alive.current || revision !== inputRevision.current) return;
      const id = draftUuid.current!;
      if (!validDraftId(id)) throw new Error("草稿编号无效，没有离开表单。");
      const saved = draftFields();
      savePreflightDraft(window.sessionStorage, scope, id, saved);
      savePreflightRepairContext(window.sessionStorage, scope, id, saved.template);
      assignLocation(`${href}?${new URLSearchParams({ return_to: preflightReturnUrl(id, saved.template) })}`);
    } catch (cause) { if (alive.current && revision === inputRevision.current) setDraftError(`临时草稿保存失败，当前输入仍保留：${cause instanceof Error ? cause.message : "存储不可用"}`); }
  }

  async function abandonDraft() {
    const revision = inputRevision.current;
    try {
      const scope = await verifiedScope();
      if (!alive.current || revision !== inputRevision.current) return;
      clearPreflightDraft(window.sessionStorage, scope, draftUuid.current!);
      assignLocation("/production");
    } catch (cause) { if (alive.current && revision === inputRevision.current) setDraftError(`没有放弃草稿，当前输入仍保留：${cause instanceof Error ? cause.message : "存储不可用"}`); }
  }

  function input(value = draftFields()): CreationPlanInput {
    const durationSeconds = Number(value.duration);
    if (!Number.isSafeInteger(durationSeconds) || durationSeconds <= 0) {
      throw new Error("目标时长必须为有效的正整数秒数。");
    }
    return {
      ...(value.template ? { template_id: value.template.id, template_version: value.template.version } : {}),
      goal: value.goal.trim(), ...(value.name.trim() ? { name: value.name.trim() } : {}), target_count: Number(value.targetCount),
      language: value.language, aspect_ratio: value.aspectRatio, duration_target_seconds: durationSeconds,
      presenter_share_target: value.presenterShare, lipsync_quality: value.lipsyncQuality,
      ...(value.knowledgePin ? { selected_knowledge: value.knowledgePin } : {}),
      ...(value.voicePin ? {selected_voice: value.voicePin} : {}), ...(value.personPin ? {selected_person: value.personPin} : {}),
    };
  }

  function invalidatePreview() {
    userEdited.current = true;
    ++inputRevision.current;
    if (replyOriginRef.current) {
      replyOriginRef.current = { ...replyOriginRef.current, request_id: crypto.randomUUID() };
      setReplyOrigin(replyOriginRef.current);
    }
    setPreview(null);
    setCreationRequestId(null);
    creationRequestKey.current = null;
    setError(null);
  }

  async function runPreview(saved?: PreflightDraftFields) {
    if (submittingRef.current || (draftId && !saved && !draftReady)) return;
    submittingRef.current = true;
    setPending("preview");
    setError(null);
    const revision = inputRevision.current;
    const request = ++operation.current;
    try {
      await verifiedScope();
      if (!alive.current || revision !== inputRevision.current) return;
      const values = input(saved);
      if (values.target_count > 1) {
        if (replyOriginRef.current) throw new Error("当前选中的回复只绑定一条文案来源。请先为这条回复创建单条任务，再分别准备其他视频的具体目标。");
        creationRequestKey.current ??= crypto.randomUUID();
        const result = await createCreationRequest({ ...values, request_key: creationRequestKey.current });
        await verifiedScope();
        if (alive.current && revision === inputRevision.current) { setCreationRequestId(result.id); setPreview(null); }
        return;
      }
      const result = await previewCreationPlan(values);
      await verifiedScope();
      if (alive.current && revision === inputRevision.current) setPreview(result);
    } catch (cause) {
      if (alive.current && revision === inputRevision.current) setError(messageForError(cause));
    } finally {
      if (alive.current && request === operation.current) { submittingRef.current = false; setPending(null); }
    }
  }

  async function handlePreview(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await runPreview();
  }

  async function handleConfirm() {
    if (!preview || !preview.confirmable || submittingRef.current) return;
    submittingRef.current = true;
    setPending("confirm");
    setError(null);
    const revision = inputRevision.current;
    const confirmDraftId = draftUuid.current;
    const request = ++operation.current;
    try {
      const scope = await verifiedScope();
      if (!alive.current || revision !== inputRevision.current) { if (alive.current && request === operation.current) { submittingRef.current = false; setPending(null); } return; }
      const plan = await confirmCreationPlan({
        ...input(), preflight_fingerprint: preview.preflight_fingerprint,
        ...(replyOriginRef.current ? { reply_origin: replyOriginRef.current } : {}),
      });
      let cleanupWarning = "";
      try { clearPreflightDraft(window.sessionStorage, scope, confirmDraftId!); }
      catch (cause) { cleanupWarning = `临时草稿清理失败：${cause instanceof Error ? cause.message : "存储不可用"}`; }
      if (!alive.current || request !== operation.current) return;
      setCreated(true); setPending(null);
      try { await verifiedScope(); }
      catch (cause) { if (alive.current && request === operation.current) setDraftError(`任务已创建，但当前成员或工作区未重新确认，没有自动打开任务：${cause instanceof Error ? cause.message : "身份读取失败"}${cleanupWarning ? `；${cleanupWarning}` : ""}`); return; }
      if (!alive.current || request !== operation.current) return;
      assignLocation(`/tasks/${encodeURIComponent(plan.creation_task.id)}?${new URLSearchParams({ toast: "success", toast_message: `创作任务已创建，选题尚未开始。${cleanupWarning}` }).toString()}`);
    } catch (cause) {
      if (!alive.current || request !== operation.current) return;
      if (cause instanceof CreationPlanApiError && cause.code === "creation_plan_preflight_stale") {
        setPreview(null);
      }
      setError(messageForError(cause));
      submittingRef.current = false;
      setPending(null);
    }
  }

  const isPending = pending !== null || created;
  return (
    <div className={`creation-plan-form ${ui.form}`}>
      {template ? <section aria-label="所选模板"><strong>{template.name} · v{template.version}</strong><p>{template.settings.script_section_count ? `${template.settings.script_section_count} 段文案 · ` : ""}{template.settings.fit_mode === "fit" ? "完整显示画面" : "填满画面"} · 复用字幕样式</p><p>{template.settings.style_instruction || "未指定额外文案风格"}</p><button type="button" disabled={!draftReady || isPending} onClick={() => void repair("/templates")}>重新选择模板</button></section> : null}
      {knowledgePin ? <section aria-label="所选知识资料"><strong>{knowledgeName ?? "所选知识资料"} · 固定版本 {knowledgePin.version}</strong><p>本次制作方案将核验并引用这个固定版本；修改制作目标不会更换资料版本。</p>{knowledgeError ? <p role="alert">{knowledgeError}</p> : null}<button type="button" disabled={isPending} onClick={() => { setKnowledgePin(null); invalidatePreview(); }}>取消本次资料引用</button><button type="button" disabled={!draftReady || isPending} onClick={() => void repair("/knowledge")}>查看资料或重新选择</button></section> : null}
      {replyOrigin ? <p role="status">已选择一条首页完成回复作为文案来源。这里仍需确认制作方案和选题；页面只保存会话与轮次定位，服务端会重新读取回复正文。</p> : null}
      <p className={ui.draftNotice}>{restored ? (pending === "preview" ? "已恢复本次输入，正在重新检查当前资格。" : preview ? "已恢复本次输入并取得新预检结果。" : "已恢复本次输入，请重新检查当前资格。") : "前往补充资源前会保存当前输入为本成员、本工作区的临时草稿。"}关闭或返回列表不会创建任务；已保存的临时草稿仅保留在本次浏览会话，创建成功后清理。</p>
      <button type="button" disabled={!draftReady || isPending} onClick={() => void abandonDraft()}>放弃本次草稿并返回列表</button>
      {draftError ? <p className={ui.draftNotice} role="alert">{draftError}</p> : null}
      <form onSubmit={handlePreview} aria-busy={pending !== null}>
        <label><span>制作目标</span><textarea autoFocus={autoFocus} disabled={isPending} name="goal" onChange={(event) => { setGoal(event.target.value); invalidatePreview(); }} placeholder="例如：用本周可用素材制作一条介绍 AI 工具观察的竖版视频" required rows={4} value={goal} /></label>
        <div className="creation-plan-form__settings">
          <label><span>任务名称（可选）</span><input disabled={isPending} name="name" onChange={(event) => { setName(event.target.value); invalidatePreview(); }} placeholder="例如：本周 AI 工具观察" value={name} /></label>
          <label><span>成片数量</span><input disabled={isPending} max={Number.MAX_SAFE_INTEGER} min="1" name="target_count" onChange={(event) => { setTargetCount(event.target.value); invalidatePreview(); }} required type="number" value={targetCount} /></label>
          <label><span>语言</span><AppSelect disabled name="language" value={language}><AppSelectOption value="zh-CN">中文（当前版本）</AppSelectOption></AppSelect></label>
          <label><span>目标时长（秒）</span><input aria-label="目标时长（秒）" aria-describedby={template ? `${formId}-duration-default` : undefined} disabled={isPending} min="1" step="1" name="duration_target_seconds" onChange={(event) => { setDuration(event.target.value); invalidatePreview(); }} required type="number" value={duration} />{template ? <small id={`${formId}-duration-default`}>模板默认 {template.settings.duration_target_seconds ?? 60} 秒，可调整本次时长。</small> : null}</label>
        </div>
        <p className="creation-plan-form__ratio">画幅：{aspectRatio}（{template ? "继承所选模板" : "当前本地服务支持的画幅"}）</p>
        {profilesError ? <p role="alert">{profilesError}</p> : null}
        <label>声音固定版本<AppSelect aria-label="任务声音" disabled={isPending} value={voicePin ? `${voicePin.profile_id}:${voicePin.version}` : ""} onValueChange={newValue =>{voiceSelectionEdited.current=true;const selected=voices.find(item=>`${item.id}:${item.version}`===newValue);setVoicePin(selected ? {profile_id:selected.id,version:selected.version}:undefined);invalidatePreview();}}><AppSelectOption value="">使用已保存默认版本</AppSelectOption>{voicePin && !voices.some(v=>v.id===voicePin.profile_id && v.version===voicePin.version) ? <AppSelectOption value={`${voicePin.profile_id}:${voicePin.version}`} title={voicePin.version}>声音固定版本未返回</AppSelectOption> : null}{voices.map(item=><AppSelectOption key={item.id} value={`${item.id}:${item.version}`} title={item.version}>{profileOptionLabel(item)}</AppSelectOption>)}</AppSelect></label>
        <label>人物固定版本<AppSelect aria-label="任务人物" disabled={isPending || presenterShare===0} value={personPin ? `${personPin.profile_id}:${personPin.version}` : ""} onValueChange={newValue =>{const selected=people.find(item=>`${item.id}:${item.version}`===newValue);setPersonPin(selected ? {profile_id:selected.id,version:selected.version}:undefined);invalidatePreview();}}><AppSelectOption value="">选择独立人物</AppSelectOption>{personPin && !people.some(p=>p.id===personPin.profile_id && p.version===personPin.version) ? <AppSelectOption value={`${personPin.profile_id}:${personPin.version}`} title={personPin.version}>人物固定版本未返回</AppSelectOption> : null}{people.map(item=><AppSelectOption key={item.id} value={`${item.id}:${item.version}`} title={item.version}>{profileOptionLabel(item)}</AppSelectOption>)}</AppSelect></label>
        <PresenterShareSlider disabled={isPending} onChange={(value) => { setPresenterShare(value); invalidatePreview(); }} value={presenterShare} />
        <LipSyncQualitySelect disabled={isPending || presenterShare === 0} onChange={(value) => { setLipsyncQuality(value); invalidatePreview(); }} value={lipsyncQuality} />
        {presenterShare === 0 ? <p>露脸占比为 0%，本次无需人物口型，口型档位暂不可调整。</p> : null}
        <div className="creation-plan-form__actions"><button className="modern-primary-button" aria-describedby={`${formId}-preview-scope`} disabled={isPending || !draftReady || !goal.trim()} type="submit">{pending === "preview" ? <LoaderCircle className="is-spinning" size={16} /> : <SearchCheck size={16} />}{pending === "preview" ? "正在检查…" : "检查可用内容"}</button><span id={`${formId}-preview-scope`}>{!goal.trim() ? "先填写制作目标，再检查。" : !draftReady ? "当前成员与工作区尚未确认，暂不能检查。" : preview ? (preview.confirmable ? "预检完成，可以确认创建。" : "预检完成，但仍有创建阻断需要处理。") : "检查不会创建任务，也不会生成选题、文案或成片。"}</span></div>
      </form>

      {creationRequestId ? <CreationRequestPanel key={creationRequestId} requestId={creationRequestId} scopeKey={draftScope.current ? `${draftScope.current.memberId}:${draftScope.current.workspaceId}` : ""} isCurrentScope={() => alive.current && creationRequestKey.current !== null} /> : null}

      {error ? <section className="creation-plan-error" role="alert"><AlertCircle size={17} /><div><strong>操作未完成：{error.message}</strong>{error.impact ? <p>{error.impact}</p> : null}{error.repairAction ? <p>处理方式：{error.repairAction}</p> : null}</div>{error.repairAction ? <button disabled={isPending || !goal.trim()} onClick={() => void runPreview()} type="button">重新检查可用内容</button> : null}</section> : null}

      {preview ? <div className="creation-plan-preview"><header><div><span>预检结果</span><h3>{preview.confirmable ? "本次检查允许创建创作任务" : "先处理创建阻断"}</h3><p>预检画幅：{preview.aspect_ratio}</p><p>预检目标时长：{preview.duration_target_seconds} 秒</p><p>以下事实来自本次只读检查；创建许可不代表全部制作阶段已就绪。</p></div><span className={preview.confirmable ? "creation-plan-preview__state is-ready" : "creation-plan-preview__state is-blocking"}>{preview.confirmable ? "可创建任务" : "创建受阻"}</span></header><PreflightFacts preview={preview} voices={voices} people={people} onRepair={href => void repair(href)} disabled={!draftReady || isPending} /><Blockers preview={preview} onRepair={href => void repair(href)} disabled={!draftReady || isPending} /><div className="creation-plan-confirm"><div><strong>确认后将创建创作任务</strong><p>创建后从“选题：未开始”进入后续流程，不会自动生成选题、文案或成片。</p><p id={`${formId}-confirm-scope`}>{preview.confirmable ? "当前允许创建任务；后续阶段的限制见待处理事项。" : "当前不能创建任务，请先处理标记为阻止创建的事项并重新检查。"}</p></div><button className="modern-primary-button" aria-describedby={`${formId}-confirm-scope`} disabled={!preview.confirmable || !draftReady || isPending} onClick={() => void handleConfirm()} type="button">{pending === "confirm" ? <LoaderCircle className="is-spinning" size={16} /> : <ChevronRight size={16} />}{created ? "任务已创建" : pending === "confirm" ? "正在创建…" : "确认并创建任务"}</button></div></div> : null}
    </div>
  );
}
