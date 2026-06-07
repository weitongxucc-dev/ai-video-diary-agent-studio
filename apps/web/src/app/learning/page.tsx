import Link from "next/link";
import { revertFeedbackAction } from "@/app/actions";
import { AppShell } from "@/components/AppShell";
import { MetricCard } from "@/components/MetricCard";
import { PixelIcon } from "@/components/PixelIcon";
import { getFeedbackSummary, getPreferences } from "@/lib/api";
import type { FeedbackSummary, Preferences } from "@/lib/types";

export const dynamic = "force-dynamic";

async function loadLearning(): Promise<{
  summary: FeedbackSummary | null;
  preferences: Preferences | null;
  offline: boolean;
}> {
  try {
    const [summary, preferences] = await Promise.all([
      getFeedbackSummary(),
      getPreferences(),
    ]);
    return { summary, preferences, offline: false };
  } catch {
    return { summary: null, preferences: null, offline: true };
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

function feedbackTargetLabel(targetType: string): string {
  if (targetType === "topic") {
    return "选题反馈";
  }

  if (targetType === "published_episode") {
    return "发布反馈";
  }

  return "口播段落反馈";
}

export default async function LearningPage() {
  const { summary, preferences, offline } = await loadLearning();
  const total = summary?.total_count ?? 0;
  const up = summary?.up_count ?? 0;
  const down = summary?.down_count ?? 0;
  const recentEvents = summary?.recent_events ?? [];
  const preferenceItems = preferences?.preferences ?? [];

  return (
    <AppShell>
      <div className="workspace-page">
        <section className="workspace-main pixel-card">
          <div className="workspace-header">
            <div>
              <span className="section-kicker">反馈学习</span>
              <h1>偏好记忆</h1>
              <p>
                {offline
                  ? "暂时无法连接本地 API。"
                  : total > 0
                    ? "这些赞踩会进入下一次选题和文案生成上下文。"
                    : "还没有反馈记录，先在选题或口播稿里点赞/踩。"}
              </p>
            </div>
            <Link className="pixel-button secondary" href="/topics">
              去选题池反馈
            </Link>
          </div>

          <div className="learning-summary">
            <MetricCard icon="thumb-up" value={String(up)} label="点赞" tone="green" />
            <MetricCard icon="thumb-down" value={String(down)} label="踩" tone="orange" />
            <MetricCard icon="spark" value={String(total)} label="有效反馈" tone="purple" />
          </div>

          <div className="workspace-list">
            {preferenceItems.map((preference) => (
              <article className="workspace-row" key={`${preference.label}-${preference.signal}`}>
                <PixelIcon name={preference.label === "偏好" ? "thumb-up" : "thumb-down"} />
                <div>
                  <strong>{preference.label}：{preference.signal}</strong>
                  <span>
                    证据 {preference.evidence_count} 条 · 置信度 {Math.round(preference.confidence * 100)}%
                  </span>
                </div>
              </article>
            ))}
            {recentEvents.map((event) => (
              <article className="workspace-row" key={event.id}>
                <PixelIcon name={event.value === "up" ? "thumb-up" : "thumb-down"} />
                <div>
                  <strong>{feedbackTargetLabel(event.target_type)}</strong>
                  <span>
                    {event.value === "up" ? "喜欢" : "不喜欢"} · {formatDate(event.created_at)}
                    {event.active ? "" : " · 已撤销"}
                  </span>
                  <p>{event.revert_reason || event.reason || "没有填写原因。"}</p>
                  {event.active && event.episode_id ? (
                    <form action={revertFeedbackAction} className="feedback-revert-form">
                      <input name="episode_id" type="hidden" value={event.episode_id} />
                      <input name="feedback_event_id" type="hidden" value={event.id} />
                      <input name="reason" type="hidden" value="用户在反馈学习页撤销" />
                      <button className="pixel-button secondary" type="submit">
                        撤销这条反馈
                      </button>
                    </form>
                  ) : null}
                </div>
              </article>
            ))}
            {total === 0 && preferenceItems.length === 0 && recentEvents.length === 0 && (
              <div className="workspace-empty">
                <PixelIcon name="spark" />
                <strong>还没有偏好样本</strong>
                <p>你点过的赞和踩会在这里汇总，后续会影响 Agent 的选题判断。</p>
              </div>
            )}
          </div>
        </section>

        <aside className="workspace-side">
          <div className="pixel-card side-note">
            <h2>当前学习方式</h2>
            <p>第一版不训练模型，只把未撤销的赞踩、原因和修改偏好写回 prompt 上下文。</p>
          </div>
        </aside>
      </div>
    </AppShell>
  );
}
