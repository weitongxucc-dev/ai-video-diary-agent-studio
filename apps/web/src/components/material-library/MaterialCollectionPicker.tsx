"use client";
import { FolderPlus } from "lucide-react";
import type { MaterialCollection } from "@/lib/types";
import styles from "../MaterialLibraryControls.module.css";
type Props = {
    query: string;
    onQueryChange: (query: string) => void;
    collections: MaterialCollection[];
    collectionDraft: string;
    newCollectionName: string;
    disabled: boolean;
    onChoose: (id: string) => void;
    onNameChange: (name: string) => void;
    onCreate: () => void;
};
export function MaterialCollectionPicker({ query, onQueryChange, collections, collectionDraft, newCollectionName, disabled, onChoose, onNameChange, onCreate }: Props) {
    return <>
    <div className={styles.collectionPicker}>
            <label htmlFor="material-collection-search">标签夹</label>
            <input id="material-collection-search" onChange={(event) => onQueryChange(event.target.value)} placeholder="搜索标签夹" type="search" value={query}/>
            <div className={styles.collectionOptions} role="group" aria-label="选择标签夹">
              {[{ id: "", name: "未归类" }, ...collections]
            .filter((collection) => collection.name.includes(query.trim()))
            .map((collection) => (<button aria-pressed={collectionDraft === collection.id} className={collectionDraft === collection.id ? styles.isSelected : ""} key={collection.id || "none"} onClick={() => onChoose(collection.id)} type="button">{collection.name}</button>))}
              {!collections.some((collection) => collection.name.includes(query.trim())) && !"未归类".includes(query.trim()) ? <small>没有匹配的标签夹</small> : null}
            </div>
          </div>
          <div className="library-collection-create">
            <input aria-label="新标签夹名称" onChange={(event) => onNameChange(event.target.value)} placeholder="新建标签夹" value={newCollectionName}/>
            <button disabled={disabled || !newCollectionName.trim()} onClick={onCreate} type="button">
              <FolderPlus size={14}/>
              新建
            </button>
          </div>
  </>;
}
