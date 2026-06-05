import Link from "next/link";
import { ingestManualLinkAction, syncSourcesAction } from "@/app/actions";
import { AppShell } from "@/components/AppShell";
import { MetricCard } from "@/components/MetricCard";
import { PixelIcon } from "@/components/PixelIcon";
import { getSourceItems, getSystemStatus } from "@/lib/api";
import type { SourceItems, SystemStatus } from "@/lib/types";

export const dynamic = "force-dynamic";

async function loadSources(): Promise<{
  sources: SourceItems | null;
  status: SystemStatus | null;
  offline: boolean;
}> {
  try {
    const [sources, status] = await Promise.all([getSourceItems(), getSystemStatus()]);
    return { sources, status, offline: false };
  } catch {
    return { sources: null, status: null, offline: true };
  }
}

export default async function SourcesPage() {
  const { sources, status, offline } = await loadSources();
  const counts = sources?.source_counts ?? {};

  return (
    <AppShell>
      <div className="workspace-page">
        <section className="workspace-main pixel-card">
          <div className="workspace-header">
            <div>
              <span className="section-kicker">信息源</span>
              <h1>Hermes 雷达素材</h1>
              <p>
                {offline
                  ? "暂时无法连接本地 API。"
                  : `已读取 ${sources?.total_count ?? 0} 条今日外部素材。`}
              </p>
            </div>
            <div className="topic-actions">
              <form action={syncSourcesAction}>
                <button className="pixel-button secondary" type="submit">
                  同步信息源
                </button>
              </form>
              <Link className="pixel-button" href="/topics">
                去生成选题
              </Link>
            </div>
          </div>

          <form action={ingestManualLinkAction} className="manual-link-form">
            <PixelIcon name="link" />
            <input
              aria-label="手动链接"
              name="url"
              placeholder="粘贴 Product Hunt、观猹或任意文章链接"
              type="url"
            />
            <button className="pixel-button secondary" type="submit">
              识别链接
            </button>
          </form>

          <div className="source-counts">
            {Object.entries(counts).map(([source, count]) => (
              <span className="source-badge" key={source}>
                {source} · {count}
              </span>
            ))}
          </div>

          <div className="workspace-list">
            {(sources?.items ?? []).map((item) => (
              <article className="workspace-row" key={item.ref}>
                <PixelIcon name={item.source_type === "Product Hunt" ? "product" : "link"} />
                <div>
                  <strong>{item.title}</strong>
                  <span>{item.source_type} · {item.ref}</span>
                  <p>{item.excerpt}</p>
                </div>
              </article>
            ))}
            {sources?.items.length === 0 && (
              <div className="workspace-empty">
                <PixelIcon name="link" />
                <strong>还没有素材</strong>
                <p>等待 Hermes 日报落盘，或检查 `HERMES_RADAR_PATH`。</p>
              </div>
            )}
          </div>
        </section>

        <aside className="workspace-side">
          <MetricCard icon="ingest" value={String(status?.hermes_radar.total_count ?? 0)} label="Hermes" tone="blue" />
          <MetricCard icon="product" value={String(status?.hermes_radar.product_hunt_count ?? 0)} label="Product Hunt" tone="green" />
          <MetricCard icon="link" value={String(status?.hermes_radar.watcha_count ?? 0)} label="观猹" tone="orange" />
          <div className="pixel-card side-note">
            <h2>日报文件</h2>
            <p>{status?.hermes_radar.latest_report ?? "未发现"}</p>
            <code>{status?.hermes_radar.path ?? "--"}</code>
          </div>
        </aside>
      </div>
    </AppShell>
  );
}
