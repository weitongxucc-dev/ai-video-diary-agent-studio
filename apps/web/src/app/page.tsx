import { AppShell } from "@/components/AppShell";
import { PixelIcon } from "@/components/PixelIcon";
import { PixelMascot } from "@/components/PixelMascot";
import { getSystemStatus, getTodayEpisode } from "@/lib/api";
import type { Episode, SystemStatus } from "@/lib/types";

const HOME_API_TIMEOUT_MS = 4000;

async function safelyGetTodayEpisode(): Promise<Episode | null> {
  try {
    return await getTodayEpisode({ timeoutMs: HOME_API_TIMEOUT_MS });
  } catch {
    return null;
  }
}

async function safelyGetSystemStatus(): Promise<SystemStatus | null> {
  try {
    return await getSystemStatus({ timeoutMs: HOME_API_TIMEOUT_MS });
  } catch {
    return null;
  }
}

export default async function HomePage() {
  const [episode, systemStatus] = await Promise.all([
    safelyGetTodayEpisode(),
    safelyGetSystemStatus(),
  ]);
  const currentTopic = episode?.topics[0] ?? null;
  const selectedTopic = episode?.topics.find(
    (topic) => topic.id === episode.selected_topic_id,
  );
  const topicForWorkbench = selectedTopic ?? currentTopic;
  const hasApprovedTopic = Boolean(episode?.selected_topic_id);
  const obsidianConnected = Boolean(systemStatus?.obsidian.exists);
  const radarMaterialCount = systemStatus?.hermes_radar.total_count ?? 0;
  const hasMaterialSource = obsidianConnected || radarMaterialCount > 0;
  const materialMetricValue = radarMaterialCount > 0
    ? String(radarMaterialCount)
    : obsidianConnected
      ? "已连接"
      : "0";
  const materialDetail = systemStatus
    ? `${obsidianConnected ? "Obsidian 已连接" : "Obsidian 待连接"} · 雷达 ${radarMaterialCount} 条`
    : "等待连接本地素材";
  const setupRequired = Boolean(
    !systemStatus?.llm.configured ||
    !systemStatus?.obsidian.exists ||
    !systemStatus?.video.inbox_exists ||
    !systemStatus?.video.output_exists ||
    !systemStatus?.video.ffmpeg_available,
  );
  const primaryFlowHref = setupRequired
    ? "/setup"
    : topicForWorkbench && hasApprovedTopic
      ? "/script"
      : "/topics";
  const primaryFlowLabel = topicForWorkbench
    ? setupRequired
      ? "进入配置向导"
      : hasApprovedTopic
      ? "查看口播稿 →"
      : "继续今天的流程 →"
    : setupRequired
      ? "进入配置向导"
      : "去生成今日选题";
  const scriptReady = Boolean(episode?.script);
  const processedVideoReady = Boolean(episode?.video_processed_file_name);
  const draftReady = Boolean(episode?.draft_package_name);
  const stageItems = [
    {
      code: "STAGE 01",
      icon: "ingest" as const,
      label: "信息摄入",
      state: hasMaterialSource ? "已连接" : "待处理",
      status: hasMaterialSource ? "completed" : "pending",
    },
    {
      code: "STAGE 02",
      icon: "doc" as const,
      label: "选题生成",
      state: episode?.topics.length ? "进行中" : "待处理",
      status: hasApprovedTopic ? "completed" : "active",
    },
    {
      code: "STAGE 03",
      icon: "check" as const,
      label: "选题复核",
      state: hasApprovedTopic ? "已完成" : "待处理",
      status: hasApprovedTopic ? "completed" : "pending",
    },
    {
      code: "STAGE 04",
      icon: "script" as const,
      label: "口播稿",
      state: scriptReady ? "已生成" : "待处理",
      status: scriptReady ? "completed" : hasApprovedTopic ? "active" : "pending",
    },
    {
      code: "STAGE 05",
      icon: "video" as const,
      label: "视频等待",
      state: (systemStatus?.video.pending_count ?? 0) > 0 ? "有素材" : "待处理",
      status: (systemStatus?.video.pending_count ?? 0) > 0 ? "active" : "pending",
    },
    {
      code: "STAGE 06",
      icon: "agent" as const,
      label: "AI处理",
      state: processedVideoReady ? "已完成" : "待处理",
      status: processedVideoReady ? "completed" : scriptReady ? "active" : "pending",
    },
    {
      code: "STAGE 07",
      icon: "cover" as const,
      label: "封面",
      state: scriptReady ? "可生成" : "待处理",
      status: scriptReady && !processedVideoReady ? "active" : processedVideoReady ? "completed" : "pending",
    },
    {
      code: "STAGE 08",
      icon: "clip" as const,
      label: "剪映草稿",
      state: draftReady ? "已生成" : "待处理",
      status: draftReady ? "completed" : processedVideoReady ? "active" : "pending",
    },
    {
      code: "STAGE 09",
      icon: "publish" as const,
      label: "发布反馈",
      state: episode?.status === "published" ? "已发布" : "待处理",
      status: episode?.status === "published" ? "completed" : draftReady ? "active" : "pending",
    },
  ];
  const completedStages = stageItems.filter((stage) => stage.status === "completed").length;
  const progressPercent = Math.round((completedStages / stageItems.length) * 100);
  const currentStage =
    stageItems.find((stage) => stage.status === "active") ??
    stageItems.find((stage) => stage.status === "pending") ??
    stageItems[stageItems.length - 1];
  const queueItems = [
    ["1", "复核选题", hasApprovedTopic ? "已完成" : "等待人工确认"],
    ["2", "生成口播稿", scriptReady ? "已完成" : hasApprovedTopic ? "准备写入稿件记忆" : "等待选题"],
    ["3", "视频处理", processedVideoReady ? "已处理" : "转码 / 合并 / 降噪 / 剪辑"],
    ["4", "写入反馈学习", episode?.status === "published" ? "已登记反馈" : "总结复盘，持续优化"],
  ];

  return (
    <AppShell>
      <div className="page-workstation workbench-page">
        <header className="page-workstation-head">
          <div className="workbench-title-row">
            <div>
              <h1>今日工作台</h1>
              <p>从选题、口播、视频处理到发布反馈，按今日生产线推进。</p>
            </div>
            <div className="workbench-episode-meta" aria-label="Episode 信息">
              <span>Episode #{episode?.day_number ?? "--"}</span>
              <span>今日目标：完成 1 条视频并发布</span>
            </div>
          </div>
        </header>

        <section className="workbench-stage-strip" aria-label="今日生产阶段">
          {stageItems.map((stage) => (
            <article className={`workbench-stage ${stage.status}`} key={stage.code}>
              <span className="stage-code">{stage.code}</span>
              <PixelIcon name={stage.icon} />
              <strong>{stage.label}</strong>
              <span className="status-badge">{stage.state}</span>
            </article>
          ))}
        </section>

        <section className="workbench-three-column" aria-label="今日生产控制台">
          <article className="workbench-task-panel retro-panel-secondary">
            <h2>今天你要做什么</h2>
            <ul className="workbench-task-list">
              <li>
                <span className={`status-dot ${hasApprovedTopic ? "success" : "processing"}`} />
                <span>等待确认 {hasApprovedTopic ? "0" : "1"} 个选题</span>
              </li>
              <li>
                <span className={`status-dot ${scriptReady ? "success" : "processing"}`} />
                <span>生成并优化 800-1000 字口播稿</span>
              </li>
              <li>
                <span className={`status-dot ${processedVideoReady ? "success" : "partial"}`} />
                <span>处理并剪辑 1 条视频</span>
              </li>
            </ul>
            <div className="workbench-progress-box">
              <strong>今日进度概览</strong>
              <span className="workbench-progress-track">
                <span className="workbench-progress-fill" style={{ width: `${progressPercent}%` }} />
              </span>
              <span>完成 {completedStages} / {stageItems.length} 阶段 · {progressPercent}%</span>
            </div>
          </article>

          <article className="workbench-queue-panel retro-panel-secondary">
            <h2>今日队列</h2>
            <ol className="workbench-queue-list">
              {queueItems.map(([index, label, state]) => (
                <li key={index}>
                  <span>[{index}]</span>
                  <strong>{label}</strong>
                  <em>{state}</em>
                </li>
              ))}
            </ol>
            <a className="pixel-button" href={primaryFlowHref}>
              {primaryFlowLabel}
            </a>
          </article>

          <aside className="workbench-agent-panel inspector-panel">
            <header className="agent-compact-head">
              <h2><PixelIcon name="agent" /> Agent 状态</h2>
              <span className="status-badge"><span className="status-dot processing" /> {currentStage.label}</span>
            </header>
            <div className="agent-compact-diagnostic">
              <div className="agent-robot compact">
                <PixelMascot />
              </div>
              <dl>
                <div>
                  <dt>当前阶段</dt>
                  <dd>{currentStage.code} · {currentStage.state}</dd>
                </div>
                <div>
                  <dt>今日完成度</dt>
                  <dd>{completedStages}/{stageItems.length} · {progressPercent}%</dd>
                </div>
                <div>
                  <dt>下一步</dt>
                  <dd>{primaryFlowLabel.replace(" →", "")}</dd>
                </div>
              </dl>
            </div>
            <ul className="workbench-agent-list">
              <li>
                <span className={`status-dot ${systemStatus?.llm.configured ? "success" : "missing"}`} />
                <span>模型：{systemStatus?.llm.model ?? "DeepSeek-v3"}</span>
              </li>
              <li>
                <span className={`status-dot ${systemStatus?.obsidian.exists ? "success" : "missing"}`} />
                <span>记忆：{systemStatus?.obsidian.exists ? "已连接 Obsidian" : "待连接 Obsidian"}</span>
              </li>
              <li>
                <span className={`status-dot ${hasMaterialSource ? "success" : "partial"}`} />
                <span>素材：{materialDetail}</span>
              </li>
              <li>
                <span className={`status-dot ${systemStatus?.video.ffmpeg_available ? "success" : "missing"}`} />
                <span>FFmpeg：{systemStatus?.video.ffmpeg_available ? "可用" : "待配置"}</span>
              </li>
            </ul>
            <a className="pixel-button secondary compact" href={primaryFlowHref}>
              打开当前任务
            </a>
          </aside>
        </section>

        <section className="workbench-light-metrics" aria-label="今日数据概览">
          <div>
            <strong>{materialMetricValue}</strong>
            <span>真实素材</span>
            <small>{materialDetail}</small>
          </div>
          <div>
            <strong>{episode?.topics.length ?? 0}</strong>
            <span>生成选题</span>
            <small>待复核 {episode?.topics.length ?? 0} 个</small>
          </div>
          <div>
            <strong>{hasApprovedTopic ? "1" : "0"}</strong>
            <span>已确认</span>
            <small>{hasApprovedTopic ? "已通过人工确认" : "等待人工确认"}</small>
          </div>
          <div>
            <strong>{systemStatus?.video.pending_count ?? 0}</strong>
            <span>待处理视频</span>
            <small>{hasApprovedTopic ? "等待录制素材" : "等待口播稿"}</small>
          </div>
        </section>

        <section className="current-topic retro-panel">
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
    </AppShell>
  );
}
