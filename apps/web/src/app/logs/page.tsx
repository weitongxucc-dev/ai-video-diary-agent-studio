import { AppShell } from "@/components/AppShell";
import { PixelIcon } from "@/components/PixelIcon";
import { diagnosticsReportUrl, getDiagnosticsStatus } from "@/lib/api";
import type {
  DiagnosticsLogStatus,
  DiagnosticsSettingSnapshot,
  DiagnosticsStatus,
} from "@/lib/types";

export const dynamic = "force-dynamic";

const MAX_VISIBLE_ERRORS = 5;
const MAX_VISIBLE_SETTINGS = 12;

async function loadDiagnostics(): Promise<{
  diagnostics: DiagnosticsStatus | null;
  offline: boolean;
}> {
  try {
    return { diagnostics: await getDiagnosticsStatus(), offline: false };
  } catch {
    return { diagnostics: null, offline: true };
  }
}

function statusLabel(status: DiagnosticsLogStatus): string {
  const labels: Record<DiagnosticsLogStatus, string> = {
    available: "可用",
    degraded: "异常",
    empty: "暂无",
  };
  return labels[status];
}

function formatDate(value: string | null | undefined): string {
  if (!value) {
    return "--";
  }

  return new Intl.DateTimeFormat("zh-CN", {
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    month: "2-digit",
    timeZone: "Asia/Shanghai",
  }).format(new Date(value));
}

function settingEntries(
  settings: Record<string, DiagnosticsSettingSnapshot> | undefined,
) {
  return Object.entries(settings ?? {}).sort(([left], [right]) =>
    left.localeCompare(right),
  );
}

export default async function LogsPage() {
  const { diagnostics, offline } = await loadDiagnostics();
  const visibleErrors = diagnostics?.recent_errors.slice(0, MAX_VISIBLE_ERRORS) ?? [];
  const hiddenErrorCount = Math.max(
    0,
    (diagnostics?.recent_errors.length ?? 0) - visibleErrors.length,
  );
  const visibleSettings = settingEntries(diagnostics?.settings_snapshot).slice(
    0,
    MAX_VISIBLE_SETTINGS,
  );
  const hiddenSettingCount = Math.max(
    0,
    settingEntries(diagnostics?.settings_snapshot).length - visibleSettings.length,
  );

  return (
    <AppShell>
      <div className="workspace-page logs-page">
        <section className="workspace-main pixel-card">
          <div className="workspace-header">
            <div>
              <span className="section-kicker">内测日志</span>
              <h1>问题定位与错误报告包</h1>
              <p>
                {offline
                  ? "暂时无法连接本地 API。"
                  : "汇总运行状态、最近错误、脱敏配置和可导出的结构化诊断文件。"}
              </p>
            </div>
            <a
              className="pixel-button"
              download
              href={diagnosticsReportUrl()}
              aria-disabled={offline}
            >
              下载错误报告包
            </a>
          </div>

          <div className="diagnostic-summary-grid">
            <div className="health-summary-card">
              <strong>{diagnostics?.summary.health_ready ?? 0}</strong>
              <span>健康就绪</span>
            </div>
            <div className="health-summary-card">
              <strong>{diagnostics?.summary.health_missing ?? 0}</strong>
              <span>依赖缺失</span>
            </div>
            <div className="health-summary-card">
              <strong>{diagnostics?.summary.recent_error_count ?? 0}</strong>
              <span>最近错误</span>
            </div>
            <div className="health-summary-card">
              <strong>{diagnostics?.summary.report_file_count ?? 0}</strong>
              <span>报告文件</span>
            </div>
          </div>

          <div className="log-category-grid" aria-label="日志分类">
            {diagnostics ? (
              diagnostics.log_categories.map((category) => (
                <article
                  className={`log-category-card ${category.status}`}
                  key={category.id}
                >
                  <div>
                    <PixelIcon
                      name={category.status === "degraded" ? "settings" : "code"}
                    />
                    <strong>{category.label}</strong>
                    <span>{statusLabel(category.status)}</span>
                  </div>
                  <p>{category.latest_message}</p>
                  <small>{category.entry_count} 条记录</small>
                </article>
              ))
            ) : (
              <article className="log-category-card degraded">
                <div>
                  <PixelIcon name="settings" />
                  <strong>API 连接</strong>
                  <span>异常</span>
                </div>
                <p>无法读取诊断状态。启动本地 API 后刷新页面。</p>
                <small>0 条记录</small>
              </article>
            )}
          </div>

          <section className="workspace-subsection">
            <div className="script-section-header compact">
              <div>
                <span className="section-kicker">最近错误</span>
                <strong>最近错误</strong>
              </div>
              <span>{formatDate(diagnostics?.generated_at)}</span>
            </div>
            <div className="recent-error-list bounded-list">
              {visibleErrors.length > 0 ? (
                visibleErrors.map((error) => (
                  <div className="recent-error-row" key={`${error.source}:${error.created_at}`}>
                    <span>{error.source}</span>
                    <strong>{error.message}</strong>
                    <em>{error.episode_id ?? "system"} · {formatDate(error.created_at)}</em>
                  </div>
                ))
              ) : (
                <div className="recent-error-row empty">
                  <span>none</span>
                  <strong>{offline ? "无法读取错误状态" : "最近没有记录错误"}</strong>
                  <em>保持内测包可排障</em>
                </div>
              )}
              {hiddenErrorCount > 0 ? (
                <p className="bounded-list-note">还有 {hiddenErrorCount} 条错误记录已收进报告包。</p>
              ) : null}
            </div>
          </section>

          <section className="workspace-subsection">
            <div className="script-section-header compact">
              <div>
                <span className="section-kicker">错误报告包</span>
                <strong>导出内容</strong>
              </div>
            </div>
            <div className="diagnostic-file-list">
              {(diagnostics?.report_files ?? [
                "runtime_status.json",
                "settings_snapshot_redacted.json",
                "last_errors.log",
                "episode_manifest.json",
                "draft_manifest.json",
                "edit_plan.json",
              ]).map((fileName) => (
                <code key={fileName}>{fileName}</code>
              ))}
            </div>
          </section>
        </section>

        <aside className="workspace-side">
          <div className="pixel-card side-note">
            <h2>脱敏配置</h2>
            <div className="redacted-settings-grid">
              {visibleSettings.map(([name, item]) => (
                <div className="redacted-setting-row" key={name}>
                  <span>{name}</span>
                  <strong>{item.redacted ? "已脱敏" : item.value ?? "--"}</strong>
                  <em>{item.configured ? "configured" : "missing"}</em>
                </div>
              ))}
              {hiddenSettingCount > 0 ? (
                <p className="bounded-list-note">还有 {hiddenSettingCount} 项配置只在报告包中列出。</p>
              ) : null}
            </div>
          </div>

          <div className="pixel-card side-note">
            <h2>不会导出</h2>
            <ul className="diagnostic-exclusion-list">
              {(diagnostics?.excluded_items ?? [
                "API Key 明文",
                "用户完整 Obsidian 内容",
                "未经用户选择的视频文件",
                "剪映私有配置原文",
              ]).map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          </div>

          <div className="pixel-card side-note">
            <h2>内测定位</h2>
            <p>报告包优先帮助定位依赖、任务、ASR、剪辑计划和剪映导入问题；桌面 App 的 api.log、worker.log、web.log 位于 ~/Library/Logs/AI视频日记 Agent Studio。</p>
          </div>
        </aside>
      </div>
    </AppShell>
  );
}
