"use client";
import { WorkspaceVideo } from "../workbench/WorkspaceMedia";
import { useRef, useState, type RefObject } from "react";
import { ChevronDown, LoaderCircle, Mic, Save } from "lucide-react";
import { HomeCardDialog } from "../HomeCardDialog";
import { apiUrl } from "@/lib/api";
import type { BusyAction } from "./edit-context";
import styles from "../CreationTaskWorkspace.module.css";
type Props = {
    versionId: string | null;
    outputUrl: string;
    subtitleSummary: string;
    coverValid: boolean;
    canEditSubtitle: boolean;
    reviewChecks: {
        title: string;
        detail: string;
        ready: boolean;
    }[];
    busy: BusyAction | null;
    montageVideoRef: RefObject<HTMLVideoElement | null>;
    initialTime: number;
    onPlaybackPosition: (video: HTMLVideoElement) => void;
    onEditMontage: () => void;
    onRecord: () => void;
    onEditSubtitles: () => void;
    onSave: () => void;
};
export function ReviewOutputView({ versionId, outputUrl, subtitleSummary, coverValid, canEditSubtitle, reviewChecks, busy, montageVideoRef, initialTime, onPlaybackPosition, onEditMontage, onRecord, onEditSubtitles, onSave }: Props) {
    const [reviewActionsOpen, setReviewActionsOpen] = useState(false);
    const reviewActionsTrigger = useRef<HTMLButtonElement>(null);
    const reviewReadyCount = reviewChecks.filter(item => item.ready).length;
    return (<section className={styles.workSection}><div className={styles.sectionHeading}><div><h2>检查成片</h2><p>{subtitleSummary} · {coverValid ? "封面已生成" : "封面尚待确认"}</p></div><div className={styles.reviewActions}><button ref={reviewActionsTrigger} className={styles.secondaryButton} aria-haspopup="dialog" aria-expanded={reviewActionsOpen} onClick={() => setReviewActionsOpen(true)} type="button">调整成片<ChevronDown size={14}/></button>
        <HomeCardDialog kind="selection" title="调整成片" anchor={reviewActionsTrigger} open={reviewActionsOpen} onClose={() => setReviewActionsOpen(false)}><div className={styles.reviewMenu}><button disabled={busy !== null} onClick={() => { setReviewActionsOpen(false); onEditMontage(); }} type="button">修改镜头</button><button disabled={busy !== null} onClick={() => { setReviewActionsOpen(false); onRecord(); }} type="button"><Mic size={16}/>电脑录音替换旁白</button>{canEditSubtitle ? <button disabled={busy !== null} onClick={() => { setReviewActionsOpen(false); onEditSubtitles(); }} type="button">字幕封面</button> : null}</div></HomeCardDialog><button className="modern-primary-button" disabled={busy !== null} onClick={onSave} type="button">{busy === "save" ? <LoaderCircle className="is-spinning" size={16}/> : <Save size={16}/>}保存到本地</button></div></div><div className={styles.evidenceList}><strong>检查清单 · {reviewReadyCount}/{reviewChecks.length} 项已就绪</strong>{reviewChecks.map((item) => <article key={item.title}><span>{item.ready ? "已就绪" : "待确认"}</span><div><strong>{item.title}</strong><small>{item.detail}</small></div></article>)}</div><div className={styles.videoReview}><WorkspaceVideo key={versionId} aria-label="成片预览" controls preload="metadata" ref={montageVideoRef} onPause={(event) => onPlaybackPosition(event.currentTarget)} onSeeked={(event) => onPlaybackPosition(event.currentTarget)} onTimeUpdate={(event) => onPlaybackPosition(event.currentTarget)} onLoadedMetadata={(event) => { event.currentTarget.currentTime = initialTime; }} src={apiUrl(`${outputUrl}?version=${encodeURIComponent(versionId ?? "")}`)}/><div><p>请逐项检查字幕、声音和封面。</p></div></div></section>);
}
