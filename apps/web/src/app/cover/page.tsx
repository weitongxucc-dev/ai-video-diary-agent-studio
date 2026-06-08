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
    <section className="script-empty pixel-card">
      <span className="section-kicker">封面</span>
      <h1>{copy.title}</h1>
      <p>{copy.description}</p>
      {episode ? <p className="script-empty-meta">Episode #{episode.day_number}</p> : null}
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
  return (
    <section className="cover-preview-panel pixel-card" aria-label="封面预览">
      <div className="cover-preview-frame">
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

  return (
    <AppShell>
      <div className="cover-page">
        {cover && episode ? (
          <>
            <CoverPreview cover={cover} episode={episode} />
            <section className="cover-editor pixel-card">
              <div className="workspace-header compact">
                <div>
                  <span className="section-kicker">封面编辑器</span>
                  <h1>确认剪映封面</h1>
                  <p>编辑主标题、副标题和标签。保存后会更新封面文案，素材包生成时会写入新的 cover.png。</p>
                </div>
                <Link className="pixel-button secondary" href="/draft">
                  回到草稿
                </Link>
              </div>

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
                  <legend>封面风格模板</legend>
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
            </section>
          </>
        ) : (
          <CoverEmptyState episode={episode} error={error} />
        )}

        <aside className="workspace-side">
          <div className="pixel-card side-note">
            <PixelIcon name="cover" />
            <h2>封面来源</h2>
            <p>当前封面会写入脚本的 cover_copy 字段。</p>
            <code>{cover?.cover_copy ?? "--"}</code>
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
