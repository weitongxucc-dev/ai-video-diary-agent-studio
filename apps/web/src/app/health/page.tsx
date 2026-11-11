import Link from "next/link";
import { AppShell } from "@/components/AppShell";
import { Check, AlertCircle } from "lucide-react";
import { getRuntimeHealth } from "@/lib/api.server";
import type { RuntimeHealth, RuntimeHealthItem, RuntimeHealthStatus } from "@/lib/types";
import styles from "./HealthWorkspace.module.css";

export const dynamic = "force-dynamic";

async function loadRuntimeHealth(): Promise<{ health: RuntimeHealth | null; offline: boolean }> {
  try { return { health: await getRuntimeHealth(), offline: false }; }
  catch { return { health: null, offline: true }; }
}

const labels: Record<RuntimeHealthStatus, string> = {
  degraded: "受限制", invalid: "需调整", missing: "未备好", ready: "检查通过",
};
function itemStatusLabel(item: RuntimeHealthItem): string {
  // runtime_health._deepseek_item maps the observed available service state to ready.
  return item.id === "deepseek" && item.status === "ready" ? "最近调用成功" : labels[item.status];
}

const checks: Record<string, { label: string; scope: string }> = {
  deepseek: { label: "对话与文字服务", scope: "最近调用与配置记录" },
  ffmpeg: { label: "视频编码工具", scope: "程序路径检查" },
  obsidian: { label: "本地资料目录", scope: "目录访问检查" },
  local_whisper: { label: "本地听写", scope: "程序与模型文件检查" },
  pi: { label: "对话程序", scope: "程序运行检查" },
  pi_node: { label: "对话运行环境", scope: "运行环境版本检查" },
  pi_tools: { label: "对话工具", scope: "工具文件检查" },
  local_vlm: { label: "画面理解模型", scope: "模型与运行条件检查" },
  local_visual_semantics: { label: "画面检索模型", scope: "模型与运行条件检查" },
  video_inbox: { label: "视频接收目录", scope: "目录访问检查" },
  video_output: { label: "视频保存目录", scope: "目录访问检查" },
  draft_output: { label: "剪映素材包目录", scope: "目录访问检查" },
  jianying: { label: "剪映草稿目录", scope: "目录与导入条件检查" },
  external_sources: { label: "资料来源", scope: "来源配置检查" },
};
const settingsTargets: Record<string, string> = {
  deepseek: "model", local_whisper: "asr", external_sources: "sources",
  obsidian: "paths", video_inbox: "paths", video_output: "paths",
  draft_output: "paths", jianying: "paths", local_vlm: "paths", local_visual_semantics: "paths",
};
const technicalRepairs: Record<string, string> = {
  pi: "修复应用的对话程序，再重新检查。",
  pi_node: "更新对话运行环境，重新打开应用后检查。",
  pi_tools: "修复应用的对话工具文件，再重新检查。",
  ffmpeg: "安装或修复视频编码工具，重新打开应用后检查。",
};
function healthRows(items: RuntimeHealthItem[]): RuntimeHealthItem[] {
  const order = ["deepseek", "obsidian", "ffmpeg", "local_whisper"];
  return [...items].sort((left, right) => {
    const a = order.indexOf(left.id), b = order.indexOf(right.id);
    return a === -1 && b === -1 ? left.label.localeCompare(right.label) : a === -1 ? 1 : b === -1 ? -1 : a - b;
  });
}

export default async function HealthPage() {
  const { health, offline } = await loadRuntimeHealth();
  return <AppShell><div className={styles.page}>
    <header className={styles.header}><h2>运行情况</h2><Link href="/settings?section=runtime">返回运行与诊断</Link></header>
    <p className={styles.scope}>这里检查配置、文件与运行条件；生成仍需通过模型和设备资源检查。</p>
    {offline ? <p className={styles.offline} role="alert"><AlertCircle size={16}/>暂时连不上本机服务，请恢复服务后刷新。</p> : health ? <ul className={styles.list} aria-label="本机检查项">
      {healthRows(health.items).map(item => <li className={styles.row} key={item.id}>
        <details className={styles.item}>
          <summary><span className={styles.label}>{item.status === "ready" ? <Check size={16}/> : <AlertCircle size={16}/>}<span><strong>{checks[item.id]?.label ?? item.label}</strong><small>{checks[item.id]?.scope ?? "当前配置检查"}</small></span></span><span className={styles.status}>{itemStatusLabel(item)}</span><span className={styles.expand}>详情</span></summary>
          <div className={styles.detail}>
            <dl><div><dt>影响</dt><dd>{item.impact}</dd></div><div><dt>处理</dt><dd>{item.status !== "ready" && technicalRepairs[item.id] ? technicalRepairs[item.id] : item.repair_action}</dd></div></dl>
            <details className={styles.diagnostic}><summary>原始检查详情</summary><p>{item.reason}</p><p>{item.repair_action}</p><p>检查对象：{item.label}</p><p>检查时间：<time dateTime={item.last_checked_at}>{new Date(item.last_checked_at).toLocaleString("zh-CN")}</time></p></details>
          </div>
        </details>
        {settingsTargets[item.id] ? <Link className={styles.repair} aria-label={`调整${checks[item.id]?.label ?? item.label}设置`} href={`/settings?section=${settingsTargets[item.id]}`}>设置</Link> : null}
      </li>)}
    </ul> : null}
  </div></AppShell>;
}
