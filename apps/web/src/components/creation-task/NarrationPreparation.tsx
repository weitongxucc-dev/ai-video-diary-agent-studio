"use client";
import { WorkspaceAudio } from "../workbench/WorkspaceMedia";
import Link from "next/link";
import { AppSelect, AppSelectOption } from "../AppSelect";
import { Button } from "../ui/button";
import { AlertCircle, Check, LoaderCircle, Mic, Volume2 } from "lucide-react";
import { apiUrl } from "@/lib/api";
import type { CreationTaskRecording, CreationTaskVoiceOption } from "@/lib/types";
import type { BusyAction } from './edit-context';
import styles from "../CreationTaskWorkspace.module.css";

const voiceUnavailableReasons: Record<string, string> = {
    voice_reference_unavailable: "声音参考暂不可用，请检查来源和使用范围。",
    voice_language_unavailable: "声音不支持当前方案语言。",
};
const voiceUnavailableReason = (reason: string | null) => reason && voiceUnavailableReasons[reason]
    || "此声音暂不可用，服务未提供可显示的具体原因。";
type Props = {
    taskId: string;
    canReviseScript: boolean;
    scriptConfirmed: boolean;
    scriptSectionCount: number;
    hasSelectedVoice: boolean;
    voices: CreationTaskVoiceOption[];
    selectedVoiceId: string | null;
    recordings: CreationTaskRecording[];
    recordingsError: string | null;
    busy: BusyAction | null;
    onRecord: () => void;
    onReviseScript: () => void;
    onAdoptRecording: (recording: CreationTaskRecording) => void;
    onVoiceChange: (id: string) => void;
    onUseVoice: () => void;
};
export function NarrationPreparation({ taskId, canReviseScript, scriptConfirmed, scriptSectionCount, hasSelectedVoice, voices, selectedVoiceId, recordings, recordingsError, busy, onRecord, onReviseScript, onAdoptRecording, onVoiceChange, onUseVoice }: Props) {
    const unavailable = voices.filter(voice => !voice.runnable);
    const selectedRunnable = voices.some(voice => voice.profile_id === selectedVoiceId && voice.runnable);
    return (<section className={styles.workSection}>
            <div className={styles.emptyAction}>
              <Volume2 size={30}/>
              <h2>准备配音</h2>
              <p>可采用本人录音，或使用已选声音制作。</p>
              {scriptConfirmed ? <Button variant="outline" className={styles.secondaryButton} disabled={busy !== null} onClick={onRecord} type="button"><Mic size={16}/>录一段旁白</Button> : null}
              {canReviseScript ? <Button variant="outline" className={styles.secondaryButton} disabled={busy !== null} onClick={onReviseScript} type="button">修改文案</Button> : null}
              {recordingsError ? <p role="alert">已保存录音读取失败：{recordingsError}</p> : null}
              {recordings.map((recording) => (<article key={recording.source_ref}>
                  <strong>本机录音 · {(recording.duration_ms / 1000).toFixed(1)} 秒</strong>
                  <WorkspaceAudio controls preload="metadata" src={apiUrl(recording.audio_url)}/>
                  <span>{recording.applied ? "已采用为当前配音" : "已保存 · 尚未采用"}</span>
                  {!recording.applied ? (scriptConfirmed && scriptSectionCount === 1 ?
                <Button className="modern-primary-button" disabled={busy !== null} onClick={() => onAdoptRecording(recording)} type="button">{busy === "recording-adopt" ? <LoaderCircle className="is-spinning" size={16}/> : <Check size={16}/>}采用这段录音</Button>
                : <p>当前文案不是单段确认稿，无法确定录音对应关系；请先整理为一个明确段落。</p>) : null}
                </article>))}
              {hasSelectedVoice ? <p>已选择授权声音。如需生成配音，可在连续制作中确认要求后继续；本机录音也可独立采用。</p>
            : voices.length ? <div className={styles.voiceSelection}><label className={styles.field}><span>用于生成配音的声音</span><AppSelect aria-label="用于生成配音的声音" disabled={busy !== null} onValueChange={onVoiceChange} value={selectedVoiceId ?? ""}>{voices.map((voice) => <AppSelectOption key={`${voice.profile_id}:${voice.version}`} value={voice.profile_id} disabled={!voice.runnable}>{voice.person_display_name} · {voice.version}</AppSelectOption>)}</AppSelect></label><Button className="modern-primary-button" disabled={busy !== null || !selectedRunnable} onClick={onUseVoice} type="button">{busy === "voice-select" ? <LoaderCircle className="is-spinning" size={16}/> : <Check size={16}/>}采用此声音</Button></div>
                : <div className={styles.voiceMissing}><AlertCircle size={18}/><div><strong>还没有可用于生成配音的授权声音</strong><p>你仍可采用这次本人录音；使用声音模型时再登记获得授权的声音。</p></div><Link className="modern-primary-button" href={`/profiles?view=voices&task=${encodeURIComponent(taskId)}`}>添加声音</Link></div>}
              {unavailable.length ? <div role="status"><ul aria-label="暂不可用的声音">{unavailable.map(voice => <li key={`${voice.profile_id}:${voice.version}`}>{voice.person_display_name} · {voice.version}：{voiceUnavailableReason(voice.runnable_reason)}</li>)}</ul><Link href={`/profiles?view=voices&task=${encodeURIComponent(taskId)}`}>检查声音参考</Link></div> : null}
            </div>
          </section>);
}
