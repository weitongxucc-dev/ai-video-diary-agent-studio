import Link from "next/link";
import { AppShell } from "@/components/AppShell";
import { PixelBlockMeter } from "@/components/PixelBlockMeter";
import { PixelIcon } from "@/components/PixelIcon";
import { getRuntimeHealth } from "@/lib/api";
import type { RuntimeHealth, RuntimeHealthItem, RuntimeHealthStatus } from "@/lib/types";

export const dynamic = "force-dynamic";

async function loadRuntimeHealth(): Promise<{
  health: RuntimeHealth | null;
  offline: boolean;
}> {
  try {
    return { health: await getRuntimeHealth(), offline: false };
  } catch {
    return { health: null, offline: true };
  }
}

function statusLabel(status: RuntimeHealthStatus): string {
  const labels: Record<RuntimeHealthStatus, string> = {
    degraded: "降级",
    invalid: "无效",
    missing: "缺失",
    ready: "就绪",
  };
  return labels[status];
}

function statusProgress(status: RuntimeHealthStatus): number {
  const progress: Record<RuntimeHealthStatus, number> = {
    degraded: 50,
    invalid: 25,
    missing: 0,
    ready: 100,
  };
  return progress[status];
}

function healthRows(items: RuntimeHealthItem[]): RuntimeHealthItem[] {
  const order = ["deepseek", "obsidian", "ffmpeg", "local_whisper"];
  return [...items].sort((left, right) => {
    const leftIndex = order.indexOf(left.id);
    const rightIndex = order.indexOf(right.id);
    if (leftIndex === -1 && rightIndex === -1) {
      return left.label.localeCompare(right.label);
    }
    if (leftIndex === -1) {
      return 1;
    }
    if (rightIndex === -1) {
      return -1;
    }
    return leftIndex - rightIndex;
  });
}

export default async function HealthPage() {
  const { health, offline } = await loadRuntimeHealth();

  return (
    <AppShell>
      <div className="workspace-page health-page">
        <section className="workspace-main pixel-card">
          <div className="workspace-header">
            <div>
              <span className="section-kicker">运行健康</span>
              <h1>本地依赖诊断台</h1>
              <p>
                {offline
                  ? "暂时无法连接本地 API。"
                  : "检查 DeepSeek、Obsidian、ffmpeg、Whisper、视频目录和剪映草稿目录是否可用。"}
              </p>
            </div>
            <Link className="pixel-button secondary" href="/settings">
              去设置
            </Link>
          </div>

          <div className="health-summary-grid">
            <div className="health-summary-card">
              <strong>{health?.summary.ready ?? 0}</strong>
              <span>就绪</span>
            </div>
            <div className="health-summary-card">
              <strong>{health?.summary.missing ?? 0}</strong>
              <span>缺失</span>
            </div>
            <div className="health-summary-card">
              <strong>{health?.summary.degraded ?? 0}</strong>
              <span>降级</span>
            </div>
            <div className="health-summary-card">
              <strong>{health?.summary.invalid ?? 0}</strong>
              <span>无效</span>
            </div>
          </div>

          <div className="health-table" role="table" aria-label="运行健康检查">
            <div className="health-table-head" role="row">
              <span role="columnheader">依赖</span>
              <span role="columnheader">状态</span>
              <span role="columnheader">原因</span>
              <span role="columnheader">影响</span>
              <span role="columnheader">修复动作</span>
            </div>
            {health ? (
              healthRows(health.items).map((item) => (
                <div className={`health-row ${item.status}`} key={item.id} role="row">
                  <div role="cell">
                    <PixelIcon
                      name={item.status === "ready" ? "check" : "settings"}
                    />
                    <strong>{item.label}</strong>
                  </div>
                  <div role="cell">
                    <span className="health-status-chip">{statusLabel(item.status)}</span>
                    <PixelBlockMeter value={statusProgress(item.status)} />
                  </div>
                  <p role="cell">{item.reason}</p>
                  <p role="cell">{item.impact}</p>
                  <p role="cell">{item.repair_action}</p>
                </div>
              ))
            ) : (
              <div className="health-row missing" role="row">
                <div role="cell">
                  <PixelIcon name="settings" />
                  <strong>API</strong>
                </div>
                <div role="cell">
                  <span className="health-status-chip">缺失</span>
                  <PixelBlockMeter value={0} />
                </div>
                <p role="cell">无法读取运行健康状态。</p>
                <p role="cell">页面无法判断 DeepSeek、ffmpeg 等依赖状态。</p>
                <p role="cell">启动本地 API 后刷新页面。</p>
              </div>
            )}
          </div>
        </section>

        <aside className="workspace-side">
          <div className="pixel-card side-note">
            <h2>检查重点</h2>
            <p>DeepSeek 决定选题和脚本生成；ffmpeg 决定视频处理；Whisper 决定本地转写和重复录制检测。</p>
          </div>
          <div className="pixel-card side-note">
            <h2>内测建议</h2>
            <p>分享给同事前，先确认缺失项为 0；降级项可接受但需要在剪映里人工验证。</p>
          </div>
        </aside>
      </div>
    </AppShell>
  );
}
