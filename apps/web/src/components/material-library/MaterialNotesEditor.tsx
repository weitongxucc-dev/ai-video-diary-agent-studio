"use client";
import { Button } from "../ui/button";
import { DesktopVoiceRecorder } from "../DesktopVoiceRecorder";
import styles from "../MaterialLibraryControls.module.css";
type Props = {
    draft: string;
    uploadContext: string;
    error: string | undefined;
    saved: boolean | undefined;
    saving: boolean;
    disabled: boolean;
    voiceNoteActive: boolean;
    voiceNoteNotice: string;
    onDraftChange: (value: string) => void;
    onStartVoiceNote: () => void;
    onRecorded: (blob: Blob) => Promise<void>;
    onCancelVoiceNote: () => void;
    onSave: () => void;
};
export function MaterialNotesEditor({ draft, uploadContext, error, saved, saving, disabled, voiceNoteActive, voiceNoteNotice, onDraftChange, onStartVoiceNote, onRecorded, onCancelVoiceNote, onSave }: Props) {
    return (<section className={styles.notesEditor} aria-label="素材文字说明">
        <label>本素材说明（可选）<textarea disabled={saving} value={draft} onChange={event => onDraftChange(event.target.value)}/></label>
        <p>只记录你的补充，不改片段描述或事实标签；留空不影响分析。</p>
        <p>已保存背景：{uploadContext || "未填写"}</p>
        <Button variant="outline" disabled={disabled || voiceNoteActive} onClick={onStartVoiceNote} type="button">补口述说明</Button>
        <p>仅补当前素材库条目的说明，不改变公共背景。</p>
        {voiceNoteActive ? <>
          <DesktopVoiceRecorder purpose="material-notes" onRecorded={onRecorded} onCancel={onCancelVoiceNote}/>
          <Button variant="outline" onClick={onCancelVoiceNote} type="button">取消口述说明</Button>
        </> : null}
        {voiceNoteNotice ? <p role="status">{voiceNoteNotice}</p> : null}
        {error ? <p role="alert">{error}</p> : null}
        {saved ? <p role="status">本素材说明已保存。</p> : null}
        <Button variant="outline" disabled={disabled} onClick={onSave} type="button">保存本素材说明</Button>
      </section>);
}
