import Link from "next/link";
import {
  AlertCircle,
  ArrowRight,
  CheckCircle2,
  Clock3,
  Film,
  FolderKanban,
  LoaderCircle,
  Plus,
  Settings2,
  Sparkles,
} from "lucide-react";
import { AppShell } from "@/components/AppShell";
import { CreateProductionTaskForm } from "@/components/CreateProductionTaskForm";
import { CreationTaskTopicWorkspace } from "@/components/CreationTaskTopicWorkspace";
import { ProcessingRefresh } from "@/components/ProcessingRefresh";
import { ProductionTaskControls } from "@/components/ProductionTaskControls";
import { CreationPlanApiError, getCreationPlan, getCreationTaskTopics, getProductionFoundation, listCreationTasks } from "@/lib/api.server";
import { creationTaskStatusPresentation } from "@/lib/creation-task-status";
import type { CreationPlan, CreationTask, CreationTaskTopicsResponse, ProductionBatch, ProductionFoundation } from "@/lib/types";

export const dynamic = "force-dynamic";

async function loadFoundation(): Promise<ProductionFoundation | null> {
  return getProductionFoundation().catch(() => null);
}

async function loadCreationPlan(planId: string): Promise<CreationPlan | null> {
  return getCreationPlan(planId).catch(() => null);
}

async function loadCreationTasks(): Promise<CreationTask[] | null> {
  return listCreationTasks().then((response) => response.items).catch(() => null);
}

type TopicLoadResult = {
  topics: CreationTaskTopicsResponse | null;
  error: { message: string; impact: string | null; repairAction: string | null; reason: string | null } | null;
};

async function loadCreationTaskTopics(taskId: string): Promise<TopicLoadResult> {
  try {
    return { topics: await getCreationTaskTopics(taskId), error: null };
  } catch (error) {
    if (error instanceof CreationPlanApiError) {
      return { topics: null, error: { message: error.message, impact: error.impact, repairAction: error.repairAction, reason: error.reason } };
    }
    return { topics: null, error: { message: "本地服务未返回选题状态。", impact: null, repairAction: "检查本地服务后重新检查。", reason: null } };
  }
}

function mergeCreationTasks(...groups: CreationTask[][]): CreationTask[] {
  const seen = new Set<string>();
  return groups.flat().filter((task) => {
    if (seen.has(task.id)) return false;
    seen.add(task.id);
    return true;
  }).slice(0, 6);
}

function CreationPlanStartInspector({ goal }: { goal: string }) {
  return <div className="creation-plan-inspector"><div className="readiness-callout"><Clock3 size={17} /><div><strong>尚未检查可用内容</strong><span>{goal ? "先查看本次目标可用的资料、素材、声音与模型，再确认创建任务。" : "在中间写下制作目标后，开始只读检查。"}</span></div></div><section className="queue-blockers"><h3>创建顺序</h3><p>1. 检查可用内容</p><p>2. 查看资料、授权、素材与模型状态</p><p>3. 确认并创建创作任务</p></section></div>;
}

function state(batch: ProductionBatch) {
  if (batch.status === "running") return { icon: LoaderCircle, label: "运行中", tone: "running" };
  if (batch.status === "waiting_for_user") return { icon: AlertCircle, label: "等待确认", tone: "warning" };
  if (batch.status === "ready" || batch.status === "reviewed") return { icon: CheckCircle2, label: "已完成", tone: "success" };
  if (batch.status === "failed" || batch.status === "blocked") return { icon: AlertCircle, label: "需要处理", tone: "danger" };
  if (batch.status === "interrupted") return { icon: AlertCircle, label: "已暂停", tone: "warning" };
  return { icon: Clock3, label: "等待开始", tone: "idle" };
}

function CreationTaskRow({ task }: { task: CreationTask }) {
  const taskState = creationTaskStatusPresentation(task.stage_status, task.stage);
  const TaskStateIcon = taskState.tone === "success" ? CheckCircle2 : taskState.tone === "running" ? LoaderCircle : taskState.tone === "warning" || taskState.tone === "danger" ? AlertCircle : Clock3;
  return (
    <article className="queue-row" key={task.id}>
      <div className={`queue-row__state status-${taskState.tone}`}><TaskStateIcon size={17} /></div>
      <div className="queue-row__main">
        <div className="queue-row__title"><strong>{displayTaskText(task.name)}</strong><span className={`status-${taskState.tone}`}>{taskState.label}</span></div>
        <p>{taskState.stageLabel}</p>
        <small>{taskState.tone === "idle" ? "任务已保存，等待开始选题。" : "当前状态来自已保存的创作任务。"}</small>
      </div>
      <Link aria-label={`打开 ${displayTaskText(task.name)}`} className="queue-row__open" href={`/production?plan=${encodeURIComponent(task.plan_id)}`}><ArrowRight size={16} /></Link>
    </article>
  );
}

function displayTaskText(value?: string | null) {
  return (value || "")
    .replace(/\bb-?roll\b/gi, "补充画面")
    .replace(/\bbatch\b/gi, "创作任务")
    .replace(/批次/g, "创作任务")
    .replace(/\bepisode\b/gi, "内容")
    .replace(/\bagent\b/gi, "智能助手")
    .replace(/\bprovider\b/gi, "服务")
    .replace(/混剪/g, "视频剪辑")
    .replace(/\bSRT\s+字幕/gi, "字幕");
}

export default async function ProductionPage({ searchParams }: { searchParams?: Promise<{ new?: string; batch?: string; plan?: string; goal?: string; view?: string }> }) {
  const params = searchParams ? await searchParams : {};
  const [foundation, persistedTasks] = await Promise.all([loadFoundation(), loadCreationTasks()]);
  const creationTasks = persistedTasks ?? [];

  if (params.view === "automations") {
    return (
      <AppShell creationTasks={creationTasks}>
        <div className="automation-unavailable">
          <AlertCircle size={21} />
          <div><h1>自动任务尚未接入</h1><p>当前本地服务尚未提供触发器、审核点、重试规则和通知配置；不会创建或假装运行自动任务。</p></div>
          <Link href="/templates">查看视频模板</Link>
        </div>
      </AppShell>
    );
  }

  if (params.plan) {
    const plan = await loadCreationPlan(params.plan);
    if (!plan) {
      return <AppShell creationTasks={creationTasks}><div className="production-offline"><AlertCircle size={21} /><div><h1>无法打开创作任务详情</h1><p>本地服务没有返回这项已创建任务。检查运行环境后再试，系统不会用旧任务数据替代。</p></div><Link href="/health">检查运行情况</Link></div></AppShell>;
    }
    const currentTasks = mergeCreationTasks(creationTasks, [plan.creation_task]);
    const topicState = await loadCreationTaskTopics(plan.creation_task.id);
    return <CreationTaskTopicWorkspace creationTasks={currentTasks} initialError={topicState.error} initialTopics={topicState.topics} plan={plan} />;
  }

  if (params.new === "1") {
    const initialGoal = params.goal ?? "";
    return <AppShell creationTasks={creationTasks} inspector={<CreationPlanStartInspector goal={initialGoal} />} inspectorTitle="创建前检查"><div className="modern-queue"><header className="modern-queue__header"><div><span>创作任务</span><h1>检查并创建创作任务</h1><p>先检查当前可用内容，再由你确认创建。检查不会生成选题、文案或成片。</p></div><Link href="/production">返回任务列表</Link></header><section className="queue-create-panel--preflight"><div><span><Sparkles size={17} /></span><div><h2>从制作目标开始</h2><p>检查会读取当前工作区的真实资料、信息源、素材、声音、授权和模型状态。</p></div></div><CreateProductionTaskForm autoFocus={!initialGoal} initialGoal={initialGoal} /></section></div></AppShell>;
  }

  if (!foundation) {
    return <AppShell><div className="production-offline"><AlertCircle size={21} /><div><h1>无法打开创作任务</h1><p>本地生产服务没有返回数据。修复运行环境后，已有任务会自动恢复。</p></div><Link href="/health">检查运行情况</Link></div></AppShell>;
  }

  const { readiness } = foundation;

  const taskStatusCounts = creationTasks.reduce<Record<CreationTask["stage_status"], number>>((counts, task) => {
    counts[task.stage_status] += 1;
    return counts;
  }, { not_started: 0, running: 0, waiting_for_user: 0, completed: 0, failed: 0 });

  const defaultInspector = (
    <div className="queue-inspector">
      <div className={`readiness-callout${readiness.ready ? " is-ready" : " is-blocked"}`}>
        {readiness.ready ? <CheckCircle2 size={17} /> : <AlertCircle size={17} />}
        <div><strong>{readiness.ready ? "可以开始制作" : "制作基础未就绪"}</strong><span>{readiness.ready ? "档案、声音、来源和素材已满足门槛。" : readiness.blockers[0]?.message}</span></div>
      </div>
      <dl className="queue-inspector__facts">
        <div><dt>素材总数</dt><dd>{readiness.asset_count}</dd></div>
        <div><dt>已审核素材</dt><dd>{readiness.approved_asset_count}</dd></div>
        <div><dt>可用片段</dt><dd>{readiness.approved_segment_count}</dd></div>
        <div><dt>已创建任务</dt><dd>{creationTasks.length}</dd></div>
        <div><dt>等待开始</dt><dd>{taskStatusCounts.not_started}</dd></div>
        <div><dt>选题生成中</dt><dd>{taskStatusCounts.running}</dd></div>
        <div><dt>等待确认</dt><dd>{taskStatusCounts.waiting_for_user}</dd></div>
        <div><dt>选题已完成</dt><dd>{taskStatusCounts.completed}</dd></div>
        <div><dt>需要处理</dt><dd>{taskStatusCounts.failed}</dd></div>
      </dl>
      {!readiness.ready ? <div className="queue-blockers"><h3>当前阻断</h3>{readiness.blockers.map((blocker) => <p key={blocker.code}>{blocker.message}</p>)}</div> : null}
      <div className="queue-inspector__links"><Link href="/library">管理素材库 <ArrowRight size={13} /></Link><Link href="/settings">制作设置 <ArrowRight size={13} /></Link></div>
    </div>
  );

  if (!params.batch) {
    return (
      <AppShell creationTasks={creationTasks} inspector={defaultInspector} inspectorTitle="任务运行情况" workspaceStatus={readiness.ready ? { kind: "connected", label: "工作区已就绪" } : { kind: "attention", label: "工作区存在阻断" }}>
        <div className="modern-queue">
          <header className="modern-queue__header">
            <div><span>创作任务</span><h1>创作任务</h1><p>每项任务保留制作过程、人工决定、失败原因与恢复点；需要你确认时会暂停。</p></div>
            <Link className="modern-primary-button" href="/production?new=1"><Plus size={16} />开始制作新视频</Link>
          </header>

          {!readiness.ready ? (
            <section className="queue-gate queue-gate--blocked"><AlertCircle size={20} /><div><strong>开始制作前还有 {readiness.blockers.length} 项需要处理</strong><p>{readiness.blockers[0]?.message}</p></div><Link href={readiness.approved_segment_count ? "/settings" : "/library"}>{readiness.approved_segment_count ? "打开制作设置" : "审核素材"}<ArrowRight size={14} /></Link></section>
          ) : (
            <section className="queue-gate queue-gate--ready"><CheckCircle2 size={20} /><div><strong>工作区已就绪</strong><p>可以创建新任务，或继续下面尚未完成的任务。</p></div><Link href="/production?new=1">开始制作<ArrowRight size={14} /></Link></section>
          )}

          <section className="queue-section">
            <header><div><span>任务</span><h2>最近任务</h2></div></header>
            {creationTasks.length ? <div className="queue-list">{creationTasks.map((task) => <CreationTaskRow key={task.id} task={task} />)}</div> : (
              <div className="queue-empty"><FolderKanban size={22} /><div><strong>还没有创作任务</strong><p>检查当前可用内容后，创建第一个创作任务。</p></div><Link href="/production?new=1">开始制作</Link></div>
            )}
          </section>

          <section className="queue-secondary-links"><Link href="/library"><Film size={16} /><span><strong>素材库</strong><small>识别、审核和管理补充画面</small></span><ArrowRight size={14} /></Link><Link href="/settings"><Settings2 size={16} /><span><strong>制作设置</strong><small>品牌、声音、模型与存储</small></span><ArrowRight size={14} /></Link></section>
        </div>
      </AppShell>
    );
  }

  const { recent_batches: batches, recent_montage_plans: plans, eligible_episodes: episodes } = foundation;
  const selectedBatch = batches.find((batch) => batch.id === params.batch) ?? null;
  const waitingCount = batches.filter((batch) => batch.status === "waiting_for_user" || batch.status === "blocked").length;
  const runningCount = batches.filter((batch) => batch.status === "running").length;
  const legacyInspector = (
    <div className="queue-inspector">
      <div className={`readiness-callout${readiness.ready ? " is-ready" : " is-blocked"}`}>
        {readiness.ready ? <CheckCircle2 size={17} /> : <AlertCircle size={17} />}
        <div><strong>{readiness.ready ? "历史任务可以继续处理" : "历史任务所在环境未就绪"}</strong><span>{readiness.ready ? "这是旧流程的临时续办边界。" : readiness.blockers[0]?.message}</span></div>
      </div>
      <dl className="queue-inspector__facts">
        <div><dt>素材总数</dt><dd>{readiness.asset_count}</dd></div>
        <div><dt>已审核素材</dt><dd>{readiness.approved_asset_count}</dd></div>
        <div><dt>可用片段</dt><dd>{readiness.approved_segment_count}</dd></div>
        <div><dt>运行中旧任务</dt><dd>{runningCount}</dd></div>
        <div><dt>等待处理</dt><dd>{waitingCount}</dd></div>
      </dl>
      {!readiness.ready ? <div className="queue-blockers"><h3>当前阻断</h3>{readiness.blockers.map((blocker) => <p key={blocker.code}>{blocker.message}</p>)}</div> : null}
      <div className="queue-inspector__links"><Link href="/library">管理素材库 <ArrowRight size={13} /></Link><Link href="/settings">制作设置 <ArrowRight size={13} /></Link></div>
    </div>
  );

  if (!selectedBatch) {
    return <AppShell creationTasks={creationTasks}><div className="production-offline"><AlertCircle size={21} /><div><h1>无法打开这项历史任务</h1><p>本地服务没有返回该任务。返回创作任务列表查看已保存任务。</p></div><Link href="/production">返回任务列表</Link></div></AppShell>;
  }

  return (
    <AppShell creationTasks={creationTasks} inspector={legacyInspector} inspectorTitle="历史任务（旧流程）" workspaceStatus={readiness.ready ? { kind: "connected", label: "工作区已就绪" } : { kind: "attention", label: "工作区存在阻断" }}>
      <div className="modern-queue">
        <header className="modern-queue__header">
          <div><span>历史任务（旧流程）</span><h1>{displayTaskText(selectedBatch.name) || "历史任务"}</h1><p>这是旧流程的临时续办入口；它不会出现在当前创作任务列表或左栏。</p></div>
          <Link className="modern-primary-button" href="/production?new=1"><Plus size={16} />开始制作新视频</Link>
        </header>

        {!readiness.ready ? (
          <section className="queue-gate queue-gate--blocked"><AlertCircle size={20} /><div><strong>开始制作前还有 {readiness.blockers.length} 项需要处理</strong><p>{readiness.blockers[0]?.message}</p></div><Link href={readiness.approved_segment_count ? "/settings" : "/library"}>{readiness.approved_segment_count ? "打开制作设置" : "审核素材"}<ArrowRight size={14} /></Link></section>
        ) : null}

        <section className="creation-task-stages">
            <header><div><span>制作流程</span><h2>九个制作阶段</h2></div><p>阶段明细尚未由本地服务提供；下方不伪造阶段进度。</p></header>
            <ol>
              {["资料准备", "选题", "文案", "配音", "人物与口型", "视频剪辑", "字幕与封面", "检查与发布", "数据监控"].map((stage, index) => <li key={stage}><span>{index + 1}</span><strong>{stage}</strong><small>等待阶段状态接入</small></li>)}
            </ol>
        </section>

        <section className="queue-section">
          <header><div><span>任务</span><h2>{displayTaskText(selectedBatch.name)}</h2></div><Link href="/production">返回任务列表</Link></header>
          {selectedBatch ? (
            <div className="queue-list">
              {[selectedBatch].map((batch) => {
                const batchState = state(batch);
                const StateIcon = batchState.icon;
                const batchPlans = plans.filter((plan) => plan.batch_id === batch.id);
                const availableEpisodes = episodes.filter((episode) => !batchPlans.some((plan) => plan.episode_id === episode.id && plan.status !== "failed"));
                const canRun = !["blocked", "ready", "reviewed", "running"].includes(batch.status);
                return (
                  <article className="queue-row" key={batch.id}>
                    <div className={`queue-row__state status-${batchState.tone}`}><StateIcon className={batch.status === "running" ? "is-spinning" : ""} size={17} /></div>
                    <div className="queue-row__main">
                      <div className="queue-row__title"><strong>{displayTaskText(batch.name)}</strong><span className={`status-${batchState.tone}`}>{batchState.label}</span></div>
                      <p>{displayTaskText(batch.message)}</p>
                      <div className="queue-progress"><span style={{ width: `${Math.min(100, batch.target_count ? (batch.rendered_count / batch.target_count) * 100 : 0)}%` }} /></div>
                      <small>已完成 {batch.rendered_count}/{batch.target_count}{batch.waiting_review_count ? ` · ${batch.waiting_review_count} 项待审核` : ""}{batch.failed_count ? ` · ${batch.failed_count} 项失败` : ""}</small>

                      {batch.items.length ? <div className="queue-items">{batch.items.map((item) => <div key={item.episode_id}><span>{displayTaskText(item.message)}</span>{item.plan_id ? <Link href={`/production/${batch.id}/episodes/${item.episode_id}/montage`}>审核视频剪辑</Link> : null}</div>)}</div> : null}

                      {batch.status === "running" ? <ProcessingRefresh /> : null}

                      <ProductionTaskControls batchId={batch.id} canRun={canRun} episodes={availableEpisodes.map((episode) => ({ id: episode.id, title: episode.title }))} runLabel={batch.status === "interrupted" || batch.failed_count ? "继续任务" : "开始制作"} />
                      <div className="queue-row__actions">
                        {batchPlans.map((plan) => <Link key={plan.id} href={`/production/${batch.id}/episodes/${plan.episode_id}/montage`}><Film size={14} />{plan.low_confidence_clip_count ? `审核 ${plan.low_confidence_clip_count} 个镜头` : "打开视频剪辑"}</Link>)}
                      </div>
                    </div>
                  </article>
                );
              })}
            </div>
          ) : (
            <div className="queue-empty"><FolderKanban size={22} /><div><strong>找不到这项历史任务</strong><p>返回创作任务列表查看已保存任务。</p></div><Link href="/production">返回任务列表</Link></div>
          )}
        </section>

        <section className="queue-secondary-links"><Link href="/library"><Film size={16} /><span><strong>素材库</strong><small>识别、审核和管理补充画面</small></span><ArrowRight size={14} /></Link><Link href="/settings"><Settings2 size={16} /><span><strong>制作设置</strong><small>品牌、声音、模型与存储</small></span><ArrowRight size={14} /></Link></section>
      </div>
    </AppShell>
  );
}
