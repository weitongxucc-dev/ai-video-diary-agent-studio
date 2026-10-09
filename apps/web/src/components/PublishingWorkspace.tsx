"use client";

import Link from "next/link";
import { AlertCircle, ArrowRight, CheckCircle2, CircleDashed, Film, LoaderCircle, RefreshCw, Send, UserRound } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AppShell } from "@/components/AppShell";
import { HomeCardDialog } from "@/components/HomeCardDialog";
import { connectPublishingAccount, createPlatformPublishJob, getCreationTaskTimelineVersion, listCreationTasks, listPlatformPublishJobs, listPublishingAccounts, listPublishingPlatforms, verifyPublishingAccount } from "@/lib/api";
import type { CreationTask, CreationTaskTimelineVersionDetail, PlatformBrowserAccount, PlatformPublishJob, PublishPlatform, PublishPlatformCapability } from "@/lib/types";
import styles from "./ProductWorkspace.module.css";
import publishStyles from "./PublishingWorkspace.module.css";

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
type PublishDraft = { title: string; description: string; topics: string };
type PublishConfirmation = PublishDraft & { taskId: string; versionId: string; accountRef: string };
const emptyDraft: PublishDraft = { title: "", description: "", topics: "" };

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
  const [drafts, setDrafts] = useState<Record<string, PublishDraft>>({});
  const [verifiedAccountRef, setVerifiedAccountRef] = useState<string | null>(null);
  const [versionDetail, setVersionDetail] = useState<CreationTaskTimelineVersionDetail | null>(null);
  const [versionError, setVersionError] = useState<string | null>(null);
  const [versionLoading, setVersionLoading] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [confirmation, setConfirmation] = useState<PublishConfirmation | null>(null);
  const latestRequest = useRef(0);
  const latestJobsRequest = useRef(0);
  const latestVersionRequest = useRef(0);

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

  const refreshJobs = useCallback(async (taskId: string | null) => {
    const requestId = ++latestJobsRequest.current;
    if (!taskId) { setJobs([]); return; }
    try {
      const response = await listPlatformPublishJobs(taskId);
      if (requestId === latestJobsRequest.current) setJobs(response.items);
    } catch (cause) {
      if (requestId === latestJobsRequest.current) setError(cause instanceof Error ? cause.message : "无法读取发布记录。");
    }
  }, []);

  useEffect(() => { void refreshTasks(); void refreshPublishing(); }, [refreshTasks, refreshPublishing]);

  const filtered = useMemo(() => tasks.filter((task) => {
    if (filter === "ready") return task.output_ready;
    if (filter === "attention") return task.stage_status === "waiting_for_user";
    if (filter === "failed") return task.stage_status === "failed";
    return true;
  }), [filter, tasks]);
  const selected = tasks.find((task) => task.id === selectedId) ?? null;
  const connectedDouyin = accounts.find((account) => account.platform === "douyin");
  const versionId = selected?.current_timeline_version_id ?? null;
  const draftKey = selected && versionId ? `${selected.id}:${versionId}` : null;
  const currentDraft = draftKey ? drafts[draftKey] ?? emptyDraft : emptyDraft;
  const videoArtifact = versionDetail?.artifacts.find((artifact) => artifact.kind === "video");
  const currentOutputReady = Boolean(selected?.output_ready && versionId && versionDetail?.task_id === selected.id
    && versionDetail.version.id === versionId && versionDetail.version.is_current && videoArtifact?.status === "available" && videoArtifact.url);
  const accountReady = Boolean(verifiedAccountRef);

  useEffect(() => {
    const requestId = ++latestVersionRequest.current;
    setVersionDetail(null);
    setVersionError(null);
    if (!selected?.output_ready || !versionId) { setVersionLoading(false); return; }
    setVersionLoading(true);
    getCreationTaskTimelineVersion(selected.id, versionId)
      .then((detail) => { if (requestId === latestVersionRequest.current) setVersionDetail(detail); })
      .catch((cause) => { if (requestId === latestVersionRequest.current) setVersionError(cause instanceof Error ? cause.message : "暂时无法核对当前成片版本。"); })
      .finally(() => { if (requestId === latestVersionRequest.current) setVersionLoading(false); });
    return () => { latestVersionRequest.current += 1; };
  }, [selected?.id, selected?.output_ready, versionId]);

  useEffect(() => {
    if (confirmation && (confirmation.taskId !== selected?.id || confirmation.versionId !== versionId)) {
      setConfirmOpen(false); setConfirmation(null);
    }
  }, [confirmation, selected?.id, versionId]);

  useEffect(() => {
    if (filtered.length && !filtered.some((task) => task.id === selectedId)) {
      setSelectedId(filtered[0]?.id ?? null);
    } else if (!filtered.length && selectedId) {
      setSelectedId(null);
    }
  }, [filter, filtered, selectedId]);

  useEffect(() => {
    void refreshJobs(selected?.id ?? null);
    return () => { latestJobsRequest.current += 1; };
  }, [refreshJobs, selected?.id]);

  async function refreshAll() {
    setError(null); setVerifiedAccountRef(null); setConfirmOpen(false); setConfirmation(null);
    await Promise.all([refreshTasks(), refreshPublishing(), refreshJobs(selected?.id ?? null)]);
  }

  async function connectDouyin() {
    setBusy("connect"); setError(null); setNotice(null); setVerifiedAccountRef(null); setConfirmOpen(false); setConfirmation(null);
    try {
      const result = await connectPublishingAccount("douyin");
      setNotice(result.detail);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "无法打开独立浏览器。"); }
    finally { setBusy(null); }
  }

  async function verifyDouyin() {
    setBusy("verify"); setError(null); setNotice(null); setVerifiedAccountRef(null); setConfirmOpen(false); setConfirmation(null);
    try {
      const result = await verifyPublishingAccount("douyin");
      if (result.connected && result.account_ref) {
        setVerifiedAccountRef(result.account_ref);
        setNotice(`已从独立浏览器读取账号 ${result.account_ref}；请提交前再次核对。`);
        await refreshPublishing();
      } else { setVerifiedAccountRef(null); setNotice(result.detail || "未读到当前登录账号，请先在独立浏览器完成登录。"); }
    } catch (cause) { setError(cause instanceof Error ? cause.message : "账号核验失败。"); }
    finally { setBusy(null); }
  }

  function updateDraft(field: keyof PublishDraft, value: string) {
    if (!draftKey) return;
    setDrafts((current) => ({ ...current, [draftKey]: { ...(current[draftKey] ?? emptyDraft), [field]: value } }));
  }

  function selectTask(taskId: string | null) {
    setSelectedId(taskId);
    if (taskId && !filtered.some((task) => task.id === taskId)) setFilter("all");
    setConfirmOpen(false); setConfirmation(null);
  }

  async function openPublishConfirmation() {
    if (!selected || !versionId || !currentOutputReady || !verifiedAccountRef || busy !== null) return;
    setBusy("preflight"); setError(null); setNotice(null); setConfirmOpen(false); setConfirmation(null);
    try {
      const [taskResponse, accountCheck, detail] = await Promise.all([
        listCreationTasks(), verifyPublishingAccount("douyin"), getCreationTaskTimelineVersion(selected.id, versionId),
      ]);
      const freshTask = taskResponse.items.find((task) => task.id === selected.id);
      setTasks(taskResponse.items);
      if (!freshTask?.output_ready || freshTask.current_timeline_version_id !== versionId
        || detail.task_id !== selected.id || detail.version.id !== versionId || detail.version.sequence !== freshTask.timeline_sequence || !detail.version.is_current
        || !detail.artifacts.some((artifact) => artifact.kind === "video" && artifact.status === "available" && artifact.url)
        || !accountCheck.connected || !accountCheck.account_ref) {
        setVerifiedAccountRef(accountCheck.connected ? accountCheck.account_ref ?? null : null);
        setNotice("发布目标或账号状态已变化；请重新选择并核验后再编辑。");
        return;
      }
      setVerifiedAccountRef(accountCheck.account_ref);
      setVersionDetail(detail);
      const current = draftKey ? drafts[draftKey] ?? emptyDraft : emptyDraft;
      setConfirmation({ taskId: freshTask.id, versionId, accountRef: accountCheck.account_ref, ...current });
      setConfirmOpen(true);
    } catch (cause) { setVerifiedAccountRef(null); setError(cause instanceof Error ? cause.message : "发布预检未完成，请重新读取任务和账号。"); }
    finally { setBusy(null); }
  }

  async function submitPublish() {
    if (!confirmation || !selected || busy !== null) return;
    setBusy("publish"); setError(null); setNotice(null);
    try {
      const [taskResponse, accountCheck, detail] = await Promise.all([
        listCreationTasks(), verifyPublishingAccount("douyin"), getCreationTaskTimelineVersion(confirmation.taskId, confirmation.versionId),
      ]);
      const freshTask = taskResponse.items.find((task) => task.id === confirmation.taskId);
      setTasks(taskResponse.items);
      if (selected.id !== confirmation.taskId || selected.current_timeline_version_id !== confirmation.versionId
        || !freshTask?.output_ready || freshTask.current_timeline_version_id !== confirmation.versionId
        || detail.task_id !== confirmation.taskId || detail.version.id !== confirmation.versionId || detail.version.sequence !== freshTask.timeline_sequence || !detail.version.is_current
        || !detail.artifacts.some((artifact) => artifact.kind === "video" && artifact.status === "available" && artifact.url)
        || !accountCheck.connected || accountCheck.account_ref !== confirmation.accountRef) {
        setVerifiedAccountRef(accountCheck.connected ? accountCheck.account_ref ?? null : null);
        setConfirmOpen(false); setConfirmation(null);
        setNotice("确认期间任务版本或账号已变化，未提交发布作业；请重新核对。");
        return;
      }
      const normalizedTopics = confirmation.topics.split(/[，,\n]/).map((topic) => topic.trim().replace(/^#/, "")).filter(Boolean);
      const job = await createPlatformPublishJob(confirmation.taskId, {
        platform: "douyin", account_ref: confirmation.accountRef,
        timeline_version_id: confirmation.versionId,
        title: confirmation.title, description: confirmation.description, topics: normalizedTopics,
        idempotency_key: crypto.randomUUID(), confirmed: true,
      });
      setConfirmOpen(false); setConfirmation(null); setNotice(`发布作业 ${job.id} 已写入本机队列；实际平台结果以队列回读为准。`);
      const response = await listPlatformPublishJobs(confirmation.taskId);
      setJobs(response.items);
    } catch (cause) { setVerifiedAccountRef(null); setConfirmOpen(false); setConfirmation(null); setError(cause instanceof Error ? cause.message : "发布作业未能提交。"); }
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
      {error ? <section className={styles.inspectorNotice} role="alert"><strong>操作未完成</strong><p>{error}</p><button className={styles.secondaryButton} onClick={() => void refreshAll()} type="button">重新读取</button></section> : null}
      {notice ? <p role="status" className={styles.inspectorMuted}>{notice}</p> : null}

      <section className={styles.inspectorMuted} aria-label="发布平台">
        <strong>平台接入状态</strong>
        {platforms.map((platform) => {
          const account = accounts.find((item) => item.platform === platform.platform);
          return <div key={platform.platform} style={{ display: "flex", alignItems: "center", gap: 8, padding: "8px 0", borderBottom: "1px solid var(--border-1)" }}>
            <UserRound size={16} /><span style={{ flex: 1 }}><strong>{platform.label}</strong><small style={{ display: "block", color: "var(--text-3)" }}>{account ? `上次核验账号：${account.account_ref} · ${formatTime(account.verified_at)}` : platform.requirement}</small></span>
            <span>{platform.platform === "douyin" && verifiedAccountRef ? `本轮已核验：${verifiedAccountRef}` : account ? "有上次核验记录，尚未本轮核验" : platform.status === "candidate_unverified" ? "候选接入，未完成真实验收" : "尚未接通"}</span>
            {platform.platform === "douyin" ? <>
              <button className={styles.secondaryButton} disabled={busy !== null} onClick={() => void connectDouyin()} type="button">{busy === "connect" ? "正在打开…" : "打开专用浏览器"}</button>
              <button className={styles.secondaryButton} disabled={busy !== null} onClick={() => void verifyDouyin()} type="button">{busy === "verify" ? "正在核验…" : "读取当前账号"}</button>
            </> : null}
          </div>;
        })}
        <p>本机尚未完成真实抖音页面控件验收；不会把候选选择器、登录窗口或本地排队描述为平台已发布。</p>
      </section>

      <section className={`${styles.inspectorMuted} ${publishStyles.publishFlow}`} aria-label="发布内容确认">
        <h3 className={publishStyles.flowTitle}>1. 选择发布目标</h3>
        <label className={publishStyles.field}>任务与成片版本<select aria-label="发布目标（任务与成片版本）" value={selected?.id ?? ""} onChange={(event) => selectTask(event.target.value || null)}>
          <option value="" disabled>选择创作任务</option>
          {tasks.map((task) => <option key={task.id} value={task.id}>{task.name} · {task.current_timeline_version_id ? `V${task.timeline_sequence}` : "无当前版本"} · {task.output_ready ? "待核对成片" : "未就绪"}</option>)}
        </select></label>
        {selected ? <p className={publishStyles.targetSummary}>发布目标：{selected.name} · {versionId ? `V${selected.timeline_sequence}` : "无当前版本"} · {currentOutputReady ? "当前视频产物可用" : selected.output_ready ? "正在核对产物" : "尚未就绪"}</p> : <p>暂无创作任务。任务读取后会显示可检查的发布目标。</p>}
        {versionLoading ? <p role="status">正在核对所选版本的成片文件…</p> : null}
        {versionError ? <p role="alert">成片版本核对失败：{versionError} <button className={styles.secondaryButton} onClick={() => { if (selected && versionId) { setVersionError(null); setVersionLoading(true); getCreationTaskTimelineVersion(selected.id, versionId).then(setVersionDetail).catch((cause) => setVersionError(cause instanceof Error ? cause.message : "暂时无法核对当前成片版本。")).finally(() => setVersionLoading(false)); } }} type="button">重新核对</button></p> : null}
        {!selected?.output_ready || !versionId ? <p className={publishStyles.gate}>这个任务还没有当前可发布成片。可先查看记录或打开任务继续制作，不能在此输入会被误认为可发布的文案。</p> : null}
        {selected?.output_ready && versionId && !versionLoading && !versionError && !currentOutputReady ? <p className={publishStyles.gate}>当前版本未提供可核验的视频文件，暂不能作为发布目标；请打开任务检查成片。</p> : null}

        <h3 className={publishStyles.flowTitle}>2. 核验当前账号</h3>
        <p>{verifiedAccountRef ? `本轮独立核验账号：${verifiedAccountRef}` : connectedDouyin ? `上次核验记录：${connectedDouyin.account_ref}；需重新读取当前登录状态。` : "尚无账号核验记录。"}</p>
        <button className={styles.secondaryButton} disabled={busy !== null} onClick={() => void verifyDouyin()} type="button">{busy === "verify" ? "正在核验…" : "重新核验当前抖音账号"}</button>

        {currentOutputReady && accountReady ? <>
          <h3 className={publishStyles.flowTitle}>3. 编辑本轮临时文案</h3>
          <p className={publishStyles.draftNote}>草稿仅保存在本轮页面内，并绑定 {selected?.name} · V{selected?.timeline_sequence}；切换目标会显示该目标自己的草稿，刷新页面不会恢复。</p>
          <label className={publishStyles.field}>发布标题<input aria-label="发布标题" value={currentDraft.title} maxLength={30} onChange={(event) => updateDraft("title", event.target.value)} /></label>
          <label className={publishStyles.field}>发布描述<textarea aria-label="发布描述" value={currentDraft.description} maxLength={1000} onChange={(event) => updateDraft("description", event.target.value)} /></label>
          <label className={publishStyles.field}>话题（逗号或换行分隔）<input aria-label="话题（逗号或换行分隔）" value={currentDraft.topics} onChange={(event) => updateDraft("topics", event.target.value)} /></label>
          <button className={styles.primaryButton} disabled={busy !== null || !currentDraft.title.trim()} onClick={() => void openPublishConfirmation()} type="button">
            <Send size={14} /> {busy === "preflight" ? "正在复核任务与账号…" : "检查并确认抖音发布"}
          </button>
        </> : null}
      </section>

      <section className={styles.recordList} aria-label="持久发布作业">
        <div className={styles.sectionHeading}><h2>发布作业</h2><button className={styles.secondaryButton} disabled={!selected || busy !== null} onClick={() => void refreshJobs(selected?.id ?? null)} type="button"><RefreshCw size={14} />刷新结果</button></div>
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
      <div className={styles.summaryRow}><span><strong>{tasks.length}</strong> 全部任务</span><span><strong>{tasks.filter((task) => task.output_ready).length}</strong> 成片就绪</span><button className={styles.secondaryButton} disabled={loading} onClick={() => void refreshAll()} type="button"><RefreshCw size={14} />刷新</button></div>
      <nav aria-label="记录筛选" className={styles.tabs}>{filters.map((item) => <button aria-pressed={filter === item.id} className={filter === item.id ? styles.activeTab : undefined} key={item.id} onClick={() => setFilter(item.id)} type="button">{item.label}</button>)}</nav>
      <section className={styles.recordList}><div className={styles.sectionHeading}><h2>创作任务</h2><p>{loading ? "正在读取…" : `${filtered.length} 项`}</p></div>
        {filtered.length ? <ol className={styles.records}>{filtered.map((task) => { const state = taskState(task); const StateIcon = state.icon; return <li key={task.id}><button aria-pressed={selected?.id === task.id} className={selected?.id === task.id ? styles.selectedRecord : undefined} data-open-details onClick={() => selectTask(task.id)} type="button"><span className={`${styles.recordState} ${state.tone}`}><StateIcon size={16} /></span><span className={styles.recordBody}><span className={styles.recordTitle}><strong>{task.name}</strong><em>{state.label}</em></span><span>{stageLabels[task.stage]} · {task.output_ready ? `当前成片版本 V${task.timeline_sequence}` : "打开任务继续处理"}</span><span className={styles.progressTrack} aria-label={`进度 ${task.progress}%`}><i style={{ width: `${task.progress}%` }} /></span></span><span className={styles.recordTime}>{formatTime(task.updated_at)}<ArrowRight size={15} /></span></button></li>; })}</ol> : <div className={styles.emptyStage}><span className={styles.emptyIcon}><Film size={22} /></span><div><h2>{loading ? "正在读取任务" : "当前没有可显示的记录"}</h2><p>{error || "发布只会使用已确认且仍为当前版本的有效成片。"}</p></div></div>}
      </section>

      {confirmOpen && selected && confirmation ? <HomeCardDialog kind="confirmation" title="确认抖音发布内容" onClose={() => { setConfirmOpen(false); setConfirmation(null); }} footer={<><button className={styles.secondaryButton} data-card-cancel onClick={() => { setConfirmOpen(false); setConfirmation(null); }} type="button">返回修改</button><button className={styles.primaryButton} disabled={busy !== null} onClick={() => void submitPublish()} type="button">{busy === "publish" ? "正在重新核验…" : "我已核对，创建发布作业"}</button></>}>
        <p>平台：抖音</p><p>账号：{confirmation.accountRef}</p><p>成片：{selected.name} · 当前版本 V{selected.timeline_sequence}（{confirmation.versionId}）</p><p>标题：{confirmation.title}</p><p>描述：{confirmation.description || "（空）"}</p><p>话题：{confirmation.topics || "（无）"}</p><p>确认后还会复核任务版本和独立浏览器中的账号；发生变化将取消本次确认。创建的是本机发布作业，不代表平台已发布。</p>
      </HomeCardDialog> : null}
    </div>
  </AppShell>;
}
