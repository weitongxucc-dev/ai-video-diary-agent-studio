"use client";
import Link from "next/link";
import type { MaterialUsageRecord } from "@/lib/types";
import { clipRoleLabel } from './material-labels';
type Props = {
    usageRecords: MaterialUsageRecord[];
    usageRead: {
        status: "loading" | "ready" | "error";
        message: string;
    };
    retryUsageRead: () => void;
};
export function MaterialUsageEvidence({ usageRecords, usageRead, retryUsageRead }: Props) {
    return (<section className="library-speech-evidence" aria-label="片段使用记录">
            <strong>使用记录</strong>
            <p>回查这个片段进过哪条成片。只列已实际写入的使用记录，读取不会增加使用次数。用途和版本都只反映该计划当前的取值，不是当次使用时的历史值。</p>
            {usageRead.status === "loading" ? <p>正在读取使用记录……</p> : null}
            {usageRead.status === "error" ? <p role="alert">{usageRead.message}<button type="button" onClick={retryUsageRead}>重新读取使用记录</button></p> : null}
            {usageRead.status === "ready" && !usageRecords.length ? <p>这个片段还没有被任何成片使用过。</p> : null}
            {usageRead.status === "ready" ? usageRecords.map(record => <article key={`${record.plan_id}:${record.clip_id}`}>
              <p>
                {record.task?.name || "成片使用记录"} · {clipRoleLabel(record.role)}
                {record.manually_selected === null ? "" : record.manually_selected ? " · 人工选定" : " · 自动匹配"}
              </p>
              <p>
                {new Date(record.used_at).toLocaleString("zh-CN")}
                {record.task
                ? ` · 任务 ${record.task.stage ?? "阶段未记录"} / ${record.task.status ?? "状态未记录"}`
                : " · 关联任务未记录"}
              </p>
              {record.task?.id ? <p><Link href={"/tasks/" + encodeURIComponent(record.task.id)}>打开任务{record.task.name ? `：${record.task.name}` : ""}</Link></p> : null}
              <p>
                {record.resolution === "current" && record.timeline_version_id
                ? "关联任务当前时间线（当次使用版本未记录）"
                : "时间线版本未记录"}
              </p>
            </article>) : null}
          </section>);
}
