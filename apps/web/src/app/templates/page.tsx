import Link from "next/link";
import {
  refreshTemplatesAction,
  selectContentTemplateAction,
} from "@/app/actions";
import { AppShell } from "@/components/AppShell";
import { PixelIcon } from "@/components/PixelIcon";
import { PixelTooltip } from "@/components/PixelTooltip";
import { getContentTemplates, getTodayEpisode } from "@/lib/api.server";
import type { ContentTemplate, Episode } from "@/lib/types";

export const dynamic = "force-dynamic";

async function loadTemplateState(): Promise<{
  episode: Episode | null;
  templates: ContentTemplate[];
  offline: boolean;
}> {
  try {
    const [episode, templates] = await Promise.all([
      getTodayEpisode(),
      getContentTemplates(),
    ]);
    return { episode, templates: templates.templates, offline: false };
  } catch {
    return { episode: null, templates: [], offline: true };
  }
}

function joinList(items: string[]): string {
  return items.join(" / ");
}

function EmptyTemplatesState({ offline }: { offline: boolean }) {
  return (
    <div className="template-empty pixel-card">
      <div className="template-empty-icon" aria-hidden="true">
        <span className="template-empty-icon-frame">
          <PixelIcon name="product" />
        </span>
      </div>
      <h2>暂无内容模板</h2>
      <p>
        内容模板定义了视频类型、脚本结构和剪辑风格。
      </p>
      {offline ? (
        <span className="template-empty-hint">
          API 未连接，刷新无效。请先确认本地 API 已启动。
        </span>
      ) : (
        <form action={refreshTemplatesAction} className="template-empty-actions">
          <button className="pixel-button" type="submit">
            刷新模板
          </button>
        </form>
      )}
    </div>
  );
}

export default async function TemplatesPage() {
  const { episode, templates, offline } = await loadTemplateState();
  const selectedTemplateId = episode?.content_template_id ?? "video_diary";
  const hasTemplates = templates.length > 0;

  return (
    <AppShell>
      <div className="workspace-page templates-page">
        <section className="workspace-main pixel-card">
          <div className="workspace-header">
            <div>
              <span className="section-kicker">内容模板</span>
              <h1>2.0 内容类型控制台</h1>
              <p>
                {offline
                  ? "暂时无法连接本地 API。"
                  : "选择今天 Episode 的生产结构，后续选题、脚本、素材和剪映样式都会围绕这个模板推进。"}
              </p>
            </div>
            <Link className="pixel-button secondary" href="/">
              返回工作台
            </Link>
          </div>

          <div className="template-grid">
            {hasTemplates
              ? templates.map((template) => {
                  const active = template.template_id === selectedTemplateId;

                  return (
                    <article
                      className={`template-card${active ? " active" : ""}`}
                      key={template.template_id}
                    >
                      <div className="template-card-head">
                        <PixelIcon name="product" />
                        <div>
                          <span>{template.template_id}</span>
                          <h2>{template.name}</h2>
                        </div>
                        <em>{active ? "当前" : "可选"}</em>
                      </div>

                      <p>{template.scenario}</p>
                      <dl className="template-spec-table">
                        <div>
                          <dt>推荐时长</dt>
                          <dd>{template.recommended_duration}</dd>
                        </div>
                        <div>
                          <dt>选题结构</dt>
                          <dd>{joinList(template.topic_structure)}</dd>
                        </div>
                        <div>
                          <dt>脚本结构</dt>
                          <dd>{joinList(template.script_structure)}</dd>
                        </div>
                        <div>
                          <dt>素材需求</dt>
                          <dd>{joinList(template.material_requirements)}</dd>
                        </div>
                        <div>
                          <dt>剪映样式</dt>
                          <dd>{template.jianying_style}</dd>
                        </div>
                        <div>
                          <dt>反馈维度</dt>
                          <dd>{joinList(template.default_feedback_dimensions)}</dd>
                        </div>
                      </dl>

                      {episode ? (
                        <form action={selectContentTemplateAction}>
                          <input name="episode_id" type="hidden" value={episode.id} />
                          <input
                            name="template_id"
                            type="hidden"
                            value={template.template_id}
                          />
                          <button
                            className={
                              active ? "pixel-button secondary" : "pixel-button"
                            }
                            disabled={active}
                            type="submit"
                          >
                            {active ? "已选择" : "选择这个模板"}
                          </button>
                        </form>
                      ) : (
                        <PixelTooltip label="请先创建一个 Episode">
                          <button
                            aria-label="等待 Episode"
                            className="pixel-button"
                            disabled
                            type="button"
                          >
                            等待 Episode
                          </button>
                        </PixelTooltip>
                      )}
                    </article>
                  );
                })
              : <EmptyTemplatesState offline={offline} />}
          </div>
        </section>

        <aside className="workspace-side">
          <div className="pixel-card side-note">
            <h2>当前 Episode</h2>
            <p>模板：{selectedTemplateId}</p>
            <p>状态：{episode?.status ?? "--"}</p>
          </div>
          <div className="pixel-card side-note">
            <h2>2.0 范围</h2>
            <p>video_diary / tool_review / tutorial_demo 是 2.0 的第一组内容生产模板。</p>
          </div>
        </aside>
      </div>
    </AppShell>
  );
}
