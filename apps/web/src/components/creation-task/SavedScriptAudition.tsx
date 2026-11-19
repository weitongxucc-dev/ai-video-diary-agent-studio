"use client";

import { useState } from "react";
import type { CreationTaskScriptSection } from "@/lib/types";
import { DeliveryVoiceSample, type AuditionVoice } from "./DeliveryVoiceSample";
import styles from "../CreationTaskWorkspace.module.css";

type SavedSection = { ordinal: number; section: CreationTaskScriptSection };

export function SavedScriptAudition({ taskId, scriptId, updatedAt, items, voice }: {
    taskId: string;
    scriptId: string;
    updatedAt: string;
    items: readonly SavedSection[];
    voice: AuditionVoice;
}) {
    const [selectedId, setSelectedId] = useState("");
    const section = items.find(item => item.section.id === selectedId)?.section;

    return <details className={styles.requirementDetails}>
        <summary>试听已保存段落</summary>
        <label className={styles.field}><span>选择试听段落</span>
            <select value={section?.id ?? ""} onChange={event => setSelectedId(event.target.value)}>
                <option value="">选择已保存的段落</option>
                {items.map(({ ordinal, section }) => <option key={section.id} value={section.id}>
                    第 {ordinal} 段 · {section.title}
                </option>)}
            </select>
        </label>
        {section ? <DeliveryVoiceSample {...voice} savedSection={{
            sourceKey: JSON.stringify([JSON.stringify([taskId, scriptId, updatedAt]), section.id]),
            reference: { task_id: taskId, script_id: scriptId, section_id: section.id },
            text: section.content,
        }}/> : null}
    </details>;
}
