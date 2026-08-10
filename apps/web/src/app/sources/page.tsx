import Link from "next/link";
import {
  ingestManualLinkAction,
  retrySourceSyncAction,
  syncSourcesAction,
} from "@/app/actions";
import { AppShell } from "@/components/AppShell";
import { MetricCard } from "@/components/MetricCard";
import { PixelIcon } from "@/components/PixelIcon";
import { getSourceItems, getSourcesStatus, getSystemStatus } from "@/lib/api";
import type {
  SourceItems,
  SourcesStatus,
  SourceSyncStatus,
  SystemStatus,
} from "@/lib/types";

export const dynamic = "force-dynamic";

type SourceTone = "green" | "yellow" | "red" | "muted";

type SourcesLoadState = {
  sources: SourceItems | null;
  sourcesStatus: SourcesStatus | null;
  status: SystemStatus | null;
  offline: boolean;
};

async function loadSources(): Promise<SourcesLoadState> {
  try {
    const [sources, sourcesStatus, status] = await Promise.all([
      getSourceItems(),
      getSourcesStatus().catch(() => null),
      getSystemStatus(),
    ]);
    return { sources, sourcesStatus, status, offline: false };
  } catch {
    return { sources: null, sourcesStatus: null, status: null, offline: true };
  }
}

function formatDateTime(value: string | null): string {
  if (!value) {
    return "从未同步";
  }
  return new Intl.DateTimeFormat("zh-CN", {
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    month: "2-digit",
    timeZone: "Asia/Shanghai",
  }).format(new Date(value));
}

function formatDate(value: string | null): string {
  if (!value) {
    return "从未同步";
  }
  return new Intl.DateTimeFormat("zh-CN", {
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    month: "2-digit",
    timeZone: "Asia/Shanghai",
  }).format(new Date(value));
}

function getSourceTone(source: SourceSyncStatus): SourceTone {
  if (!source.configured) {
    return "muted";
  }
  if (source.error_message && !source.last_success_at) {
    return "red";
  }
  if (source.error_message) {
    return "yellow";
  }
  return "green";
}

const TONE_LABEL: Record<SourceTone, string> = {
  green: "同步正常",
  yellow: "近期失败",
  red: "同步失败",
  muted: "未配置",
};

export default async function SourcesPage() {
  const { sources, sourcesStatus, status, offline } = await loadSources();
  const counts = sources?.source_counts ?? {};
  const syncSources = sourcesStatus?.statuses ?? [];
  const healthyCount = syncSources.filter(
    (s) => s.configured && !s.error_message,
  ).length;
  const degradedCount = syncSources.filter(
    (s) => s.configured && s.error_message && s.last_success_at,
  ).length;
  const failedCount = syncSources.filter(
    (s) => s.configured && s.error_message && !s.last_success_at,
  ).length;
  const notConfiguredCount = syncSources.filter((s) => !s.configured).length;

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

          <section aria-label="来源同步健康" className="source-health-section">
            <div className="source-health-heading">
              <strong>同步健康</strong>
              <span>
                健康 {healthyCount} · 降级 {degradedCount} · 失败 {failedCount} · 未配置{" "}
                {notConfiguredCount}
              </span>
            </div>
            {syncSources.length > 0 ? (
              <div className="source-health-grid">
                {syncSources.map((source) => {
                  const tone = getSourceTone(source);
                  const retryable = source.configured && tone !== "green";
                  return (
                    <article
                      className={`source-health-card ${tone}`}
                      data-source-key={source.source_type}
                      key={source.source_type}
                    >
                      <header className="source-health-card-head">
                        <span
                          aria-label={`状态：${TONE_LABEL[tone]}`}
                          className={`source-health-light ${tone}`}
                          title={TONE_LABEL[tone]}
                        />
                        <strong>{source.source_type}</strong>
                        <span className={`step-state ${tone}`}>
                          {TONE_LABEL[tone]}
                        </span>
                      </header>
                      <div className="source-health-body">
                        <span>
                          最近同步 {formatDateTime(source.last_sync_at)}
                        </span>
                        <span>
                          最近成功 {formatDateTime(source.last_success_at)}
                        </span>
                        {source.error_message ? (
                          <code className="source-health-reason">
                            失败原因：{source.error_message}
                          </code>
                        ) : (
                          <code className="source-health-reason muted">
                            暂无失败记录
                          </code>
                        )}
                      </div>
                      <form
                        action={retrySourceSyncAction}
                        className="source-health-actions"
                      >
                        <input
                          name="source_key"
                          type="hidden"
                          value={source.source_type}
                        />
                        <button
                          className="pixel-button secondary"
                          disabled={!retryable}
                          title={
                            retryable
                              ? "触发该来源重新拉取"
                              : source.configured
                                ? "当前来源同步正常"
                                : "来源未配置，无法重试"
                          }
                          type="submit"
                        >
                          重试同步
                        </button>
                      </form>
                    </article>
                  );
                })}
              </div>
            ) : (
              <div className="source-health-empty">
                <PixelIcon name="ingest" />
                <div>
                  <strong>
                    {offline
                      ? "API 未连接"
                      : "尚未拉取来源同步状态"}
                  </strong>
                  <p>
                    {offline
                      ? "连接恢复后会显示每个来源的同步灯。"
                      : "调用 /api/sources/status 后会展示每个来源的健康状态。"}
                  </p>
                </div>
              </div>
            )}
          </section>

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
            <span>
              同步状态 ·{" "}
              {sourcesStatus
                ? `${healthyCount} 健康 / ${degradedCount} 降级 / ${failedCount} 失败`
                : "尚未拉取"}
            </span>
          </div>
          <div className="pixel-card side-note">
            <h2>最近同步时间</h2>
            {syncSources.length > 0 ? (
              <ul className="source-health-list-compact">
                {syncSources.map((source) => (
                  <li key={`compact-${source.source_type}`}>
                    <span>{source.source_type}</span>
                    <code>{formatDate(source.last_sync_at)}</code>
                  </li>
                ))}
              </ul>
            ) : (
              <p>暂未拉取同步状态。</p>
            )}
          </div>
        </aside>
      </div>
    </AppShell>
  );
}