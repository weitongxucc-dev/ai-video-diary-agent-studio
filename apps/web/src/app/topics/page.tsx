import { AppShell } from "@/components/AppShell";
import { PixelIcon } from "@/components/PixelIcon";
import { ProcessingRefresh } from "@/components/ProcessingRefresh";
import { TopicCandidateCard } from "@/components/TopicCandidateCard";
import {
  approveTopicAndGenerateScript,
  generateTopicsAction,
  regenerateTopicsAction,
  sendTopicFeedbackAction,
} from "@/app/actions";
import { getTodayEpisode } from "@/lib/api";
import type { Episode } from "@/lib/types";
import Link from "next/link";

export const dynamic = "force-dynamic";

type TopicsPageProps = {
  searchParams?: Promise<{ topic_id?: string; topics_error?: string; topics_job?: string }>;
};

type TopicLoadState = {
  episode: Episode | null;
  error: "offline" | null;
};

async function loadTopicEpisode(): Promise<TopicLoadState> {
  try {
    return { episode: await getTodayEpisode(), error: null };
  } catch {
    return { episode: null, error: "offline" };
  }
}

function EmptyTopicState({
  episode,
  error,
  isTopicProcessing,
}: {
  episode: Episode | null;
  error: TopicLoadState["error"];
  isTopicProcessing: boolean;
}) {
  const copy =
    error === "offline"
      ? {
          title: "暂时无法连接选题池",
          description: "API 暂时不可用，稍后再试即可。你也可以先整理今天想聊的素材。",
        }
      : {
          title: "还没有候选选题",
          description: "点击生成后，Agent 会读取 Obsidian、外链素材源和已配置的产品源来生成候选。",
        };

  return (
    <section className="topic-empty-workstation empty-state-workflow">
      <div className="topic-empty-layout">
        <aside className="topic-empty-side retro-panel-secondary">
          <span className="section-kicker">选题池</span>
          <h2>候选将会出现在这里</h2>
          <p>生成后，复核通过的选题卡片会显示在今日工作台中，供你继续优化、提稿与发布。</p>
        </aside>

        <div className="topic-empty-main retro-panel-secondary">
          <div>
            <span className="section-kicker">Agent 工作站</span>
            <h1>{copy.title}</h1>
            <p>{copy.description}</p>
          </div>
          <div className="topic-source-grid" aria-label="Agent 将参考以下数据源">
            <h2>Agent 将参考以下数据源</h2>
            <ul className="topic-source-grid">
              <li>
                <PixelIcon name="folder" />
                <span>Obsidian 个人知识库</span>
                <span className="status-badge"><span className="status-dot ready" /> 已连接</span>
              </li>
              <li>
                <PixelIcon name="link" />
                <span>外链素材源</span>
                <span className="status-badge"><span className="status-dot partial" /> 部分可用</span>
              </li>
              <li>
                <PixelIcon name="product" />
                <span>产品资料库</span>
                <span className="status-badge"><span className="status-dot ready" /> 已就绪</span>
              </li>
              <li>
                <PixelIcon name="archive" />
                <span>历史反馈记录</span>
                <span className="status-badge"><span className="status-dot partial" /> 待积累</span>
              </li>
            </ul>
          </div>
          <div aria-label="生成后你将获得">
            <h2>生成后你将获得</h2>
            <ul className="topic-outcome-list">
              <li><PixelIcon name="spark" /><span>3-5 个候选选题，贴合目标、具备传播潜力</span></li>
              <li><PixelIcon name="agent" /><span>每个选题的爆点理由、口播角度与风险提醒</span></li>
              <li><PixelIcon name="script" /><span>可继续进入口播稿工作流的人工确认入口</span></li>
            </ul>
          </div>
          <div className="script-empty-actions">
            {episode ? (
              <form action={generateTopicsAction}>
                <input name="episode_id" type="hidden" value={episode.id} />
                <button className="pixel-button" disabled={isTopicProcessing} type="submit">
                  {isTopicProcessing ? "生成中" : "生成今日选题"}
                </button>
              </form>
            ) : null}
            <Link className="pixel-button secondary" href="/">
              返回今日工作台
            </Link>
          </div>
        </div>

        <aside className="topic-inspector inspector-panel">
          <h2>Agent 提示</h2>
          <PixelIcon name="agent" />
          <p>我会结合多源信息为你生成更有价值的选题，提升视频内容的传播力与转化潜力。</p>
          <ul className="topic-inspector-list" aria-label="数据源状态">
            <li><span>Obsidian</span><span className="status-badge"><span className="status-dot ready" /> 已连接</span></li>
            <li><span>外链素材源</span><span className="status-badge"><span className="status-dot partial" /> 部分可用</span></li>
            <li><span>产品资料库</span><span className="status-badge"><span className="status-dot ready" /> 已就绪</span></li>
            <li><span>历史反馈记录</span><span className="status-badge"><span className="status-dot partial" /> 待积累</span></li>
          </ul>
          <Link className="pixel-button secondary" href="/settings">
            管理数据源
          </Link>
        </aside>
      </div>
    </section>
  );
}

function TopicJobBanner({ episode }: { episode: Episode | null }) {
  if (!episode) {
    return null;
  }

  const topic_job_status = episode.topic_job_status;

  if (topic_job_status === "processing") {
    return (
      <section className="topic-job-banner pixel-card" role="status">
        <div>
          <span className="section-kicker">后台生成</span>
          <strong>{episode.topic_job_message ?? "正在生成候选选题"}</strong>
          <p>你可以继续查看当前候选；新候选生成完成后会自动刷新。</p>
        </div>
        <ProcessingRefresh label="候选选题正在后台生成，页面会自动刷新。" />
      </section>
    );
  }

  if (topic_job_status === "failed" || topic_job_status === "interrupted") {
    return (
      <section className="topic-job-banner failed pixel-card" role="status">
        <div>
          <span className="section-kicker">生成未完成</span>
          <strong>
            {topic_job_status === "interrupted" ? "后台生成已中断" : "候选生成失败"}
          </strong>
          <p>{episode.topic_job_error ?? "检查配置或稍后重新生成即可。"}</p>
        </div>
        <form action={regenerateTopicsAction}>
          <input name="episode_id" type="hidden" value={episode.id} />
          <button className="pixel-button secondary" type="submit">
            重试生成
          </button>
        </form>
      </section>
    );
  }

  return null;
}

function topicFeedbackLabel(feedback: string | null) {
  if (feedback === "up") return "已赞";
  if (feedback === "down") return "已踩";
  return "待复核";
}

function topicActionErrorCopy(error?: string) {
  if (error === "configuration") {
    return {
      title: "选题生成需要先完成配置",
      description: "请先配置 DeepSeek，并至少保留一个可用素材源。配置完成后再生成今日选题。",
      actionHref: "/settings",
      actionLabel: "打开设置",
    };
  }
  if (error === "generation_failed") {
    return {
      title: "选题生成暂时失败",
      description: "服务已经运行，但这次生成没有完成。检查日志或稍后重新生成即可。",
      actionHref: "/logs",
      actionLabel: "查看日志",
    };
  }
  return null;
}

export default async function TopicsPage({ searchParams }: TopicsPageProps) {
  const params = await searchParams;
  const { episode, error } = await loadTopicEpisode();
  const topics = episode?.topics ?? [];
  const isTopicProcessing = episode?.topic_job_status === "processing";
  const activeTopicId = params?.topic_id ?? episode?.selected_topic_id ?? topics[0]?.id;
  const selected =
    topics.find((topic) => topic.id === activeTopicId) ?? topics[0] ?? null;
  const actionError = topicActionErrorCopy(params?.topics_error);

  return (
    <AppShell>
      <div className="topic-page">
        {actionError ? (
          <section className="topic-error-banner pixel-card">
            <div>
              <strong>{actionError.title}</strong>
              <p>{actionError.description}</p>
            </div>
            <Link className="pixel-button secondary" href={actionError.actionHref}>
              {actionError.actionLabel}
            </Link>
          </section>
        ) : null}
        <section className="topic-list" aria-label="候选选题">
          {episode && topics.length > 0 ? (
            <form action={regenerateTopicsAction} className="topic-regenerate-form">
              <input name="episode_id" type="hidden" value={episode.id} />
              <button className="pixel-button secondary" disabled={isTopicProcessing} type="submit">
                {isTopicProcessing ? "后台生成中" : "重新生成候选"}
              </button>
            </form>
          ) : episode ? (
            <form action={generateTopicsAction} className="topic-regenerate-form">
              <input name="episode_id" type="hidden" value={episode.id} />
              <button className="pixel-button" disabled={isTopicProcessing} type="submit">
                {isTopicProcessing ? "后台生成中" : "生成今日选题"}
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
            <div className="topic-card pixel-card">
              <div className="topic-card-header">
                <span className="score-badge">--</span>
                <span>待生成</span>
              </div>
              <h2>今日暂无候选选题</h2>
              <p>连接恢复后，这里会展示可复核的选题卡片。</p>
            </div>
          )}
        </section>

        <div className="topic-main-column">
          <TopicJobBanner episode={episode} />
          {selected ? (
            <section className="topic-detail pixel-card">
              <header className="topic-detail-header">
                <div>
                  <span className="section-kicker">推荐选题</span>
                  <h1>{selected.title}</h1>
                  <p>{selected.reason}</p>
                </div>
                <div className="topic-detail-status" aria-label="选题状态">
                  <span>分数</span>
                  <strong>{selected.score}</strong>
                  <em>{topicFeedbackLabel(selected.feedback)}</em>
                </div>
              </header>

              <div className="topic-review-workbench">
                <div className="topic-detail-main">
                  <section className="topic-console-panel">
                    <h3>3 分钟结构</h3>
                    <ol>
                      {selected.outline.map((item, index) => (
                        <li key={item}>
                          <span>{String(index + 1).padStart(2, "0")}</span>
                          <strong>{item}</strong>
                        </li>
                      ))}
                    </ol>
                  </section>

                  <section className="topic-console-panel">
                    <h3>核心观点</h3>
                    <p>{selected.core_viewpoint}</p>
                    <dl className="topic-mini-meta">
                      <div><dt>受众</dt><dd>{selected.audience}</dd></div>
                      <div><dt>选题类型</dt><dd>技术反思</dd></div>
                    </dl>
                  </section>

                  <section className="topic-console-panel evidence-console">
                    <h3>来源依据</h3>
                    <div className="evidence-list">
                      {selected.source_refs.map((source) => (
                        <div className="evidence-item" key={`${source.source_type}-${source.ref}`}>
                          <strong>{source.source_type}</strong>
                          <span>{source.title}</span>
                          <code>{source.ref}</code>
                        </div>
                      ))}
                      {selected.source_refs.length === 0 ? (
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
                    <ul>
                      <li>
                        <span className="pixel-icon-slot">
                          <PixelIcon name="check" />
                        </span>
                        有真实案例支撑
                      </li>
                      <li>
                        <span className="pixel-icon-slot">
                          <PixelIcon name="check" />
                        </span>
                        适合 60 秒口播
                      </li>
                      <li>
                        <span className="pixel-icon-slot">
                          <PixelIcon name="note" />
                        </span>
                        冲突观点需再锐化
                      </li>
                      <li>
                        <span className="pixel-icon-slot">
                          <PixelIcon name="note" />
                        </span>
                        可作为今日发布
                      </li>
                    </ul>
                  </section>

                  <section className="topic-audit-card">
                    <h3>选题元信息</h3>
                    <dl>
                      <div><dt>素材真实性</dt><dd>{selected.source_refs.length} 条来源</dd></div>
                      <div><dt>冲突感</dt><dd>中</dd></div>
                      <div><dt>发布优先级</dt><dd>{selected.score >= 8 ? "高" : "中"}</dd></div>
                      <div><dt>风险</dt><dd>案例可更具体</dd></div>
                    </dl>
                  </section>

                  <section className="topic-agent-terminal">
                    <h3>Agent 判断</h3>
                    <p>推荐原因：技术人自省类选题与近期偏好匹配</p>
                    <p>风险：案例不够具体</p>
                    <p>建议：加入一次真实项目决策场景</p>
                  </section>
                </aside>
              </div>

              <div className="topic-action-dock">
                <div>
                  <strong>操作台</strong>
                  <span>确认后将进入口播稿生成流程</span>
                </div>
                <form action={approveTopicAndGenerateScript}>
                  <input name="episode_id" type="hidden" value={episode?.id ?? ""} />
                  <input name="topic_id" type="hidden" value={selected.id} />
                  <button className="pixel-button" type="submit">
                    通过并生成口播稿
                  </button>
                </form>
                <form action={sendTopicFeedbackAction}>
                  <input name="episode_id" type="hidden" value={episode?.id ?? ""} />
                  <input name="topic_id" type="hidden" value={selected.id} />
                  <input name="reason" type="hidden" value="这个方向值得继续强化" />
                  <button className="pixel-button secondary" name="value" type="submit" value="up">
                    赞这个选题
                  </button>
                </form>
                <form action={sendTopicFeedbackAction}>
                  <input name="episode_id" type="hidden" value={episode?.id ?? ""} />
                  <input name="topic_id" type="hidden" value={selected.id} />
                  <input name="reason" type="hidden" value="这个方向暂时不够具体" />
                  <button className="pixel-button secondary" name="value" type="submit" value="down">
                    踩这个选题
                  </button>
                </form>
                <form action={sendTopicFeedbackAction}>
                  <input name="episode_id" type="hidden" value={episode?.id ?? ""} />
                  <input name="topic_id" type="hidden" value={selected.id} />
                  <input name="reason" type="hidden" value="想换成更贴近日记感的角度" />
                  <button className="pixel-button secondary" name="value" type="submit" value="down">
                    换一个角度
                  </button>
                </form>
              </div>
            </section>
          ) : (
            <EmptyTopicState
              episode={episode}
              error={error}
              isTopicProcessing={isTopicProcessing}
            />
          )}
        </div>
      </div>
    </AppShell>
  );
}
