import Link from "next/link";
import {
  disablePreferenceCardAction,
  restorePreferenceCardAction,
  revertFeedbackAction,
} from "@/app/actions";
import { AppShell } from "@/components/AppShell";
import { MetricCard } from "@/components/MetricCard";
import { PixelIcon } from "@/components/PixelIcon";
import {
  getFeedbackSummary,
  getPreferenceCards,
  getPreferenceEvidence,
  getPreferences,
} from "@/lib/api";
import type {
  FeedbackSummary,
  PreferenceCards,
  PreferenceEvidence,
  Preferences,
} from "@/lib/types";

export const dynamic = "force-dynamic";

type LearningLoadState = {
  summary: FeedbackSummary | null;
  preferences: Preferences | null;
  preferenceCards: PreferenceCards | null;
  activeEvidence: PreferenceEvidence | null;
  activePreferenceId: string | null;
  offline: boolean;
};

async function loadLearning(
  activePreferenceId: string | null,
): Promise<LearningLoadState> {
  try {
    const [summary, preferences, preferenceCards] = await Promise.all([
      getFeedbackSummary(),
      getPreferences(),
      getPreferenceCards(),
    ]);
    let activeEvidence: PreferenceEvidence | null = null;
    if (activePreferenceId) {
      try {
        activeEvidence = await getPreferenceEvidence(activePreferenceId);
      } catch {
        activeEvidence = null;
      }
    }
    return {
      summary,
      preferences,
      preferenceCards,
      activeEvidence,
      activePreferenceId,
      offline: false,
    };
  } catch {
    return {
      summary: null,
      preferences: null,
      preferenceCards: null,
      activeEvidence: null,
      activePreferenceId,
      offline: true,
    };
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

function getConfidenceTone(confidence: number): string {
  if (confidence >= 0.75) {
    return "green";
  }
  if (confidence >= 0.4) {
    return "blue";
  }
  return "orange";
}

type LearningSearchParams = {
  preference_id?: string | string[];
};

export default async function LearningPage({
  searchParams,
}: {
  searchParams?: Promise<LearningSearchParams>;
}) {
  const resolvedSearchParams = searchParams ? await searchParams : {};
  const rawPreferenceId = resolvedSearchParams.preference_id;
  const activePreferenceId =
    typeof rawPreferenceId === "string" && rawPreferenceId.length > 0
      ? rawPreferenceId
      : null;
  const { summary, preferences, preferenceCards, activeEvidence, offline } =
    await loadLearning(activePreferenceId);
  const total = summary?.total_count ?? 0;
  const up = summary?.up_count ?? 0;
  const down = summary?.down_count ?? 0;
  const recentEvents = summary?.recent_events ?? [];
  const preferenceItems = preferences?.preferences ?? [];
  const cards = preferenceCards?.cards ?? [];

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
            <MetricCard
              icon="check"
              value={String(preferenceCards?.enabled_count ?? 0)}
              label="生效偏好"
              tone="blue"
            />
          </div>

          <section aria-label="偏好记忆卡" className="preference-cards-section">
            <div className="preference-cards-heading">
              <strong>偏好记忆卡</strong>
              <span>
                共 {preferenceCards?.total_count ?? cards.length} 张 ·
                {" "}启用 {preferenceCards?.enabled_count ?? 0} ·
                {" "}已禁用 {preferenceCards?.disabled_count ?? 0}
              </span>
            </div>
            {cards.length > 0 ? (
              <ul className="preference-cards-list">
                {cards.map((card) => {
                  const confidenceTone = getConfidenceTone(card.confidence);
                  const isActive = activePreferenceId === card.id;
                  const viewHref = isActive
                    ? "/learning"
                    : `/learning?preference_id=${encodeURIComponent(card.id)}`;
                  return (
                    <li
                      className={`preference-card ${card.enabled ? "enabled" : "disabled"} ${isActive ? "active" : ""}`}
                      key={card.id}
                    >
                      <div className="preference-card-main">
                        <Link
                          className="preference-card-link"
                          href={viewHref}
                        >
                          <strong>{card.signal}</strong>
                          <span>{card.category}</span>
                        </Link>
                        <div className="preference-card-meta">
                          <span
                            className={`step-state ${confidenceTone}`}
                            title={`置信度 ${Math.round(card.confidence * 100)}%`}
                          >
                            置信度 {Math.round(card.confidence * 100)}%
                          </span>
                          <span className="preference-card-evidence">
                            证据 {card.evidence_count} 条
                          </span>
                          {card.latest_evidence_at ? (
                            <span className="preference-card-fresh">
                              最近证据 {formatDate(card.latest_evidence_at)}
                            </span>
                          ) : (
                            <span className="preference-card-fresh">
                              尚无证据时间
                            </span>
                          )}
                          {!card.enabled ? (
                            <span className="step-state muted">已禁用</span>
                          ) : null}
                        </div>
                        <div className="preference-card-actions">
                          {card.enabled ? (
                            <form
                              action={disablePreferenceCardAction}
                              className="preference-card-action-form"
                            >
                              <input
                                name="preference_id"
                                type="hidden"
                                value={card.id}
                              />
                              <button
                                className="pixel-button secondary"
                                type="submit"
                              >
                                禁用这张卡
                              </button>
                            </form>
                          ) : (
                            <form
                              action={restorePreferenceCardAction}
                              className="preference-card-action-form"
                            >
                              <input
                                name="preference_id"
                                type="hidden"
                                value={card.id}
                              />
                              <button
                                className="pixel-button"
                                type="submit"
                              >
                                恢复这张卡
                              </button>
                            </form>
                          )}
                        </div>
                      </div>

                      {isActive ? (
                        <section
                          aria-label="偏好卡证据"
                          className="preference-card-evidence-list"
                        >
                          <div className="preference-card-evidence-heading">
                            <strong>证据来源</strong>
                            <span>
                              {activeEvidence
                                ? `共 ${activeEvidence.total} 条证据`
                                : "正在拉取证据..."}
                            </span>
                            <Link
                              className="pixel-button secondary"
                              href="/learning"
                            >
                              收起
                            </Link>
                          </div>
                          {activeEvidence && activeEvidence.items.length > 0 ? (
                            <ol className="preference-card-evidence-items">
                              {activeEvidence.items.map((item) => (
                                <li
                                  className={`preference-card-evidence-item ${item.value}`}
                                  key={item.feedback_event_id}
                                >
                                  <strong>
                                    {item.value === "up" ? "赞" : "踩"} ·{" "}
                                    {item.target_type === "topic"
                                      ? "选题"
                                      : "口播段落"}
                                  </strong>
                                  <span>
                                    {formatDate(item.created_at)}
                                    {item.episode_date
                                      ? ` · ${item.episode_date}`
                                      : ""}
                                  </span>
                                  <p>
                                    {item.reason ?? "未填写原因"}
                                  </p>
                                </li>
                              ))}
                            </ol>
                          ) : (
                            <div className="preference-card-evidence-empty">
                              <span>
                                {activeEvidence
                                  ? "这条偏好卡暂无证据。"
                                  : "证据暂未加载，稍后再试。"}
                              </span>
                            </div>
                          )}
                        </section>
                      ) : null}
                    </li>
                  );
                })}
              </ul>
            ) : (
              <div className="preference-card-empty">
                <PixelIcon name="spark" />
                <div>
                  <strong>
                    {offline ? "API 未连接" : "还没有偏好记忆卡"}
                  </strong>
                  <p>
                    累计 1 次以上赞踩后会自动汇总成偏好卡。
                  </p>
                </div>
              </div>
            )}
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
                  <strong>{event.target_type === "topic" ? "选题反馈" : "口播段落反馈"}</strong>
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
          <div className="pixel-card side-note">
            <h2>偏好卡说明</h2>
            <p>每张卡汇总一类偏好信号；禁用后 Agent 在生成时不再参考，但保留历史证据。</p>
          </div>
        </aside>
      </div>
    </AppShell>
  );
}