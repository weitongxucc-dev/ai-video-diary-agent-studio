"use client";

import { useContext, useEffect, useState } from "react";
import { listPlatformPublishJobs } from "@/lib/api/publishing";
import type { CreationTask, PlatformPublishJob } from "@/lib/types";
import { formatPublishingTime, publishingPlatformNames, publishStatusLabels, PublishingWorkspace } from "./PublishingWorkspace";
import { useWorkspacePage, WorkbenchAssistantTargetContext } from "./workbench/context";
import styles from "./PublishingWorkspace.module.css";

type SummaryRead =
  | { status: "loading" | "error" }
  | { status: "ready"; records: PlatformPublishJob[] };

export function PublishingRouteView() {
  const page = useWorkspacePage();
  const task = useContext(WorkbenchAssistantTargetContext)?.task;
  if (page?.active === false) return null;
  if (!page?.auxiliary) return <PublishingWorkspace />;
  return task
    ? <PublishingSummary key={task.id} task={task} />
    : <p role="status" className={styles.summary}>正在定位当前作品…</p>;
}

function PublishingSummary({ task }: { task: CreationTask }) {
  const [read, setRead] = useState<SummaryRead>({ status: "loading" });
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    let current = true;
    void listPlatformPublishJobs(task.id).then(
      ({ items }) => {
        // The existing job read returns persisted creation order.
        if (current) setRead({ status: "ready", records: items.slice(-3).reverse() });
      },
      () => { if (current) setRead({ status: "error" }); },
    );
    return () => { current = false; };
  }, [task.id, retry]);

  return <section className={styles.summary} aria-label="当前作品发布摘要">
    <h2>{task.name}</h2>
    {read.status === "loading" ? <p role="status">正在读取发布记录…</p> : null}
    {read.status === "error" ? <div role="alert">
      <p>发布记录读取失败，当前结果未知。</p>
      <button type="button" className={styles.secondaryButton} onClick={() => { setRead({ status: "loading" }); setRetry(value => value + 1); }}>重新读取</button>
    </div> : null}
    {read.status === "ready" && !read.records.length ? <p>当前作品没有已保存的发布记录。</p> : null}
    {read.status === "ready" && read.records.length ? <>
      <ol className={styles.summaryRecords} aria-label="最近发布记录">
        {read.records.map(record => <PublishingSummaryRecord key={record.id} record={record} task={task} />)}
      </ol>
      <p className={styles.source}>来源为本机保存的发布作业及其事件。</p>
    </> : null}
  </section>;
}

function PublishingSummaryRecord({ record, task }: { record: PlatformPublishJob; task: CreationTask }) {
  const prepared = record.events.find(event => event.type === "platform_publish_prepared")?.payload;
  const receipt = record.events.find(event => event.type === "platform_publish_succeeded")?.payload;
  const lastEvent = record.events.at(-1);
  const platform = prepared?.platform ?? receipt?.platform;
  const account = prepared?.account_ref ?? receipt?.account_ref;
  const version = prepared?.timeline_version_id;
  return <li>
    <strong>{publishStatusLabels[record.status]}</strong>
    {lastEvent ? <time dateTime={lastEvent.created_at}>{formatPublishingTime(lastEvent.created_at)}</time> : null}
    <dl className={styles.summaryFacts}>
      <dt>平台</dt><dd>{typeof platform === "string" ? publishingPlatformNames[platform] ?? platform : "未知"}</dd>
      <dt>账号</dt><dd>{typeof account === "string" ? account : "未知"}</dd>
      <dt>成片版本</dt><dd>{typeof version === "string" ? version === task.current_timeline_version_id ? `V${task.timeline_sequence}` : version : "未知"}</dd>
      {typeof receipt?.remote_publish_id === "string" ? <><dt>平台返回编号</dt><dd>{receipt.remote_publish_id}</dd></> : null}
    </dl>
    {record.impact ? <p>{record.impact}</p> : null}
  </li>;
}
