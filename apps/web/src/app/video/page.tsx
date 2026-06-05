import Link from "next/link";
import {
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
  getSystemStatus,
  getTodayEpisode,
} from "@/lib/api";
import type { Episode, PendingVideo, RetakeReport, SystemStatus } from "@/lib/types";

export const dynamic = "force-dynamic";

type VideoLoadState = {
  videos: PendingVideo[];
  systemStatus: SystemStatus | null;
  episode: Episode | null;
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

    let retakeReport: RetakeReport | null = null;
    let isLatestRetakeReport = false;
    try {
      if (episode.video_dedup_report_path) {
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
      retakeReport,
      isLatestRetakeReport,
      offline: false,
    };
  } catch {
    return {
      videos: [],
      systemStatus: null,
      episode: null,
      retakeReport: null,
      isLatestRetakeReport: false,
      offline: true,
    };
  }
}

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

export default async function VideoPage() {
  const {
    videos,
    systemStatus,
    episode,
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

          <div className="video-mode-strip" aria-label="视频生产模式">
            <div className="video-mode-card">
              <strong>单条截取</strong>
              <span>处理一条录制视频，生成预览或完整素材包。</span>
            </div>
            <div className="video-mode-card">
              <strong>多段拼剪</strong>
              <span>勾选多条片段后按序合并，适合分段录制。</span>
            </div>
            <div className="video-mode-card">
              <strong>自动生产包</strong>
              <span>带字幕、封面、素材包，进入剪映草稿流程。</span>
            </div>
          </div>

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
                      min="1"
                      name={`order:${video.file_name}`}
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
          </div>
          <p>
            当前阶段已支持真实的视频扫描、长静音保守剪辑计划、1.05x 加速、音量标准化、SRT 字幕、4:3 封面和素材包生成。
          </p>
        </aside>
      </div>
    </AppShell>
  );
}
