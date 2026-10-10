import styles from "@/components/ProductWorkspace.module.css";
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
    degraded: "受限制",
    invalid: "需调整",
    missing: "未备好",
    ready: "检查通过",
  };
  return labels[status];
}

function itemStatusLabel(item: RuntimeHealthItem): string {
  // This endpoint checks configuration and local prerequisites, not model calls.
  return item.id === "deepseek" && item.status === "ready"
    ? "已配置·待验证"
    : statusLabel(item.status);
}

const settingsTargets: Record<string, string> = {
  deepseek: "model", local_whisper: "asr", external_sources: "sources",
  obsidian: "paths", video_inbox: "paths", video_output: "paths",
  draft_output: "paths", jianying: "paths", local_vlm: "paths", local_visual_semantics: "paths",
};

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

  const names: Record<string, string> = { "Obsidian": "内容资料", "ffmpeg": "视频制作", "本地 Whisper": "听写文字", "AI 文案模型": "文案助手", "对话与文字模型": "对话助手" };
  return <AppShell><div className={styles.page}>
    <div className={styles.sectionHeading}><h2>制作准备</h2><Link className={styles.secondaryButton} href="/settings">打开设置</Link></div>
    {offline ? <section className={styles.errorState} role="alert"><AlertCircle size={20}/><p>暂时连不上本机服务，请重试。</p></section> : <div className={styles.healthCards}>
      {health && healthRows(health.items).map(item => <details key={item.id} className={styles.healthCard}>
        <summary><span>{item.status === "ready" ? <Check size={16}/> : <AlertCircle size={16}/>}<strong>{names[item.label] ?? item.label}</strong></span><small>{itemStatusLabel(item)}</small></summary>
        <div><p>{item.reason}</p><p>{item.impact}</p><p>{item.repair_action}</p>
          <p>检查时间：<time dateTime={item.last_checked_at}>{new Date(item.last_checked_at).toLocaleString("zh-CN")}</time></p>
          {settingsTargets[item.id] ? <Link href={`/settings?section=${settingsTargets[item.id]}`}>调整相关设置</Link> : null}
        </div>
      </details>)}
    </div>}
  </div></AppShell>;
}
