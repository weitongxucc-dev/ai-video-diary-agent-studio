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

function PlanReadiness({ foundation, creationTasks }: { foundation: ProductionFoundation; creationTasks: CreationTask[] }) {
  const missingAssets = Math.max(0, foundation.readiness.asset_count - foundation.readiness.approved_asset_count);
  const mostUrgentTask = creationTasks.find((task) => task.stage_status === "failed")
    ?? creationTasks.find((task) => task.stage_status === "waiting_for_user")
    ?? creationTasks.find((task) => task.stage_status === "running")
    ?? creationTasks.find((task) => task.stage_status === "not_started")
    ?? creationTasks.find((task) => task.stage_status === "completed");
  const taskSummary = mostUrgentTask ? creationTaskStatusPresentation(mostUrgentTask.stage_status, mostUrgentTask.stage) : null;
  const rows = [
    {
      label: "资料与素材",
      detail: `已审核 ${foundation.readiness.approved_asset_count} 项素材${missingAssets ? `，${missingAssets} 项待审核` : ""}`,
      state: missingAssets ? "需要处理" : "已就绪",
      tone: missingAssets ? "warning" : "success",
    },
    {
      label: "创作任务",
      detail: creationTasks.length ? `当前有 ${creationTasks.length} 项创作任务` : "还没有创作任务",
      state: taskSummary?.label ?? "等待创建",
      tone: taskSummary?.tone ?? "idle",
    },
    {
      label: "试跑条件",
      detail: foundation.readiness.ready ? "本地制作条件已满足，可在确认后试跑。" : foundation.readiness.blockers[0]?.message || "本地服务仍在检查制作条件。",
      state: foundation.readiness.ready ? "可以试跑" : "需要修复",
      tone: foundation.readiness.ready ? "success" : "danger",
    },
  ];

  return (
    <section className="home-plan-readiness" aria-label="当前制作条件">
      <header><div><span>当前工作区</span><h2>生成方案前会检查</h2></div><span className="home-plan-readiness__hint">所有状态来自本机工作区</span></header>
      <div>{rows.map((row) => <article key={row.label}><span className={`home-plan-readiness__dot status-${row.tone}`} /><strong>{row.label}</strong><p>{row.detail}</p><small className={`status-${row.tone}`}>{row.state}</small></article>)}</div>
    </section>
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
            <div><dt>已创建</dt><dd>{creationTasks.length}<small>项任务</small></dd></div>
            <div><dt>等待开始</dt><dd>{taskStatusCounts.not_started}<small>项任务</small></dd></div>
            <div><dt>进行中或待确认</dt><dd>{taskStatusCounts.running + taskStatusCounts.waiting_for_user}<small>项任务</small></dd></div>
            <div><dt>需要处理</dt><dd>{taskStatusCounts.failed}<small>项任务</small></dd></div>
            <div><dt>已完成</dt><dd>{taskStatusCounts.completed}<small>项任务</small></dd></div>
          </dl>
          <section className="home-run-board__notifications" aria-label="需要处理的通知">
            <header><h3>通知</h3><Link href="/production">查看任务</Link></header>
            <p className="home-run-board__empty">当前没有需要处理的通知。</p>
          </section>
          <Link className="inspector-link" href="/health">查看运行情况 <ArrowRight size={14} /></Link>
        </div>
      ) : null}
      inspectorTitle="运行看板与通知"
      workspaceStatus={foundation?.readiness.ready ? { kind: "connected", label: "工作区已就绪" } : { kind: "attention", label: "工作区需要检查" }}
    >
      <div className="modern-home">
        <section className="modern-home__hero">
          <div className="modern-home__eyebrow"><Video size={15} /> 从素材到成片</div>
          <h1>今天想制作什么？</h1>
          <p>描述目标后，先检查当前可用的资料、信息源、素材、声音与模型；确认后才会创建创作任务。</p>
          <HomeCommandComposer />
        </section>

        {!foundation ? (
          <section className="home-state-panel home-state-panel--error">
            <AlertCircle size={19} />
            <div><strong>暂时无法连接本地制作服务</strong><p>检查运行环境后可以继续，已有素材和任务不会丢失。</p></div>
            <Link href="/health">检查运行状态</Link>
          </section>
        ) : null}

        {foundation ? <PlanReadiness creationTasks={creationTasks} foundation={foundation} /> : null}

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
