"use client";
import { WorkspaceVideo } from "../workbench/WorkspaceMedia";
import { UserRound } from "lucide-react";
import { apiUrl } from "@/lib/api";
import type { CreationTaskWorkspace as WorkspaceState } from "@/lib/types";
import styles from "../CreationTaskWorkspace.module.css";
import { capabilityLabel, lipsyncLabel, narrationSegmentStatus, personRenderStageDetail } from './stage-labels';
import { PersonRenderPanel } from './NarrationStage';
export function PersonStageView({ busy, onGenerate, onOpenNarration, onSelect, selectedSegmentId, narration, renders, selectedPerson, lipsyncQuality, presenterShareTarget, presenterShareActual, capability, portraitAvailable }: {
    busy: boolean;
    onGenerate: (segmentId: string) => void;
    onOpenNarration: () => void;
    onSelect: (segmentId: string) => void;
    selectedSegmentId: string | null;
    narration: WorkspaceState["narration"];
    renders: WorkspaceState["person_renders"];
    selectedPerson: WorkspaceState["plan"]["selected_person"];
    lipsyncQuality: WorkspaceState["plan"]["lipsync_quality"];
    presenterShareTarget: number | null;
    presenterShareActual: number | null | undefined;
    capability: WorkspaceState["person_render_capability"];
    portraitAvailable: boolean | undefined;
}) {
    const selectedRender = renders.find((render) => render.narration_segment_id === selectedSegmentId) ?? null;
    const summary = presenterShareTarget === 0
        ? "本方案的露脸目标为 0%，自动口型阶段已跳过。"
        : renders.some((render) => render.status === "ready")
            ? `已有 ${renders.filter((render) => render.status === "ready").length} 段人物口型预览。`
            : renders.some((render) => render.status === "failed")
                ? "有口型没有生成完成，需要处理后才能进入成片。"
                : renders.length
                    ? "口型正在生成中。"
                    : "还没有生成任何口型；系统只为选中的露脸段生成口型。";
    return (<section className={styles.workSection}>
      <div className={styles.sectionHeading}><div><h2>当前口型</h2><p>{summary}</p></div><span className={styles.artifactStatus}><UserRound size={15}/>{presenterShareTarget === 0 ? "明确跳过" : "按露脸段生成"}</span></div>
      <details className={styles.requirementDetails}><summary>人物与口型要求</summary><div className={styles.artifactGrid}>
        <section><strong>露脸目标</strong><p>{presenterShareTarget === null ? "当前方案没有记录露脸占比。" : `${presenterShareTarget}% · 实际 ${presenterShareActual == null ? "未返回" : `${presenterShareActual}%`}`}</p></section>
        <section><strong>口型档位</strong><p>{lipsyncQuality === "high" ? "高精度 · LatentSync" : lipsyncQuality === "fast" ? "低精度 · MuseTalk" : "方案未选择档位"}</p></section>
        <section><strong>人物素材</strong><p>{portraitAvailable === undefined ? "未返回该事实" : portraitAvailable ? "已就绪" : "没有可用人物素材"}</p></section>
        {presenterShareTarget !== 0 ? <section><strong>当前口型能力</strong><p>{lipsyncLabel(capability.model_id)} · {capabilityLabel(capability.available)}</p></section> : null}
      </div>
      </details>
      {presenterShareTarget !== 0 && !capability.available ? <div className={styles.personCapabilityIssue} role="status"><strong>人物口型暂不可用</strong><p>{capability.message}</p>{capability.repair_action ? <small>处理方式：{capability.repair_action}</small> : null}</div> : null}
      {!narration ? <div className={styles.emptyAction}><UserRound size={30}/><h2>还没有配音事实</h2><p>口型由最终配音驱动。先生成配音，才能为选中的露脸段制作口型。</p><button className="modern-primary-button" disabled={busy} onClick={onOpenNarration} type="button">前往配音</button></div> : null}
      {narration && presenterShareTarget === null ? <div className={styles.emptyAction}><UserRound size={30}/><h2>未记录露脸占比</h2><p>当前方案的露脸占比没有记录，不能按 0% 当作跳过处理。请先在制作方案中确认露脸占比，再回到本阶段生成口型。</p></div> : null}
      {narration && presenterShareTarget === 0 ? <div className={styles.emptyAction}><UserRound size={30}/><p>方案目标不会覆盖当前成片的实际露脸结果。{presenterShareActual == null ? "当前未返回实际占比。" : `当前成片实际露脸 ${presenterShareActual}%。`}</p></div> : null}
      {narration && presenterShareTarget !== null && presenterShareTarget > 0 && !selectedPerson
            ? <div className={styles.emptyAction}><UserRound size={30}/><h2>尚未选择当前人物</h2><p>声音档案中的关联人物不能替代本任务的已选人物。请先在制作方案中选择人物版本，再为配音片段生成口型。</p></div>
            : null}
      {presenterShareTarget === 0 && renders.length ? <details className={styles.requirementDetails}><summary>已保存口型（{renders.length} 段）</summary>{renders.map(render => <article key={render.id}><p>{personRenderStageDetail(render.status)}</p>{render.status === "ready" && render.media_url ? <WorkspaceVideo controls preload="metadata" aria-label="已保存口型预览" className={styles.materialPreview} src={apiUrl(render.media_url)}/> : null}</article>)}</details> : null}
      {narration && presenterShareTarget !== null && presenterShareTarget > 0 && selectedPerson ? <>
        <div className={styles.narrationSegments}>
          {narration.segments.map((segment, index) => (<article className={selectedSegmentId === segment.id ? styles.selectedSegment : ""} key={segment.id}>
              <button aria-label={`选择配音 ${index + 1}`} aria-pressed={selectedSegmentId === segment.id} disabled={busy} onClick={() => onSelect(segment.id)} type="button">
                <span>第 {index + 1} 段</span>
                <strong>{segment.text}</strong>
                <small>{segment.duration_seconds !== null ? `${segment.duration_seconds.toFixed(1)} 秒 · ` : ""}{narrationSegmentStatus(segment.status)}{renders.some((render) => render.narration_segment_id === segment.id) ? ` · ${personRenderStageDetail(renders.find((render) => render.narration_segment_id === segment.id)!.status)}` : " · 未生成口型"}</small>
              </button>
            </article>))}
        </div>
        <PersonRenderPanel busy={busy} onGenerate={onGenerate} selectedRender={selectedRender} selectedSegmentId={selectedSegmentId} hasPerson={Boolean(selectedPerson)}/>
      </> : null}
    </section>);
}
