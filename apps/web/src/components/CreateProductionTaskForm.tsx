"use client";

import { AlertCircle, CheckCircle2, ChevronRight, LoaderCircle, SearchCheck, ShieldCheck } from "lucide-react";
import { type FormEvent, useEffect, useId, useRef, useState } from "react";
import {
  confirmCreationPlan,
  CreationPlanApiError,
  previewCreationPlan,
} from "@/lib/api";
import type { CreationPlanInput, CreationPlanPreview, VideoTemplate } from "@/lib/types";
import { PresenterShareSlider, type PresenterShare } from "@/components/PresenterShareSlider";
import { LipSyncQualitySelect, type LipSyncQuality } from "@/components/LipSyncQualitySelect";
import ui from "./CreateProductionTaskForm.module.css";

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

function materialSummary(value: string) {
  return value.replace(/\bb-?roll\b/gi, "补充画面");
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

function stateLabel(ready: boolean) {
  return ready ? "已就绪" : "不可用";
}

export function PreflightFacts({ preview }: { preview: CreationPlanPreview }) {
  const matches = preview.material_matches as PreflightMatch[];
  return (
    <div className="creation-plan-facts" aria-label="可用内容检查结果">
      <section>
        <header><SearchCheck size={16} /><div><span>资料与信息源</span><strong>{preview.knowledge_references.length + preview.source_references.length} 条已纳入检查</strong></div></header>
        {preview.knowledge_references.length ? <ul>{preview.knowledge_references.map((item) => <li key={`${item.id}-${item.version}`}><strong>{item.title}</strong><span>{item.authorization} · {item.ownership}</span></li>)}</ul> : null}
        {preview.source_references.length ? <ul>{preview.source_references.map((item) => <li key={item.id}><strong>{item.title}</strong><span>{item.source_label} · {item.license}</span></li>)}</ul> : null}
        {!preview.knowledge_references.length && !preview.source_references.length ? <p>本次检查没有返回资料或信息源。</p> : null}
      </section>
      <section>
        <header><ShieldCheck size={16} /><div><span>素材候选</span><strong>{matches.length} 个检索候选</strong></div></header>
        <p>候选按主题、词语或结构条件检索，需在选片时核对内容与使用范围。</p>
        {matches.length ? <ul>{matches.slice(0, 4).map((item) => <li key={`${item.asset_ref}-${item.segment_ref}`}><strong>{matchTierLabel(item.match_tier)}</strong><span>{item.reasons.join("；")}</span><details className={ui.diagnostics}><summary>候选诊断</summary><p>引用：{item.asset_ref} · {item.segment_ref}</p><p>层级：{item.match_tier ?? "未返回"} · 启发分值：{item.score}（用于排序，不是适配概率）</p>{item.asset_file_name ? <p>文件：{item.asset_file_name}</p> : null}</details></li>)}</ul> : <p>当前目标没有返回检索候选。<a href="/library">前往素材库补充内容</a></p>}
        {matches.length > 4 ? <p>当前展示前 4 个，检查共返回 {matches.length} 个候选。</p> : null}
        <details className={ui.diagnostics}><summary>素材计数与筛选口径</summary><p>本 API 旧审核口径：{preview.approved_asset_count} 个已审核素材 · {preview.approved_clip_count} 个已审核片段；不代表当前可用资格计数。</p><p>{materialSummary(preview.material_rule_summary)}</p></details>
        {preview.selected_voice ? <><p>本次检查已选择声音，适用语言：{preview.selected_voice.language}。配音及人物阶段是否可进入，见待处理事项。</p><details className={ui.diagnostics}><summary>声音引用诊断</summary><p>声音：{preview.selected_voice.profile_id} · {preview.selected_voice.version}</p><p>人物：{preview.selected_voice.person_profile_id} · {preview.selected_voice.person_version}</p><p>模型：{preview.selected_voice.model_id}</p></details></> : <p>本次检查未选定声音，配音阶段的影响见待处理事项。</p>}
        {preview.pronunciation_references.length ? <ul>{preview.pronunciation_references.map((item) => <li key={`${item.id}-${item.version}`}><strong>发音词典</strong><span>{scopeLabel(item.scope)} · {item.language}</span></li>)}</ul> : null}
      </section>
      <section>
        <header><CheckCircle2 size={16} /><div><span>模型与人工确认</span><strong>{preview.model_snapshot.length} 项能力已检查</strong></div></header>
        {preview.model_snapshot.length ? <ul>{preview.model_snapshot.map((item) => <li key={item.id}><strong>{capabilityLabel(item.capability)} · {stateLabel(item.ready)}</strong><span>{item.message}</span><details className={ui.diagnostics}><summary>模型诊断</summary><p>{item.id} · {item.capability} · {item.state}</p></details></li>)}</ul> : <p>本地服务没有返回模型状态。</p>}
        {preview.confirmation_points.length ? <ul>{preview.confirmation_points.map((item) => <li key={item}><strong>需要你确认</strong><span>{item}</span></li>)}</ul> : null}
      </section>
    </div>
  );
}

export function Blockers({ preview }: { preview: CreationPlanPreview }) {
  if (!preview.blockers.length) return null;
  return <section className="creation-plan-blockers" aria-label="待处理事项"><header><AlertCircle size={17} /><div><span>需要处理</span><h3>{preview.blockers.length} 项待处理</h3></div></header><div>{preview.blockers.map((item, index) => <article key={`${item.reason}-${index}`}><strong>{item.impact}</strong><p>受影响阶段：{stageLabel(item.stage)}</p><p>怎么处理：{item.repair_action}</p><span className={item.blocks_confirmation ? "is-blocking" : ""}>{item.blocks_confirmation ? "阻止创建任务" : `不阻止创建；进入${stageLabel(item.stage)}前需处理`}</span><details className={ui.diagnostics}><summary>阻断诊断</summary><p>{item.reason} · {item.stage}</p></details></article>)}</div></section>;
}

export function CreateProductionTaskForm({
  template = null,
  autoFocus = false,
  initialGoal = "",
  assignLocation = assignDocumentLocation,
}: CreateProductionTaskFormProps) {
  const [goal, setGoal] = useState(initialGoal);
  const [name, setName] = useState("");
  const [targetCount, setTargetCount] = useState("1");
  const language: CreationPlanInput["language"] = template?.settings.language ?? "zh-CN";
  const aspectRatio: CreationPlanInput["aspect_ratio"] = template?.settings.aspect_ratio ?? "9:16";
  const [duration, setDuration] = useState(String(template?.settings.duration_target_seconds ?? 60));
  const [presenterShare, setPresenterShare] = useState<PresenterShare>(template?.settings.presenter_share_target ?? 20);
  const [lipsyncQuality, setLipsyncQuality] = useState<LipSyncQuality>(template?.settings.lipsync_quality ?? (template ? "fast" : "high"));
  const [preview, setPreview] = useState<CreationPlanPreview | null>(null);
  const [pending, setPending] = useState<"preview" | "confirm" | null>(null);
  const [error, setError] = useState<FormError | null>(null);
  const submittingRef = useRef(false);
  const inputRevision = useRef(0);
  const formId = useId();

  useEffect(() => {
    ++inputRevision.current;
    setPreview(null);
    setError(null);
    setDuration(String(template?.settings.duration_target_seconds ?? 60));
  }, [template?.id, template?.version]);

  function input(): CreationPlanInput {
    return {
      ...(template ? { template_id: template.id, template_version: template.version } : {}),
      goal: goal.trim(),
      ...(name.trim() ? { name: name.trim() } : {}),
      target_count: Number(targetCount),
      language,
      aspect_ratio: aspectRatio,
      duration_target_seconds: Number(duration),
      presenter_share_target: presenterShare,
      lipsync_quality: lipsyncQuality,
    };
  }

  function invalidatePreview() {
    ++inputRevision.current;
    setPreview(null);
    setError(null);
  }

  async function runPreview() {
    if (submittingRef.current) return;
    submittingRef.current = true;
    setPending("preview");
    setError(null);
    const revision = inputRevision.current;
    try {
      const result = await previewCreationPlan(input());
      if (revision === inputRevision.current) setPreview(result);
    } catch (cause) {
      if (revision === inputRevision.current) setError(messageForError(cause));
    } finally {
      submittingRef.current = false;
      setPending(null);
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
    try {
      const plan = await confirmCreationPlan({ ...input(), preflight_fingerprint: preview.preflight_fingerprint });
      assignLocation(`/tasks/${encodeURIComponent(plan.creation_task.id)}?${new URLSearchParams({ toast: "success", toast_message: "创作任务已创建，选题尚未开始。" }).toString()}`);
    } catch (cause) {
      if (cause instanceof CreationPlanApiError && cause.code === "creation_plan_preflight_stale") {
        setPreview(null);
      }
      setError(messageForError(cause));
      submittingRef.current = false;
      setPending(null);
    }
  }

  const isPending = pending !== null;
  return (
    <div className={`creation-plan-form ${ui.form}`}>
      {template ? <section aria-label="所选模板"><strong>{template.name} · v{template.version}</strong><p>{template.settings.script_section_count ? `${template.settings.script_section_count} 段文案 · ` : ""}{template.settings.fit_mode === "fit" ? "完整显示画面" : "填满画面"} · 复用字幕样式</p><p>{template.settings.style_instruction || "未指定额外文案风格"}</p><a href="/templates">重新选择模板</a></section> : null}
      <form onSubmit={handlePreview} aria-busy={isPending}>
        <label><span>制作目标</span><textarea autoFocus={autoFocus} disabled={isPending} name="goal" onChange={(event) => { setGoal(event.target.value); invalidatePreview(); }} placeholder="例如：用本周已审核素材制作一条介绍 AI 工具观察的竖版视频" required rows={4} value={goal} /></label>
        <div className="creation-plan-form__settings">
          <label><span>任务名称（可选）</span><input disabled={isPending} name="name" onChange={(event) => { setName(event.target.value); invalidatePreview(); }} placeholder="例如：本周 AI 工具观察" value={name} /></label>
          <label><span>成片数量</span><input disabled={isPending} max="50" min="1" name="target_count" onChange={(event) => { setTargetCount(event.target.value); invalidatePreview(); }} required type="number" value={targetCount} /></label>
          <label><span>语言</span><select disabled name="language" value={language}><option value="zh-CN">中文（当前版本）</option></select></label>
          <label><span>目标时长（秒）</span><input aria-label="目标时长（秒）" aria-describedby={template ? `${formId}-duration-default` : undefined} disabled={isPending} max="600" min="15" name="duration_target_seconds" onChange={(event) => { setDuration(event.target.value); invalidatePreview(); }} required type="number" value={duration} />{template ? <small id={`${formId}-duration-default`}>模板默认 {template.settings.duration_target_seconds ?? 60} 秒，可调整本次时长。</small> : null}</label>
        </div>
        <p className="creation-plan-form__ratio">画幅：{aspectRatio}（{template ? "继承所选模板" : "当前本地服务支持的画幅"}）</p>
        <PresenterShareSlider disabled={isPending} onChange={(value) => { setPresenterShare(value); invalidatePreview(); }} value={presenterShare} />
        <LipSyncQualitySelect disabled={isPending || presenterShare === 0} onChange={(value) => { setLipsyncQuality(value); invalidatePreview(); }} value={lipsyncQuality} />
        {presenterShare === 0 ? <p>露脸占比为 0%，本次无需人物口型，口型档位暂不可调整。</p> : null}
        <div className="creation-plan-form__actions"><button className="modern-primary-button" aria-describedby={`${formId}-preview-scope`} disabled={isPending || !goal.trim()} type="submit">{pending === "preview" ? <LoaderCircle className="is-spinning" size={16} /> : <SearchCheck size={16} />}{pending === "preview" ? "正在检查…" : "检查可用内容"}</button><span id={`${formId}-preview-scope`}>{!goal.trim() ? "先填写制作目标，再检查。" : preview ? (preview.confirmable ? "预检完成，可以确认创建。" : "预检完成，但仍有创建阻断需要处理。") : "检查不会创建任务，也不会生成选题、文案或成片。"}</span></div>
      </form>

      {error ? <section className="creation-plan-error" role="alert"><AlertCircle size={17} /><div><strong>操作未完成：{error.message}</strong>{error.impact ? <p>{error.impact}</p> : null}{error.repairAction ? <p>处理方式：{error.repairAction}</p> : null}</div>{error.repairAction ? <button disabled={isPending || !goal.trim()} onClick={() => void runPreview()} type="button">重新检查可用内容</button> : null}</section> : null}

      {preview ? <div className="creation-plan-preview"><header><div><span>预检结果</span><h3>{preview.confirmable ? "本次检查允许创建创作任务" : "先处理创建阻断"}</h3><p>预检画幅：{preview.aspect_ratio}</p><p>预检目标时长：{preview.duration_target_seconds} 秒</p><p>以下事实来自本次只读检查；创建许可不代表全部制作阶段已就绪。</p></div><span className={preview.confirmable ? "creation-plan-preview__state is-ready" : "creation-plan-preview__state is-blocking"}>{preview.confirmable ? "可创建任务" : "创建受阻"}</span></header><PreflightFacts preview={preview} /><Blockers preview={preview} /><div className="creation-plan-confirm"><div><strong>确认后将创建创作任务</strong><p>创建后从“选题：未开始”进入后续流程，不会自动生成选题、文案或成片。</p><p id={`${formId}-confirm-scope`}>{preview.confirmable ? "当前允许创建任务；后续阶段的限制见待处理事项。" : "当前不能创建任务，请先处理标记为阻止创建的事项并重新检查。"}</p></div><button className="modern-primary-button" aria-describedby={`${formId}-confirm-scope`} disabled={!preview.confirmable || isPending} onClick={() => void handleConfirm()} type="button">{pending === "confirm" ? <LoaderCircle className="is-spinning" size={16} /> : <ChevronRight size={16} />}{pending === "confirm" ? "正在创建…" : "确认并创建任务"}</button></div></div> : null}
    </div>
  );
}
