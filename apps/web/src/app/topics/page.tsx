import { AppShell } from "@/components/AppShell";
import { PixelIcon } from "@/components/PixelIcon";
import { TopicCandidateCard } from "@/components/TopicCandidateCard";
import {
  approveTopicAndGenerateScript,
  generateTopicsAction,
  regenerateTopicsAction,
  sendTopicFeedbackAction,
} from "@/app/actions";
import { getTodayEpisode, getTopicEvidence } from "@/lib/api.server";
import type {
  Episode,
  SourceEvidenceFreshness,
  TopicCandidate,
  TopicEvidence,
} from "@/lib/types";
import Link from "next/link";

export const dynamic = "force-dynamic";

type TopicsPageProps = {
  searchParams?: Promise<{ topic_id?: string }>;
};

type TopicLoadState = {
  episode: Episode | null;
  evidence: TopicEvidence[];
  error: "offline" | null;
};

async function loadTopicEpisode(): Promise<TopicLoadState> {
  try {
    const episode = await getTodayEpisode();
    let evidence: TopicEvidence[] = [];
    try {
      const evidenceList = await getTopicEvidence(episode.id);
      evidence = evidenceList.items;
    } catch {
      evidence = [];
    }
    return { episode, evidence, error: null };
  } catch {
    return { episode: null, evidence: [], error: "offline" };
  }
}

const FRESHNESS_LABEL: Record<SourceEvidenceFreshness, string> = {
  fresh: "新鲜",
  aging: "陈旧",
  stale: "过期",
  unknown: "未知",
};

function EmptyTopicState({
  episode,
  error,
}: {
  episode: Episode | null;
  error: TopicLoadState["error"];
}) {
  const copy =
    error === "offline"
      ? {
          title: "暂时无法连接选题池",
          description: "API 暂时不可用，稍后再试即可。你也可以先整理今天想聊的素材。",
        }
      : {
          title: "还没有候选选题",
          description: "点击生成后，系统会读取已连接的资料与信息源来生成候选。",
        };

  return (
    <section className="topic-empty ui-panel">
      <span className="section-kicker">选题池</span>
      <h1>{copy.title}</h1>
      <p>{copy.description}</p>
      <div className="script-empty-actions">
        {episode ? (
          <form action={generateTopicsAction}>
            <input name="episode_id" type="hidden" value={episode.id} />
            <button className="ui-button" type="submit">
              生成今日选题
            </button>
          </form>
        ) : null}
        <Link className="ui-button secondary" href="/">
          返回今日工作台
        </Link>
      </div>
    </section>
  );
}

function topicFeedbackLabel(feedback: string | null) {
  if (feedback === "up") return "已赞";
  if (feedback === "down") return "已踩";
  return "待复核";
}

type ExtendedTopicFields = {
  conflictLevel?: string | null;
  topicType?: string | null;
  risk?: string | null;
  agentRecommendation?: string | null;
  agentRisk?: string | null;
  agentSuggestion?: string | null;
  checklistItems?: { id: string; label: string; tone: "check" | "note" }[];
};

function readExtendedTopic(topic: TopicCandidate): ExtendedTopicFields {
  const extended = (topic as unknown as { extended?: ExtendedTopicFields })
    .extended;
  return extended ?? {};
}

function formatEmpty(value: string | null | undefined): string {
  return typeof value === "string" && value.trim().length > 0 ? value : "--";
}

function hasAnyAgentJudgment(fields: ExtendedTopicFields): boolean {
  return Boolean(
    fields.agentRecommendation ||
      fields.agentRisk ||
      fields.agentSuggestion,
  );
}

function hasAnyChecklist(fields: ExtendedTopicFields): boolean {
  return Boolean(fields.checklistItems && fields.checklistItems.length > 0);
}

type TopicDetailProps = {
  topic: TopicCandidate;
  evidenceByKey: Map<string, TopicEvidence>;
  episodeId: string | null;
};

function TopicDetail({ topic, evidenceByKey, episodeId }: TopicDetailProps) {
  const extended = readExtendedTopic(topic);
  return (
    <section className="topic-detail ui-panel">
      <header className="topic-detail-header">
        <div>
          <span className="section-kicker">推荐选题</span>
          <h1>{topic.title}</h1>
          <p>{topic.reason}</p>
        </div>
        <div className="topic-detail-status" aria-label="选题状态">
          <span>分数</span>
          <strong>{topic.score}</strong>
          <em>{topicFeedbackLabel(topic.feedback)}</em>
        </div>
      </header>

      <div className="topic-review-workbench">
        <div className="topic-detail-main">
          <section className="topic-console-panel">
            <h3>3 分钟结构</h3>
            {topic.outline.length > 0 ? (
              <ol>
                {topic.outline.map((item, index) => (
                  <li key={item}>
                    <span>{String(index + 1).padStart(2, "0")}</span>
                    <strong>{item}</strong>
                  </li>
                ))}
              </ol>
            ) : (
              <p className="topic-empty-cell">等待系统补充 3 分钟结构</p>
            )}
          </section>

          <section className="topic-console-panel">
            <h3>核心观点</h3>
            <p>
              {topic.core_viewpoint?.trim() ||
                "等待系统补充核心观点"}
            </p>
            <dl className="topic-mini-meta">
              <div>
                <dt>受众</dt>
                <dd>{formatEmpty(topic.audience)}</dd>
              </div>
              {extended.topicType ? (
                <div>
                  <dt>选题类型</dt>
                  <dd>{extended.topicType}</dd>
                </div>
              ) : null}
            </dl>
          </section>

          <section className="topic-console-panel evidence-console">
            <h3>来源依据</h3>
            <div className="evidence-list">
              {topic.source_refs.map((source) => {
                const evidenceEntry = evidenceByKey.get(
                  `${source.source_type}::${source.ref}`,
                );
                const freshness = evidenceEntry?.freshness ?? "unknown";
                const freshnessLabel =
                  evidenceEntry?.freshness_label ??
                  FRESHNESS_LABEL[freshness];
                const participated =
                  evidenceEntry?.participated_topics ?? 0;
                return (
                  <div
                    className={`evidence-item ${freshness}`}
                    key={`${source.source_type}-${source.ref}`}
                  >
                    <strong>{source.source_type}</strong>
                    <div className="evidence-item-body">
                      <span>{source.title}</span>
                      {evidenceEntry?.excerpt ? (
                        <p>{evidenceEntry.excerpt}</p>
                      ) : null}
                      <code>{source.ref}</code>
                    </div>
                    <div className="evidence-item-meta">
                      <span
                        className={`evidence-freshness ${freshness}`}
                        title={`新鲜度：${freshnessLabel}`}
                      >
                        {freshnessLabel}
                      </span>
                      <span
                        className="evidence-participation"
                        title={`参与过 ${participated} 个候选选题`}
                      >
                        参与 {participated} 次
                      </span>
                    </div>
                  </div>
                );
              })}
              {topic.source_refs.length === 0 ? (
                <div className="evidence-item empty">
                  <strong>--</strong>
                  <span>暂无来源，建议补充 1 条真实案例</span>
                  <code>waiting/source</code>
                </div>
              ) : null}
            </div>
          </section>
        </div>

        <aside className="topic-review-aside">
          <section className="topic-audit-card">
            <h3>复核清单</h3>
            {hasAnyChecklist(extended) ? (
              <ul>
                {(extended.checklistItems ?? []).map((item) => (
                  <li key={item.id}>
                    <PixelIcon name={item.tone} />
                    <span>{item.label}</span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="topic-empty-cell">等待系统补充复核清单</p>
            )}
          </section>

          <section className="topic-audit-card">
            <h3>选题元信息</h3>
            <dl>
              <div>
                <dt>素材真实性</dt>
                <dd>{topic.source_refs.length} 条来源</dd>
              </div>
              {extended.conflictLevel ? (
                <div>
                  <dt>冲突感</dt>
                  <dd>{extended.conflictLevel}</dd>
                </div>
              ) : null}
              <div>
                <dt>发布优先级</dt>
                <dd>{topic.score >= 8 ? "高" : "中"}</dd>
              </div>
              {extended.risk ? (
                <div>
                  <dt>风险</dt>
                  <dd>{extended.risk}</dd>
                </div>
              ) : null}
            </dl>
          </section>

          {hasAnyAgentJudgment(extended) ? (
            <section className="topic-agent-terminal">
              <h3>系统判断</h3>
              {extended.agentRecommendation ? (
                <p>推荐原因：{extended.agentRecommendation}</p>
              ) : null}
              {extended.agentRisk ? (
                <p>风险：{extended.agentRisk}</p>
              ) : null}
              {extended.agentSuggestion ? (
                <p>建议：{extended.agentSuggestion}</p>
              ) : null}
            </section>
          ) : null}
        </aside>
      </div>

      <div className="topic-action-dock">
        <div>
          <strong>操作台</strong>
          <span>确认后将进入口播稿生成流程</span>
        </div>
        <form action={approveTopicAndGenerateScript}>
          <input name="episode_id" type="hidden" value={episodeId ?? ""} />
          <input name="topic_id" type="hidden" value={topic.id} />
          <button className="ui-button" type="submit">
            通过并生成口播稿
          </button>
        </form>
        <form action={sendTopicFeedbackAction}>
          <input name="episode_id" type="hidden" value={episodeId ?? ""} />
          <input name="topic_id" type="hidden" value={topic.id} />
          <input name="reason" type="hidden" value="这个方向值得继续强化" />
          <button className="ui-button secondary" name="value" type="submit" value="up">
            赞这个选题
          </button>
        </form>
        <form action={sendTopicFeedbackAction}>
          <input name="episode_id" type="hidden" value={episodeId ?? ""} />
          <input name="topic_id" type="hidden" value={topic.id} />
          <input name="reason" type="hidden" value="这个方向暂时不够具体" />
          <button className="ui-button secondary" name="value" type="submit" value="down">
            踩这个选题
          </button>
        </form>
        <form action={sendTopicFeedbackAction}>
          <input name="episode_id" type="hidden" value={episodeId ?? ""} />
          <input name="topic_id" type="hidden" value={topic.id} />
          <input name="reason" type="hidden" value="想换成更贴近日记感的角度" />
          <button className="ui-button secondary" name="value" type="submit" value="down">
            换一个角度
          </button>
        </form>
      </div>
    </section>
  );
}

export default async function TopicsPage({ searchParams }: TopicsPageProps) {
  const params = await searchParams;
  const { episode, evidence, error } = await loadTopicEpisode();
  const topics = episode?.topics ?? [];
  const requestedTopicId =
    typeof params?.topic_id === "string" && params.topic_id.length > 0
      ? params.topic_id
      : null;
  const fallbackTopicId =
    episode?.selected_topic_id ?? topics[0]?.id ?? null;
  const requestedExists =
    requestedTopicId !== null
      ? topics.some((topic) => topic.id === requestedTopicId)
      : true;
  const activeTopicId = requestedExists
    ? requestedTopicId ?? fallbackTopicId
    : fallbackTopicId;
  const selected = topics.find((topic) => topic.id === activeTopicId) ?? null;
  const evidenceByKey = new Map(
    evidence.map((entry) => [
      `${entry.source_type}::${entry.ref}`,
      entry,
    ]),
  );

  return (
    <AppShell>
      <div className="topic-page">
        {requestedTopicId !== null && !requestedExists ? (
          <div
            aria-live="polite"
            className="ui-form-error"
            role="status"
          >
            <span aria-hidden="true" className="ui-form-error-light" />
            <div className="ui-form-error-body">
              <strong>请求的 topic_id 不存在</strong>
              <span>
                找不到 “{requestedTopicId}”，已回退到当前默认选题。
              </span>
            </div>
          </div>
        ) : null}
        <section className="topic-list" aria-label="候选选题">
          {episode && topics.length > 0 ? (
            <form action={regenerateTopicsAction} className="topic-regenerate-form">
              <input name="episode_id" type="hidden" value={episode.id} />
              <button className="ui-button secondary" type="submit">
                重新生成候选
              </button>
            </form>
          ) : episode ? (
            <form action={generateTopicsAction} className="topic-regenerate-form">
              <input name="episode_id" type="hidden" value={episode.id} />
              <button className="ui-button" type="submit">
                生成今日选题
              </button>
            </form>
          ) : null}
          {topics.length > 0 ? (
            topics.map((topic) => (
              <TopicCandidateCard
                active={topic.id === selected?.id}
                key={topic.id}
                topic={topic}
              />
            ))
          ) : (
            <div className="topic-card ui-panel">
              <div className="topic-card-header">
                <span className="score-badge">--</span>
                <span>待生成</span>
              </div>
              <h2>今日暂无候选选题</h2>
              <p>连接恢复后，这里会展示可复核的选题卡片。</p>
            </div>
          )}
        </section>

        {selected ? (
          <TopicDetail
            episodeId={episode?.id ?? null}
            evidenceByKey={evidenceByKey}
            topic={selected}
          />
        ) : (
          <EmptyTopicState episode={episode} error={error} />
        )}
      </div>
    </AppShell>
  );
}
