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
} from "lucide-react";
import { AppShell } from "@/components/AppShell";
import { CreateProductionTaskForm } from "@/components/CreateProductionTaskForm";
import { ScheduledTasksWorkspace } from "@/components/ScheduledTasksWorkspace";
import { getProductionFoundation, listCreationTasks, listVideoTemplates } from "@/lib/api.server";
import { creationTaskStatusPresentation } from "@/lib/creation-task-status";
import type { CreationTask, ProductionFoundation } from "@/lib/types";

export const dynamic = "force-dynamic";

async function loadFoundation(): Promise<ProductionFoundation | null> {
  return getProductionFoundation().catch(() => null);
}

async function loadCreationTasks(): Promise<CreationTask[] | null> {
  return listCreationTasks()
    .then((response) => response.items)
    .catch(() => null);
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

function CreationTaskRow({ task }: { task: CreationTask }) {
  const taskState = creationTaskStatusPresentation(
    task.stage_status,
    task.stage,
  );
  const TaskStateIcon =
    taskState.tone === "success"
      ? CheckCircle2
      : taskState.tone === "running"
        ? LoaderCircle
        : taskState.tone === "warning" || taskState.tone === "danger"
          ? AlertCircle
          : Clock3;
  return (
    <article className="queue-row">
      <div className={`queue-row__state status-${taskState.tone}`}>
        <TaskStateIcon size={17} />
      </div>
      <div className="queue-row__main">
        <div className="queue-row__title">
          <strong>{displayTaskText(task.name)}</strong>
          <span className={`status-${taskState.tone}`}>{taskState.label}</span>
        </div>
        <p>{taskState.stageLabel}</p>
        <small>
          {taskState.tone === "idle"
            ? "任务已保存，等待开始选题。"
            : "当前状态来自已保存的创作任务。"}
        </small>
      </div>
      <Link
        aria-label={`打开 ${displayTaskText(task.name)}`}
        className="queue-row__open"
        href={`/tasks/${encodeURIComponent(task.id)}`}
      >
        <ArrowRight size={16} />
      </Link>
    </article>
  );
}

export default async function ProductionPage({
  searchParams,
}: {
  searchParams?: Promise<{ new?: string; goal?: string; view?: string; template_id?: string; template_version?: string; draft_id?: string; reply_conversation_id?: string; reply_turn_id?: string; reply_request_id?: string }>;
}) {
  const params = searchParams ? await searchParams : {};
  const [foundation, persistedTasks] = await Promise.all([
    loadFoundation(),
    loadCreationTasks(),
  ]);
  const creationTasks = persistedTasks ?? [];

  if (params.view === "automations") {
    return (
      <AppShell creationTasks={creationTasks} workspaceStatus={{ kind: "attention", label: "定时任务仅供只读预览，尚未接通执行" }}>
        <ScheduledTasksWorkspace />
      </AppShell>
    );
  }

  if (params.new === "1") {
    const initialGoal = params.goal ?? "";
    const replyValues = [params.reply_conversation_id, params.reply_turn_id, params.reply_request_id];
    const hasReplyOrigin = replyValues.some(Boolean);
    const isUuid = (value: string | undefined): value is string => Boolean(value && /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value));
    const replyOrigin = replyValues.every(isUuid) ? {
      conversation_id: params.reply_conversation_id!, turn_id: params.reply_turn_id!, request_id: params.reply_request_id!,
    } : null;
    if (hasReplyOrigin && !replyOrigin) return <AppShell creationTasks={creationTasks}><section role="alert"><h1>回复来源无法识别</h1><p>没有读取对话正文或创建任务；请从已完成的首页回复重新选择。</p></section></AppShell>;
    let template = null;
    if (params.template_id || params.template_version) {
      const templates = await listVideoTemplates().catch(() => null);
      template = templates?.items.find((item) => item.id === params.template_id && item.version === Number(params.template_version)) ?? null;
      if (!template) return <AppShell creationTasks={creationTasks}><section role="alert"><p>无法读取所选模板版本，请返回模板页重新选择。</p><Link href="/templates">返回视频模板</Link></section></AppShell>;
    }
    return (
      <AppShell creationTasks={creationTasks}>
        <div className="modern-queue">
          <div className="creation-plan-form__actions"><Link href="/production">返回任务列表</Link></div>
          <section>
            <CreateProductionTaskForm
              autoFocus={!initialGoal}
              initialGoal={initialGoal}
              draftId={params.draft_id}
              replyOrigin={replyOrigin}
              template={template}
            />
          </section>
        </div>
      </AppShell>
    );
  }

  if (!foundation) {
    return (
      <AppShell creationTasks={creationTasks}>
        <div className="production-offline">
          <AlertCircle size={21} />
          <div>
            <h1>无法打开创作任务</h1>
            <p>
              本地生产服务没有返回数据。修复运行环境后，已有任务会自动恢复。
            </p>
          </div>
          <Link href="/health">检查运行情况</Link>
        </div>
      </AppShell>
    );
  }

  const { readiness } = foundation;
  const taskStatusCounts = creationTasks.reduce<
    Record<CreationTask["stage_status"], number>
  >(
    (counts, task) => {
      counts[task.stage_status] += 1;
      return counts;
    },
    {
      not_started: 0,
      running: 0,
      waiting_for_user: 0,
      completed: 0,
      failed: 0,
    },
  );

  const defaultInspector = (
    <div className="queue-inspector">
      <div
        className={`readiness-callout${readiness.ready ? " is-ready" : " is-blocked"}`}
      >
        {readiness.ready ? <CheckCircle2 size={17} /> : <AlertCircle size={17} />}
        <div>
          <strong>{readiness.ready ? "可以开始制作" : "制作基础未就绪"}</strong>
          <span>
            {readiness.ready
              ? "档案、声音、来源和素材已满足门槛。"
              : readiness.blockers[0]?.message}
          </span>
        </div>
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
      {!readiness.ready ? (
        <div className="queue-blockers">
          <h3>当前阻断</h3>
          {readiness.blockers.map((blocker) => (
            <p key={blocker.code}>{blocker.message}</p>
          ))}
        </div>
      ) : null}
      <div className="queue-inspector__links">
        <Link href="/library">管理素材库 <ArrowRight size={13} /></Link>
        <Link href="/settings">制作设置 <ArrowRight size={13} /></Link>
      </div>
    </div>
  );

  return (
    <AppShell
      creationTasks={creationTasks}
      inspector={defaultInspector}
      inspectorTitle="任务运行情况"
      workspaceStatus={readiness.ready
        ? { kind: "connected", label: "工作区已就绪" }
        : { kind: "attention", label: "工作区存在阻断" }}
    >
      <div className="modern-queue">
        {!readiness.ready ? (
          <section className="queue-gate queue-gate--blocked">
            <AlertCircle size={20} />
            <div>
              <strong>开始制作前还有 {readiness.blockers.length} 项需要处理</strong>
              <p>{readiness.blockers[0]?.message}</p>
            </div>
            <Link href={readiness.approved_segment_count ? "/settings" : "/library"}>
              {readiness.approved_segment_count ? "打开制作设置" : "审核素材"}
              <ArrowRight size={14} />
            </Link>
          </section>
        ) : (
          <section className="queue-gate queue-gate--ready">
            <CheckCircle2 size={20} />
            <div>
              <strong>工作区已就绪</strong>
              <p>可以创建新任务，或继续下面尚未完成的任务。</p>
            </div>
            <Link href="/?new=1">开始制作<ArrowRight size={14} /></Link>
          </section>
        )}

        <section className="queue-section">
          <header>
            <h2>最近任务</h2>
            <Link className="modern-primary-button" href="/?new=1">
              <Plus size={16} />新建视频
            </Link>
          </header>
          {creationTasks.length ? (
            <div className="queue-list">
              {creationTasks.map((task) => <CreationTaskRow key={task.id} task={task} />)}
            </div>
          ) : (
            <div className="queue-empty">
              <FolderKanban size={22} />
              <div>
                <strong>还没有创作任务</strong>
                <p>检查当前可用内容后，创建第一个创作任务。</p>
              </div>
              <Link href="/?new=1">开始制作</Link>
            </div>
          )}
        </section>

        <section className="queue-secondary-links">
          <Link href="/library">
            <Film size={16} />
            <span><strong>素材库</strong><small>识别、审核和管理补充画面</small></span>
            <ArrowRight size={14} />
          </Link>
          <Link href="/settings">
            <Settings2 size={16} />
            <span><strong>制作设置</strong><small>品牌、声音、模型与存储</small></span>
            <ArrowRight size={14} />
          </Link>
        </section>
      </div>
    </AppShell>
  );
}
