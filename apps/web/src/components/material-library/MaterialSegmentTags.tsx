"use client";
import { useRef } from "react";
import { X } from "lucide-react";
import type { MaterialSegment, MaterialTagStat } from "@/lib/types";
import { duration, tagLabel } from "./material-labels";
import { observedTagSuggestions } from "./segment-tags";
import styles from "../MaterialLibraryControls.module.css";
type Props = {
    segment: MaterialSegment;
    commonTags: MaterialTagStat[];
    tagDraft: string;
    tagError: string;
    disabled: boolean;
    onDraftChange: (value: string) => void;
    onAdd: (tag: string) => void;
    onRemove: (tag: string) => void;
};
export function MaterialSegmentTags({ segment, commonTags, tagDraft, tagError, disabled, onDraftChange, onAdd, onRemove }: Props) {
    const isComposingTag = useRef(false);
    return <>
    <div className="library-tag-list" aria-label="系统标签">
            {segment.system_tags.map((tag) => (<span key={tag}>{tagLabel(tag)}</span>))}
            {!segment.system_tags.length ? (<small>等待画面理解生成系统标签</small>) : null}
          </div>
          {observedTagSuggestions(segment).length ? <details className={styles.tagSuggestions}>
            <summary>标签建议 · {observedTagSuggestions(segment).length} 项待采纳</summary>
            <p>根据这段画面的实际观察整理。采纳后加入我的标签。</p>
            {observedTagSuggestions(segment).map(suggestion => <div key={suggestion.label}>
              <span><strong>{suggestion.label}</strong><small>{suggestion.source} · 采样 {suggestion.times.map(duration).join("、")}</small></span>
              <button type="button" disabled={disabled} aria-label={`采纳标签 ${suggestion.label}`} onClick={() => onAdd(suggestion.label)}>采纳</button>
            </div>)}
          </details> : null}
          <div className={styles.tagEditor}>
            <strong>我的标签</strong>
            <div className={styles.tagChips} aria-label="已保存的我的标签">
              {segment.user_tags.length ? segment.user_tags.map((tag) => (<span className={styles.tagChip} key={tag}>{tag}
                  <button aria-label={`移除标签 ${tag}`} disabled={disabled} onClick={() => onRemove(tag)} type="button"><X size={13}/></button>
                </span>)) : <small>这段画面还没有我的标签。</small>}
            </div>
            <label htmlFor="material-tag-input">添加标签</label>
            {tagError ? <p className={styles.tagError} role="alert">{tagError}</p> : null}
            <input autoComplete="off" disabled={disabled} id="material-tag-input" onChange={(event) => onDraftChange(event.target.value)} onCompositionStart={() => { isComposingTag.current = true; }} onCompositionEnd={() => { isComposingTag.current = false; }} onKeyDown={(event) => {
            if (event.key === "Enter" && !isComposingTag.current && !event.nativeEvent.isComposing) {
                event.preventDefault();
                onAdd(tagDraft);
            }
        }} placeholder="输入标签后按回车，如产品演示" value={tagDraft}/>
            {commonTags.some((entry) => !segment.user_tags.includes(entry.tag)) ? (<div className={styles.commonTags} aria-label="常用标签">
                <span>常用标签</span>
                {commonTags.filter((entry) => !segment.user_tags.includes(entry.tag)).slice(0, 6).map((entry) => (<button disabled={disabled} key={entry.tag} onClick={() => onAdd(entry.tag)} type="button">{tagLabel(entry.tag)}</button>))}
              </div>) : null}
          </div>
  </>;
}
