import Link from "next/link";
import {
  analyzeCommentInsightsAction,
  markPublishedAction,
} from "@/app/actions";
import { PixelIcon } from "@/components/PixelIcon";
import type { Episode } from "@/lib/types";

type PublishFeedbackPanelProps = {
  episode: Episode | null;
};

function formatDate(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", {
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    month: "2-digit",
    timeZone: "Asia/Shanghai",
  }).format(new Date(value));
}

export function PublishFeedbackPanel({ episode }: PublishFeedbackPanelProps) {
  const activePublishFeedback = episode?.feedback_events.find(
    (event) => event.active && event.target_type === "published_episode",
  );
  const activePublishFeedbackValue = activePublishFeedback?.value ?? "";

  return (
    <div className="workspace-subsection publish-feedback-section">
      <div className="publish-feedback-head">
        <div>
          <h2>发布反馈</h2>
          <p>发布后登记链接、首轮数据和明确赞踩原因，下一轮选题与口播会读取这批偏好。</p>
        </div>
        <Link className="pixel-button secondary" href="/learning">
          查看记忆
        </Link>
      </div>
      {episode ? (
        <div className="publish-feedback-tools">
          <form action={markPublishedAction} className="publish-feedback-form">
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
            <div className="publish-feedback-metrics">
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
            <div className="publish-feedback-grid">
              <fieldset className="publish-feedback-choice">
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

          <form action={analyzeCommentInsightsAction} className="publish-comment-insights-form">
            <input name="episode_id" type="hidden" value={episode.id} />
            <div className="publish-comment-insights-head">
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
  );
}
