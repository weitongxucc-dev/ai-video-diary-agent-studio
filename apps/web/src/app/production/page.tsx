import Link from "next/link";
import {
  AlertCircle,
  ArrowRight,
  CheckCircle2,
  Clock3,
  Film,
  FolderKanban,
  LoaderCircle,
  Play,
  Plus,
  Settings2,
  Sparkles,
} from "lucide-react";
import {
  createMontagePlanAction,
  createProductionBatchAction,
  runProductionBatchAction,
} from "@/app/actions";
import { AppShell } from "@/components/AppShell";
import { ProcessingRefresh } from "@/components/ProcessingRefresh";
import { getProductionFoundation, getVoiceProfiles } from "@/lib/api.server";
import type { ProductionBatch, ProductionFoundation, VoiceProfile } from "@/lib/types";

export const dynamic = "force-dynamic";

async function loadFoundation(): Promise<{ foundation: ProductionFoundation | null; voices: VoiceProfile[] }> {
  const [foundation, voiceCatalog] = await Promise.all([
    getProductionFoundation().catch(() => null),
    getVoiceProfiles().catch(() => ({ voices: [], total_count: 0 })),
  ]);
  return { foundation, voices: voiceCatalog.voices };
}

function state(batch: ProductionBatch) {
  if (batch.status === "running") return { icon: LoaderCircle, label: "生产中", tone: "running" };
  if (batch.status === "waiting_for_user") return { icon: AlertCircle, label: "等待审核", tone: "warning" };
  if (batch.status === "ready" || batch.status === "reviewed") return { icon: CheckCircle2, label: "已完成", tone: "success" };
  if (batch.status === "failed" || batch.status === "blocked") return { icon: AlertCircle, label: "需要处理", tone: "danger" };
  if (batch.status === "interrupted") return { icon: AlertCircle, label: "已中断", tone: "warning" };
  return { icon: Clock3, label: "等待开始", tone: "idle" };
}

export default async function ProductionPage({ searchParams }: { searchParams?: Promise<{ new?: string; batch?: string }> }) {
  const params = searchParams ? await searchParams : {};
  const { foundation, voices } = await loadFoundation();

  if (!foundation) {
    return <AppShell><div className="production-offline"><AlertCircle size={21} /><div><h1>无法打开生产队列</h1><p>本地生产服务没有返回数据。修复运行环境后，已有批次会自动恢复。</p></div><Link href="/health">检查运行健康</Link></div></AppShell>;
  }

  const { readiness, recent_batches: batches, recent_montage_plans: plans, eligible_episodes: episodes } = foundation;
  const selectedBatch = batches.find((batch) => batch.id === params.batch) ?? null;
  const waitingCount = batches.filter((batch) => batch.status === "waiting_for_user" || batch.status === "blocked").length;
  const runningCount = batches.filter((batch) => batch.status === "running").length;

  const inspector = (
    <div className="queue-inspector">
      <div className={`readiness-callout${readiness.ready ? " is-ready" : " is-blocked"}`}>
        {readiness.ready ? <CheckCircle2 size={17} /> : <AlertCircle size={17} />}
        <div><strong>{readiness.ready ? "可以开始生产" : "生产基础未就绪"}</strong><span>{readiness.ready ? "档案、声音、来源和素材已满足门禁。" : readiness.blockers[0]?.message}</span></div>
      </div>
      <dl className="queue-inspector__facts">
        <div><dt>素材总数</dt><dd>{readiness.asset_count}</dd></div>
        <div><dt>已审核素材</dt><dd>{readiness.approved_asset_count}</dd></div>
        <div><dt>可用片段</dt><dd>{readiness.approved_segment_count}</dd></div>
        <div><dt>运行中批次</dt><dd>{runningCount}</dd></div>
        <div><dt>等待处理</dt><dd>{waitingCount}</dd></div>
      </dl>
      {!readiness.ready ? <div className="queue-blockers"><h3>当前阻断</h3>{readiness.blockers.map((blocker) => <p key={blocker.code}>{blocker.message}</p>)}</div> : null}
      <div className="queue-inspector__links"><Link href="/library">管理素材库 <ArrowRight size={13} /></Link><Link href="/settings">生产设置 <ArrowRight size={13} /></Link></div>
    </div>
  );

  return (
    <AppShell inspector={inspector} inspectorTitle="生产就绪度" workspaceStatus={readiness.ready ? { kind: "connected", label: "生产环境已就绪" } : { kind: "attention", label: "生产环境存在阻断" }}>
      <div className="modern-queue">
        <header className="modern-queue__header">
          <div><span>自动生产</span><h1>生产队列</h1><p>创建批次后，Agent 会连续完成选题、文案、配音和混剪，并在需要你决定时暂停。</p></div>
          {readiness.ready && params.new !== "1" ? <Link className="modern-primary-button" href="/production?new=1"><Plus size={16} />新建生产</Link> : null}
        </header>

        {!readiness.ready ? (
          <section className="queue-gate queue-gate--blocked"><AlertCircle size={20} /><div><strong>开始生产前还有 {readiness.blockers.length} 项需要处理</strong><p>{readiness.blockers[0]?.message}</p></div><Link href={readiness.approved_segment_count ? "/settings" : "/library"}>{readiness.approved_segment_count ? "打开生产设置" : "审核素材"}<ArrowRight size={14} /></Link></section>
        ) : params.new === "1" || !batches.length ? (
          <section className="queue-create-panel">
            <div><span><Sparkles size={17} /></span><div><h2>创建生产批次</h2><p>先设置本批目标。每条内容仍会在最终混剪阶段等待你的审核。</p></div></div>
            <form action={createProductionBatchAction}>
              <label><span>批次名称</span><input autoFocus={params.new === "1"} name="name" placeholder="例如：本周 AI 工具观察" /></label>
              <label><span>成片数量</span><input defaultValue="10" max="50" min="1" name="target_count" type="number" /></label>
              <div><Link href="/production">取消</Link><button type="submit"><Plus size={15} />创建批次</button></div>
            </form>
          </section>
        ) : (
          <section className="queue-gate queue-gate--ready"><CheckCircle2 size={20} /><div><strong>生产环境已就绪</strong><p>可以创建新批次，或继续下面尚未完成的任务。</p></div><Link href="/production?new=1">新建生产<ArrowRight size={14} /></Link></section>
        )}

        <section className="queue-section">
          <header><div><span>任务</span><h2>{selectedBatch ? selectedBatch.name : "最近批次"}</h2></div>{selectedBatch ? <Link href="/production">返回全部</Link> : null}</header>
          {batches.length ? (
            <div className="queue-list">
              {(selectedBatch ? [selectedBatch] : batches).map((batch) => {
                const batchState = state(batch);
                const StateIcon = batchState.icon;
                const batchPlans = plans.filter((plan) => plan.batch_id === batch.id);
                const availableEpisodes = episodes.filter((episode) => !batchPlans.some((plan) => plan.episode_id === episode.id && plan.status !== "failed"));
                const canRun = !["blocked", "ready", "reviewed", "running"].includes(batch.status);
                return (
                  <article className="queue-row" key={batch.id}>
                    <div className={`queue-row__state status-${batchState.tone}`}><StateIcon className={batch.status === "running" ? "is-spinning" : ""} size={17} /></div>
                    <div className="queue-row__main">
                      <div className="queue-row__title"><strong>{batch.name}</strong><span className={`status-${batchState.tone}`}>{batchState.label}</span></div>
                      <p>{batch.message}</p>
                      <div className="queue-progress"><span style={{ width: `${Math.min(100, batch.target_count ? (batch.rendered_count / batch.target_count) * 100 : 0)}%` }} /></div>
                      <small>已完成 {batch.rendered_count}/{batch.target_count}{batch.waiting_review_count ? ` · ${batch.waiting_review_count} 项待审核` : ""}{batch.failed_count ? ` · ${batch.failed_count} 项失败` : ""}</small>

                      {batch.items.length ? <div className="queue-items">{batch.items.map((item) => <div key={item.episode_id}><span>{item.message}</span>{item.plan_id ? <Link href={`/production/${batch.id}/episodes/${item.episode_id}/montage`}>审核混剪</Link> : null}</div>)}</div> : null}

                      {batch.status === "running" ? <ProcessingRefresh /> : null}

                      <div className="queue-row__actions">
                        {canRun ? <form action={runProductionBatchAction}><input name="batch_id" type="hidden" value={batch.id} /><button className="is-primary" type="submit"><Play size={14} />{batch.status === "interrupted" || batch.failed_count ? "继续生产" : "开始生产"}</button></form> : null}
                        {batchPlans.map((plan) => <Link key={plan.id} href={`/production/${batch.id}/episodes/${plan.episode_id}/montage`}><Film size={14} />{plan.low_confidence_clip_count ? `审核 ${plan.low_confidence_clip_count} 个镜头` : "打开混剪"}</Link>)}
                      </div>

                      {batch.status !== "blocked" && batch.status !== "running" && availableEpisodes.length ? (
                        <details className="queue-plan-create"><summary><Plus size={13} />为已确认文案生成混剪</summary><form action={createMontagePlanAction}><input name="batch_id" type="hidden" value={batch.id} /><label><span>选择文案</span><select name="episode_id" required>{availableEpisodes.map((episode) => <option key={episode.id} value={episode.id}>{episode.title}</option>)}</select></label><label><span>本次配音</span><select name="voice_profile_id"><option value="">使用默认声音</option>{voices.map((voice) => <option key={voice.id} value={voice.id}>{voice.name}{voice.is_default ? "（默认）" : ""}</option>)}</select></label><label><span>已有旁白音频路径（可选）</span><input name="narration_audio_path" /></label><button type="submit"><Sparkles size={14} />生成配音与时间线</button></form></details>
                      ) : null}
                    </div>
                    {!selectedBatch ? <Link aria-label={`打开 ${batch.name}`} className="queue-row__open" href={`/production?batch=${batch.id}`}><ArrowRight size={16} /></Link> : null}
                  </article>
                );
              })}
            </div>
          ) : (
            <div className="queue-empty"><FolderKanban size={22} /><div><strong>还没有生产批次</strong><p>生产基础就绪后，创建第一个自动生产批次。</p></div>{readiness.ready ? <Link href="/production?new=1">新建生产</Link> : null}</div>
          )}
        </section>

        <section className="queue-secondary-links"><Link href="/library"><Film size={16} /><span><strong>素材库</strong><small>识别、审核和管理 B-roll</small></span><ArrowRight size={14} /></Link><Link href="/settings"><Settings2 size={16} /><span><strong>生产设置</strong><small>品牌、声音、模型与存储</small></span><ArrowRight size={14} /></Link></section>
      </div>
    </AppShell>
  );
}
