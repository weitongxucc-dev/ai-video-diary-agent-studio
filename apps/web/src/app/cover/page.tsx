import type { CSSProperties } from "react";
import Link from "next/link";
import { updateCoverAction } from "@/app/actions";
import { AppShell } from "@/components/AppShell";
import { PixelIcon } from "@/components/PixelIcon";
import { getEpisodeCover, getTodayEpisode } from "@/lib/api";
import type { CoverDraft, Episode } from "@/lib/types";

export const dynamic = "force-dynamic";

type CoverPageState = {
  cover: CoverDraft | null;
  episode: Episode | null;
  error: "offline" | "missing_script" | null;
};

type CoverCandidate = {
  id: string;
  label: string;
  headline: string;
  subline: string;
  tag: string;
  template_id: string;
  source: string;
  reason: string;
};

async function loadCoverPage(): Promise<CoverPageState> {
  try {
    const episode = await getTodayEpisode();
    if (!episode.script) {
      return { cover: null, episode, error: "missing_script" };
    }
    return {
      cover: await getEpisodeCover(episode.id),
      episode,
      error: null,
    };
  } catch {
    return { cover: null, episode: null, error: "offline" };
  }
}

function compactText(value: string | null | undefined, fallback: string, maxLength = 44): string {
  const text = value?.trim() || fallback;
  return text.length > maxLength ? `${text.slice(0, maxLength - 1)}...` : text;
}

function selectedTopic(episode: Episode) {
  return episode.topics.find((topic) => topic.id === episode.selected_topic_id) ?? episode.topics[0] ?? null;
}

function deriveCoverCandidates(cover: CoverDraft, episode: Episode): CoverCandidate[] {
  const script = episode.script;
  const topic = selectedTopic(episode);
  const titleOptions = script?.title_options.filter(Boolean) ?? [];
  const hook = script?.hook ?? "";
  const firstSection = script?.sections[0]?.title ?? "";
  const defaultTag = cover.tag || `DAY ${episode.day_number}`;
  const candidates: CoverCandidate[] = [
    {
      id: "script-cover",
      label: "脚本推荐",
      headline: cover.headline,
      subline: cover.subline || compactText(hook, "今天的AI产品观察", 34),
      tag: defaultTag,
      template_id: cover.template_id,
      source: "cover_copy",
      reason: "沿用口播稿生成时写入的封面文案。",
    },
    {
      id: "title-option",
      label: "标题冲突",
      headline: compactText(titleOptions.find((title) => title !== cover.headline), titleOptions[0] ?? cover.headline, 34),
      subline: compactText(hook, cover.subline || firstSection || "把冲突讲清楚", 38),
      tag: defaultTag,
      template_id: cover.template_id,
      source: "title_options + hook",
      reason: "用标题候选承接第一眼冲突，副标题补充开头钩子。",
    },
    {
      id: "topic-angle",
      label: "选题观点",
      headline: compactText(topic?.title, cover.headline, 34),
      subline: compactText(topic?.core_viewpoint, firstSection || cover.subline || "观点更聚焦", 38),
      tag: "观点",
      template_id: cover.template_id,
      source: "selected_topic",
      reason: "从选题核心观点里抽取更稳定的封面承诺。",
    },
  ];

  const seen = new Set<string>();
  return candidates.filter((candidate) => {
    const key = `${candidate.headline}|${candidate.subline}|${candidate.tag}`;
    if (seen.has(key)) {
      return false;
    }
    seen.add(key);
    return true;
  });
}

function CoverEmptyState({
  episode,
  error,
}: {
  episode: Episode | null;
  error: CoverPageState["error"];
}) {
  const copy =
    error === "offline"
      ? {
          title: "暂时无法连接封面工作区",
          description: "本地 API 不可用，稍后刷新即可。",
        }
      : {
          title: "请先生成口播稿",
          description: "封面编辑器会读取脚本里的封面文案和标题候选。",
        };

  return (
    <section className="cover-workbench cover-missing-workbench pixel-card">
      <div className="workspace-header compact">
        <div>
          <span className="section-kicker">封面工位</span>
          <h1>{copy.title}</h1>
          <p>{copy.description}</p>
        </div>
        {episode ? <span className="step-state">Episode #{episode.day_number}</span> : null}
      </div>
      <div className="cover-prereq-grid" aria-label="封面生成前置条件">
        <div>
          <strong>1 口播稿确认</strong>
          <span>封面会读取脚本的标题候选、开头钩子和 cover_copy。</span>
        </div>
        <div>
          <strong>2 生成封面候选</strong>
          <span>脚本完成后，这里会自动整理 3 个封面方向。</span>
        </div>
        <div>
          <strong>3 保存并导出</strong>
          <span>确认后回到剪映草稿，素材包会带上 4:3 cover.png。</span>
        </div>
      </div>
      <div className="script-empty-actions">
        <Link className="pixel-button" href="/script">
          去口播稿
        </Link>
        <Link className="pixel-button secondary" href="/draft">
          去草稿页
        </Link>
      </div>
    </section>
  );
}

function CoverPreview({ cover, episode }: { cover: CoverDraft; episode: Episode }) {
  const template = cover.templates.find((item) => item.template_id === cover.template_id) ?? cover.templates[0];

  return (
    <section className="cover-preview-panel" aria-label="封面预览">
      <div
        className="cover-preview-frame"
        style={{ "--cover-accent": template?.accent ?? "#1d4ed8" } as CSSProperties}
      >
        <div className="cover-preview-top">
          <span>AI VIDEO DIARY</span>
          <strong>DAY {episode.day_number}</strong>
        </div>
        <div className="cover-preview-copy">
          <h1>{cover.headline}</h1>
          <p>{cover.subline || "今天的AI产品观察"}</p>
        </div>
        <div className="cover-preview-footer">
          <strong>{cover.tag || "待导入剪映"}</strong>
          <span>{cover.preview_aspect_ratio}</span>
        </div>
      </div>
    </section>
  );
}

export default async function CoverPage() {
  const { cover, episode, error } = await loadCoverPage();
  const candidates = cover && episode ? deriveCoverCandidates(cover, episode) : [];
  const topic = episode ? selectedTopic(episode) : null;

  return (
    <AppShell>
      <div className="cover-page">
        {cover && episode ? (
          <>
            <section className="cover-workbench cover-primary-stage pixel-card">
              <div className="workspace-header compact cover-workbench-head">
                <div>
                  <span className="section-kicker">封面工位</span>
                  <h1>今天这条视频最终用哪张封面</h1>
                  <p>先看 4:3 预览，再从脚本和选题里选择封面方向，最后保存到草稿素材包。</p>
                </div>
                <Link className="pixel-button secondary" href="/script">
                  回到口播稿
                </Link>
              </div>

              <div className="cover-summary-strip" aria-label="封面当前状态">
                <span>当前文案 · {compactText(cover.cover_copy, cover.headline, 28)}</span>
                <span>候选 · {candidates.length}</span>
                <span>导出 · 4:3 cover.png</span>
              </div>

              <div className="cover-workbench-grid">
                <CoverPreview cover={cover} episode={episode} />
                <div className="cover-decision-panel">
                  <section className="cover-candidate-list" aria-label="封面候选">
                    <div className="cover-panel-title">
                      <span>生成封面候选</span>
                      <strong>{candidates.length} 个方向</strong>
                    </div>
                    {candidates.map((candidate, index) => (
                      <form action={updateCoverAction} className="cover-candidate-card" key={candidate.id}>
                        <input name="episode_id" type="hidden" value={episode.id} />
                        <input name="headline" type="hidden" value={candidate.headline} />
                        <input name="subline" type="hidden" value={candidate.subline} />
                        <input name="tag" type="hidden" value={candidate.tag} />
                        <input name="template_id" type="hidden" value={candidate.template_id} />
                        <div>
                          <span>{String(index + 1).padStart(2, "0")}</span>
                          <strong>{candidate.label}</strong>
                          <em>{candidate.source}</em>
                        </div>
                        <h2>{candidate.headline}</h2>
                        <p>{candidate.subline}</p>
                        <small>{candidate.reason}</small>
                        <button className="pixel-button secondary compact" type="submit">
                          换一版文案
                        </button>
                      </form>
                    ))}
                  </section>
                </div>
              </div>

              <details className="cover-secondary-drawer cover-source-stack" aria-label="封面文案来源">
                <summary>
                  <span>封面文案来源</span>
                  <strong>脚本读取</strong>
                </summary>
                <dl>
                  <div>
                    <dt>cover_copy</dt>
                    <dd>{cover.cover_copy || "--"}</dd>
                  </div>
                  <div>
                    <dt>标题候选</dt>
                    <dd>{episode.script?.title_options.slice(0, 2).join(" / ") || "--"}</dd>
                  </div>
                  <div>
                    <dt>选题观点</dt>
                    <dd>{topic?.core_viewpoint ?? "--"}</dd>
                  </div>
                </dl>
              </details>
            </section>

            <details className="cover-editor cover-secondary-drawer pixel-card">
              <summary>
                <span>微调封面文案</span>
                <strong>主标题 / 副标题 / 标签</strong>
              </summary>
              <p className="cover-secondary-note">
                候选不合适时，直接改主标题、副标题和标签。保存后会更新脚本 cover_copy。
              </p>

              <form action={updateCoverAction} className="cover-editor-form">
                <input name="episode_id" type="hidden" value={episode.id} />
                <label className="settings-field wide">
                  <span>主标题</span>
                  <input
                    defaultValue={cover.headline}
                    maxLength={48}
                    name="headline"
                    placeholder="一句冲突标题"
                  />
                </label>
                <label className="settings-field wide">
                  <span>副标题</span>
                  <input
                    defaultValue={cover.subline}
                    maxLength={72}
                    name="subline"
                    placeholder="一行辅助说明"
                  />
                </label>
                <label className="settings-field">
                  <span>标签</span>
                  <input
                    defaultValue={cover.tag}
                    maxLength={18}
                    name="tag"
                    placeholder="AI观察"
                  />
                </label>

                <fieldset className="cover-template-grid">
                  <legend>封面视觉方向</legend>
                  {cover.templates.map((template) => (
                    <label className="cover-template-option" key={template.template_id}>
                      <input
                        defaultChecked={template.template_id === cover.template_id}
                        name="template_id"
                        type="radio"
                        value={template.template_id}
                      />
                      <span style={{ "--cover-accent": template.accent } as CSSProperties} />
                      <strong>{template.name}</strong>
                      <em>{template.description}</em>
                    </label>
                  ))}
                </fieldset>

                <div className="settings-actions">
                  <button className="pixel-button" type="submit">
                    保存封面设置
                  </button>
                  <Link className="pixel-button secondary" href="/script">
                    回到口播稿
                  </Link>
                </div>
              </form>
            </details>
          </>
        ) : (
          <CoverEmptyState episode={episode} error={error} />
        )}

        <aside className="workspace-side">
          <div className="pixel-card side-note cover-export-status">
            <PixelIcon name="cover" />
            <h2>导出状态</h2>
            <p>{cover ? "待生成素材包" : "等待脚本"}</p>
            <span>保存后回到剪映草稿，生成素材包时写入 4:3 cover.png。</span>
          </div>
          <div className="pixel-card side-note">
            <h2>下一步</h2>
            <p>确认后回到草稿页生成素材包，系统会导出 4:3 cover.png。</p>
            <Link className="pixel-button secondary" href="/draft">
              查看草稿素材包
            </Link>
          </div>
        </aside>
      </div>
    </AppShell>
  );
}
