import Link from "next/link";
import { redirect } from "next/navigation";
import { AlertCircle, ArrowRight, CheckCircle2, Clock3, LoaderCircle, Video } from "lucide-react";
import { AppShell } from "@/components/AppShell";
import { HomeCommandComposer } from "@/components/HomeCommandComposer";
import { getProductionFoundation, getSetupPreflight } from "@/lib/api.server";
import type { ProductionBatch, ProductionFoundation } from "@/lib/types";

export const dynamic = "force-dynamic";

async function loadFoundation(): Promise<ProductionFoundation | null> {
  try {
    const preflight = await getSetupPreflight();
    if (preflight.overall !== "ready") redirect("/onboarding");
    return await getProductionFoundation();
  } catch (error) {
    if (error instanceof Error && error.message === "NEXT_REDIRECT") throw error;
    return null;
  }
}

function batchTone(batch: ProductionBatch) {
  if (batch.status === "running") return { icon: LoaderCircle, label: "正在生产", tone: "running" };
  if (batch.status === "waiting_for_user") return { icon: AlertCircle, label: "等待审核", tone: "warning" };
  if (batch.status === "ready" || batch.status === "reviewed") return { icon: CheckCircle2, label: "已就绪", tone: "success" };
  if (batch.status === "failed" || batch.status === "blocked") return { icon: AlertCircle, label: "需要处理", tone: "danger" };
  return { icon: Clock3, label: "等待开始", tone: "idle" };
}

function BatchRow({ batch }: { batch: ProductionBatch }) {
  const state = batchTone(batch);
  const StateIcon = state.icon;
  return (
    <Link className="home-batch-row" href={`/production?batch=${batch.id}`}>
      <span className={`home-batch-row__icon status-${state.tone}`}><StateIcon className={batch.status === "running" ? "is-spinning" : ""} size={16} /></span>
      <span className="home-batch-row__copy">
        <strong>{batch.name || "未命名批次"}</strong>
        <small>{batch.message || `${batch.rendered_count}/${batch.target_count} 条内容已完成`}</small>
      </span>
      <span className={`home-batch-row__status status-${state.tone}`}>{state.label}</span>
      <ArrowRight size={15} />
    </Link>
  );
}

export default async function HomePage() {
  const foundation = await loadFoundation();
  const batches = foundation?.recent_batches ?? [];
  const waiting = batches.filter((batch) => batch.status === "waiting_for_user" || batch.status === "blocked");
  const active = batches.filter((batch) => batch.status === "running" || batch.status === "created" || batch.status === "interrupted");

  return (
    <AppShell
      inspector={foundation ? (
        <div className="home-inspector">
          <div className={`readiness-callout${foundation.readiness.ready ? " is-ready" : " is-blocked"}`}>
            {foundation.readiness.ready ? <CheckCircle2 size={17} /> : <AlertCircle size={17} />}
            <div><strong>{foundation.readiness.ready ? "生产环境已就绪" : "生产环境存在阻断"}</strong><span>{foundation.readiness.ready ? "可以创建新批次并开始混剪。" : foundation.readiness.blockers[0]?.message}</span></div>
          </div>
          <dl className="home-inspector__facts">
            <div><dt>已审核素材</dt><dd>{foundation.readiness.approved_asset_count}</dd></div>
            <div><dt>可用片段</dt><dd>{foundation.readiness.approved_segment_count}</dd></div>
            <div><dt>最近批次</dt><dd>{batches.length}</dd></div>
          </dl>
          <Link className="inspector-link" href="/health">查看运行健康 <ArrowRight size={14} /></Link>
        </div>
      ) : null}
      inspectorTitle="工作区状态"
      workspaceStatus={foundation?.readiness.ready ? { kind: "connected", label: "生产环境已就绪" } : { kind: "attention", label: "生产环境需要检查" }}
    >
      <div className="modern-home">
        <section className="modern-home__hero">
          <div className="modern-home__eyebrow"><Video size={15} /> 从素材到成片</div>
          <h1>今天想制作什么？</h1>
          <p>描述目标，Agent 会结合文案、配音和已审核素材生成可复核的混剪方案。</p>
          <HomeCommandComposer episodes={foundation?.eligible_episodes ?? []} />
        </section>

        {!foundation ? (
          <section className="home-state-panel home-state-panel--error">
            <AlertCircle size={19} />
            <div><strong>暂时无法连接本地生产服务</strong><p>检查运行环境后可以继续，已有素材和批次不会丢失。</p></div>
            <Link href="/health">检查运行状态</Link>
          </section>
        ) : null}

        {waiting.length ? (
          <section className="home-section">
            <header><div><span>需要你处理</span><h2>等待审核与修复</h2></div><Link href="/production">查看全部</Link></header>
            <div className="home-batch-list">{waiting.map((batch) => <BatchRow batch={batch} key={batch.id} />)}</div>
          </section>
        ) : null}

        {active.length ? (
          <section className="home-section">
            <header><div><span>进行中</span><h2>当前生产任务</h2></div><Link href="/production">打开队列</Link></header>
            <div className="home-batch-list">{active.map((batch) => <BatchRow batch={batch} key={batch.id} />)}</div>
          </section>
        ) : null}

        <section className="home-section">
          <header><div><span>最近</span><h2>生产批次</h2></div><Link href="/production">管理批次</Link></header>
          {batches.length ? <div className="home-batch-list">{batches.slice(0, 6).map((batch) => <BatchRow batch={batch} key={batch.id} />)}</div> : (
            <div className="home-empty-state"><Video size={20} /><div><strong>还没有生产批次</strong><p>创建第一个批次，把已审核素材组合成可导出的成片。</p></div><Link href="/production?new=1">新建生产</Link></div>
          )}
        </section>
      </div>
    </AppShell>
  );
}
