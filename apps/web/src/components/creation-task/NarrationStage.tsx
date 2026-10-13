"use client";
import { LoaderCircle, UserRound } from "lucide-react";
import { apiUrl } from "@/lib/api";
import type { CreationTaskPersonRender, CreationTaskWorkspace as WorkspaceState } from "@/lib/types";
import styles from "../CreationTaskWorkspace.module.css";
import { narrationSegmentStatus } from './stage-labels';
export function PersonRenderPanel({ busy, onGenerate, selectedRender, selectedSegmentId, hasPerson }: {
    busy: boolean;
    onGenerate: (segmentId: string) => void;
    selectedRender: CreationTaskPersonRender | null;
    selectedSegmentId: string | null;
    hasPerson: boolean;
}) {
    return (<div className={styles.personRenderPanel}>
      <div>
        <UserRound size={18}/>
        <div><strong>人物口型预览</strong><p>选择一段配音，用当前人物版本检查口型效果；确认编辑后才会进入成片时间线。</p></div>
      </div>
      {!hasPerson ? <span role="status">当前方案没有选择人物版本；请先在制作方案中选择人物。</span> : null}
      {!selectedSegmentId ? <span>先选择上方一段配音</span> : null}
      {selectedSegmentId && !selectedRender && hasPerson ? <button className="modern-primary-button" disabled={busy} onClick={() => onGenerate(selectedSegmentId)} type="button">生成人物口型预览</button> : null}
      {selectedRender?.status === "running" || selectedRender?.status === "pending" ? <span><LoaderCircle className="is-spinning" size={15}/>正在生成人物口型预览</span> : null}
      {selectedRender?.status === "failed" ? <div className={styles.personRenderFailure}><strong>生成没有完成</strong><p>{selectedRender.impact ?? selectedRender.failure_reason}</p>{selectedRender.repair_action ? <small>{selectedRender.repair_action}</small> : null}<button className="modern-primary-button" disabled={busy} onClick={() => onGenerate(selectedSegmentId!)} type="button">重新生成</button></div> : null}
      {selectedRender?.status === "ready" && selectedRender.media_url ? <div className={styles.personRenderPreview}><video controls preload="metadata" src={apiUrl(selectedRender.media_url)}/><p>{selectedRender.model_id} · {selectedRender.duration_seconds?.toFixed(1)} 秒 · 人物 {selectedRender.person_version}</p></div> : null}
    </div>);
}
export function NarrationResultView({ busy, onGeneratePersonRender, onSelect, selectedSegmentId, hasPerson, taskId, narration, personRenders }: {
    busy: boolean;
    onGeneratePersonRender: (segmentId: string) => void;
    taskId: string;
    narration: WorkspaceState["narration"];
    personRenders: WorkspaceState["person_renders"];
    onSelect: (segmentId: string) => void;
    selectedSegmentId: string | null;
    hasPerson: boolean;
}) {
    if (!narration)
        return null;
    const ready = narration.segments.length > 0 && narration.segments.every(segment => segment.status === "ready");
    const selectedRender = personRenders.find((render) => render.narration_segment_id === selectedSegmentId) ?? null;
    return (<section className={styles.workSection}>
      <div className={styles.sectionHeading}><div><h2>试听当前声音来源</h2><p>{narration.model_id ?? "本机录音"} · {narration.duration_seconds.toFixed(1)} 秒{narration.sample_rate ? ` · ${narration.sample_rate} Hz` : ""}</p></div><span className={styles.artifactStatus}>{ready ? "已采用" : narration.segments.some(segment => segment.status === "failed") ? "有段落未完成" : "等待配音完成"}</span></div>
      {ready ? <audio className={styles.audioPreview} controls preload="metadata" src={apiUrl(`/api/creation-tasks/${taskId}/narration/audio`)}/> : null}
      <div className={styles.narrationSegments}>
        {narration.segments.map((segment, index) => (<article className={selectedSegmentId === segment.id ? styles.selectedSegment : ""} key={segment.id}>
            <button aria-label={`选择配音 ${index + 1}`} aria-pressed={selectedSegmentId === segment.id} disabled={busy} onClick={() => onSelect(segment.id)} type="button">
              <span>第 {index + 1} 段</span>
              <strong>{segment.text}</strong>
              <small>{segment.duration_seconds !== null ? `${segment.duration_seconds.toFixed(1)} 秒 · ` : ""}{narrationSegmentStatus(segment.status)}</small>
            </button>
            {segment.audio_url ? <audio aria-label={`试听配音 ${index + 1}`} controls preload="metadata" src={apiUrl(segment.audio_url)}/> : null}
          </article>))}
      </div>
      <PersonRenderPanel busy={busy} onGenerate={onGeneratePersonRender} selectedRender={selectedRender} selectedSegmentId={selectedSegmentId} hasPerson={hasPerson}/>
    </section>);
}
