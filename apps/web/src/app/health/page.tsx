import Link from "next/link";
import { AppShell } from "@/components/AppShell";
import { Check, AlertCircle } from "lucide-react";
import { getRuntimeHealth } from "@/lib/api.server";
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
        <section className="workspace-main ui-panel">
          <div className="health-toolbar">
            {offline ? <p role="alert">暂时无法连接本地服务，请启动服务后刷新。</p> : (
              <div className="health-summary-grid" aria-label="依赖状态摘要">
                {(["ready", "missing", "degraded", "invalid"] as const).map((state) => (
                  <span className={`health-summary-card ${state}`} key={state}>
                    <strong>{health?.summary[state] ?? 0}</strong> {statusLabel(state)}
                  </span>
                ))}
              </div>
            )}
            <Link className="ui-button secondary" href="/settings">打开设置</Link>
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
                    {item.status === "ready" ? <Check size={16} /> : <AlertCircle size={16} />}
                    <strong>{item.label}</strong>
                  </div>
                  <div role="cell">
                    <span className="health-status-chip">{statusLabel(item.status)}</span>
                  </div>
                  <p role="cell">{item.reason}</p>
                  <p role="cell">{item.impact}</p>
                  <p role="cell">{item.repair_action}</p>
                </div>
              ))
            ) : (
              <div className="health-row missing" role="row">
                <div role="cell">
                  <AlertCircle size={16} />
                  <strong>API</strong>
                </div>
                <div role="cell">
                  <span className="health-status-chip">缺失</span>
                </div>
                <p role="cell">无法读取运行健康状态。</p>
                <p role="cell">无法判断当前模型与媒体处理依赖是否可用。</p>
                <p role="cell">启动本地 API 后刷新页面。</p>
              </div>
            )}
          </div>
        </section>


      </div>
    </AppShell>
  );
}
