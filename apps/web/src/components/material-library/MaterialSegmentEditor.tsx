"use client";
import type { ReactNode } from "react";
import { AppSelect, AppSelectOption } from "../AppSelect";
import type { MaterialSegment, MaterialAsset } from "@/lib/types";
import { duration } from "./material-labels";
import styles from "../MaterialLibraryControls.module.css";
type Props = {
    segment: MaterialSegment;
    speechAnalysis: MaterialAsset["speech_analysis"];
    descriptionDraft: string;
    transcriptDraft: string;
    usageRoleDraft: MaterialSegment["usage_role"];
    disabled: boolean;
    saving: boolean;
    usageEvidence: ReactNode;
    tagsEditor: ReactNode;
    collectionPicker: ReactNode;
    onDescriptionChange: (value: string) => void;
    onTranscriptChange: (value: string) => void;
    onUsageRoleChange: (value: MaterialSegment["usage_role"]) => void;
    onSeekSpeech: (seconds: number) => void;
    onSave: () => void;
};
export function MaterialSegmentEditor({ segment, speechAnalysis, descriptionDraft, transcriptDraft, usageRoleDraft, disabled, saving, usageEvidence, tagsEditor, collectionPicker, onDescriptionChange, onTranscriptChange, onUsageRoleChange, onSeekSpeech, onSave }: Props) {
    return (<div className="library-segment-organizer">
          <div className="library-segment-heading">
            <strong>片段分类</strong>
            <span>
              已用 {segment.usage_count} 次
              {segment.last_used_at
            ? ` · 最近 ${new Date(segment.last_used_at).toLocaleDateString("zh-CN")}`
            : ""}
            </span>
          </div>
          <p className={styles.segmentRange}>当前片段：{duration(segment.start_seconds)}–{duration(segment.end_seconds)}。下面的标签只用于这段画面。</p>
          {usageEvidence}
          {segment.understanding ? <section className="library-speech-evidence" aria-label="画面观察证据">
            <strong>画面观察</strong>
            {segment.understanding.status === "ready" ? <>
              <p>{segment.understanding.summary}</p>
              <div className="library-evidence-times" aria-label="采样帧">{segment.understanding.sample_times.map(time => <button key={time} type="button" aria-label={`查看采样帧 ${time.toFixed(2)} 秒`} onClick={() => onSeekSpeech(time)}><time>{duration(time)}</time></button>)}</div>
              {segment.understanding.facts.length ? <details><summary>查看 {segment.understanding.facts.length} 条观察依据</summary>
                {segment.understanding.facts.map((fact, index) => <button key={index} type="button" aria-label={`核对画面：${fact.text}`} onClick={() => onSeekSpeech(fact.sample_times[0])}>
                  <time>{duration(fact.sample_times[0])}</time><span>{fact.text}</span>
                </button>)}
              </details> : null}
              {segment.understanding.unknowns.length ? <p>待核对：{segment.understanding.unknowns.join("；")}</p> : null}
            </> : <p>{segment.understanding.message}</p>}
            <strong>画面文字</strong>
            {segment.understanding.ocr_status === "ready" ? segment.understanding.ocr_frames.some(frame => frame.observations.length) ?
                Array.from(new Set(segment.understanding.ocr_frames.flatMap(frame => frame.observations.map(item => item.text)))).map(text => {
                    const frame = segment.understanding!.ocr_frames.find(frame => frame.observations.some(item => item.text === text))!;
                    const eligible = segment.understanding!.ocr_frames.some(item => item.observations.some(observation => observation.text === text && observation.retrieval_eligible));
                    return <button key={text} type="button" onClick={() => onSeekSpeech(frame.timestamp_seconds)}><time>{duration(frame.timestamp_seconds)}</time><span>{text}{!eligible ? " · 低置信度，未用于检索" : ""}</span></button>;
                }) : <p>采样帧中未识别到文字。</p>
                : <p>{segment.understanding.ocr_status === "failed" ? "本地文字识别失败，可重新分析。" : "本地文字识别尚未配置。"}</p>}
          </section> : null}
          <label><span>片段描述</span><textarea value={descriptionDraft} onChange={event => onDescriptionChange(event.target.value)}/></label>
          <label><span>转写纠正（留空使用识别结果）</span><textarea value={transcriptDraft} onChange={event => onTranscriptChange(event.target.value)}/></label>
          <label>
            <span>片段用途</span>
            <AppSelect disabled={disabled} value={usageRoleDraft} onValueChange={newValue => onUsageRoleChange(newValue as MaterialSegment["usage_role"])}>
              {usageRoleDraft === "unassigned" ? <AppSelectOption value="unassigned" disabled>未指定（当前用途）</AppSelectOption> : null}
              {usageRoleDraft === "exclude" ? <AppSelectOption value="exclude" disabled>不使用（已排除）</AppSelectOption> : null}
              <AppSelectOption value="a_roll">人物主镜头</AppSelectOption>
              <AppSelectOption value="b_roll">补充画面</AppSelectOption>
            </AppSelect>
          </label>
          {speechAnalysis ? <section className="library-speech-evidence" aria-label="原片语音证据">
            <strong>原片语音</strong>
            {speechAnalysis.status !== "ready" || !speechAnalysis.observations.length ? <p>{speechAnalysis.message}</p> : null}
            {speechAnalysis.observations.filter(item => item.end_ms > segment.start_seconds * 1000 && item.start_ms < segment.end_seconds * 1000).map((item, index) => <button key={index} type="button" onClick={() => onSeekSpeech(item.start_ms / 1000)}>
                <time>{duration(item.start_ms / 1000)}</time><span>{item.text}</span>
              </button>)}
          </section> : null}
          {tagsEditor}
          {collectionPicker}
          <button className="library-segment-save" disabled={disabled} onClick={onSave} type="button">
            {saving ? "保存中…" : "保存片段修改"}
          </button>
        </div>);
}
