"use client";

import { useId, type FormEvent } from "react";
import type { CreationTaskExactEditInput } from "@/lib/types";
import { useWorkspaceViewState } from "../workbench/context";
import styles from "../SubtitleWorkspace.module.css";

type FirstCueInput = Extract<CreationTaskExactEditInput, { operation_type: "AddFirstSubtitleCue" }>;

export function FirstSubtitleCueEditor({ versionId, duration, language, busy, canRedo, onRedo, onSave }: {
  versionId: string;
  duration: number;
  language: FirstCueInput["language"];
  busy: boolean;
  canRedo: boolean;
  onRedo: () => void;
  onSave: (input: FirstCueInput) => void;
}) {
  const headingId = useId();
  const [text, setText] = useWorkspaceViewState(`first-cue:${versionId}:text`, "");
  const [start, setStart] = useWorkspaceViewState(`first-cue:${versionId}:start`, "0");
  const [end, setEnd] = useWorkspaceViewState(`first-cue:${versionId}:end`, String(Math.min(1, duration)));
  const [selectedLanguage, setLanguage] = useWorkspaceViewState(`first-cue:${versionId}:language`, language);

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    const fields = event.currentTarget.elements;
    const textField = fields.namedItem("text") as HTMLTextAreaElement;
    const endField = fields.namedItem("end_seconds") as HTMLInputElement;
    const startSeconds = Number(start);
    const endSeconds = Number(end);
    const subtitleText = text.trim();
    textField.setCustomValidity(!subtitleText ? "请输入字幕文字" :
      Array.from(subtitleText).length > 96 ? "字幕文字最多 96 字" :
      subtitleText.split("\n").some(line => !line.trim()) ? "请移除字幕中的空行" : "");
    endField.setCustomValidity(Math.round(endSeconds * 1000) <= Math.round(startSeconds * 1000)
      ? "结束时间须晚于开始时间" : "");
    if (!event.currentTarget.reportValidity()) return;
    onSave({ operation_type: "AddFirstSubtitleCue", text: subtitleText,
      start_seconds: startSeconds, end_seconds: endSeconds, language: selectedLanguage });
  }

  return (
    <section className={styles.workspace}>
      <form className={styles.subtitleCue} method="post" aria-labelledby={headingId} onSubmit={submit}>
        <h2 className={styles.firstCueTitle} id={headingId}>添加首条字幕</h2>
        <div className={styles.cueFields}>
          <label>字幕文字
            <textarea name="text" required value={text} disabled={busy}
              onChange={event => { event.target.setCustomValidity(""); setText(event.target.value); }} />
          </label>
          <div className={styles.timeFields}>
            <label>开始（秒）
              <input name="start_seconds" type="number" step="any" min={0} max={duration}
                required value={start} disabled={busy} onChange={event => {
                  const endInput = event.target.form?.elements.namedItem("end_seconds") as HTMLInputElement;
                  endInput.setCustomValidity("");
                  setStart(event.target.value);
                }} />
            </label>
            <label>结束（秒）
              <input name="end_seconds" type="number" step="any" min={0} max={duration}
                required value={end} disabled={busy}
                onChange={event => { event.target.setCustomValidity(""); setEnd(event.target.value); }} />
            </label>
          </div>
        </div>
        <fieldset className={styles.firstCueLanguage} disabled={busy}>
          <legend>字幕语言</legend>
          <label><input type="radio" name="language" value="zh-CN" checked={selectedLanguage === "zh-CN"}
            onChange={() => setLanguage("zh-CN")} />中文</label>
          <label><input type="radio" name="language" value="en-US" checked={selectedLanguage === "en-US"}
            onChange={() => setLanguage("en-US")} />英文</label>
        </fieldset>
        <div className={styles.cueAction}>
          {canRedo ? <button className={styles.secondaryButton} disabled={busy} type="button" onClick={onRedo}>重做</button> : null}
          <button className="modern-primary-button" disabled={busy} type="submit">保存字幕</button>
        </div>
      </form>
    </section>
  );
}
