import { redirect } from "next/navigation";
import { AppShell } from "@/components/AppShell";
import { EpisodeProgress } from "@/components/EpisodeProgress";
import { MetricCard } from "@/components/MetricCard";
import { PixelIcon } from "@/components/PixelIcon";
import { PixelMascot } from "@/components/PixelMascot";
import { PixelAgentPanel } from "@/components/PixelAgentPanel";
import {
  getSetupPreflight,
  getSystemStatus,
  getTodayEpisode,
} from "@/lib/api.server";
import type { Episode, SystemStatus } from "@/lib/types";

async function safelyGetTodayEpisode(): Promise<Episode | null> {
  try {
    return await getTodayEpisode();
  } catch {
    return null;
  }
}

async function safelyGetSystemStatus(): Promise<SystemStatus | null> {
  try {
    return await getSystemStatus();
  } catch {
    return null;
  }
}

async function ensureConfigured(): Promise<void> {
  let ready = false;
  try {
    const preflight = await getSetupPreflight();
    ready = preflight.overall === "ready";
  } catch {
    redirect("/onboarding");
  }
  if (!ready) redirect("/onboarding");
}

export default async function HomePage() {
  await ensureConfigured();
  const [episode, systemStatus] = await Promise.all([
    safelyGetTodayEpisode(),
    safelyGetSystemStatus(),
  ]);
  const currentTopic = episode?.topics?.[0] ?? null;
  const selectedTopic = episode?.topics?.find(
    (topic) => topic.id === episode?.selected_topic_id,
  ) ?? null;
  const topicForWorkbench = selectedTopic ?? currentTopic;
  const hasApprovedTopic = Boolean(episode?.selected_topic_id);
  const materialCount =
    (systemStatus?.obsidian.markdown_count ?? 0) +
    (systemStatus?.hermes_radar.total_count ?? 0);
  const primaryFlowHref = topicForWorkbench && hasApprovedTopic ? "/script" : "/topics";
  const primaryFlowLabel = topicForWorkbench
    ? hasApprovedTopic
      ? "查看口播稿 →"
      : "继续今天的流程 →"
    : "去生成今日选题";

  return (
    <AppShell>
      <div className="workbench-grid">
        <div className="workbench-main">
          <EpisodeProgress episode={episode} />

          <section className="task-banner pixel-card">
            <div className="task-terminal">
              <h1>今天你要做什么</h1>
              <p>等待确认 {hasApprovedTopic ? "0" : "1"} 个选题</p>
              <p>Agent 将在确认后生成口播稿</p>
            </div>

            <div className="task-queue" aria-label="今日队列">
              <strong>今日队列</strong>
              <ol>
                <li><span>[1]</span> 复核选题</li>
                <li><span>[2]</span> 生成口播稿</li>
                <li><span>[3]</span> 视频处理</li>
                <li><span>[4]</span> 写入反馈学习</li>
              </ol>
            </div>

            <div className="task-agent-next">
              <div className="pixel-person">
                <PixelMascot variant="creator" />
              </div>
              <div className="task-agent-copy">
                <span>DeepSeek：{hasApprovedTopic ? "准备生成口播稿" : "等待选题确认"}</span>
                <span>Hermes：准备写入偏好记忆</span>
              </div>
              <a className="pixel-button" href={primaryFlowHref}>
                {primaryFlowLabel}
              </a>
            </div>
          </section>

          <section className="metric-grid">
            <MetricCard icon="folder" value={String(materialCount)} label="真实素材" tone="blue" detail="今日新增 12 条" progress={75} />
            <MetricCard icon="spark" value={String(episode?.topics.length ?? 0)} label="生成选题" tone="green" detail={`待复核 ${episode?.topics.length ?? 0} 个`} progress={50} />
            <MetricCard icon="check" value={hasApprovedTopic ? "1" : "0"} label="已确认" tone="purple" detail={hasApprovedTopic ? "已通过人工确认" : "等待人工确认"} progress={hasApprovedTopic ? 100 : 0} />
            <MetricCard icon="clip" value={String(systemStatus?.video.pending_count ?? 0)} label="待处理视频" tone="orange" detail={hasApprovedTopic ? "等待录制素材" : "等待口播稿"} progress={25} />
          </section>

          <section className="current-topic pixel-card">
            <div className="current-topic-main">
              <div>
                <span className="section-kicker">当前选题</span>
                <h2>
                  {topicForWorkbench?.title ??
                    (episode ? "还没有生成今日选题" : "暂时无法连接今日工作台")}
                </h2>
                <p>
                  {topicForWorkbench?.core_viewpoint ??
                    (episode
                      ? "进入选题池生成今天的候选选题。"
                      : "API 暂时不可用，稍后再试即可。你也可以先进入选题池准备今天内容。")}
                </p>
              </div>
              <div className="topic-actions">
                {topicForWorkbench ? (
                  <>
                    <a className="pixel-button secondary" href="/topics">
                      查看详情
                    </a>
                    <a className="pixel-button" href={hasApprovedTopic ? "/script" : "/topics"}>
                      {hasApprovedTopic ? "查看口播稿" : "去复核选题"}
                    </a>
                  </>
                ) : (
                  <a className="pixel-button" href="/topics">
                    去生成今日选题
                  </a>
                )}
              </div>
            </div>

            <div className="topic-review-panel" aria-label="选题复核清单">
              <div className="topic-review-meta">
                <strong>选题元信息</strong>
                <dl>
                  <div><dt>素材来源</dt><dd>Obsidian / Hermes / 外链</dd></div>
                  <div><dt>选题类型</dt><dd>技术反思</dd></div>
                  <div><dt>推荐理由</dt><dd>近期内容方向匹配</dd></div>
                  <div><dt>复核状态</dt><dd>{hasApprovedTopic ? "已确认" : "等待人工确认"}</dd></div>
                </dl>
              </div>
              <ul className="topic-checklist">
                <li><PixelIcon name={hasApprovedTopic ? "check" : "note"} /> 是否有真实案例</li>
                <li><PixelIcon name="note" /> 是否适合 60 秒口播</li>
                <li><PixelIcon name="note" /> 是否有明确冲突观点</li>
                <li><PixelIcon name="note" /> 是否适合今天发布</li>
              </ul>
            </div>
          </section>
        </div>
        <PixelAgentPanel episode={episode} systemStatus={systemStatus} />
      </div>
    </AppShell>
  );
}
