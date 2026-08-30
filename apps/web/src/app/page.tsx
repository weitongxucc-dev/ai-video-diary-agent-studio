import Link from "next/link";
import { AlertCircle, ArrowRight, CheckCircle2, Clock3, Video } from "lucide-react";
import { AppShell } from "@/components/AppShell";
import { HomeCommandComposer } from "@/components/HomeCommandComposer";
import { getProductionFoundation, listCreationTasks } from "@/lib/api.server";
import { creationTaskStatusPresentation } from "@/lib/creation-task-status";
import type { CreationTask, ProductionFoundation } from "@/lib/types";

export const dynamic = "force-dynamic";

function displayTaskName(name?: string) {
  return (name || "未命名创作任务")
    .replace(/\bb-?roll\b/gi, "补充画面")
    .replace(/\bbatch\b/gi, "创作任务")
    .replace(/\bepisode\b/gi, "内容")
    .replace(/\brecipe\b/gi, "制作方案")
    .replace(/\bprovider\b/gi, "服务");
}

async function loadFoundation(): Promise<ProductionFoundation | null> {
  try {
    return await getProductionFoundation();
  } catch {
    return null;
  }
}

function CreationTaskRow({ task }: { task: CreationTask }) {
  const state = creationTaskStatusPresentation(task.stage_status, task.stage);
  const StateIcon = state.tone === "success" ? CheckCircle2 : state.tone === "warning" || state.tone === "danger" ? AlertCircle : Clock3;
  return (
    <Link className="home-batch-row" href={`/tasks/${encodeURIComponent(task.id)}`}>
      <span className={`home-batch-row__icon status-${state.tone}`}><StateIcon className={state.tone === "running" ? "is-spinning" : ""} size={16} /></span>
      <span className="home-batch-row__copy">
        <strong>{displayTaskName(task.name)}</strong>
        <small>{state.stageLabel}</small>
      </span>
      <span className={`home-batch-row__status status-${state.tone}`}>{state.label}</span>
      <ArrowRight size={15} />
    </Link>
  );
}

export default async function HomePage() {
  const [foundation, creationTaskResponse] = await Promise.all([
    loadFoundation(),
    listCreationTasks().catch(() => null),
  ]);
  const creationTasks = creationTaskResponse?.items ?? [];
  const taskStatusCounts = creationTasks.reduce<Record<CreationTask["stage_status"], number>>((counts, task) => {
    counts[task.stage_status] += 1;
    return counts;
  }, { not_started: 0, running: 0, waiting_for_user: 0, completed: 0, failed: 0 });
  const runFacts = [
    ["全部任务", creationTasks.length],
    ["等待开始", taskStatusCounts.not_started],
    ["进行中或待确认", taskStatusCounts.running + taskStatusCounts.waiting_for_user],
    ["需要处理", taskStatusCounts.failed],
    ["已完成", taskStatusCounts.completed],
  ] as const;
  const visibleRunFacts = runFacts.filter(([label, count]) => label === "全部任务" || count > 0);
  const compactReadiness = foundation ? (
    <section className={`home-readiness-strip${foundation.readiness.ready ? " is-ready" : " is-blocked"}`} aria-label="当前制作条件">
      {foundation.readiness.ready ? <CheckCircle2 size={16} /> : <AlertCircle size={16} />}
      <div><strong>{foundation.readiness.ready ? "当前条件可以试跑" : "开始前需要处理一项阻断"}</strong><span>{foundation.readiness.ready ? `已审核 ${foundation.readiness.approved_asset_count} 项素材` : foundation.readiness.blockers[0]?.message || "请检查本地制作条件。"}</span></div>
      <Link href="/health">查看处理方式 <ArrowRight size={13} /></Link>
    </section>
  ) : null;

  return (
    <AppShell
      creationTasks={creationTasks}
      inspector={foundation ? (
        <div className="home-run-board">
          <div className={`readiness-callout${foundation.readiness.ready ? " is-ready" : " is-blocked"}`}>
            {foundation.readiness.ready ? <CheckCircle2 size={17} /> : <AlertCircle size={17} />}
            <div><strong>{foundation.readiness.ready ? "当前工作区可以试跑" : "当前工作区存在阻断"}</strong><span>{foundation.readiness.ready ? "制作方案将使用已审核的资料与素材。" : foundation.readiness.blockers[0]?.message}</span></div>
          </div>
          <dl className="home-run-board__facts">
            {visibleRunFacts.map(([label, count]) => <div key={label}><dt>{label}</dt><dd>{count}<small>项</small></dd></div>)}
          </dl>
          <Link className="inspector-link" href="/health">查看运行情况 <ArrowRight size={14} /></Link>
        </div>
      ) : null}
      inspectorTitle="运行看板与通知"
      workspaceStatus={foundation?.readiness.ready ? { kind: "connected", label: "工作区已就绪" } : { kind: "attention", label: "工作区需要检查" }}
    >
      <div className="modern-home">
        <section className="modern-home__hero">
          <h1>今天想制作什么？</h1>
          <HomeCommandComposer />
        </section>

        {compactReadiness}

        {!foundation ? (
          <section className="home-state-panel home-state-panel--error">
            <AlertCircle size={19} />
            <div><strong>暂时无法连接本地制作服务</strong><p>检查运行环境后可以继续，已有素材和任务不会丢失。</p></div>
            <Link href="/health">检查运行状态</Link>
          </section>
        ) : null}

        <section className="home-section">
          <header><div><span>最近</span><h2>创作任务</h2></div><Link href="/production">管理任务</Link></header>
          {creationTasks.length ? <div className="home-batch-list">{creationTasks.slice(0, 6).map((task) => <CreationTaskRow key={task.id} task={task} />)}</div> : (
            <div className="home-empty-state"><Video size={20} /><div><strong>还没有创作任务</strong><p>创建第一个任务，把已审核素材组合成可导出的成片。</p></div><Link href="/production?new=1">开始制作</Link></div>
          )}
        </section>
      </div>
    </AppShell>
  );
}
