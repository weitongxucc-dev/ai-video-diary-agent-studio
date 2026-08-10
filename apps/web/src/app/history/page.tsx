import Link from "next/link";
import { AppShell } from "@/components/AppShell";
import { MetricCard } from "@/components/MetricCard";
import { PixelIcon } from "@/components/PixelIcon";
import { getEpisodeHistory } from "@/lib/api";
import type {
  EpisodeHistory,
  EpisodeHistoryItem,
  EpisodeStatus,
} from "@/lib/types";

export const dynamic = "force-dynamic";

const HISTORY_STATUS_FILTERS: { value: "all" | EpisodeStatus; label: string }[] = [
  { value: "all", label: "全部" },
  { value: "draft_ready", label: "草稿就绪" },
  { value: "published", label: "已发布" },
  { value: "failed", label: "失败" },
];

const STATUS_LABEL: Record<EpisodeStatus, string> = {
  created: "已创建",
  sources_ingesting: "素材收集中",
  topics_ready: "选题就绪",
  topic_approved: "选题已确认",
  script_ready: "口播稿就绪",
  script_approved: "口播稿已确认",
  waiting_video: "等待视频",
  video_processing: "视频处理中",
  draft_ready: "草稿就绪",
  published: "已发布",
  failed: "失败",
};

const STATUS_TONE: Record<EpisodeStatus, string> = {
  created: "muted",
  sources_ingesting: "muted",
  topics_ready: "blue",
  topic_approved: "blue",
  script_ready: "blue",
  script_approved: "blue",
  waiting_video: "orange",
  video_processing: "blue",
  draft_ready: "green",
  published: "green",
  failed: "red",
};

type HistorySearchParams = {
  status?: string | string[];
};

function pickStatusFilter(value: string | string[] | undefined): "all" | EpisodeStatus {
  if (typeof value !== "string") {
    return "all";
  }
  if (
    value === "draft_ready" ||
    value === "published" ||
    value === "failed" ||
    value === "all"
  ) {
    return value;
  }
  return "all";
}

async function loadHistory(status: "all" | EpisodeStatus): Promise<{
  history: EpisodeHistory | null;
  offline: boolean;
}> {
  try {
    const history = await getEpisodeHistory(status === "all" ? undefined : status);
    return { history, offline: false };
  } catch {
    return { history: null, offline: true };
  }
}

function safeFormatDate(value: string, withYear: boolean): string {
  if (typeof value !== "string" || value.length === 0) {
    return "—";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "—";
  }
  try {
    return new Intl.DateTimeFormat("zh-CN", {
      day: "2-digit",
      month: "2-digit",
      ...(withYear ? { year: "numeric" as const } : {}),
      hour: withYear ? undefined : "2-digit",
      minute: "2-digit",
      timeZone: "Asia/Shanghai",
    }).format(date);
  } catch {
    return "—";
  }
}

function formatDate(value: string): string {
  return safeFormatDate(value, false);
}

function formatLongDate(value: string): string {
  return safeFormatDate(value, true);
}

function formatNumber(value: number | null | undefined): string {
  if (value === null || value === undefined) {
    return "--";
  }
  return value.toLocaleString("zh-CN");
}

function getBaseName(path: string | null): string {
  if (!path) {
    return "--";
  }
  return path.split(/[\\/]/).pop() ?? path;
}

function getStatusLabel(status: EpisodeStatus): string {
  return STATUS_LABEL[status];
}

function getStatusTone(status: EpisodeStatus): string {
  return STATUS_TONE[status];
}

function summarizeItem(item: EpisodeHistoryItem): string {
  const segments: string[] = [];
  segments.push(formatLongDate(item.episode_date));
  if (item.topic_title) {
    segments.push(item.topic_title);
  } else if (item.script_title) {
    segments.push(item.script_title);
  } else {
    segments.push("未填写选题或脚本");
  }
  return segments.join(" · ");
}

export default async function HistoryPage({
  searchParams,
}: {
  searchParams?: Promise<HistorySearchParams>;
}) {
  const resolvedSearchParams = searchParams ? await searchParams : {};
  const activeFilter = pickStatusFilter(resolvedSearchParams.status);
  const { history, offline } = await loadHistory(activeFilter);
  const items = history?.items ?? [];
  const totalCount = history?.total_count ?? 0;
  const filteredCount = history?.filtered_count ?? items.length;

  return (
    <AppShell>
      <div className="history-page">
        <section className="history-main pixel-card">
          <div className="history-header">
            <div>
              <span className="section-kicker">Episode 历史</span>
              <h1>发布与回溯</h1>
              <p>
                {offline
                  ? "暂时无法连接本地 API。"
                  : `共 ${totalCount} 条 Episode，当前筛选 ${filteredCount} 条。`}
              </p>
            </div>
            <Link className="pixel-button secondary" href="/">
              返回工作台
            </Link>
          </div>

          <nav aria-label="状态筛选" className="history-filter">
            {HISTORY_STATUS_FILTERS.map((filter) => {
              const isActive = activeFilter === filter.value;
              const href =
                filter.value === "all"
                  ? "/history"
                  : `/history?status=${encodeURIComponent(filter.value)}`;
              return (
                <Link
                  aria-current={isActive ? "page" : undefined}
                  className={`history-filter-chip ${isActive ? "active" : ""}`}
                  href={href}
                  key={filter.value}
                >
                  {filter.label}
                </Link>
              );
            })}
          </nav>

          <ol className="history-list">
            {items.length > 0 ? (
              items.map((item) => {
                const statusTone = getStatusTone(item.status);
                return (
                  <li className="history-item" key={item.id}>
                    <div className="history-item-summary">
                      <div className="history-item-day">
                        <strong>第 {item.day_number} 天</strong>
                        <span>{formatLongDate(item.episode_date)}</span>
                      </div>
                      <div className="history-item-body">
                        <strong>{item.topic_title ?? "未填写选题"}</strong>
                        <span>{summarizeItem(item)}</span>
                        <div className="history-item-meta">
                          <span className={`step-state ${statusTone}`}>
                            {getStatusLabel(item.status)}
                          </span>
                          <span>
                            播放 {formatNumber(item.play_count)} · 点赞{" "}
                            {formatNumber(item.like_count)} · 评论{" "}
                            {formatNumber(item.comment_count)}
                          </span>
                          {item.platform ? (
                            <span>平台 {item.platform}</span>
                          ) : null}
                          {item.published_at ? (
                            <span>发布 {formatDate(item.published_at)}</span>
                          ) : null}
                        </div>
                      </div>
                      <div className="history-item-actions">
                        {item.draft_package_path ? (
                          <Link
                            className="pixel-button secondary"
                            href="/draft"
                          >
                            查看素材包
                          </Link>
                        ) : (
                          <span className="step-state">无素材包</span>
                        )}
                        {item.video_dedup_report_path ? (
                          <Link
                            className="pixel-button secondary"
                            href="/video"
                          >
                            查看重录报告
                          </Link>
                        ) : (
                          <span className="step-state">无重录报告</span>
                        )}
                        {item.publish_url ? (
                          <a
                            className="pixel-button"
                            href={item.publish_url}
                            rel="noreferrer"
                            target="_blank"
                          >
                            打开发布链接
                          </a>
                        ) : (
                          <span className="step-state">未填写发布链接</span>
                        )}
                      </div>
                    </div>
                    <code className="history-item-path">
                      {getBaseName(item.draft_package_path) !== "--"
                        ? `素材包 · ${getBaseName(item.draft_package_path)}`
                        : getBaseName(item.video_dedup_report_path) !== "--"
                          ? `重录报告 · ${getBaseName(item.video_dedup_report_path)}`
                          : `Episode ID · ${item.id}`}
                    </code>
                  </li>
                );
              })
            ) : (
              <li className="history-empty">
                <PixelIcon name="calendar" />
                <div>
                  <strong>
                    {offline
                      ? "API 未连接"
                      : activeFilter === "all"
                        ? "还没有 Episode"
                        : `当前筛选下没有 Episode`}
                  </strong>
                  <p>
                    {offline
                      ? "连接恢复后这里会按倒序列出所有 Episode。"
                      : "完成一次内容发布流程后会自动出现在这里。"}
                  </p>
                </div>
              </li>
            )}
          </ol>
        </section>

        <aside className="history-side">
          <MetricCard
            icon="calendar"
            value={String(filteredCount)}
            label="当前筛选"
            tone="blue"
          />
          <MetricCard
            icon="check"
            value={String(
              items.filter((item) => item.status === "published").length,
            )}
            label="已发布"
            tone="green"
          />
          <MetricCard
            icon="spark"
            value={String(
              items.filter((item) => item.status === "draft_ready").length,
            )}
            label="草稿就绪"
            tone="purple"
          />
          <MetricCard
            icon="code"
            value={String(items.filter((item) => item.status === "failed").length)}
            label="失败"
            tone="orange"
          />
          <div className="pixel-card side-note">
            <h2>统计说明</h2>
            <p>展示当前筛选下的状态分布。</p>
            <span>数据来源：本地 Episode 数据库。</span>
          </div>
        </aside>
      </div>
    </AppShell>
  );
}