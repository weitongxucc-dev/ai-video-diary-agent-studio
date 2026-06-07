import Link from "next/link";
import {
  confirmPreferenceAction,
  deletePreferenceAction,
  disablePreferenceAction,
  restorePreferenceAction,
  revertFeedbackAction,
} from "@/app/actions";
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

  if (targetType === "published_comment_insight") {
    return "评论洞察";
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
  const longTermPreferences = preferences?.long_term_preferences ?? [];
  const candidates = preferences?.candidates ?? [];

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

          <section className="workspace-subsection" data-contract="long_term_preferences">
            <div className="script-section-header compact">
              <div>
                <span className="section-kicker">长期记忆</span>
                <strong>长期偏好记忆</strong>
              </div>
            </div>
            <div className="preference-control-grid">
              {longTermPreferences.length > 0 ? (
                longTermPreferences.map((preference) => (
                  <article
                    className={`preference-memory-card${preference.enabled ? "" : " disabled"}${preference.deleted ? " deleted" : ""}`}
                    key={preference.id}
                  >
                    <div>
                      <PixelIcon name={preference.kind === "prefer" ? "thumb-up" : "thumb-down"} />
                      <strong>{preference.label}：{preference.content}</strong>
                    </div>
                    <p>
                      影响范围 {preference.scope} · 来源 {preference.source_event_ids.length} 条 ·
                      置信度 {Math.round(preference.confidence * 100)}%
                    </p>
                    <span>{preference.deleted ? "已删除" : preference.enabled ? "启用中" : "已禁用"}</span>
                    <div className="preference-actions">
                      {!preference.deleted && preference.enabled ? (
                        <form action={disablePreferenceAction}>
                          <input name="preference_id" type="hidden" value={preference.id} />
                          <button className="pixel-button secondary compact" type="submit">
                            禁用
                          </button>
                        </form>
                      ) : null}
                      {!preference.deleted && !preference.enabled ? (
                        <form action={restorePreferenceAction}>
                          <input name="preference_id" type="hidden" value={preference.id} />
                          <button className="pixel-button secondary compact" type="submit">
                            恢复
                          </button>
                        </form>
                      ) : null}
                      {!preference.deleted ? (
                        <form action={deletePreferenceAction}>
                          <input name="preference_id" type="hidden" value={preference.id} />
                          <button className="pixel-button danger compact" type="submit">
                            删除
                          </button>
                        </form>
                      ) : null}
                    </div>
                  </article>
                ))
              ) : (
                <div className="workspace-empty compact">
                  <PixelIcon name="spark" />
                  <strong>还没有确认的长期偏好</strong>
                  <p>把下方候选确认后，Agent 会在下一次生成时优先读取。</p>
                </div>
              )}
            </div>
          </section>

          <section className="workspace-subsection" data-contract="candidates">
            <div className="script-section-header compact">
              <div>
                <span className="section-kicker">候选记忆</span>
                <strong>候选偏好</strong>
              </div>
            </div>
            <div className="preference-candidate-grid">
              {candidates.map((candidate) => (
                <article className="preference-memory-card candidate" key={`${candidate.kind}-${candidate.content}`}>
                  <div>
                    <PixelIcon name={candidate.kind === "prefer" ? "thumb-up" : "thumb-down"} />
                    <strong>{candidate.label}：{candidate.content}</strong>
                  </div>
                  <p>
                    影响范围 {candidate.scope} · 证据 {candidate.evidence_count} 条 ·
                    置信度 {Math.round(candidate.confidence * 100)}%
                  </p>
                  {candidate.confirmed ? (
                    <span>已进入长期记忆</span>
                  ) : (
                    <form action={confirmPreferenceAction} className="preference-confirm-form">
                      <input name="kind" type="hidden" value={candidate.kind} />
                      <input name="content" type="hidden" value={candidate.content} />
                      <input name="confidence" type="hidden" value={candidate.confidence} />
                      <input name="scope" type="hidden" value={candidate.scope} />
                      {candidate.source_event_ids.map((eventId) => (
                        <input key={eventId} name="source_event_id" type="hidden" value={eventId} />
                      ))}
                      <button className="pixel-button compact" type="submit">
                        确认进入长期记忆
                      </button>
                    </form>
                  )}
                </article>
              ))}
            </div>
          </section>

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
