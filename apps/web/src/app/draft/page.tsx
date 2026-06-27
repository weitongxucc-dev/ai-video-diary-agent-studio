import Link from "next/link";
import {
  createDraftPackageAction,
  importDraftPackageAction,
} from "@/app/actions";
import { AppShell } from "@/components/AppShell";
import { MetricCard } from "@/components/MetricCard";
import { PixelIcon } from "@/components/PixelIcon";
import { getDraftStatus } from "@/lib/api";
import type { DraftAsset, DraftStatus } from "@/lib/types";

export const dynamic = "force-dynamic";

async function loadDraftPage(): Promise<{
  draft: DraftStatus | null;
  offline: boolean;
}> {
  try {
    const draft = await getDraftStatus();
    return { draft, offline: false };
  } catch {
    return { draft: null, offline: true };
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

function formatFileSize(sizeBytes: number): string {
  if (sizeBytes < 1024 * 1024) {
    return `${Math.max(1, Math.round(sizeBytes / 1024))} KB`;
  }

  return `${(sizeBytes / 1024 / 1024).toFixed(1)} MB`;
}

const BASE_TEMPLATE_EFFECT_LABELS = [
  "标题卡",
  "章节字幕条",
  "关键词高亮",
  "重点句大字贴纸",
  "封面",
  "B-roll 插入点",
  "简单转场",
  "背景音乐",
  "片头",
  "片尾",
];

function DraftTemplatePreview({ item }: { item: DraftAsset }) {
  const hasPreview =
    item.template_id !== null ||
    item.track_preview.length > 0 ||
    item.effect_preview.length > 0;
  if (!hasPreview) {
    return null;
  }

  const effects =
    item.effect_preview.length > 0
      ? item.effect_preview
      : BASE_TEMPLATE_EFFECT_LABELS;

  return (
    <div className="draft-template-preview">
      <div className="draft-template-head">
        <strong>剪映轨道结构</strong>
        <span>{item.template_name ?? item.template_id ?? "模板预览"}</span>
      </div>
      <ol className="draft-track-preview">
        {item.track_preview.slice(0, 6).map((track) => (
          <li key={track}>{track}</li>
        ))}
      </ol>
      <div className="draft-effect-list">
        <strong>基础模板元素</strong>
        <div className="draft-effect-chips">
          {effects.slice(0, 10).map((effect) => (
            <span className="draft-effect-chip" key={effect}>
              {effect}
            </span>
          ))}
        </div>
      </div>
    </div>
  );
}

export default async function DraftPage() {
  const { draft, offline } = await loadDraftPage();
  const env = draft?.environment;
  const toolAvailable = env?.draft_tool.available ?? false;
  const bridgeReady = env?.editable_draft_supported ?? false;
  const processedVideos = draft?.processed_videos ?? [];
  const packages = draft?.packages ?? [];
  const recentPackages = packages.slice(0, 5);
  const olderPackages = packages.slice(5);
  return (
    <AppShell>
      <div className="workspace-page draft-workstation">
        <section className="workspace-main retro-panel draft-main">
          <div className="workspace-header">
            <div>
              <span className="section-kicker">剪映草稿</span>
              <h1>草稿素材包</h1>
              <p>
                {offline
                  ? "暂时无法连接本地 API。"
                  : bridgeReady
                    ? "视频剪好后先生成剪映素材包，确认轨道结构后再写入草稿箱。"
                    : toolAvailable
                      ? "已安装草稿工具，请先配置剪映草稿目录。"
                    : "当前未检测到剪映草稿工具，只生成真实可追溯的本地素材包。"}
              </p>
            </div>
            <Link className="pixel-button secondary" href="/video">
              去处理视频
            </Link>
            <Link className="pixel-button secondary" href="/cover">
              编辑封面
            </Link>
          </div>

          <div className="draft-flow draft-stage-strip" aria-label="剪映草稿流程">
            <div className="draft-flow-step active">
              <strong>1 生成素材包</strong>
              <span>从预处理视频生成可追溯素材。</span>
            </div>
            <div className="draft-flow-step">
              <strong>2 确认轨道结构</strong>
              <span>先看主视频、字幕、标题贴纸、关键词和封面轨道。</span>
            </div>
            <div className="draft-flow-step">
              <strong>3 导入剪映验证</strong>
              <span>确认后写入草稿箱，再打开剪映人工验证。</span>
            </div>
          </div>

          <div className="workspace-list">
            {processedVideos.map((video) => (
              <article className="workspace-row draft-row" key={video.path}>
                <PixelIcon name="video" />
                <div>
                  <strong>{video.file_name}</strong>
                  <span>{formatFileSize(video.size_bytes)} · {formatDate(video.modified_at)}</span>
                  <code>{video.path}</code>
                </div>
                <form action={createDraftPackageAction}>
                  <input name="video_file_name" type="hidden" value={video.file_name} />
                  <button className="pixel-button" type="submit">
                    生成素材包
                  </button>
                </form>
              </article>
            ))}
            {processedVideos.length === 0 && (
              <div className="workspace-empty">
                <PixelIcon name="clip" />
                <strong>没有可用预处理视频</strong>
                <p>先在视频处理页生成 preview，再回来创建剪映草稿素材包。</p>
              </div>
            )}
          </div>

          <div className="workspace-subsection">
            <h2>最近生成素材包</h2>
            <div className="workspace-list compact">
              {recentPackages.map((item) => (
                <article className="workspace-row draft-row" key={item.path}>
                  <PixelIcon name="archive" />
                  <div>
                    <strong>{item.file_name}</strong>
                    <span>
                      {formatDate(item.modified_at)}
                      {item.import_status ? ` · ${item.import_status}` : ""}
                    </span>
                    <code>{item.path}</code>
                    {item.import_message ? <p>{item.import_message}</p> : null}
                    {item.draft_path ? <code>{item.draft_path}</code> : null}
                    <DraftTemplatePreview item={item} />
                  </div>
                  {item.import_status === "imported" ? (
                    <span className="step-state done">已导入</span>
                  ) : (
                    <form action={importDraftPackageAction}>
                      <input name="package_name" type="hidden" value={item.file_name} />
                      <button className="pixel-button secondary" type="submit">
                        {item.import_status === "not_attempted" ? "确认导入剪映" : "重试导入剪映"}
                      </button>
                    </form>
                  )}
                </article>
              ))}
              {packages.length === 0 ? (
                <div className="workspace-empty">
                  <PixelIcon name="archive" />
                  <strong>还没有素材包</strong>
                  <p>处理视频后，这里会显示最近生成的剪映素材包。</p>
                </div>
              ) : null}
            </div>
            {olderPackages.length > 0 ? (
              <details className="draft-history">
                <summary>更早素材包 {olderPackages.length} 个</summary>
                <div className="workspace-list compact">
                  {olderPackages.map((item) => (
                    <article className="workspace-row draft-row compact" key={item.path}>
                      <PixelIcon name="archive" />
                      <div>
                        <strong>{item.file_name}</strong>
                        <span>
                          {formatDate(item.modified_at)}
                          {item.import_status ? ` · ${item.import_status}` : ""}
                        </span>
                        <code>{item.path}</code>
                        {item.draft_path ? <code>{item.draft_path}</code> : null}
                        <DraftTemplatePreview item={item} />
                      </div>
                      {item.import_status === "imported" ? (
                        <span className="step-state done">已导入</span>
                      ) : (
                        <form action={importDraftPackageAction}>
                          <input name="package_name" type="hidden" value={item.file_name} />
                          <button className="pixel-button secondary" type="submit">
                            {item.import_status === "not_attempted" ? "确认导入剪映" : "重试导入剪映"}
                          </button>
                        </form>
                      )}
                    </article>
                  ))}
                </div>
              </details>
            ) : null}
          </div>

        </section>

        <aside className="workspace-side draft-inspector">
          <MetricCard icon="clip" value={String(env?.package_count ?? 0)} label="素材包" tone="purple" />
          <MetricCard icon="video" value={String(env?.processed_video_count ?? 0)} label="可用视频" tone="green" />
          <div className="retro-panel-secondary side-note">
            <h2>草稿工具</h2>
            <p>{toolAvailable ? env?.draft_tool.tool_name : "未安装"}</p>
            <code>{env?.draft_tool.checked_modules.join(" / ") ?? "--"}</code>
            <span>{env?.editable_draft_supported ? "兼容桥接可用，需确认导入" : "当前只能生成素材包 fallback"}</span>
          </div>
          <div className="retro-panel-secondary side-note">
            <h2>剪映目录</h2>
            <p>{env?.jianying_root_exists ? "已发现" : "未发现"}</p>
            <code>{env?.jianying_root_path ?? "--"}</code>
            {env?.jianying_blocker ? <span>{env.jianying_blocker}</span> : null}
          </div>
          <div className="retro-panel-secondary side-note">
            <h2>输出目录</h2>
            <code>{env?.output_path ?? "--"}</code>
          </div>
        </aside>
      </div>
    </AppShell>
  );
}
