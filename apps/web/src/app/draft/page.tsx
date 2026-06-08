import Link from "next/link";
import {
  analyzeCommentInsightsAction,
  createDraftPackageAction,
  importDraftPackageAction,
  markPublishedAction,
} from "@/app/actions";
import { AppShell } from "@/components/AppShell";
import { MetricCard } from "@/components/MetricCard";
import { PixelIcon } from "@/components/PixelIcon";
import { getDraftStatus, getTodayEpisode } from "@/lib/api";
import type { DraftAsset, DraftStatus, Episode } from "@/lib/types";

export const dynamic = "force-dynamic";

async function loadDraftPage(): Promise<{
  draft: DraftStatus | null;
  episode: Episode | null;
  offline: boolean;
}> {
  try {
    const [draft, episode] = await Promise.all([
      getDraftStatus(),
      getTodayEpisode(),
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

const BASE_TEMPLATE_EFFECT_LABELS = [
  "标题卡",
  "章节字幕条",
  "关键词高亮",
  "重点句大字贴纸",
  "封面",
  "B-roll 插入点",
  "简单转场",
  "背景音乐",
  "片头",
  "片尾",
];

function DraftTemplatePreview({ item }: { item: DraftAsset }) {
  const hasPreview =
    item.template_id !== null ||
    item.track_preview.length > 0 ||
    item.effect_preview.length > 0;
  if (!hasPreview) {
    return null;
  }

  const effects =
    item.effect_preview.length > 0
      ? item.effect_preview
      : BASE_TEMPLATE_EFFECT_LABELS;

  return (
    <div className="draft-template-preview">
      <div className="draft-template-head">
        <strong>剪映轨道结构</strong>
        <span>{item.template_name ?? item.template_id ?? "模板预览"}</span>
      </div>
      <ol className="draft-track-preview">
        {item.track_preview.slice(0, 6).map((track) => (
          <li key={track}>{track}</li>
        ))}
      </ol>
      <div className="draft-effect-list">
        <strong>基础模板元素</strong>
        <div className="draft-effect-chips">
          {effects.slice(0, 10).map((effect) => (
            <span className="draft-effect-chip" key={effect}>
              {effect}
            </span>
          ))}
        </div>
      </div>
    </div>
  );
}

export default async function DraftPage() {
  const { draft, episode, offline } = await loadDraftPage();
  const env = draft?.environment;
  const toolAvailable = env?.draft_tool.available ?? false;
  const bridgeReady = env?.editable_draft_supported ?? false;
  const processedVideos = draft?.processed_videos ?? [];
  const packages = draft?.packages ?? [];
  const recentPackages = packages.slice(0, 5);
  const olderPackages = packages.slice(5);
  const activePublishFeedback = episode?.feedback_events.find(
    (event) => event.active && event.target_type === "published_episode",
  );
  const activePublishFeedbackValue = activePublishFeedback?.value ?? "";

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
                    ? "视频剪好后先生成剪映素材包，确认轨道结构后再写入草稿箱。"
                    : toolAvailable
                      ? "已安装草稿工具，请先配置剪映草稿目录。"
                    : "当前未检测到剪映草稿工具，只生成真实可追溯的本地素材包。"}
              </p>
            </div>
            <Link className="pixel-button secondary" href="/video">
              去处理视频
            </Link>
            <Link className="pixel-button secondary" href="/cover">
              编辑封面
            </Link>
          </div>

          <div className="draft-flow" aria-label="剪映草稿流程">
            <div className="draft-flow-step active">
              <strong>1 生成素材包</strong>
              <span>从预处理视频生成可追溯素材。</span>
            </div>
            <div className="draft-flow-step">
              <strong>2 确认轨道结构</strong>
              <span>先看主视频、字幕、标题贴纸、关键词和封面轨道。</span>
            </div>
            <div className="draft-flow-step">
              <strong>3 导入剪映验证</strong>
              <span>确认后写入草稿箱，再打开剪映人工验证。</span>
            </div>
          </div>

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
                    生成素材包
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
                    <DraftTemplatePreview item={item} />
                  </div>
                  {item.import_status === "imported" ? (
                    <span className="step-state done">已导入</span>
                  ) : (
                    <form action={importDraftPackageAction}>
                      <input name="package_name" type="hidden" value={item.file_name} />
                      <button className="pixel-button secondary" type="submit">
                        {item.import_status === "not_attempted" ? "确认导入剪映" : "重试导入剪映"}
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
                        <DraftTemplatePreview item={item} />
                      </div>
                      {item.import_status === "imported" ? (
                        <span className="step-state done">已导入</span>
                      ) : (
                        <form action={importDraftPackageAction}>
                          <input name="package_name" type="hidden" value={item.file_name} />
                          <button className="pixel-button secondary" type="submit">
                            {item.import_status === "not_attempted" ? "确认导入剪映" : "重试导入剪映"}
                          </button>
                        </form>
                      )}
                    </article>
                  ))}
                </div>
              </details>
            ) : null}
          </div>

          <div className="workspace-subsection draft-publish-section">
            <div className="draft-publish-head">
              <div>
                <h2>发布反馈</h2>
                <p>发布后登记链接、首轮数据和明确赞踩原因，下一轮选题与口播会读取这批偏好。</p>
              </div>
              <Link className="pixel-button secondary" href="/learning">
                查看记忆
              </Link>
            </div>
            {episode ? (
              <div className="draft-publish-tools">
                <form action={markPublishedAction} className="draft-publish-form">
                  <input name="episode_id" type="hidden" value={episode.id} />
                  <label className="settings-field wide">
                    <span>发布链接</span>
                    <input
                      defaultValue={episode.publish_url ?? ""}
                      name="publish_url"
                      placeholder="https://..."
                      type="url"
                    />
                  </label>
                  <div className="draft-publish-metrics">
                    <label className="settings-field">
                      <span>播放量</span>
                      <input
                        defaultValue={episode.play_count ?? ""}
                        min="0"
                        name="play_count"
                        type="number"
                      />
                    </label>
                    <label className="settings-field">
                      <span>点赞数</span>
                      <input
                        defaultValue={episode.like_count ?? ""}
                        min="0"
                        name="like_count"
                        type="number"
                      />
                    </label>
                    <label className="settings-field">
                      <span>评论数</span>
                      <input
                        defaultValue={episode.comment_count ?? ""}
                        min="0"
                        name="comment_count"
                        type="number"
                      />
                    </label>
                  </div>
                  <div className="draft-feedback-grid">
                    <fieldset className="draft-feedback-choice">
                      <legend>发布判断</legend>
                      <label>
                        <input
                          defaultChecked={activePublishFeedbackValue === "up"}
                          name="feedback_value"
                          type="radio"
                          value="up"
                        />
                        <span>值得延续</span>
                      </label>
                      <label>
                        <input
                          defaultChecked={activePublishFeedbackValue === "down"}
                          name="feedback_value"
                          type="radio"
                          value="down"
                        />
                        <span>需要调整</span>
                      </label>
                      <label>
                        <input
                          defaultChecked={activePublishFeedbackValue === ""}
                          name="feedback_value"
                          type="radio"
                          value=""
                        />
                        <span>只登记数据</span>
                      </label>
                    </fieldset>
                    <label className="settings-field">
                      <span>反馈原因</span>
                      <textarea
                        defaultValue={activePublishFeedback?.reason ?? ""}
                        name="feedback_reason"
                        placeholder="例如：开头直接抛冲突，评论区更容易接话。"
                        rows={4}
                      />
                    </label>
                  </div>
                  <div className="settings-actions">
                    <button className="pixel-button" type="submit">
                      登记发布反馈
                    </button>
                    <span>
                      {episode.published_at
                        ? `已发布 · ${formatDate(episode.published_at)}`
                        : `当前状态 · ${episode.status}`}
                    </span>
                  </div>
                </form>

                <form action={analyzeCommentInsightsAction} className="draft-comment-insights-form">
                  <input name="episode_id" type="hidden" value={episode.id} />
                  <div className="draft-comment-insights-head">
                    <div>
                      <h3>评论洞察</h3>
                      <span>发布域 · 待确认</span>
                    </div>
                    <button className="pixel-button secondary compact" type="submit">
                      生成评论洞察
                    </button>
                  </div>
                  <label className="settings-field">
                    <span>评论原文</span>
                    <textarea
                      maxLength={2400}
                      name="comments_text"
                      placeholder={"开头真实经历很抓人\n这个教程步骤很清楚\n这条太官方了，像广告"}
                      rows={7}
                    />
                  </label>
                </form>
              </div>
            ) : (
              <div className="workspace-empty">
                <PixelIcon name="publish" />
                <strong>还没有可登记的当日 episode</strong>
                <p>连接本地 API 后，这里会显示发布登记表单。</p>
              </div>
            )}
          </div>
        </section>

        <aside className="workspace-side">
          <MetricCard icon="clip" value={String(env?.package_count ?? 0)} label="素材包" tone="purple" />
          <MetricCard icon="video" value={String(env?.processed_video_count ?? 0)} label="可用视频" tone="green" />
          <div className="pixel-card side-note">
            <h2>草稿工具</h2>
            <p>{toolAvailable ? env?.draft_tool.tool_name : "未安装"}</p>
            <code>{env?.draft_tool.checked_modules.join(" / ") ?? "--"}</code>
            <span>{env?.editable_draft_supported ? "兼容桥接可用，需确认导入" : "当前只能生成素材包 fallback"}</span>
          </div>
          <div className="pixel-card side-note">
            <h2>剪映目录</h2>
            <p>{env?.jianying_root_exists ? "已发现" : "未发现"}</p>
            <code>{env?.jianying_root_path ?? "--"}</code>
            {env?.jianying_blocker ? <span>{env.jianying_blocker}</span> : null}
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
