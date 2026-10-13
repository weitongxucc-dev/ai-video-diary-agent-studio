"use client";
import { Check, LoaderCircle, Sparkles } from "lucide-react";
import type { CreationTaskTopicsResponse } from "@/lib/types";
import type { BusyAction } from './edit-context';
import styles from "../CreationTaskWorkspace.module.css";
type Props = {
    topics: CreationTaskTopicsResponse | null;
    selectedTopicId: string | null;
    topicTitleDraft: string;
    knowledgeCount: number;
    sourceCount: number;
    busy: BusyAction | null;
    onSelect: (id: string, title: string) => void;
    onTitleChange: (title: string) => void;
    onGenerate: () => void;
    onConfirm: () => void;
};
export function TopicSelection({ topics, selectedTopicId, topicTitleDraft, knowledgeCount, sourceCount, busy, onSelect, onTitleChange, onGenerate, onConfirm }: Props) {
    const selectedTopicCandidate = topics?.candidates.find(candidate => candidate.id === selectedTopicId) ?? null;
    return (<section className={styles.workSection}><div className={styles.sectionHeading}><div><h2>{topics?.candidates.length ? "选择选题" : "生成选题"}</h2><p>{knowledgeCount} 项固定资料 · {sourceCount} 条信息源</p><details className={styles.traceDetails}><summary>本次生成范围</summary><p>使用本任务预检列出的文字摘录；确认选题后不再生成新候选。</p></details></div>{!topics?.candidates.length ? <button className="modern-primary-button" disabled={busy !== null} onClick={onGenerate} type="button">{busy === "topics" ? <LoaderCircle className="is-spinning" size={16}/> : <Sparkles size={16}/>}生成选题</button> : null}</div>{topics?.candidates.length ? <div className={styles.topicList}>{topics.candidates.map((candidate) => <label className={selectedTopicId === candidate.id ? styles.selected : ""} key={candidate.id}><input checked={selectedTopicId === candidate.id} name="topic" onChange={() => onSelect(candidate.id, candidate.title)} type="radio"/><div><header><strong>{candidate.title}</strong><span>{candidate.score} 分</span></header><p>{candidate.angle}</p><small>适合：{candidate.audience}</small><ol>{candidate.outline.map((item) => <li key={item}>{item}</li>)}</ol></div></label>)}</div> : null}{selectedTopicCandidate ? <label className={styles.field}><span>编辑标题</span><input aria-label="编辑标题" disabled={busy !== null} maxLength={80} onChange={(event) => onTitleChange(event.target.value)} placeholder="输入本选题的标题" value={topicTitleDraft}/><small className={styles.eyebrow}>确认时保存这个标题；后续文案将使用已保存的选题。</small></label> : null}{topics?.candidates.length ? <div className={styles.primaryAction}><button className="modern-primary-button" disabled={busy !== null || !selectedTopicId || !topicTitleDraft.trim()} onClick={onConfirm} type="button">{busy === "topic-confirm" ? <LoaderCircle className="is-spinning" size={16}/> : <Check size={16}/>}确认选题</button></div> : null}</section>);
}
