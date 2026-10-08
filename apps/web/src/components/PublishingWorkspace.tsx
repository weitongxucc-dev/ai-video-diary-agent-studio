"use client";

import Link from "next/link";
import { AlertCircle, ArrowRight, CheckCircle2, CircleDashed, CircleDot, Film, LoaderCircle, QrCode, RefreshCw, Send, Signal, UserRound } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { AppShell } from "@/components/AppShell";
import { listCreationTasks } from "@/lib/api";
import type { CreationTask } from "@/lib/types";
import styles from "./ProductWorkspace.module.css";

type Filter = "all" | "attention" | "ready" | "failed";
const filters: Array<{ id: Filter; label: string }> = [
  { id: "all", label: "全部记录" }, { id: "attention", label: "需要处理" },
  { id: "ready", label: "成片就绪" }, { id: "failed", label: "运行失败" },
];
const stageLabels: Record<CreationTask["stage"], string> = { topic: "选题", script: "文案", narration: "配音", montage: "视频剪辑", review: "成片检查" };

function taskState(task: CreationTask): { label: string; tone: string; icon: typeof CircleDot } {
  if (task.stage_status === "failed") return { label: "运行失败", tone: styles.stateDanger, icon: AlertCircle };
  if (task.output_ready || task.stage_status === "completed") return { label: task.output_ready ? "成片就绪" : "制作已完成", tone: styles.stateSuccess, icon: CheckCircle2 };
  if (task.stage_status === "waiting_for_user") return { label: "需要处理", tone: styles.stateWarning, icon: CircleDot };
  if (task.stage_status === "running") return { label: "正在制作", tone: styles.stateBlue, icon: LoaderCircle };
  return { label: "尚未开始", tone: styles.stateNeutral, icon: CircleDashed };
}

function formatTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "时间未知";
  return new Intl.DateTimeFormat("zh-CN", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }).format(date);
}

function taskNextStep(task: CreationTask): string {
  if (task.stage_status === "failed") {
    const repair = task.stage_repair_action || "打开任务查看失败原因";
    return task.output_ready ? `${repair}；已有成片可先检查保存` : repair;
  }
  if (task.output_ready) return "检查并保存本地成片";
  if (task.stage_repair_action) return task.stage_repair_action;
  if (task.stage_status === "waiting_for_user") return `继续确认${stageLabels[task.stage]}`;
  return `继续${stageLabels[task.stage]}`;
}

// ── 发布账号授权（WIL-113）──────────────────────────────────────────────
// 本段是「只做授权、不发内容」的授权连接层入口。后端路由尚未接线（归 WIL-112），
// 故前端用 in-browser mock 走通流程；真实 token 交换由后续平台接入包替换。
// 能力表与 apps/api/app/platform_auth/capabilities.py 逐字对齐。

type PublishPlatformKey = "douyin" | "wechat" | "xiaohongshu";
type BoundAccount = { platform: PublishPlatformKey; accountRef: string; boundAt: string; revoked: boolean };

const PLATFORM_AUTH_CAPS: Array<{ platform: PublishPlatformKey; label: string; scanOauth: boolean; note: string }> = [
  { platform: "douyin", label: "抖音", scanOauth: true, note: "支持扫码授权 + 长期 token（视频发布权限另审）" },
  { platform: "wechat", label: "微信视频号", scanOauth: false, note: "微信登录 OAuth 仅身份；视频号无第三方发布短视频 API" },
  { platform: "xiaohongshu", label: "小红书", scanOauth: false, note: "未查证" },
];

const MOCK_STORAGE_KEY = "ai-video-diary-publish-accounts-mock";
const MOCK_DRAFT_KEY = "ai-video-diary-publish-drafts-mock";

function loadMockAccounts(): BoundAccount[] {
  try {
    const raw = localStorage.getItem(MOCK_STORAGE_KEY);
    return raw ? (JSON.parse(raw) as BoundAccount[]) : [];
  } catch {
    return [];
  }
}

type DraftRecord = { platform: PublishPlatformKey; draftId: string; title: string; hashtags: string[]; createdAt: string };

function loadMockDrafts(): DraftRecord[] {
  try {
    const raw = localStorage.getItem(MOCK_DRAFT_KEY);
    return raw ? (JSON.parse(raw) as DraftRecord[]) : [];
  } catch {
    return [];
  }
}

const DRAFT_TITLE_MAX = 30;
const DRAFT_DESC_MAX = 1000;
const DRAFT_TAG_MAX = 20;



export function PublishingWorkspace() {
  const [tasks, setTasks] = useState<CreationTask[]>([]);
  const [filter, setFilter] = useState<Filter>("all");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [loadedAt, setLoadedAt] = useState<string | null>(null);
  const [stale, setStale] = useState(false);
  const latestRequest = useRef(0);

  // 授权连接层（mock）：账号持久化到 localStorage，证明「二次进入不需重新登录」
  const [accounts, setAccounts] = useState<BoundAccount[]>([]);
  const [authPlatform, setAuthPlatform] = useState<PublishPlatformKey | null>(null);
  useEffect(() => { setAccounts(loadMockAccounts()); }, []);

  function bindAccount(platform: PublishPlatformKey) {
    const account: BoundAccount = {
      platform,
      accountRef: `openid_mock_${platform}_${Math.random().toString(16).slice(2, 8)}`,
      boundAt: new Date().toISOString(),
      revoked: false,
    };
    const next = [...accounts.filter((item) => item.platform !== platform), account];
    setAccounts(next);
    try { localStorage.setItem(MOCK_STORAGE_KEY, JSON.stringify(next)); } catch { /* 存储不可用不阻断 */ }
    setAuthPlatform(null);
  }

  function simulateRevoke(platform: PublishPlatformKey) {
    const next = accounts.map((item) => (item.platform === platform ? { ...item, revoked: true } : item));
    setAccounts(next);
    try { localStorage.setItem(MOCK_STORAGE_KEY, JSON.stringify(next)); } catch { /* noop */ }
  }

  // 文案三件套 + 存草稿（mock，只到草稿为止，绝不触发发布）
  const [draftTitle, setDraftTitle] = useState("");
  const [draftDescription, setDraftDescription] = useState("");
  const [draftHashtags, setDraftHashtags] = useState("");
  const [drafts, setDrafts] = useState<DraftRecord[]>([]);
  const [draftNotice, setDraftNotice] = useState<string | null>(null);
  useEffect(() => { setDrafts(loadMockDrafts()); }, []);

  function saveDraft(platform: PublishPlatformKey) {
    const title = draftTitle.trim();
    const description = draftDescription.trim();
    const hashtags = draftHashtags.split(/[,\n]/).map((t) => t.trim().replace(/^#/, "")).filter(Boolean);
    if (!title) { setDraftNotice("请填写标题"); return; }
    if (title.length > DRAFT_TITLE_MAX) { setDraftNotice(`标题超过 ${DRAFT_TITLE_MAX} 字`); return; }
    if (description.length > DRAFT_DESC_MAX) { setDraftNotice(`描述超过 ${DRAFT_DESC_MAX} 字`); return; }
    if (hashtags.length > DRAFT_TAG_MAX) { setDraftNotice(`话题标签超过 ${DRAFT_TAG_MAX} 个`); return; }
    const record: DraftRecord = {
      platform, draftId: `draft_mock_${Math.random().toString(16).slice(2, 8)}`,
      title, hashtags, createdAt: new Date().toISOString(),
    };
    const next = [record, ...drafts];
    setDrafts(next);
    try { localStorage.setItem(MOCK_DRAFT_KEY, JSON.stringify(next)); } catch { /* noop */ }
    setDraftNotice(`已存入草稿箱（演示，${hashtags.length} 个话题）`);
  }



  async function refresh() {
    const requestId = ++latestRequest.current;
    setLoading(true); setError(null);
    try {
      const response = await listCreationTasks();
      if (requestId !== latestRequest.current) return; // 过期响应：已有更新的读取，不能覆盖新状态
      setTasks(response.items); setLoadedAt(new Date().toISOString()); setStale(false);
      setSelectedId((current) => current ?? response.items[0]?.id ?? null);
    } catch (cause) {
      if (requestId !== latestRequest.current) return;
      setError(cause instanceof Error && cause.message ? cause.message : "暂时无法连接本地服务。");
      setStale(true); // 旧数据保留展示，但必须标记为可能过期，不能冒充最新
    } finally {
      if (requestId === latestRequest.current) setLoading(false);
    }
  }

  useEffect(() => { void refresh(); }, []);

  const filtered = useMemo(() => tasks.filter((task) => {
    if (filter === "attention") return task.stage_status === "waiting_for_user";
    if (filter === "ready") return task.output_ready;
    if (filter === "failed") return task.stage_status === "failed";
    return true;
  }), [filter, tasks]);
  const selected = filtered.find((task) => task.id === selectedId) ?? filtered[0] ?? null;
  const attentionCount = tasks.filter((task) => task.stage_status === "waiting_for_user").length;
  const readyCount = tasks.filter((task) => task.output_ready).length;
  const staleHint = stale && loadedAt ? `（${formatTime(loadedAt)}）` : "";

  const inspector = selected ? (
    <div className={styles.inspector}>
      <span className={styles.objectEyebrow}>当前创作任务</span><h3>{selected.name}</h3>
      <span className={`${styles.stateBadge} ${taskState(selected).tone}`}>{taskState(selected).label}</span>
      <dl><div><dt>当前阶段</dt><dd>{stageLabels[selected.stage]}</dd></div><div><dt>整体进度</dt><dd>{selected.progress}%</dd></div><div><dt>最后更新</dt><dd>{formatTime(selected.updated_at)}</dd></div><div><dt>本地成片</dt><dd>{selected.output_ready ? "已生成，可检查" : "尚未生成"}</dd></div></dl>
      {stale ? <section className={styles.inspectorMuted}><strong>详情可能不是最新</strong><p>读取失败，以下详情来自上次成功读取{staleHint}。</p></section> : null}
      {(selected.stage_reason || selected.stage_repair_action) ? <section className={styles.inspectorNotice}><strong>{selected.stage_impact || "当前任务需要处理"}</strong>{selected.stage_reason ? <p>原因：{selected.stage_reason}</p> : null}{selected.stage_repair_action ? <p>处理方式：{selected.stage_repair_action}</p> : null}</section> : null}
      <Link className={styles.primaryButton} href={`/tasks/${encodeURIComponent(selected.id)}`}>打开任务 <ArrowRight size={15} /></Link>
      <section className={styles.inspectorMuted}><strong>平台能力</strong><p>平台发布、定时发布和发布后监控尚未接入；本页不会把本地成片计为已发布。</p></section>
    </div>
  ) : undefined;

  return (
    <AppShell creationTasks={tasks} inspector={inspector} inspectorTitle="记录详情">
      <div className={styles.page}>
        <section className={styles.sectionHeading} style={{ marginTop: 4 }}><div><span>发布账号授权 · 演示</span><h2>绑定你的发布账号</h2><p>每个成员各自绑定自己的账号；授权一次、长期有效。本段为 mock 演示，真实 token 交换尚未接入。</p></div></section>
        <div style={{ display: "grid", gap: 8, marginBottom: 12 }}>
          {PLATFORM_AUTH_CAPS.map((cap) => {
            const bound = accounts.find((item) => item.platform === cap.platform);
            const status = bound ? (bound.revoked ? "需重新授权" : "已授权") : (cap.scanOauth ? "未授权" : "该平台无此能力");
            return (
              <div key={cap.platform} style={{ display: "flex", alignItems: "center", gap: 12, padding: "10px 12px", border: "1px solid var(--border-1)", borderRadius: 8, background: "var(--surface-1)" }}>
                <UserRound size={18} />
                <div style={{ minWidth: 0, flex: 1 }}>
                  <strong style={{ fontSize: 13 }}>{cap.label}</strong>
                  <small style={{ display: "block", color: "var(--text-3)", fontSize: 12 }}>{cap.note}</small>
                </div>
                <span style={{ color: status === "已授权" ? "var(--success)" : status === "需重新授权" ? "var(--warning)" : "var(--text-3)", fontSize: 12 }}>{status}</span>
                {cap.scanOauth ? (
                  bound ? (
                    <button className={styles.secondaryButton} type="button" onClick={() => simulateRevoke(cap.platform)}>{bound.revoked ? "模拟重新授权" : "模拟令牌失效"}</button>
                  ) : (
                    <button className={styles.primaryButton} type="button" onClick={() => setAuthPlatform(cap.platform)}><QrCode size={14} />扫码授权</button>
                  )
                ) : <button className={styles.secondaryButton} type="button" disabled>无此能力</button>}
              </div>
            );
          })}
        </div>
        {accounts.length ? (
          <section className={styles.inspectorMuted}><strong>已绑定账号</strong>
            {accounts.map((item) => <p key={item.platform}>{PLATFORM_AUTH_CAPS.find((c) => c.platform === item.platform)?.label ?? item.platform} · {item.accountRef}{item.revoked ? " · 需重新授权" : " · 已授权"}</p>)}
          </section>
        ) : null}
        {accounts.some((a) => a.platform === "douyin" && !a.revoked) ? (
          <section style={{ padding: "10px 12px", border: "1px solid var(--border-1)", borderRadius: 8, background: "var(--surface-1)", marginBottom: 12 }}>
            <strong style={{ fontSize: 13 }}>填写视频文案（演示）</strong>
            <div style={{ display: "grid", gap: 8, marginTop: 8 }}>
              <label style={{ fontSize: 12, color: "var(--text-2)" }}>标题<input style={{ width: "100%", marginTop: 3, padding: "6px 8px", border: "1px solid var(--border-1)", borderRadius: 6 }} value={draftTitle} maxLength={DRAFT_TITLE_MAX} onChange={(e) => setDraftTitle(e.target.value)} placeholder={`标题（≤${DRAFT_TITLE_MAX} 字）`} /></label>
              <label style={{ fontSize: 12, color: "var(--text-2)" }}>描述<textarea style={{ width: "100%", marginTop: 3, padding: "6px 8px", border: "1px solid var(--border-1)", borderRadius: 6, minHeight: 60 }} value={draftDescription} maxLength={DRAFT_DESC_MAX} onChange={(e) => setDraftDescription(e.target.value)} placeholder={`描述（≤${DRAFT_DESC_MAX} 字）`} /></label>
              <label style={{ fontSize: 12, color: "var(--text-2)" }}>话题标签（逗号/换行分隔，≤{DRAFT_TAG_MAX} 个）<input style={{ width: "100%", marginTop: 3, padding: "6px 8px", border: "1px solid var(--border-1)", borderRadius: 6 }} value={draftHashtags} onChange={(e) => setDraftHashtags(e.target.value)} placeholder="产品, 教程" /></label>
              <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                <button className={styles.primaryButton} type="button" onClick={() => saveDraft("douyin")}>存草稿（演示）</button>
                {draftNotice ? <span style={{ fontSize: 12, color: "var(--text-2)" }}>{draftNotice}</span> : null}
              </div>
            </div>
            {drafts.length ? (
              <div style={{ marginTop: 10 }}>
                <strong style={{ fontSize: 12 }}>草稿箱（演示）</strong>
                {drafts.map((d) => <p key={d.draftId} style={{ fontSize: 12, color: "var(--text-2)", margin: "4px 0" }}>#{d.draftId} · {d.title}{d.hashtags.length ? ` · 话题 ${d.hashtags.join("、")}` : ""}</p>)}
              </div>
            ) : null}
          </section>
        ) : null}
        {authPlatform ? (
          <div style={{ position: "fixed", inset: 0, zIndex: 100, display: "grid", placeItems: "center", background: "rgba(0,0,0,.3)" }} role="dialog" aria-label="扫码授权">
            <div style={{ width: 320, padding: 20, borderRadius: 10, background: "var(--surface-1)", border: "1px solid var(--border-1)", boxShadow: "var(--shadow-float)" }}>
              <strong style={{ fontSize: 14 }}>{PLATFORM_AUTH_CAPS.find((c) => c.platform === authPlatform)?.label} · 扫码授权（演示二维码）</strong>
              <div style={{ display: "grid", placeItems: "center", height: 160, margin: "12px 0", background: "var(--surface-2)", borderRadius: 8 }} aria-hidden="true"><QrCode size={96} /></div>
              <p style={{ fontSize: 12, color: "var(--text-2)" }}>这是 mock 二维码，不连接真实平台。点击下方完成模拟授权。</p>
              <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
                <button className={styles.secondaryButton} type="button" onClick={() => setAuthPlatform(null)}>取消</button>
                <button className={styles.primaryButton} type="button" onClick={() => bindAccount(authPlatform)}>模拟扫码完成</button>
              </div>
            </div>
          </div>
        ) : null}
        <section className={styles.capabilityBanner}><span><Signal size={18} /></span><div><strong>暂不支持发布</strong><p>可以先保存成片，再到平台上传。</p></div><Link href="/health">运行情况 <ArrowRight size={14} /></Link></section>
        {error && tasks.length ? (
          <section aria-label="读取失败提示" className={styles.inspectorNotice} role="status">
            <strong>读取失败，以下统计和记录可能不是最新</strong>
            <p>{error} 页面仍显示上次成功读取{staleHint}的任务；判断和操作前请先重新读取。</p>
            <button className={styles.secondaryButton} disabled={loading} onClick={() => void refresh()} type="button">重新读取</button>
          </section>
        ) : null}
        <div className={styles.summaryRow} aria-label="真实任务摘要"><span><strong>{tasks.length}</strong> 全部任务</span><span><strong>{attentionCount}</strong> 需要处理</span><span><strong>{readyCount}</strong> 成片就绪</span><button className={styles.secondaryButton} disabled={loading} onClick={() => void refresh()} type="button"><RefreshCw className={loading ? styles.spinning : undefined} size={15} />刷新</button></div>
        <nav aria-label="记录筛选" className={styles.tabs}>{filters.map((item) => <button aria-pressed={filter === item.id} className={filter === item.id ? styles.activeTab : undefined} key={item.id} onClick={() => setFilter(item.id)} type="button">{item.label}</button>)}</nav>
        <section className={styles.recordList}>
          <div className={styles.sectionHeading}><h2>{filter === "all" ? "最近更新" : filters.find((item) => item.id === filter)?.label}</h2><p>{loading ? "正在读取…" : `${filtered.length} 项`}</p></div>
          {error && !tasks.length ? <div className={styles.errorState} role="alert"><AlertCircle size={20} /><div><strong>暂时无法读取创作记录</strong><p>{error} 尚没有可显示的任务数据。</p></div><button disabled={loading} onClick={() => void refresh()} type="button">重新读取</button></div>
            : loading && !tasks.length ? <div className={styles.loadingState}><LoaderCircle className={styles.spinning} size={19} /> 正在读取当前工作区…</div>
            : filtered.length ? (
            <ol className={styles.records}>{filtered.map((task) => { const state = taskState(task); const StateIcon = state.icon; return <li key={task.id}><button aria-pressed={selected?.id === task.id} className={selected?.id === task.id ? styles.selectedRecord : undefined} data-open-details onClick={() => setSelectedId(task.id)} type="button"><span className={`${styles.recordState} ${state.tone}`}><StateIcon className={task.stage_status === "running" ? styles.spinning : undefined} size={16} /></span><span className={styles.recordBody}><span className={styles.recordTitle}><strong>{task.name}</strong><em>{state.label}</em></span><span>{stageLabels[task.stage]} · {taskNextStep(task)}</span><span className={styles.progressTrack} aria-label={`进度 ${task.progress}%`}><i style={{ width: `${task.progress}%` }} /></span></span><span className={styles.recordTime}>{formatTime(task.updated_at)}<ArrowRight size={15} /></span></button></li>; })}</ol>
          ) : <div className={styles.emptyStage}><span className={styles.emptyIcon}>{filter === "all" ? <Film size={22} /> : <Send size={22} />}</span><div><h2>{filter === "all" ? "还没有创作记录" : "当前筛选下没有记录"}</h2><p>{filter === "all" ? "从一个真实制作目标开始，任务进度会自动出现在这里。" : "切换筛选，或继续处理已有创作任务。"}</p></div><Link className={styles.primaryButton} href="/?new=1">新建视频</Link></div>}
        </section>
      </div>
    </AppShell>
  );
}
