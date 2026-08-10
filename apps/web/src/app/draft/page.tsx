import Link from "next/link";
import {
  createDraftPackageAction,
  importDraftPackageAction,
  markEpisodePublishedAction,
  updatePublishMetricsAction,
} from "@/app/actions";
import { AppShell } from "@/components/AppShell";
import { MetricCard } from "@/components/MetricCard";
import { PixelIcon } from "@/components/PixelIcon";
import { getDraftStatus, getTodayEpisode } from "@/lib/api";
import type { DraftStatus, Episode } from "@/lib/types";

export const dynamic = "force-dynamic";

type DraftLoadState = {
  draft: DraftStatus | null;
  episode: Episode | null;
  offline: boolean;
};

async function loadDraftState(): Promise<DraftLoadState> {
  try {
    const [draft, episode] = await Promise.all([
      getDraftStatus(),
      getTodayEpisode().catch(() => null),
    ]);
    return { draft, episode, offline: false };
  } catch {
    return { draft: null, episode: null, offline: true };
  }
}

function formatDate(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", {
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    month: "2-digit",
    timeZone: "Asia/Shanghai",
  }).format(new Date(value));
}

function formatFileSize(sizeBytes: number): string {
  if (sizeBytes < 1024 * 1024) {
    return `${Math.max(1, Math.round(sizeBytes / 1024))} KB`;
  }

  return `${(sizeBytes / 1024 / 1024).toFixed(1)} MB`;
}

function formatNumber(value: number | null | undefined): string {
  if (value === null || value === undefined) {
    return "--";
  }
  return value.toLocaleString("zh-CN");
}

function getStatusLabel(status: Episode["status"] | undefined): string {
  if (!status) {
    return "未连接";
  }
  const labels: Record<Episode["status"], string> = {
    created: "已创建",
    sources_ingesting: "素材收集中",
    topics_ready: "选题就绪",
    topic_approved: "选题已确认",
    script_ready: "口播稿就绪",
    script_approved: "口播稿已确认",
    waiting_video: "等待视频",
    video_processing: "视频处理中",
    draft_ready: "草稿就绪",
    published: "已发布",
    failed: "失败",
  };
  return labels[status];
}

const PUBLISH_PLATFORMS = [
  { value: "douyin", label: "抖音" },
  { value: "bilibili", label: "B站" },
  { value: "xigua", label: "西瓜视频" },
  { value: "kuaishou", label: "快手" },
  { value: "youtube", label: "YouTube" },
  { value: "other", label: "其他" },
];

export default async function DraftPage() {
  const { draft, episode, offline } = await loadDraftState();
  const env = draft?.environment;
  const toolAvailable = env?.draft_tool.available ?? false;
  const bridgeReady = env?.editable_draft_supported ?? false;
  const processedVideos = draft?.processed_videos ?? [];
  const packages = draft?.packages ?? [];
  const recentPackages = packages.slice(0, 5);
  const olderPackages = packages.slice(5);
  const showPublishPanel =
    !offline && Boolean(episode) && (episode?.status === "draft_ready" || episode?.status === "published");
  const isPublished = episode?.status === "published";
  const existingFeedback = episode?.publish_feedback ?? null;
  const initialPublishUrl = existingFeedback?.publish_url ?? episode?.publish_url ?? "";
  const initialPlatform = existingFeedback?.platform ?? episode?.platform ?? "douyin";
  const initialNotes = existingFeedback?.notes ?? episode?.notes ?? "";
  const initialPlayCount = existingFeedback?.play_count ?? episode?.play_count ?? null;
  const initialLikeCount = existingFeedback?.like_count ?? episode?.like_count ?? null;
  const initialCommentCount = existingFeedback?.comment_count ?? episode?.comment_count ?? null;

  return (
    <AppShell>
      <div className="workspace-page">
        <section className="workspace-main pixel-card draft-main">
          <div className="workspace-header">
            <div>
              <span className="section-kicker">剪映草稿</span>
              <h1>草稿素材包</h1>
              <p>
                {offline
                  ? "暂时无法连接本地 API。"
                  : bridgeReady
                    ? "视频剪好后会自动生成剪映兼容草稿并登记到草稿箱，仍需打开剪映人工验证。"
                    : toolAvailable
                      ? "已安装草稿工具，请先配置剪映草稿目录。"
                    : "当前未检测到剪映草稿工具，只生成真实可追溯的本地素材包。"}
              </p>
            </div>
            <Link className="pixel-button secondary" href="/video">
              去处理视频
            </Link>
          </div>

          <div className="draft-flow" aria-label="剪映草稿流程">
            <div className={`draft-flow-step ${episode?.status === "draft_ready" || isPublished ? "active" : ""}`}>
              <strong>1 生成素材包</strong>
              <span>从预处理视频生成可追溯素材。</span>
            </div>
            <div className={`draft-flow-step ${isPublished ? "active" : ""}`}>
              <strong>2 自动导入剪映</strong>
              <span>剪好后自动写入草稿箱，失败时保留重试。</span>
            </div>
            <div className={`draft-flow-step ${isPublished ? "active" : ""}`}>
              <strong>3 打开剪映验证</strong>
              <span>刷新草稿箱，人工确认可编辑。</span>
            </div>
            <div className={`draft-flow-step ${isPublished ? "active" : ""}`}>
              <strong>4 标记已发布</strong>
              <span>记录发布链接与平台，写入反馈学习。</span>
            </div>
          </div>

          {showPublishPanel && episode ? (
            <section
              aria-label={isPublished ? "发布反馈" : "标记已发布"}
              className="publish-panel"
            >
              <div className="publish-panel-heading">
                <strong>
                  {isPublished ? "发布反馈与数据" : "标记已发布"}
                </strong>
                <span>
                  {isPublished
                    ? `Episode 状态：${getStatusLabel(episode.status)}，请补全发布信息。`
                    : `Episode 状态：${getStatusLabel(episode.status)}，填写发布信息后归档当日内容。`}
                </span>
              </div>

              <form
                action={isPublished ? updatePublishMetricsAction : markEpisodePublishedAction}
                className="publish-form"
              >
                <input name="episode_id" type="hidden" value={episode.id} />

                <fieldset className="publish-form-fieldset" disabled={isPublished}>
                  <legend>发布信息</legend>
                  <label className="publish-field">
                    <span>发布 URL</span>
                    <input
                      defaultValue={initialPublishUrl ?? ""}
                      name="publish_url"
                      placeholder="https://www.douyin.com/video/..."
                      type="url"
                    />
                  </label>
                  <label className="publish-field">
                    <span>平台</span>
                    <select defaultValue={initialPlatform ?? "douyin"} name="platform">
                      {PUBLISH_PLATFORMS.map((platform) => (
                        <option key={platform.value} value={platform.value}>
                          {platform.label}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="publish-field publish-field-wide">
                    <span>备注</span>
                    <textarea
                      defaultValue={initialNotes ?? ""}
                      name="notes"
                      placeholder="记录发布背景、字幕差异、二剪说明等。"
                      rows={3}
                    />
                  </label>
                </fieldset>

                <fieldset className="publish-form-fieldset">
                  <legend>{isPublished ? "最新数据" : "初始数据"}</legend>
                  <label className="publish-field">
                    <span>播放数</span>
                    <input
                      defaultValue={initialPlayCount ?? ""}
                      min="0"
                      name="play_count"
                      placeholder="0"
                      type="number"
                    />
                  </label>
                  <label className="publish-field">
                    <span>点赞数</span>
                    <input
                      defaultValue={initialLikeCount ?? ""}
                      min="0"
                      name="like_count"
                      placeholder="0"
                      type="number"
                    />
                  </label>
                  <label className="publish-field">
                    <span>评论数</span>
                    <input
                      defaultValue={initialCommentCount ?? ""}
                      min="0"
                      name="comment_count"
                      placeholder="0"
                      type="number"
                    />
                  </label>
                </fieldset>

                <div className="publish-form-actions">
                  <span>
                    {isPublished
                      ? "更新后会写入反馈学习并刷新发布历史。"
                      : "提交后会跳转到历史页，可补录后续数据。"}
                  </span>
                  <button className="pixel-button" type="submit">
                    {isPublished ? "更新发布数据" : "标记已发布"}
                  </button>
                </div>
              </form>

              {isPublished ? (
                <div className="publish-summary">
                  <strong>当前发布数据</strong>
                  <span>
                    播放 {formatNumber(episode.play_count)} · 点赞{" "}
                    {formatNumber(episode.like_count)} · 评论{" "}
                    {formatNumber(episode.comment_count)}
                  </span>
                  <code>{episode.publish_url ?? "未填写发布 URL"}</code>
                </div>
              ) : null}
            </section>
          ) : null}

          <div className="workspace-list">
            {processedVideos.map((video) => (
              <article className="workspace-row draft-row" key={video.path}>
                <PixelIcon name="video" />
                <div>
                  <strong>{video.file_name}</strong>
                  <span>{formatFileSize(video.size_bytes)} · {formatDate(video.modified_at)}</span>
                  <code>{video.path}</code>
                </div>
                <form action={createDraftPackageAction}>
                  <input name="video_file_name" type="hidden" value={video.file_name} />
                  <button className="pixel-button" type="submit">
                    生成并导入
                  </button>
                </form>
              </article>
            ))}
            {processedVideos.length === 0 && (
              <div className="workspace-empty">
                <PixelIcon name="clip" />
                <strong>没有可用预处理视频</strong>
                <p>先在视频处理页生成 preview，再回来创建剪映草稿素材包。</p>
              </div>
            )}
          </div>

          <div className="workspace-subsection">
            <h2>最近生成素材包</h2>
            <div className="workspace-list compact">
              {recentPackages.map((item) => (
                <article className="workspace-row draft-row" key={item.path}>
                  <PixelIcon name="archive" />
                  <div>
                    <strong>{item.file_name}</strong>
                    <span>
                      {formatDate(item.modified_at)}
                      {item.import_status ? ` · ${item.import_status}` : ""}
                    </span>
                    <code>{item.path}</code>
                    {item.import_message ? <p>{item.import_message}</p> : null}
                    {item.draft_path ? <code>{item.draft_path}</code> : null}
                  </div>
                  {item.import_status === "imported" ? (
                    <span className="step-state done">已自动导入</span>
                  ) : (
                    <form action={importDraftPackageAction}>
                      <input name="package_name" type="hidden" value={item.file_name} />
                      <button className="pixel-button secondary" type="submit">
                        重试导入剪映
                      </button>
                    </form>
                  )}
                </article>
              ))}
              {packages.length === 0 ? (
                <div className="workspace-empty">
                  <PixelIcon name="archive" />
                  <strong>还没有素材包</strong>
                  <p>处理视频后，这里会显示最近生成的剪映素材包。</p>
                </div>
              ) : null}
            </div>
            {olderPackages.length > 0 ? (
              <details className="draft-history">
                <summary>更早素材包 {olderPackages.length} 个</summary>
                <div className="workspace-list compact">
                  {olderPackages.map((item) => (
                    <article className="workspace-row draft-row compact" key={item.path}>
                      <PixelIcon name="archive" />
                      <div>
                        <strong>{item.file_name}</strong>
                        <span>
                          {formatDate(item.modified_at)}
                          {item.import_status ? ` · ${item.import_status}` : ""}
                        </span>
                        <code>{item.path}</code>
                        {item.draft_path ? <code>{item.draft_path}</code> : null}
                      </div>
                      {item.import_status === "imported" ? (
                        <span className="step-state done">已自动导入</span>
                      ) : (
                        <form action={importDraftPackageAction}>
                          <input name="package_name" type="hidden" value={item.file_name} />
                          <button className="pixel-button secondary" type="submit">
                            重试导入剪映
                          </button>
                        </form>
                      )}
                    </article>
                  ))}
                </div>
              </details>
            ) : null}
          </div>
        </section>

        <aside className="workspace-side">
          <MetricCard icon="clip" value={String(env?.package_count ?? 0)} label="素材包" tone="purple" />
          <MetricCard icon="video" value={String(env?.processed_video_count ?? 0)} label="可用视频" tone="green" />
          <div className="pixel-card side-note">
            <h2>草稿工具</h2>
            <p>{toolAvailable ? env?.draft_tool.tool_name : "未安装"}</p>
            <code>{env?.draft_tool.checked_modules.join(" / ") ?? "--"}</code>
            <span>{env?.editable_draft_supported ? "自动兼容桥接，需人工验证" : "当前只能生成素材包 fallback"}</span>
          </div>
          <div className="pixel-card side-note">
            <h2>剪映目录</h2>
            <p>{env?.jianying_root_exists ? "已发现" : "未发现"}</p>
            <code>{env?.jianying_root_path ?? "--"}</code>
            {env?.jianying_blocker ? <span>{env.jianying_blocker}</span> : null}
          </div>
          <div className="pixel-card side-note">
            <h2>当前 Episode</h2>
            <p>{getStatusLabel(episode?.status)}</p>
            <code>{episode?.id ?? "--"}</code>
            {episode?.published_at ? (
              <span>发布于 {formatDate(episode.published_at)}</span>
            ) : null}
          </div>
          <div className="pixel-card side-note">
            <h2>输出目录</h2>
            <code>{env?.output_path ?? "--"}</code>
          </div>
        </aside>
      </div>
    </AppShell>
  );
}