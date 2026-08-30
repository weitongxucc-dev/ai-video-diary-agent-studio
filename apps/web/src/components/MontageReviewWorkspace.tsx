"use client";

import Link from "next/link";
import {
  AlertCircle,
  ArrowLeft,
  ArrowRight,
  Check,
  ChevronDown,
  ChevronUp,
  Download,
  Film,
  Lock,
  Play,
  RefreshCw,
  Scissors,
  Sparkles,
  Unlock,
} from "lucide-react";
import { useMemo, useState } from "react";
import { AppShell } from "@/components/AppShell";
import {
  confirmMontageClips,
  getMontageClipCandidates,
  renderMontagePlan,
  reorderMontageClips,
  updateMontageClip,
} from "@/lib/api";
import type { MontageClipCandidate, MontagePlan } from "@/lib/types";

function time(value: number) {
  const minutes = Math.floor(value / 60);
  const seconds = (value % 60).toFixed(1).padStart(4, "0");
  return `${minutes}:${seconds}`;
}

function narrationLabel(status: MontagePlan["narration_status"]) {
  return status === "ready" ? "旁白已就绪" : "旁白不可用";
}

function subtitleLabel(status: MontagePlan["subtitle_status"]) {
  if (status === "ready") return "字幕已生成";
  if (status === "unavailable") return "字幕不可用";
  return "字幕将在生成成片后提供";
}

function planStatusLabel(status: MontagePlan["status"], pendingCount: number) {
  if (status === "waiting_for_user") return `${pendingCount} 个镜头待确认`;
  if (status === "ready") return "可以生成成片";
  if (status === "rendering") return "正在生成成片";
  if (status === "rendered") return "成片已生成";
  return "生成失败";
}

function displayPlanText(value?: string | null) {
  return (value || "")
    .replace(/\bb-?roll\b/gi, "补充画面")
    .replace(/\bbatch\b/gi, "创作任务")
    .replace(/批次/g, "创作任务")
    .replace(/\bepisode\b/gi, "内容")
    .replace(/\bagent\b/gi, "智能助手")
    .replace(/\bprovider\b/gi, "服务")
    .replace(/混剪/g, "视频剪辑")
    .replace(/\bSRT\s+字幕/gi, "字幕");
}

export function MontageReviewWorkspace({ initialPlan }: { initialPlan: MontagePlan }) {
  const [plan, setPlan] = useState(initialPlan);
  const [selectedId, setSelectedId] = useState(initialPlan.clips[0]?.id ?? "");
  const [candidates, setCandidates] = useState<MontageClipCandidate[]>([]);
  const [trimStart, setTrimStart] = useState(initialPlan.clips[0]?.source_start_seconds ?? 0);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");

  const selected = plan.clips.find((clip) => clip.id === selectedId) ?? plan.clips[0];
  const cue = useMemo(() => plan.cues.find((item) => item.id === selected?.cue_id), [plan.cues, selected]);
  const selectedIndex = plan.clips.findIndex((clip) => clip.id === selected?.id);

  function selectClip(id: string) {
    const clip = plan.clips.find((item) => item.id === id);
    setSelectedId(id);
    setTrimStart(clip?.source_start_seconds ?? 0);
    setCandidates([]);
    setError("");
  }

  async function mutate(label: string, action: () => Promise<MontagePlan>) {
    setBusy(label);
    setError("");
    try {
      const updated = await action();
      setPlan(updated);
      const clip = updated.clips.find((item) => item.id === selectedId);
      if (clip) setTrimStart(clip.source_start_seconds);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "操作失败，请稍后重试。");
    } finally {
      setBusy("");
    }
  }

  async function loadCandidates() {
    if (!selected) return;
    setBusy("candidates");
    setError("");
    try { setCandidates(await getMontageClipCandidates(plan.id, selected.id)); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "候选镜头加载失败"); }
    finally { setBusy(""); }
  }

  function move(direction: -1 | 1) {
    if (!selected || selected.locked) return;
    const target = selectedIndex + direction;
    if (target < 0 || target >= plan.clips.length || plan.clips[target].locked) return;
    const ids = plan.clips.map((clip) => clip.id);
    [ids[selectedIndex], ids[target]] = [ids[target], ids[selectedIndex]];
    void mutate("order", () => reorderMontageClips(plan.id, ids));
  }

  const inspector = selected ? (
    <div className="montage-inspector">
      <div className="montage-inspector__preview">
        <video controls key={`${selected.id}-${selected.source_start_seconds}`} preload="metadata" src={`/api/production/materials/${selected.asset_id}/media#t=${selected.source_start_seconds},${selected.source_end_seconds}`} />
      </div>
      <div className="montage-inspector__cue"><span>旁白时间槽 · {time(selected.timeline_start_seconds)}–{time(selected.timeline_end_seconds)}</span><p>{cue?.text ?? "未找到旁白段落"}</p></div>
      <div className="montage-confidence"><Sparkles size={14} /><div><strong>{selected.manually_selected ? "人工选择" : `匹配度 ${Math.round(selected.match_score * 100)}%`}</strong><p>{selected.match_reasons.join("；")}</p></div></div>

      <dl className="montage-inspector__facts">
        <div><dt>画幅</dt><dd>{plan.aspect_ratio} 竖屏</dd></div>
        <div><dt>旁白</dt><dd>{narrationLabel(plan.narration_status)}</dd></div>
        <div><dt>字幕</dt><dd>{subtitleLabel(plan.subtitle_status)}</dd></div>
      </dl>

      <div className="montage-trim">
        <label><span>来源起点</span><input min="0" onChange={(event) => setTrimStart(Number(event.target.value))} step="0.1" type="number" value={trimStart} /></label>
        <span>保持 {time(selected.timeline_end_seconds - selected.timeline_start_seconds)} 的旁白时间槽</span>
        <button disabled={Boolean(busy) || trimStart === selected.source_start_seconds} onClick={() => void mutate("trim", () => updateMontageClip(plan.id, selected.id, { source_start_seconds: trimStart }))} type="button"><Scissors size={14} />保存滑移裁剪</button>
      </div>

      <div className="montage-inspector__actions">
        <button disabled={Boolean(busy)} onClick={() => void mutate("lock", () => updateMontageClip(plan.id, selected.id, { locked: !selected.locked }))} type="button">{selected.locked ? <Unlock size={14} /> : <Lock size={14} />}{selected.locked ? "解除锁定" : "锁定镜头"}</button>
        {selected.needs_confirmation ? <button className="is-primary" disabled={Boolean(busy)} onClick={() => void mutate("confirm", () => updateMontageClip(plan.id, selected.id, { confirmed: true }))} type="button"><Check size={14} />确认此镜头</button> : null}
        <button disabled={Boolean(busy)} onClick={() => void loadCandidates()} type="button"><RefreshCw size={14} />{busy === "candidates" ? "加载中…" : "替换镜头"}</button>
      </div>

      {candidates.length ? <div className="montage-candidates"><h3>候选镜头</h3>{candidates.map((candidate) => <button key={`${candidate.asset_id}-${candidate.segment_id}`} onClick={() => void mutate("replace", () => updateMontageClip(plan.id, selected.id, { asset_id: candidate.asset_id, segment_id: candidate.segment_id, source_start_seconds: candidate.start_seconds }))} type="button"><Film size={14} /><span><strong>{candidate.file_name}</strong><small>{candidate.description} · {Math.round(candidate.score * 100)}%</small></span><ArrowRight size={13} /></button>)}</div> : null}
    </div>
  ) : null;

  const pendingIds = plan.clips.filter((clip) => clip.needs_confirmation).map((clip) => clip.id);

  return (
    <AppShell inspector={inspector} inspectorTitle="镜头审核">
      <div className="modern-montage">
        <header className="modern-montage__header">
          <div className="modern-montage__breadcrumb"><Link href={`/production?batch=${plan.batch_id}`}><ArrowLeft size={14} />返回创作任务</Link><span>视频剪辑</span></div>
          <div className="modern-montage__title"><div><h1>{displayPlanText(plan.script_title)}</h1><p>{time(plan.duration_seconds)} · {plan.clips.length} 个镜头 · {displayPlanText(plan.message)}</p></div><span className={`montage-status montage-status--${plan.status}`}>{planStatusLabel(plan.status, plan.low_confidence_clip_count)}</span></div>
        </header>

        {error ? <div className="montage-error" role="alert"><AlertCircle size={15} />{error}</div> : null}

        <section className="montage-player-stage">
          {selected ? <video controls key={`main-${selected.id}-${selected.source_start_seconds}`} preload="metadata" src={`/api/production/materials/${selected.asset_id}/media#t=${selected.source_start_seconds},${selected.source_end_seconds}`} /> : <div><Film size={28} />没有可预览镜头</div>}
          <div className="montage-player-stage__caption"><span>{cue?.text}</span><small>{selected ? `${time(selected.timeline_start_seconds)} / ${time(plan.duration_seconds)}` : ""}</small></div>
        </section>

        {plan.output_url ? (
          <section className="montage-output" aria-label="生成的成片">
            <div><span>成片</span><h2>已生成 {plan.aspect_ratio} 视频</h2><p>{subtitleLabel(plan.subtitle_status)}</p></div>
            <video controls preload="metadata" src={plan.output_url} />
            <div className="montage-output__actions">
              <a href={plan.output_url}><Download size={14} />打开成片</a>
              {plan.subtitle_url ? <a href={plan.subtitle_url}><Download size={14} />打开字幕</a> : null}
            </div>
          </section>
        ) : null}

        <section className="montage-review-bar">
          <div><strong>审核型时间线</strong><span>点击镜头查看依据、替换或滑移裁剪</span></div>
          <div className="montage-review-bar__actions">
            <button aria-label="向前移动镜头" disabled={selectedIndex <= 0 || selected?.locked || Boolean(busy)} onClick={() => move(-1)} type="button"><ChevronUp size={15} /></button>
            <button aria-label="向后移动镜头" disabled={selectedIndex < 0 || selectedIndex >= plan.clips.length - 1 || selected?.locked || Boolean(busy)} onClick={() => move(1)} type="button"><ChevronDown size={15} /></button>
            {pendingIds.length ? <button className="is-primary" disabled={Boolean(busy)} onClick={() => void mutate("confirm-all", () => confirmMontageClips(plan.id, pendingIds))} type="button"><Check size={14} />确认全部低置信镜头</button> : plan.status === "ready" && plan.narration_status === "ready" ? <button className="is-primary" disabled={Boolean(busy)} onClick={() => void mutate("render", () => renderMontagePlan(plan.id))} type="button"><Play size={14} />生成成片</button> : null}
          </div>
        </section>

        {plan.status === "ready" && plan.narration_status === "unavailable" ? <div className="montage-runtime-notice" role="status"><AlertCircle size={15} /><span>旁白不可用，暂不能生成成片。请回到创作任务检查已选声音与授权状态。</span><Link href={`/production?batch=${plan.batch_id}`}>返回创作任务</Link></div> : null}

        <ol className="montage-timeline">
          {plan.clips.map((clip, index) => {
            const clipCue = plan.cues.find((item) => item.id === clip.cue_id);
            return <li key={clip.id}><button className={`${selected?.id === clip.id ? "is-selected" : ""}${clip.needs_confirmation ? " needs-review" : ""}`} onClick={() => selectClip(clip.id)} type="button"><span className="montage-clip__number">{String(index + 1).padStart(2, "0")}</span><span className="montage-clip__poster"><Film size={18} />{clip.locked ? <Lock size={12} /> : null}</span><span className="montage-clip__copy"><strong>{clipCue?.text ?? "未命名镜头"}</strong><small>{time(clip.timeline_start_seconds)}–{time(clip.timeline_end_seconds)} · {clip.role === "b_roll" ? "补充画面" : "人物主镜头"}</small></span><i className={clip.needs_confirmation ? "is-warning" : "is-approved"}>{clip.needs_confirmation ? "待确认" : "已确认"}</i></button></li>;
          })}
        </ol>
      </div>
    </AppShell>
  );
}
