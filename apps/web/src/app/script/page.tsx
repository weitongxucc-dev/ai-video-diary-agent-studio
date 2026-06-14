import Link from "next/link";
import {
  approveScriptAction,
  regenerateScriptAction,
  regenerateScriptSectionAction,
  revertScriptVersionAction,
  saveScriptAction,
  sendScriptSectionFeedbackAction,
} from "@/app/actions";
import { AppShell } from "@/components/AppShell";
import { PixelIcon } from "@/components/PixelIcon";
import { ScriptQuickRewritePanel } from "@/components/ScriptQuickRewritePanel";
import { ScriptSectionEditor } from "@/components/ScriptSectionEditor";
import {
  getScriptRewriteIntents,
  getTodayEpisode,
} from "@/lib/api";
import type {
  Episode,
  ScriptPackage,
  ScriptRewriteIntent,
  ScriptVersionSource,
} from "@/lib/types";

export const dynamic = "force-dynamic";

type ScriptLoadError =
  | "offline"
  | "topics_failed"
  | "no_topics"
  | "needs_topic_review"
  | "script_failed"
  | "missing_script";

type ScriptLoadState = {
  episode: Episode | null;
  error: ScriptLoadError | null;
  script: ScriptPackage | null;
};

type ScriptPageProps = {
  searchParams?: Promise<{ script_error?: string }>;
};

async function loadScriptEpisode(): Promise<ScriptLoadState> {
  let episode: Episode;

  try {
    episode = await getTodayEpisode();
  } catch {
    return { episode: null, error: "offline", script: null };
  }

  if (episode.topics.length === 0) {
    return { episode, error: "no_topics", script: null };
  }

  if (!episode.selected_topic_id) {
    return { episode, error: "needs_topic_review", script: null };
  }

  if (!episode.script) {
    return { episode, error: "missing_script", script: null };
  }

  return { episode, error: null, script: episode.script };
}

async function loadScriptRewriteIntents(): Promise<ScriptRewriteIntent[]> {
  try {
    return (await getScriptRewriteIntents()).intents;
  } catch {
    return [];
  }
}

function EmptyScriptState({
  episode,
  error
}: {
  episode: Episode | null;
  error: ScriptLoadError | null;
}) {
  const copy =
    error === "offline"
      ? {
          title: "暂时无法连接口播稿工作流",
          description: "API 暂时不可用，稍后刷新即可。你也可以先整理今天想讲的素材。"
        }
      : error === "topics_failed"
        ? {
            title: "还没有可用选题",
            description: "今日 Episode 已创建，但选题生成没有完成。请先去选题池生成候选选题。"
          }
        : error === "no_topics"
          ? {
              title: "请先生成今日选题",
              description: "系统没有返回候选选题，因此还不能生成口播稿。"
            }
          : error === "needs_topic_review"
            ? {
                title: "请先人工复核选题",
                description: "Agent 已经准备好候选选题，但口播稿必须在你确认选题后才会生成。"
              }
            : error === "script_failed"
              ? {
                  title: "暂时无法生成口播稿",
                  description: "选题已经准备好，但脚本生成没有完成。稍后刷新页面再试。"
                }
              : {
                  title: "还没有口播稿",
                  description: "当前 Episode 没有可展示的脚本内容，请先完成选题确认和脚本生成。"
                };

  return (
    <section className="script-empty pixel-card">
      <span className="section-kicker">口播稿</span>
      <h1>{copy.title}</h1>
      <p>{copy.description}</p>
      {episode ? (
        <p className="script-empty-meta">Episode #{episode.day_number}</p>
      ) : null}
      <div className="script-empty-actions">
        <Link className="pixel-button" href="/topics">
          去选题池
        </Link>
        <Link className="pixel-button secondary" href="/">
          返回今日工作台
        </Link>
      </div>
    </section>
  );
}

function ScriptActionErrorBanner({ scriptError }: { scriptError?: string }) {
  if (scriptError !== "section_rewrite_failed") {
    return null;
  }

  return (
    <section className="script-error-banner pixel-card" role="status">
      <span className="section-kicker">重写未完成</span>
      <strong>模型返回格式异常，已保留当前口播稿。</strong>
      <p>可以再点一次快捷重写，或在“补充要求”里写得更具体一点。</p>
    </section>
  );
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

function scriptVersionLabel(source: ScriptVersionSource): string {
  const labels: Record<ScriptVersionSource, string> = {
    ai_generated: "初稿生成",
    full_regeneration: "整稿重写",
    manual_edit: "手动保存",
    revert: "版本恢复",
    section_regeneration: "单段重写",
  };

  return labels[source];
}

function episodeScriptStatusLabel(status: Episode["status"]): string {
  const labels: Partial<Record<Episode["status"], string>> = {
    script_ready: "待确认",
    script_approved: "已确认",
    waiting_video: "等待录制",
    video_processing: "视频处理中",
    draft_ready: "草稿就绪",
    published: "已发布",
  };

  return labels[status] ?? "编辑中";
}

export default async function ScriptPage({ searchParams }: ScriptPageProps) {
  const params = await searchParams;
  const { episode, error, script } = await loadScriptEpisode();
  const rewriteIntents = script ? await loadScriptRewriteIntents() : [];
  const scriptFormId = episode ? `script-edit-form-${episode.id}` : undefined;
  const titleOptions = script?.title_options.length ? script.title_options : [""];
  const versions = episode ? [...episode.script_versions].reverse() : [];
  const totalSeconds = script
    ? 3 + script.sections.reduce((sum, section) => sum + section.estimated_seconds, 0)
    : 0;
  const feedbackCount = script
    ? script.sections.filter((section) => Boolean(section.feedback)).length
    : 0;
  const sectionCount = script ? script.sections.length + 1 : 0;

  return (
    <AppShell>
      <div className="script-page">
        <ScriptActionErrorBanner scriptError={params?.script_error} />
        {script && episode && scriptFormId ? (
          <>
            <form action={saveScriptAction} className="script-edit-owner" id={scriptFormId}>
              <input name="episode_id" type="hidden" value={episode.id} />
              <input name="script_id" type="hidden" value={script.id} />
              <input name="reason" type="hidden" value="用户手动编辑口播稿" />
            </form>
            <section className="script-main" aria-label="口播稿正文">
              <section className="script-dashboard pixel-card" aria-label="脚本仪表盘">
                <div className="script-dashboard-stat">
                  <span>总时长</span>
                  <strong>{totalSeconds}s</strong>
                  <small>含开头 3 秒</small>
                </div>
                <div className="script-dashboard-stat">
                  <span>段落</span>
                  <strong>{sectionCount}</strong>
                  <small>结构完整</small>
                </div>
                <div className="script-dashboard-stat">
                  <span>反馈</span>
                  <strong>{feedbackCount}/{script.sections.length}</strong>
                  <small>可继续校准</small>
                </div>
                <div className="script-dashboard-stat">
                  <span>状态</span>
                  <strong>{episodeScriptStatusLabel(episode.status)}</strong>
                  <small>保存后可确认</small>
                </div>
                <nav className="script-section-nav" aria-label="段落导航">
                  <a href="#script-hook">开头</a>
                  {script.sections.map((section, index) => (
                    <a href={`#script-section-${section.id}`} key={section.id}>
                      {String(index + 1).padStart(2, "0")} {section.title}
                    </a>
                  ))}
                </nav>
              </section>

              <section className="script-primary-actions pixel-card" aria-label="口播稿主操作">
                <div>
                  <span className="section-kicker">写稿操作</span>
                  <strong>先保存，再确认录制</strong>
                  <p>发布素材和历史记录已收进右侧面板，正文区只保留当前写稿动作。</p>
                </div>
                <button className="pixel-button" form={scriptFormId} type="submit">
                  保存口播稿修改
                </button>
                <form action={regenerateScriptAction} className="script-control-form">
                  <input name="episode_id" type="hidden" value={episode.id} />
                  <input
                    name="reason"
                    placeholder="更犀利、更像自己、少概念"
                    type="text"
                  />
                  <button className="pixel-button secondary" type="submit">
                    重新生成整篇
                  </button>
                </form>
                <form action={approveScriptAction}>
                  <input name="episode_id" type="hidden" value={episode.id} />
                  <button className="pixel-button" type="submit">
                    确认脚本，等待录制视频
                  </button>
                </form>
              </section>

              <section className="script-hook pixel-card" id="script-hook">
                <div className="script-section-header compact">
                  <div>
                    <span className="section-kicker">开头 3 秒</span>
                    <strong>钩子句</strong>
                  </div>
                  <span className="script-section-badge">3s</span>
                </div>
                <textarea
                  aria-label="开头 3 秒口播稿"
                  className="script-hook-textarea"
                  defaultValue={script.hook}
                  form={scriptFormId}
                  name="hook"
                />
              </section>
              {script.sections.map((section) => (
                <div className="script-section-stack" key={section.id}>
                  <ScriptSectionEditor formId={scriptFormId} section={section} />
                  <details className="script-section-drawer">
                    <summary>
                      <span>本段校准</span>
                      <strong>快捷重写 / 反馈</strong>
                    </summary>
                    <ScriptQuickRewritePanel
                      episodeId={episode.id}
                      intents={rewriteIntents}
                      sectionId={section.id}
                      sectionTitle={section.title}
                    />
                    <div className="script-section-tools">
                      <div className="feedback-actions" aria-label={`${section.title} 反馈操作`}>
                        <form action={sendScriptSectionFeedbackAction}>
                          <input name="episode_id" type="hidden" value={episode.id} />
                          <input name="section_id" type="hidden" value={section.id} />
                          <input name="reason" type="hidden" value="这一段可以保留这种表达" />
                          <button
                            className={`feedback-button${section.feedback === "up" ? " active" : ""}`}
                            type="submit"
                            name="value"
                            value="up"
                            aria-label="点赞反馈"
                            aria-pressed={section.feedback === "up"}
                          >
                            <PixelIcon name="thumb-up" />
                            <span>赞</span>
                          </button>
                        </form>
                        <form action={sendScriptSectionFeedbackAction}>
                          <input name="episode_id" type="hidden" value={episode.id} />
                          <input name="section_id" type="hidden" value={section.id} />
                          <input name="reason" type="hidden" value="这一段需要重新调整表达" />
                          <button
                            className={`feedback-button${section.feedback === "down" ? " active" : ""}`}
                            type="submit"
                            name="value"
                            value="down"
                            aria-label="点踩反馈"
                            aria-pressed={section.feedback === "down"}
                          >
                            <PixelIcon name="thumb-down" />
                            <span>踩</span>
                          </button>
                        </form>
                      </div>
                      <form
                        action={regenerateScriptSectionAction}
                        className="section-regenerate-form"
                      >
                        <input name="episode_id" type="hidden" value={episode.id} />
                        <input name="section_id" type="hidden" value={section.id} />
                        <input
                          name="reason"
                          placeholder="更像日记、少一点营销感"
                          type="text"
                        />
                        <button className="pixel-button secondary" type="submit">
                          重写本段
                        </button>
                      </form>
                    </div>
                  </details>
                </div>
              ))}
            </section>

            <aside className="script-side pixel-card">
              <details className="script-side-panel script-secondary-drawer script-publish-summary">
                <summary>
                  <span>发布素材</span>
                  <strong>{titleOptions.length}</strong>
                </summary>
                <div className="script-publish-preview">
                  <p>{titleOptions[0] || "等待标题候选"}</p>
                  <span>{script.cover_copy}</span>
                </div>
                <h3>标题候选</h3>
                {titleOptions.map((title, index) => (
                  <input
                    className="script-title-option-input"
                    form={scriptFormId}
                    key={`${title}-${index}`}
                    name="title_option"
                    defaultValue={title}
                  />
                ))}
                <h3>封面文案</h3>
                <textarea
                  className="script-side-textarea"
                  defaultValue={script.cover_copy}
                  form={scriptFormId}
                  name="cover_copy"
                />
                <h3>发布文案</h3>
                <textarea
                  className="script-side-textarea"
                  defaultValue={script.publish_copy}
                  form={scriptFormId}
                  name="publish_copy"
                />
                <h3>评论引导</h3>
                <textarea
                  className="script-side-textarea"
                  defaultValue={script.comment_prompt}
                  form={scriptFormId}
                  name="comment_prompt"
                />
              </details>

              <details className="script-side-panel script-secondary-drawer script-history-panel">
                <summary>
                  <span>版本历史</span>
                  <strong>{versions.length}</strong>
                </summary>
                <div className="script-version-list">
                  {versions.map((version) => (
                    <article className="script-version-row" key={version.id}>
                      <div>
                        <strong>{scriptVersionLabel(version.source)}</strong>
                        <span>
                          {formatDate(version.created_at)}
                          {version.active ? " · 当前" : ""}
                        </span>
                        {version.reason ? <p>{version.reason}</p> : null}
                      </div>
                      <form action={revertScriptVersionAction}>
                        <input name="episode_id" type="hidden" value={episode.id} />
                        <input name="version_id" type="hidden" value={version.id} />
                        <input name="reason" type="hidden" value="从版本历史恢复" />
                        <button
                          className="pixel-button secondary"
                          disabled={version.active}
                          type="submit"
                        >
                          恢复
                        </button>
                      </form>
                    </article>
                  ))}
                </div>
              </details>
            </aside>
          </>
        ) : (
          <EmptyScriptState episode={episode} error={error} />
        )}
      </div>
    </AppShell>
  );
}
