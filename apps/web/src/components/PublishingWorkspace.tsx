"use client";

import Link from "next/link";
import { AlertCircle, ArrowRight, CheckCircle2, CircleDashed, Film, LoaderCircle, RefreshCw, Send, UserRound } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AppShell } from "@/components/AppShell";
import { HomeCardDialog } from "@/components/HomeCardDialog";
import { connectPublishingAccount, createPlatformPublishJob, listCreationTasks, listPlatformPublishJobs, listPublishingAccounts, listPublishingPlatforms, verifyPublishingAccount } from "@/lib/api";
import type { CreationTask, PlatformBrowserAccount, PlatformPublishJob, PublishPlatform, PublishPlatformCapability } from "@/lib/types";
import styles from "./ProductWorkspace.module.css";

type Filter = "all" | "ready" | "attention" | "failed";
const filters: Array<{ id: Filter; label: string }> = [
  { id: "all", label: "全部记录" }, { id: "ready", label: "成片就绪" },
  { id: "attention", label: "需要处理" }, { id: "failed", label: "运行失败" },
];
const platformLabels: Record<PublishPlatform, string> = { douyin: "抖音", wechat: "微信视频号", xiaohongshu: "小红书" };
const statusLabels: Record<PlatformPublishJob["status"], string> = {
  queued: "等待本机发布队列", running: "正在提交", succeeded: "平台已返回发布结果",
  failed: "发布前失败", cancelled: "已取消", interrupted: "结果待核对（不会自动重发）",
};
const stageLabels: Record<CreationTask["stage"], string> = { topic: "选题", script: "文案", narration: "配音", montage: "视频剪辑", review: "成片检查" };

function taskState(task: CreationTask): { label: string; tone: string; icon: typeof CircleDashed } {
  if (task.stage_status === "failed") return { label: "运行失败", tone: styles.stateDanger, icon: AlertCircle };
  if (task.output_ready) return { label: "成片就绪", tone: styles.stateSuccess, icon: CheckCircle2 };
  if (task.stage_status === "waiting_for_user") return { label: "需要处理", tone: styles.stateWarning, icon: AlertCircle };
  if (task.stage_status === "running") return { label: "正在制作", tone: styles.stateBlue, icon: LoaderCircle };
  return { label: "尚未开始", tone: styles.stateNeutral, icon: CircleDashed };
}

function formatTime(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "时间未知" : new Intl.DateTimeFormat("zh-CN", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }).format(date);
}

export function PublishingWorkspace() {
  const [tasks, setTasks] = useState<CreationTask[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [filter, setFilter] = useState<Filter>("all");
  const [accounts, setAccounts] = useState<PlatformBrowserAccount[]>([]);
  const [platforms, setPlatforms] = useState<PublishPlatformCapability[]>([]);
  const [jobs, setJobs] = useState<PlatformPublishJob[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [topics, setTopics] = useState("");
  const [confirmOpen, setConfirmOpen] = useState(false);
  const latestRequest = useRef(0);

  const refreshTasks = useCallback(async () => {
    const requestId = ++latestRequest.current;
    setLoading(true); setError(null);
    try {
      const response = await listCreationTasks();
      if (requestId !== latestRequest.current) return;
      setTasks(response.items);
      setSelectedId((current) => response.items.some((task) => task.id === current) ? current : response.items[0]?.id ?? null);
    } catch (cause) {
      if (requestId !== latestRequest.current) return;
      setError(cause instanceof Error ? cause.message : "暂时无法读取创作记录。");
    } finally { if (requestId === latestRequest.current) setLoading(false); }
  }, []);

  const refreshPublishing = useCallback(async () => {
    setError(null);
    try {
      const [platformResponse, accountResponse] = await Promise.all([listPublishingPlatforms(), listPublishingAccounts()]);
      setPlatforms(platformResponse.items); setAccounts(accountResponse.items);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "暂时无法读取发布账号。"); }
  }, []);

  useEffect(() => { void refreshTasks(); void refreshPublishing(); }, [refreshTasks, refreshPublishing]);

  const filtered = useMemo(() => tasks.filter((task) => {
    if (filter === "ready") return task.output_ready;
    if (filter === "attention") return task.stage_status === "waiting_for_user";
    if (filter === "failed") return task.stage_status === "failed";
    return true;
  }), [filter, tasks]);
  const selected = filtered.find((task) => task.id === selectedId) ?? filtered[0] ?? null;
  const connectedDouyin = accounts.find((account) => account.platform === "douyin");

  useEffect(() => {
    let active = true;
    if (!selected) { setJobs([]); return () => { active = false; }; }
    void listPlatformPublishJobs(selected.id).then((response) => { if (active) setJobs(response.items); })
      .catch((cause) => { if (active) setError(cause instanceof Error ? cause.message : "无法读取发布记录。"); });
    return () => { active = false; };
  }, [selected?.id]);

  async function connectDouyin() {
    setBusy("connect"); setError(null); setNotice(null);
    try {
      const result = await connectPublishingAccount("douyin");
      setNotice(result.detail);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "无法打开独立浏览器。"); }
    finally { setBusy(null); }
  }

  async function verifyDouyin() {
    setBusy("verify"); setError(null); setNotice(null);
    try {
      const result = await verifyPublishingAccount("douyin");
      if (result.connected && result.account_ref) {
        setNotice(`已从独立浏览器读取账号 ${result.account_ref}；请提交前再次核对。`);
        await refreshPublishing();
      } else setNotice(result.detail || "未读到当前登录账号，请先在独立浏览器完成登录。");
    } catch (cause) { setError(cause instanceof Error ? cause.message : "账号核验失败。"); }
    finally { setBusy(null); }
  }

  async function submitPublish() {
    if (!selected || !connectedDouyin?.account_ref || !selected.current_timeline_version_id) return;
    setBusy("publish"); setError(null); setNotice(null);
    try {
      const normalizedTopics = topics.split(/[，,\n]/).map((topic) => topic.trim().replace(/^#/, "")).filter(Boolean);
      const job = await createPlatformPublishJob(selected.id, {
        platform: "douyin", account_ref: connectedDouyin.account_ref,
        timeline_version_id: selected.current_timeline_version_id,
        title, description, topics: normalizedTopics,
        idempotency_key: crypto.randomUUID(), confirmed: true,
      });
      setConfirmOpen(false); setNotice(`发布作业 ${job.id} 已写入本机队列；实际平台结果以队列回读为准。`);
      const response = await listPlatformPublishJobs(selected.id);
      setJobs(response.items);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "发布作业未能提交。"); }
    finally { setBusy(null); }
  }

  const inspector = selected ? <div className={styles.inspector}>
    <span className={styles.objectEyebrow}>当前创作任务</span><h3>{selected.name}</h3>
    <span className={`${styles.stateBadge} ${taskState(selected).tone}`}>{taskState(selected).label}</span>
    <dl><div><dt>当前阶段</dt><dd>{stageLabels[selected.stage]}</dd></div><div><dt>整体进度</dt><dd>{selected.progress}%</dd></div><div><dt>当前版本</dt><dd>{selected.timeline_sequence ? `V${selected.timeline_sequence}` : "无"}</dd></div><div><dt>可发布成片</dt><dd>{selected.output_ready ? "当前版本可检查" : "尚未就绪"}</dd></div></dl>
    {(selected.stage_reason || selected.stage_repair_action) ? <section className={styles.inspectorNotice}><strong>{selected.stage_impact || "当前任务需要处理"}</strong>{selected.stage_reason ? <p>原因：{selected.stage_reason}</p> : null}{selected.stage_repair_action ? <p>处理方式：{selected.stage_repair_action}</p> : null}</section> : null}
    <Link className={styles.primaryButton} href={`/tasks/${encodeURIComponent(selected.id)}`}>打开任务 <ArrowRight size={15} /></Link>
  </div> : undefined;

  return <AppShell creationTasks={tasks} inspector={inspector} inspectorTitle="发布与账号">
    <div className={styles.page}>
      <section className={styles.sectionHeading} style={{ marginTop: 4 }}><div><span>发布工作台</span><h2>账号、成片与发布记录</h2><p>账号保存在成员专用的 Chrome 配置中；发送前会再次核对账号、当前成片版本和完整文案。</p></div></section>
      {error ? <section className={styles.inspectorNotice} role="alert"><strong>操作未完成</strong><p>{error}</p><button className={styles.secondaryButton} onClick={() => { void refreshPublishing(); void refreshTasks(); }} type="button">重新读取</button></section> : null}
      {notice ? <p role="status" className={styles.inspectorMuted}>{notice}</p> : null}

      <section className={styles.inspectorMuted} aria-label="发布平台">
        <strong>平台接入状态</strong>
        {platforms.map((platform) => {
          const account = accounts.find((item) => item.platform === platform.platform);
          return <div key={platform.platform} style={{ display: "flex", alignItems: "center", gap: 8, padding: "8px 0", borderBottom: "1px solid var(--border-1)" }}>
            <UserRound size={16} /><span style={{ flex: 1 }}><strong>{platform.label}</strong><small style={{ display: "block", color: "var(--text-3)" }}>{account ? `上次核验账号：${account.account_ref} · ${formatTime(account.verified_at)}` : platform.requirement}</small></span>
            <span>{account ? "有上次核验记录" : platform.status === "candidate_unverified" ? "候选接入，未完成真实验收" : "尚未接通"}</span>
            {platform.platform === "douyin" ? <>
              <button className={styles.secondaryButton} disabled={busy !== null} onClick={() => void connectDouyin()} type="button">{busy === "connect" ? "正在打开…" : "打开专用浏览器"}</button>
              <button className={styles.secondaryButton} disabled={busy !== null} onClick={() => void verifyDouyin()} type="button">{busy === "verify" ? "正在核验…" : "读取当前账号"}</button>
            </> : null}
          </div>;
        })}
        <p>本机尚未完成真实抖音页面控件验收；不会把候选选择器、登录窗口或本地排队描述为平台已发布。</p>
      </section>

      <section className={styles.inspectorMuted} aria-label="发布内容确认">
        <strong>选择一条当前已就绪的成片</strong>
        <label style={{ display: "block", margin: "8px 0" }}>创作任务<select value={selected?.id ?? ""} onChange={(event) => setSelectedId(event.target.value)} style={{ display: "block", width: "100%", marginTop: 4 }}>
          {tasks.filter((task) => task.output_ready && task.current_timeline_version_id).map((task) => <option key={task.id} value={task.id}>{task.name} · V{task.timeline_sequence}</option>)}
        </select></label>
        {selected ? <p>当前内容：{selected.name} · V{selected.timeline_sequence} · {selected.output_ready ? "已就绪" : "未就绪"}</p> : <p>没有已就绪的当前成片；完成并检查后才能继续。</p>}
        <label style={{ display: "block", margin: "8px 0" }}>发布标题<input value={title} maxLength={30} onChange={(event) => setTitle(event.target.value)} /></label>
        <label style={{ display: "block", margin: "8px 0" }}>发布描述<textarea value={description} maxLength={1000} onChange={(event) => setDescription(event.target.value)} /></label>
        <label style={{ display: "block", margin: "8px 0" }}>话题（逗号或换行分隔）<input value={topics} onChange={(event) => setTopics(event.target.value)} /></label>
        <button className={styles.primaryButton} disabled={!selected?.output_ready || !selected.current_timeline_version_id || !connectedDouyin || busy !== null || !title.trim()} onClick={() => setConfirmOpen(true)} type="button">
          <Send size={14} /> 检查并确认抖音发布
        </button>
        {!connectedDouyin ? <p>请先打开专用浏览器并读取当前账号。上次核验记录不等于此刻仍登录。</p> : null}
      </section>

      <section className={styles.recordList} aria-label="持久发布作业">
        <div className={styles.sectionHeading}><h2>发布作业</h2><button className={styles.secondaryButton} disabled={!selected || busy !== null} onClick={() => selected && void listPlatformPublishJobs(selected.id).then((value) => setJobs(value.items)).catch((cause) => setError(cause instanceof Error ? cause.message : "读取发布记录失败"))} type="button"><RefreshCw size={14} />刷新结果</button></div>
        {jobs.length ? <ol className={styles.records}>{jobs.map((job) => {
          const resultEvent = job.events.find((event) => event.type === "platform_publish_succeeded");
          const remoteId = resultEvent?.payload.remote_publish_id;
          const queueWaitLabel = job.queue_wait_reason === "resource_unknown" ? "本机资源条件当前不可确认，尚未开始提交。"
            : job.queue_wait_reason === "resource_busy" ? "本机资源正被其他工作占用，发布仍在等待。"
              : job.queue_wait_reason === "owner_unknown" ? "暂时不能复核任务归属，发布尚未执行。"
                : job.queue_wait_reason === "input_stale" ? "当前成片版本已变化，请重新检查并确认。" : null;
          return <li key={job.id}><div className={styles.selectedRecord} style={{ padding: 12 }}><strong>{statusLabels[job.status]}</strong><p>{job.id} · {formatTime(job.events.at(-1)?.created_at ?? "")}</p>{queueWaitLabel ? <p>{queueWaitLabel}</p> : null}{job.impact ? <p>{job.impact}</p> : null}{job.repair_action ? <p>处理：{job.repair_action}</p> : null}{typeof remoteId === "string" ? <p>平台返回编号：{remoteId}</p> : null}</div></li>;
        })}</ol> : <p>当前任务没有已保存的发布作业。</p>}
      </section>

      {error && !tasks.length ? <p role="alert">创作任务读取失败：{error}</p> : null}
      <div className={styles.summaryRow}><span><strong>{tasks.length}</strong> 全部任务</span><span><strong>{tasks.filter((task) => task.output_ready).length}</strong> 成片就绪</span><button className={styles.secondaryButton} disabled={loading} onClick={() => void refreshTasks()} type="button"><RefreshCw size={14} />刷新</button></div>
      <nav aria-label="记录筛选" className={styles.tabs}>{filters.map((item) => <button aria-pressed={filter === item.id} className={filter === item.id ? styles.activeTab : undefined} key={item.id} onClick={() => setFilter(item.id)} type="button">{item.label}</button>)}</nav>
      <section className={styles.recordList}><div className={styles.sectionHeading}><h2>创作任务</h2><p>{loading ? "正在读取…" : `${filtered.length} 项`}</p></div>
        {filtered.length ? <ol className={styles.records}>{filtered.map((task) => { const state = taskState(task); const StateIcon = state.icon; return <li key={task.id}><button aria-pressed={selected?.id === task.id} className={selected?.id === task.id ? styles.selectedRecord : undefined} data-open-details onClick={() => setSelectedId(task.id)} type="button"><span className={`${styles.recordState} ${state.tone}`}><StateIcon size={16} /></span><span className={styles.recordBody}><span className={styles.recordTitle}><strong>{task.name}</strong><em>{state.label}</em></span><span>{stageLabels[task.stage]} · {task.output_ready ? `可发布版本 V${task.timeline_sequence}` : "完成检查后可发布"}</span><span className={styles.progressTrack} aria-label={`进度 ${task.progress}%`}><i style={{ width: `${task.progress}%` }} /></span></span><span className={styles.recordTime}>{formatTime(task.updated_at)}<ArrowRight size={15} /></span></button></li>; })}</ol> : <div className={styles.emptyStage}><span className={styles.emptyIcon}><Film size={22} /></span><div><h2>{loading ? "正在读取任务" : "当前没有可显示的记录"}</h2><p>{error || "发布只会使用已确认且仍为当前版本的有效成片。"}</p></div></div>}
      </section>

      {confirmOpen && selected && connectedDouyin ? <HomeCardDialog kind="confirmation" title="确认抖音发布内容" onClose={() => setConfirmOpen(false)} footer={<><button className={styles.secondaryButton} data-card-cancel onClick={() => setConfirmOpen(false)} type="button">返回修改</button><button className={styles.primaryButton} disabled={busy !== null} onClick={() => void submitPublish()} type="button">{busy === "publish" ? "正在提交…" : "我已核对，创建发布作业"}</button></>}>
        <p>平台：抖音</p><p>账号：{connectedDouyin.account_ref}</p><p>成片：{selected.name} · 当前版本 V{selected.timeline_sequence}</p><p>标题：{title}</p><p>描述：{description || "（空）"}</p><p>话题：{topics || "（无）"}</p><p>确认后系统会先保存持久作业，再由本机浏览器驱动；若平台结果未知，不会自动重发。</p>
      </HomeCardDialog> : null}
    </div>
  </AppShell>;
}
