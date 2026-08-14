import Link from "next/link";
import {
  archiveSourcesAction,
  processAdHocVideoBatchAction,
  processAdHocVideoAction,
  processPendingVideoAction,
  processVideoAndPackageAction,
} from "@/app/actions";
import { AppShell } from "@/components/AppShell";
import { PixelIcon } from "@/components/PixelIcon";
import { ProcessingRefresh } from "@/components/ProcessingRefresh";
import {
  getEpisodeRetakeReport,
  getLatestRetakeReport,
  getPendingVideos,
  getProductionChain,
  getSourceFileStates,
  getSystemStatus,
  getTodayEpisode,
} from "@/lib/api.server";
import type {
  Episode,
  PendingVideo,
  ProductionChain,
  ProductionChainStep,
  ProductionMode,
  RetakeReport,
  SourceFileState,
  SourceFileStates,
  SourceFileStateEntry,
  SystemStatus,
} from "@/lib/types";

export const dynamic = "force-dynamic";

type VideoLoadState = {
  videos: PendingVideo[];
  systemStatus: SystemStatus | null;
  episode: Episode | null;
  productionChain: ProductionChain | null;
  sourceFileStates: SourceFileStates | null;
  retakeReport: RetakeReport | null;
  isLatestRetakeReport: boolean;
  offline: boolean;
};

async function loadVideoState(): Promise<VideoLoadState> {
  try {
    const [videos, systemStatus, episode] = await Promise.all([
      getPendingVideos(),
      getSystemStatus(),
      getTodayEpisode(),
    ]);

    let productionChain: ProductionChain | null = null;
    let sourceFileStates: SourceFileStates | null = null;
    if (episode) {
      try {
        productionChain = await getProductionChain(episode.id);
      } catch {
        productionChain = null;
      }
      try {
        sourceFileStates = await getSourceFileStates(episode.id);
      } catch {
        sourceFileStates = null;
      }
    }

    let retakeReport: RetakeReport | null = null;
    let isLatestRetakeReport = false;
    try {
      if (episode && episode.video_dedup_report_path) {
        retakeReport = await getEpisodeRetakeReport(episode.id);
      } else {
        retakeReport = await getLatestRetakeReport();
        isLatestRetakeReport = true;
      }
    } catch {
      retakeReport = null;
    }

    return {
      videos,
      systemStatus,
      episode,
      productionChain,
      sourceFileStates,
      retakeReport,
      isLatestRetakeReport,
      offline: false,
    };
  } catch {
    return {
      videos: [],
      systemStatus: null,
      episode: null,
      productionChain: null,
      sourceFileStates: null,
      retakeReport: null,
      isLatestRetakeReport: false,
      offline: true,
    };
  }
}

const PRODUCTION_MODE_CARDS: {
  mode: ProductionMode;
  title: string;
  description: string;
}[] = [
  {
    mode: "bilibili",
    title: "B站",
    description: "适合中长视频，横屏 16:9，内容深度优先。",
  },
  {
    mode: "youtube",
    title: "YouTube",
    description: "全球分发，横屏 16:9，SEO 标题优化。",
  },
  {
    mode: "shorts",
    title: "短视频",
    description: "竖屏 9:16，快节奏剪辑，适合抖音/小红书/B站 Shorts。",
  },
  {
    mode: "douyin",
    title: "抖音",
    description: "竖屏 9:16，强节奏感，前 3 秒钩子优先。",
  },
  {
    mode: "xiaohongshu",
    title: "小红书",
    description: "竖屏 3:4 或 9:16，封面文案驱动，社区氛围优先。",
  },
];

const PRODUCTION_CHAIN_STAGE_LABELS: Record<string, string> = {
  upload: "上传素材",
  dedup: "重复口播检查",
  cut: "剪辑计划",
  subtitle: "字幕生成",
  cover: "封面生成",
  package: "素材包组装",
  archive: "源文件归档",
};

const SOURCE_FILE_STATE_LABELS: Record<SourceFileState, string> = {
  pending: "待处理",
  processing: "处理中",
  processed: "已处理",
  archived: "已归档",
  skipped: "已跳过",
};

function formatFileSize(sizeBytes: number): string {
  if (sizeBytes < 1024 * 1024) {
    return `${Math.max(1, Math.round(sizeBytes / 1024))} KB`;
  }

  return `${(sizeBytes / 1024 / 1024).toFixed(1)} MB`;
}

function formatModifiedAt(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", {
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    month: "2-digit",
    timeZone: "Asia/Shanghai",
  }).format(new Date(value));
}

function formatJobTime(value: string | null): string {
  if (!value) {
    return "--";
  }

  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    timeZone: "Asia/Shanghai",
  }).format(new Date(value));
}

function formatTimeline(seconds: number): string {
  const safeSeconds = Number.isFinite(seconds) ? Math.max(0, seconds) : 0;
  const minutes = Math.floor(safeSeconds / 60);
  const remainingSeconds = (safeSeconds % 60).toFixed(1).padStart(4, "0");

  return `${String(minutes).padStart(2, "0")}:${remainingSeconds}`;
}

function getVideoJobTitle(episode: Episode): string {
  const labels: Record<Episode["video_job_status"], string> = {
    completed: "视频任务已完成",
    drafting: "正在生成草稿素材包",
    failed: "视频任务失败",
    idle: "暂无后台视频任务",
    interrupted: "后台任务已中断",
    processing: "正在处理视频",
  };

  return labels[episode.video_job_status];
}

function shouldShowVideoJob(episode: Episode | null): episode is Episode {
  return Boolean(episode && episode.video_job_status !== "idle");
}

function formatRetakeReason(reason: string): string {
  const labels: Record<string, string> = {
    interrupted_take: "中断后重录，保留更完整表达",
    possible_retake: "疑似重复口播，请人工确认",
    repeated_take: "重复口播，保留后一次表达",
  };

  return labels[reason] ?? `检测标记：${reason}`;
}

function getRetakeSummary(report: RetakeReport): string {
  if (report.status === "applied") {
    return `已自动剪掉 ${report.auto_removed.length} 段高置信度重复口播。`;
  }

  if (report.status === "no_changes") {
    return "未发现高置信度重录。";
  }

  return report.message ?? (report.status === "skipped" ? "检查已跳过。" : "检查失败。");
}

function getBaseName(path: string): string {
  return path.split(/[\\/]/).pop() ?? path;
}

function getStageLabel(stage: string): string {
  return PRODUCTION_CHAIN_STAGE_LABELS[stage] ?? stage;
}

function getChainStepTitle(step: ProductionChainStep): string {
  const label = getStageLabel(step.stage);
  if (step.output) {
    return `${label} · ${getBaseName(step.output)}`;
  }
  return label;
}

function getSourceFileStateLabel(state: SourceFileState): string {
  return SOURCE_FILE_STATE_LABELS[state];
}

function getProductionChainSummary(chain: ProductionChain | null): string {
  if (!chain) {
    return "尚未拉取生产链路状态。";
  }

  if (chain.steps.length === 0) {
    return "当前 Episode 暂无生产链路步骤。";
  }

  const completedCount = chain.steps.filter(
    (step) => step.status === "completed",
  ).length;
  const failedCount = chain.steps.filter(
    (step) => step.status === "failed",
  ).length;
  const processingCount = chain.steps.filter(
    (step) => step.status === "processing",
  ).length;

  if (failedCount > 0) {
    return `共 ${chain.steps.length} 步：已完成 ${completedCount}，处理中 ${processingCount}，失败 ${failedCount}。`;
  }
  if (processingCount > 0) {
    return `共 ${chain.steps.length} 步：已完成 ${completedCount}，处理中 ${processingCount}。`;
  }
  return `共 ${chain.steps.length} 步：已完成 ${completedCount}。`;
}

function getSourceFileStatesSummary(states: SourceFileStates | null): string {
  if (!states) {
    return "尚未拉取源文件状态。";
  }
  if (states.files.length === 0) {
    return "当前 Episode 没有源文件记录。";
  }

  const counts: Record<SourceFileState, number> = {
    pending: 0,
    processing: 0,
    processed: 0,
    archived: 0,
    skipped: 0,
  };
  for (const file of states.files) {
    counts[file.state] += 1;
  }

  const summary: string[] = [];
  (Object.keys(counts) as SourceFileState[]).forEach((key) => {
    if (counts[key] > 0) {
      summary.push(`${SOURCE_FILE_STATE_LABELS[key]} ${counts[key]}`);
    }
  });
  return `共 ${states.files.length} 个源文件：${summary.join(" · ")}。`;
}

function getArchivableFiles(files: SourceFileStateEntry[]): SourceFileStateEntry[] {
  return files.filter((file) => file.state === "processed");
}

export default async function VideoPage() {
  const {
    videos,
    systemStatus,
    episode,
    productionChain,
    sourceFileStates,
    retakeReport,
    isLatestRetakeReport,
    offline,
  } = await loadVideoState();
  const videoStatus = systemStatus?.video;
  const isVideoProcessing = episode?.status === "video_processing";
  const canProcess = Boolean(videoStatus?.ffmpeg_available) && !isVideoProcessing;
  const canCreateFullPackage = canProcess && Boolean(episode?.script);
  const episodeId = episode?.id;
  const canCreateAdHocPackage = canProcess && Boolean(episodeId);
  const currentProductionMode = episode?.production_mode ?? null;
  const archivableFiles = sourceFileStates
    ? getArchivableFiles(sourceFileStates.files)
    : [];
  const hasArchivableFiles = archivableFiles.length > 0;

  return (
    <AppShell>
      <div className="video-page">
        <section className="video-main pixel-card">
          <div className="video-header">
            <div>
              <span className="section-kicker">视频处理</span>
              <h1>待处理视频</h1>
              <p>
                {offline
                  ? "暂时无法连接本地 API。"
                  : videos.length > 0
                    ? `发现 ${videos.length} 个待处理视频。`
                    : "待处理文件夹暂无视频。"}
              </p>
            </div>
            <Link className="pixel-button secondary" href="/">
              返回工作台
            </Link>
          </div>

          <section className="video-mode-strip" aria-label="视频生产模式">
            {PRODUCTION_MODE_CARDS.map((card) => {
              const isActive = currentProductionMode === card.mode;
              return (
                <div
                  aria-current={isActive ? "true" : undefined}
                  className={`video-mode-card ${isActive ? "active" : ""}`}
                  data-mode={card.mode}
                  key={card.mode}
                >
                  <strong>{card.title}</strong>
                  <span>{card.description}</span>
                  {isActive ? (
                    <span className="video-mode-tag">当前模式</span>
                  ) : null}
                </div>
              );
            })}
          </section>

          {episodeId ? (
            <section
              aria-label="生产链路状态"
              className="video-chain"
            >
              <div className="video-chain-heading">
                <strong>生产链路</strong>
                <span>{getProductionChainSummary(productionChain)}</span>
              </div>
              {productionChain && productionChain.steps.length > 0 ? (
                <ol className="video-chain-list">
                  {productionChain.steps.map((step, index) => (
                    <li
                      className={`video-chain-row ${step.status}`}
                      key={`${step.stage}-${index}`}
                    >
                      <span className="video-chain-index">{index + 1}</span>
                      <span className="video-chain-stage">
                        {getChainStepTitle(step)}
                      </span>
                      <span className={`video-chain-status ${step.status}`}>
                        {step.status === "pending"
                          ? "待处理"
                          : step.status === "processing"
                            ? "处理中"
                            : step.status === "completed"
                              ? "已完成"
                              : step.status === "failed"
                                ? "失败"
                                : "已跳过"}
                      </span>
                    </li>
                  ))}
                </ol>
              ) : (
                <div className="video-chain-empty">
                  <span>暂无生产链路步骤，先选择生产模式或处理一条视频。</span>
                </div>
              )}
            </section>
          ) : null}

          <form
            action={processAdHocVideoBatchAction}
            className="video-batch-toolbar"
            id="ad-hoc-video-batch-form"
          >
            {episodeId ? (
              <input name="episode_id" type="hidden" value={episodeId} />
            ) : null}
            <div>
              <strong>多段拼剪</strong>
              <span>勾选至少两条片段，用序号调整合并顺序。</span>
            </div>
            <button
              className="pixel-button secondary"
              disabled={!canCreateAdHocPackage || videos.length < 2}
              type="submit"
            >
              合并处理已选片段
            </button>
          </form>
          {isVideoProcessing ? <ProcessingRefresh /> : null}
          {shouldShowVideoJob(episode) ? (
            <section
              aria-label="视频任务状态"
              className={`video-job-status ${episode.video_job_status}`}
            >
              <div>
                <strong>{getVideoJobTitle(episode)}</strong>
                <span>{episode.video_job_message ?? "等待后台更新状态"}</span>
              </div>
              <div className="video-job-times">
                <span>开始 {formatJobTime(episode.video_job_started_at)}</span>
                <span>结束 {formatJobTime(episode.video_job_finished_at)}</span>
              </div>
              {episode.video_job_error ? (
                <code>{episode.video_job_error}</code>
              ) : null}
            </section>
          ) : null}
          {retakeReport ? (
            <section className="retake-report" aria-label="重复口播检查">
              <div className="retake-report-heading">
                <strong>
                  {isLatestRetakeReport ? "最近一次重复口播检查" : "重复口播检查"}
                </strong>
                <span>{getRetakeSummary(retakeReport)}</span>
              </div>
              {retakeReport.auto_removed.length > 0 ? (
                <div className="retake-report-list">
                  {retakeReport.auto_removed.map((item) => (
                    <div
                      className="retake-report-row"
                      key={`${item.remove.start}-${item.remove.end}`}
                    >
                      <span>
                        剪掉 {formatTimeline(item.remove.start)} -{" "}
                        {formatTimeline(item.remove.end)}
                      </span>
                      <span>
                        保留 {formatTimeline(item.keep.start)} -{" "}
                        {formatTimeline(item.keep.end)}
                      </span>
                      <span>{formatRetakeReason(item.reason)}</span>
                      <strong>{Math.round(item.confidence * 100)}%</strong>
                    </div>
                  ))}
                </div>
              ) : null}
              {retakeReport.review_only.length > 0 ? (
                <div className="retake-report-review">
                  <p>
                    另有 {retakeReport.review_only.length} 段需要人工复核，未自动删除。
                  </p>
                  <div className="retake-report-list">
                    {retakeReport.review_only.map((item) => (
                      <div
                        className="retake-report-row review"
                        key={`review-${item.remove.start}-${item.remove.end}`}
                      >
                        <span>
                          复核 {formatTimeline(item.remove.start)} -{" "}
                          {formatTimeline(item.remove.end)}
                        </span>
                        <span>
                          对照 {formatTimeline(item.keep.start)} -{" "}
                          {formatTimeline(item.keep.end)}
                        </span>
                        <span>{formatRetakeReason(item.reason)}</span>
                        <strong>{Math.round(item.confidence * 100)}%</strong>
                      </div>
                    ))}
                  </div>
                </div>
              ) : null}
              {retakeReport.status === "applied" && retakeReport.output_video ? (
                <p className="retake-report-output">
                  新成片 <strong>{getBaseName(retakeReport.output_video)}</strong>
                </p>
              ) : null}
            </section>
          ) : null}
          {episode?.video_edit_plan_path ? (
            <div className="video-batch-toolbar">
              <div>
                <strong>可视化剪辑计划已生成</strong>
                <span>进入工作台浏览视频、查看片段、确认剪映草稿导入。</span>
              </div>
              <Link className="pixel-button" href="/edit-plan">
                查看剪辑计划
              </Link>
            </div>
          ) : null}

          <div className="video-list" aria-label="待处理视频列表">
            {videos.length > 0 ? (
              videos.map((video, index) => (
                <article className="video-file-row" key={video.file_name}>
                  <label className="video-batch-picker" title="加入组合视频">
                    <input
                      form="ad-hoc-video-batch-form"
                      name="file_names"
                      type="checkbox"
                      value={video.file_name}
                    />
                    <input
                      aria-label={`${video.file_name} 的合并顺序`}
                      className="video-batch-order"
                      defaultValue={index + 1}
                      form="ad-hoc-video-batch-form"
                      max={videos.length || 99}
                      min="1"
                      name={`order:${video.file_name}`}
                      required
                      title="合并顺序 1-N"
                      type="number"
                    />
                  </label>
                  <PixelIcon name="video" />
                  <div>
                    <strong>{video.file_name}</strong>
                    <span>
                      {formatFileSize(video.size_bytes)} · {formatModifiedAt(video.modified_at)}
                    </span>
                    <code>{video.path}</code>
                  </div>
                  <div className="video-actions">
                    <form action={processPendingVideoAction}>
                      <input name="file_name" type="hidden" value={video.file_name} />
                      {episodeId ? (
                        <input name="episode_id" type="hidden" value={episodeId} />
                      ) : null}
                      <button
                        className="pixel-button secondary"
                        disabled={!canProcess}
                        title={canProcess ? "生成加速和响度标准化预览" : "ffmpeg 不可用"}
                        type="submit"
                      >
                        预处理
                      </button>
                    </form>
                    <form action={processVideoAndPackageAction}>
                      <input name="file_name" type="hidden" value={video.file_name} />
                      {episodeId ? (
                        <input name="episode_id" type="hidden" value={episodeId} />
                      ) : null}
                      <button
                        className="pixel-button"
                        disabled={!canCreateFullPackage}
                        title={
                          canCreateFullPackage
                            ? "生成预处理视频、SRT字幕、封面和素材包"
                            : "先生成口播稿，才能生成带字幕和封面的完整包"
                        }
                        type="submit"
                      >
                        完整包
                      </button>
                    </form>
                    <form action={processAdHocVideoAction}>
                      <input name="file_name" type="hidden" value={video.file_name} />
                      {episodeId ? (
                        <input name="episode_id" type="hidden" value={episodeId} />
                      ) : null}
                      <button
                        className="pixel-button secondary"
                        disabled={!canCreateAdHocPackage}
                        title={
                          canCreateAdHocPackage
                            ? "跳过选题和口播稿，直接按已录视频生成剪辑素材包"
                            : "需要本地 API 创建今日 Episode"
                        }
                        type="submit"
                      >
                        直接处理已录视频
                      </button>
                    </form>
                  </div>
                </article>
              ))
            ) : (
              <div className="video-empty-state">
                <PixelIcon name="upload" />
                <div>
                  <strong>{offline ? "API 未连接" : "没有待处理视频"}</strong>
                  <p>
                    {videoStatus?.inbox_path ??
                      "连接恢复后，这里会显示本地待处理文件夹。"}
                  </p>
                </div>
              </div>
            )}
          </div>

          <section
            aria-label="源文件状态"
            className="video-source-states"
          >
            <div className="video-source-states-heading">
              <strong>源文件状态</strong>
              <span>{getSourceFileStatesSummary(sourceFileStates)}</span>
            </div>
            {episodeId && hasArchivableFiles ? (
              <form
                action={archiveSourcesAction}
                className="video-source-states-toolbar"
              >
                <input name="episode_id" type="hidden" value={episodeId} />
                {archivableFiles.map((file) => (
                  <input
                    key={file.file_name}
                    name="file_names"
                    type="hidden"
                    value={file.file_name}
                  />
                ))}
                <span>
                  将归档 {archivableFiles.length} 个已处理源文件，腾出待处理文件夹空间。
                </span>
                <button className="pixel-button" type="submit">
                  归档已处理文件
                </button>
              </form>
            ) : null}
            {sourceFileStates && sourceFileStates.files.length > 0 ? (
              <ul className="video-source-states-list">
                {sourceFileStates.files.map((file) => (
                  <li
                    className={`video-source-state-row ${file.state}`}
                    key={file.file_name}
                  >
                    <PixelIcon name="video" />
                    <div>
                      <strong>{file.file_name}</strong>
                      <span>
                        状态 {getSourceFileStateLabel(file.state)}
                        {file.output_path
                          ? ` · 输出 ${getBaseName(file.output_path)}`
                          : ""}
                      </span>
                      <span>
                        处理 {formatJobTime(file.processed_at)} · 归档{" "}
                        {formatJobTime(file.archived_at)}
                      </span>
                    </div>
                  </li>
                ))}
              </ul>
            ) : (
              <div className="video-source-states-empty">
                <span>
                  {offline
                    ? "API 未连接，无法获取源文件状态。"
                    : "暂无源文件记录，处理一条视频后会出现在这里。"}
                </span>
              </div>
            )}
          </section>
        </section>

        <aside className="video-side pixel-card">
          <h2>本地环境</h2>
          <div className="video-status-list">
            <div className="video-status-row">
              <PixelIcon name="folder" />
              <span>待处理文件夹</span>
              <strong>{videoStatus?.inbox_exists ? "存在" : "未发现"}</strong>
            </div>
            <code>{videoStatus?.inbox_path ?? "--"}</code>
            <div className="video-status-row">
              <PixelIcon name="archive" />
              <span>输出文件夹</span>
              <strong>{videoStatus?.output_exists ? "存在" : "未创建"}</strong>
            </div>
            <code>{videoStatus?.output_path ?? "--"}</code>
            <div className="video-status-row">
              <PixelIcon name="code" />
              <span>ffmpeg</span>
              <strong>{videoStatus?.ffmpeg_available ? "可用" : "不可用"}</strong>
            </div>
            <div className="video-status-row">
              <PixelIcon name="video" />
              <span>待处理数量</span>
              <strong>{String(videoStatus?.pending_count ?? 0)}</strong>
            </div>
            <div className="video-status-row">
              <PixelIcon name="script" />
              <span>当前 Episode</span>
              <strong>{episode?.status ?? "未连接"}</strong>
            </div>
            <div className="video-status-row">
              <PixelIcon name="code" />
              <span>本地 Whisper</span>
              <strong>{systemStatus?.asr.local_whisper_configured ? "可用" : "未配置"}</strong>
            </div>
            <div className="video-status-row">
              <PixelIcon name="settings" />
              <span>生产模式</span>
              <strong>{currentProductionMode ?? "未设置"}</strong>
            </div>
          </div>
          <p>
            当前阶段已支持真实的视频扫描、长静音保守剪辑计划、1.05x 加速、音量标准化、SRT 字幕、4:3 封面和素材包生成。
          </p>
        </aside>
      </div>
    </AppShell>
  );
}