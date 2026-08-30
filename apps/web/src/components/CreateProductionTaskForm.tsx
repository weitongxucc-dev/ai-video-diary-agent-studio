"use client";

import { AlertCircle, CheckCircle2, ChevronRight, LoaderCircle, SearchCheck, ShieldCheck } from "lucide-react";
import { type FormEvent, useRef, useState } from "react";
import {
  confirmCreationPlan,
  CreationPlanApiError,
  previewCreationPlan,
} from "@/lib/api";
import type { CreationPlanInput, CreationPlanPreview } from "@/lib/types";

type CreateProductionTaskFormProps = {
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

function PreflightFacts({ preview }: { preview: CreationPlanPreview }) {
  return (
    <div className="creation-plan-facts" aria-label="可用内容检查结果">
      <section>
        <header><SearchCheck size={16} /><div><span>资料与信息源</span><strong>{preview.knowledge_references.length + preview.source_references.length} 条已纳入检查</strong></div></header>
        {preview.knowledge_references.length ? <ul>{preview.knowledge_references.map((item) => <li key={`${item.id}-${item.version}`}><strong>{item.title}</strong><span>{item.authorization} · {item.ownership}</span></li>)}</ul> : null}
        {preview.source_references.length ? <ul>{preview.source_references.map((item) => <li key={item.id}><strong>{item.title}</strong><span>{item.source_label} · {item.license}</span></li>)}</ul> : null}
        {!preview.knowledge_references.length && !preview.source_references.length ? <p>当前没有可用于选题的已保存资料或信息源。</p> : null}
      </section>
      <section>
        <header><ShieldCheck size={16} /><div><span>素材与授权</span><strong>{preview.approved_asset_count} 个已审核素材 · {preview.approved_clip_count} 个可用片段</strong></div></header>
        <p>{materialSummary(preview.material_rule_summary)}</p>
        {preview.selected_voice ? <p>已核验声音及人物授权，适用语言：{preview.selected_voice.language}。</p> : <p>当前没有可用于本次目标的已授权声音。</p>}
        {preview.pronunciation_references.length ? <ul>{preview.pronunciation_references.map((item) => <li key={`${item.id}-${item.version}`}><strong>发音词典</strong><span>{scopeLabel(item.scope)} · {item.language}</span></li>)}</ul> : null}
      </section>
      <section>
        <header><CheckCircle2 size={16} /><div><span>模型与人工确认</span><strong>{preview.model_snapshot.length} 项能力已检查</strong></div></header>
        {preview.model_snapshot.length ? <ul>{preview.model_snapshot.map((item) => <li key={item.id}><strong>{capabilityLabel(item.capability)} · {stateLabel(item.ready)}</strong><span>{item.message}</span></li>)}</ul> : <p>本地服务没有返回模型状态。</p>}
        {preview.confirmation_points.length ? <ul>{preview.confirmation_points.map((item) => <li key={item}><strong>需要你确认</strong><span>{item}</span></li>)}</ul> : null}
      </section>
    </div>
  );
}

function Blockers({ preview }: { preview: CreationPlanPreview }) {
  if (!preview.blockers.length) return null;
  return <section className="creation-plan-blockers" aria-label="当前阻断"><header><AlertCircle size={17} /><div><span>需要处理</span><h3>当前预检发现 {preview.blockers.length} 项影响</h3></div></header><div>{preview.blockers.map((item, index) => <article key={`${item.reason}-${index}`}><strong>{item.impact}</strong><p>处理方式：{item.repair_action}</p><span className={item.blocks_confirmation ? "is-blocking" : ""}>{item.blocks_confirmation ? "阻止创建" : "不阻止创建"}</span></article>)}</div></section>;
}

export function CreateProductionTaskForm({
  autoFocus = false,
  initialGoal = "",
  assignLocation = assignDocumentLocation,
}: CreateProductionTaskFormProps) {
  const [goal, setGoal] = useState(initialGoal);
  const [name, setName] = useState("");
  const [targetCount, setTargetCount] = useState("1");
  const [language, setLanguage] = useState<CreationPlanInput["language"]>("zh-CN");
  const [duration, setDuration] = useState("60");
  const [preview, setPreview] = useState<CreationPlanPreview | null>(null);
  const [pending, setPending] = useState<"preview" | "confirm" | null>(null);
  const [error, setError] = useState<FormError | null>(null);
  const submittingRef = useRef(false);

  function input(): CreationPlanInput {
    return {
      goal: goal.trim(),
      ...(name.trim() ? { name: name.trim() } : {}),
      target_count: Number(targetCount),
      language,
      aspect_ratio: "9:16",
      duration_target_seconds: Number(duration),
    };
  }

  function invalidatePreview() {
    setPreview(null);
    setError(null);
  }

  async function runPreview() {
    if (submittingRef.current) return;
    submittingRef.current = true;
    setPending("preview");
    setError(null);
    try {
      setPreview(await previewCreationPlan(input()));
    } catch (cause) {
      setError(messageForError(cause));
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
      assignLocation(`/production?${new URLSearchParams({ plan: plan.id, toast: "success", toast_message: "创作任务已创建，选题尚未开始。" }).toString()}`);
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
    <div className="creation-plan-form">
      <form onSubmit={handlePreview} aria-busy={isPending}>
        <label><span>制作目标</span><textarea autoFocus={autoFocus} disabled={isPending} name="goal" onChange={(event) => { setGoal(event.target.value); invalidatePreview(); }} placeholder="例如：用本周已审核素材制作一条介绍 AI 工具观察的竖版视频" required rows={4} value={goal} /></label>
        <div className="creation-plan-form__settings">
          <label><span>任务名称（可选）</span><input disabled={isPending} name="name" onChange={(event) => { setName(event.target.value); invalidatePreview(); }} placeholder="例如：本周 AI 工具观察" value={name} /></label>
          <label><span>成片数量</span><input disabled={isPending} max="50" min="1" name="target_count" onChange={(event) => { setTargetCount(event.target.value); invalidatePreview(); }} required type="number" value={targetCount} /></label>
          <label><span>语言</span><select disabled={isPending} name="language" onChange={(event) => { setLanguage(event.target.value as CreationPlanInput["language"]); invalidatePreview(); }} value={language}><option value="zh-CN">中文</option><option value="en-US">英文</option><option value="zh-CN/en-US">中英文</option></select></label>
          <label><span>目标时长（秒）</span><input disabled={isPending} max="600" min="15" name="duration_target_seconds" onChange={(event) => { setDuration(event.target.value); invalidatePreview(); }} required type="number" value={duration} /></label>
        </div>
        <p className="creation-plan-form__ratio">画幅：9:16（当前本地服务支持的画幅）</p>
        <div className="creation-plan-form__actions"><button className="modern-primary-button" disabled={isPending || !goal.trim()} type="submit">{pending === "preview" ? <LoaderCircle className="is-spinning" size={16} /> : <SearchCheck size={16} />}{pending === "preview" ? "正在检查…" : "检查可用内容"}</button>{preview ? <span className={preview.confirmable ? "is-ready" : "is-blocking"}>{preview.confirmable ? "预检完成，可以确认创建。" : "预检完成，但仍有阻断需要处理。"}</span> : <span>检查不会创建任务，也不会生成选题、文案或成片。</span>}</div>
      </form>

      {error ? <section className="creation-plan-error" role="alert"><AlertCircle size={17} /><div><strong>操作未完成：{error.message}</strong>{error.impact ? <p>{error.impact}</p> : null}{error.repairAction ? <p>处理方式：{error.repairAction}</p> : null}</div>{error.repairAction ? <button disabled={isPending || !goal.trim()} onClick={() => void runPreview()} type="button">重新检查可用内容</button> : null}</section> : null}

      {preview ? <div className="creation-plan-preview"><header><div><span>预检结果</span><h3>{preview.confirmable ? "内容可用于创建创作任务" : "先处理当前影响，再创建创作任务"}</h3><p>以下事实来自本次只读检查；确认后会按当前结果持久化。</p></div><span className={preview.confirmable ? "creation-plan-preview__state is-ready" : "creation-plan-preview__state is-blocking"}>{preview.confirmable ? "已就绪" : "需要处理"}</span></header><PreflightFacts preview={preview} /><Blockers preview={preview} /><div className="creation-plan-confirm"><div><strong>确认后将创建创作任务</strong><p>创建后从“选题：未开始”进入后续流程，不会自动生成选题、文案或成片。</p></div><button className="modern-primary-button" disabled={!preview.confirmable || isPending} onClick={() => void handleConfirm()} type="button">{pending === "confirm" ? <LoaderCircle className="is-spinning" size={16} /> : <ChevronRight size={16} />}{pending === "confirm" ? "正在创建…" : "确认并创建任务"}</button></div></div> : null}
    </div>
  );
}
