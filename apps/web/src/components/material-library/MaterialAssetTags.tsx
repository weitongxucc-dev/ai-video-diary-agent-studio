"use client";
import { X } from "lucide-react";
import styles from "../MaterialLibraryControls.module.css";
type Props = {
    tags: readonly string[];
    error: string | undefined;
    draft: string;
    pending: boolean;
    saving: boolean;
    onRemove: (tag: string) => void;
    onDraftChange: (draft: string) => void;
    onAdd: () => void;
};
export function MaterialAssetTags({ tags, error, draft, pending, saving, onRemove, onDraftChange, onAdd }: Props) {
    return (<section className={styles.tagEditor} aria-label="整条素材的我的标签">
        <strong>整条素材 · 我的标签</strong>
        <div className={styles.tagChips} aria-label="整条素材已保存的我的标签">
          {tags.length ? tags.map((tag) => (<span className={styles.tagChip} key={tag}>{tag}
              <button aria-label={`移除整条素材标签 ${tag}`} disabled={pending} onClick={() => onRemove(tag)} type="button"><X size={13}/></button>
            </span>)) : <small>整条素材还没有我的标签。</small>}
        </div>
        <label htmlFor="material-asset-tag-input">添加到整条素材</label>
        {error ? <p className={styles.tagError} role="alert">{error}</p> : null}
        <input autoComplete="off" disabled={pending} id="material-asset-tag-input" onChange={(event) => onDraftChange(event.target.value)} onKeyDown={(event) => {
            if (event.key === "Enter" && !event.nativeEvent.isComposing) {
                event.preventDefault();
                onAdd();
            }
        }} placeholder="输入整条素材标签后按回车" value={draft}/>
        {saving ? <small role="status">正在保存整条素材标签…</small> : null}
      </section>);
}
