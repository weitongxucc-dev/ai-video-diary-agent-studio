"use client";

import { AppSelect, AppSelectOption } from "@/components/AppSelect";
import Link from "next/link";
import { ArrowRight, Film, RefreshCw, Search, Send, UserRound, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AppShell } from "@/components/AppShell";
import { HomeCardDialog } from "@/components/HomeCardDialog";
import { connectPublishingAccount, createPlatformPublishJob, getCreationTaskTimelineVersion, listCreationTasks, listPlatformPublishJobs, listPublishingAccounts, listPublishingPlatforms, verifyPublishingAccount } from "@/lib/api";
import type { CreationTask, CreationTaskTimelineVersionDetail, PlatformBrowserAccount, PlatformPublishJob, PublishPlatform, PublishPlatformCapability } from "@/lib/types";
import styles from "./PublishingWorkspace.module.css";


const statusLabels: Record<PlatformPublishJob["status"], string> = {
  queued: "等待本机发布队列", running: "正在提交", succeeded: "平台已返回发布结果",
  failed: "发布失败", cancelled: "已取消", interrupted: "结果待核对（不会自动重发）",
};
type PublishDraft = { title: string; description: string; topics: string };
type PublishConfirmation = PublishDraft & { taskId: string; taskName: string; versionId: string; accountRef: string };
const emptyDraft: PublishDraft = { title: "", description: "", topics: "" };

function formatTime(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "时间未知" : new Intl.DateTimeFormat("zh-CN", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }).format(date);
}

export function PublishingWorkspace() {
  const [view, setView] = useState<"videos"|"jobs"|"accounts"|"data">("videos");
  const [card, setCard] = useState<"prepare"|"account"|"job"|null>(null);
  const [query, setQuery] = useState("");
  const [selectedPlatform, setSelectedPlatform] = useState<PublishPlatform>("douyin");
  const [selectedJobId, setSelectedJobId] = useState<string|null>(null);
  const [readAt, setReadAt] = useState<string|null>(null);
  const [jobsReadAt, setJobsReadAt] = useState<Record<string,string>>({});
  const [jobsErrors, setJobsErrors] = useState<Record<string,string|null>>({});
  const [jobsLoading, setJobsLoading] = useState<Record<string,boolean>>({});
  const publishingRequest = useRef(0);
  const [publishingError, setPublishingError] = useState<string|null>(null);
  const [tasks, setTasks] = useState<CreationTask[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [accounts, setAccounts] = useState<PlatformBrowserAccount[]>([]);
  const [platforms, setPlatforms] = useState<PublishPlatformCapability[]>([]);
  const [jobsByTask, setJobsByTask] = useState<Record<string, PlatformPublishJob[]>>({});
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
  const latestJobsRequest = useRef(new Map<string, number>());
  const latestVersionRequest = useRef(0);
  const operationRevision = useRef(0);
  const busyOperation = useRef<number | null>(null);
  const sendInFlight = useRef<number | null>(null);

  const refreshTasks = useCallback(async () => {
    const requestId = ++latestRequest.current;
    setLoading(true); setError(null);
    try {
      const response = await listCreationTasks();
      if (requestId !== latestRequest.current) return;
      setTasks(response.items); setReadAt(new Date().toISOString());
      setSelectedId((current) => response.items.some((task) => task.id === current) ? current : response.items[0]?.id ?? null);
    } catch (cause) {
      if (requestId !== latestRequest.current) return;
      setError(cause instanceof Error ? cause.message : "暂时无法读取创作记录。");
    } finally { if (requestId === latestRequest.current) setLoading(false); }
  }, []);

  const refreshPublishing = useCallback(async () => {
    const revision=++publishingRequest.current;
    setPublishingError(null);
    try {
      const [platformResponse, accountResponse] = await Promise.all([listPublishingPlatforms(), listPublishingAccounts()]);
      if (revision !== publishingRequest.current) return;
      setPlatforms(platformResponse.items); setAccounts(accountResponse.items);
    } catch (cause) { if (revision === publishingRequest.current) setPublishingError(cause instanceof Error ? cause.message : "暂时无法读取发布账号。"); }
  }, []);

  const refreshJobs = useCallback(async (taskId: string | null) => {
    if (!taskId) return;
    const requestId = (latestJobsRequest.current.get(taskId) ?? 0) + 1;
    latestJobsRequest.current.set(taskId, requestId);
    setJobsLoading(current=>({...current,[taskId]:true}));
    setJobsErrors(current=>({...current,[taskId]:null}));
    try {
      const response = await listPlatformPublishJobs(taskId);
      if (requestId === latestJobsRequest.current.get(taskId)) {
        setJobsByTask((current) => ({ ...current, [taskId]: response.items }));
        setJobsReadAt(current=>({...current,[taskId]:new Date().toISOString()}));
      }
    } catch (cause) {
      if (requestId === latestJobsRequest.current.get(taskId)) setJobsErrors(current=>({...current,[taskId]:cause instanceof Error ? cause.message : "无法读取发布记录。"}));
    } finally { if (requestId === latestJobsRequest.current.get(taskId)) setJobsLoading(current=>({...current,[taskId]:false})); }
  }, []);

  useEffect(() => { void refreshTasks(); void refreshPublishing(); }, [refreshTasks, refreshPublishing]);

  const filtered = useMemo(() => tasks.filter(task=>task.name.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase())), [tasks,query]);
  const selected = tasks.find((task) => task.id === selectedId) ?? null;
  const jobs = selected ? jobsByTask[selected.id] ?? [] : [];
  const connectedDouyin = accounts.find((account) => account.platform === "douyin");
  const douyinCapability = platforms.find(platform=>platform.platform === "douyin");
  const versionId = selected?.current_timeline_version_id ?? null;
  const draftKey = selected && versionId ? `${selected.id}:${versionId}` : null;
  const currentDraft = draftKey ? drafts[draftKey] ?? emptyDraft : emptyDraft;
  const videoArtifact = versionDetail?.artifacts.find((artifact) => artifact.kind === "video");
  const currentOutputReady = Boolean(selected?.output_ready && versionId && versionDetail?.task_id === selected.id
    && versionDetail.version.id === versionId && versionDetail.version.is_current && videoArtifact?.status === "available" && videoArtifact.url);
  const accountReady = Boolean(verifiedAccountRef);

  function invalidatePendingOperation() {
    operationRevision.current += 1;
    if (sendInFlight.current === null) { busyOperation.current = null; setBusy(null); }
  }

  const readVersionDetail = useCallback((taskId: string | null, selectedVersionId: string | null) => {
    const requestId = ++latestVersionRequest.current;
    if (!taskId || !selectedVersionId) {
      setVersionDetail(null);
      setVersionError(null);
      setVersionLoading(false);
      return;
    }
    setVersionDetail(null);
    setVersionError(null);
    setVersionLoading(true);
    getCreationTaskTimelineVersion(taskId, selectedVersionId)
      .then((detail) => { if (requestId === latestVersionRequest.current) setVersionDetail(detail); })
      .catch((cause) => { if (requestId === latestVersionRequest.current) setVersionError(cause instanceof Error ? cause.message : "暂时无法核对当前成片版本。"); })
      .finally(() => { if (requestId === latestVersionRequest.current) setVersionLoading(false); });
  }, []);

  useEffect(() => {
    readVersionDetail(selected?.output_ready ? selected.id : null, selected?.output_ready ? versionId : null);
    return () => { latestVersionRequest.current += 1; };
  }, [readVersionDetail, selected?.id, selected?.output_ready, versionId]);

  useEffect(() => {
    if (confirmation && (confirmation.taskId !== selected?.id || confirmation.versionId !== versionId)) {
      setConfirmOpen(false); setConfirmation(null);
    }
  }, [confirmation, selected?.id, versionId]);

  useEffect(() => {
    const requests=latestJobsRequest.current;
    const id=selected?.id;
    void refreshJobs(id ?? null);
    return () => { if (id) requests.set(id, (requests.get(id) ?? 0) + 1); };
  }, [refreshJobs, selected?.id]);

  async function refreshAll() {
    invalidatePendingOperation();
    setError(null); setVerifiedAccountRef(null); setConfirmOpen(false); setConfirmation(null);
    readVersionDetail(selected?.output_ready ? selected.id : null, selected?.output_ready ? versionId : null);
    await Promise.all([refreshTasks(), refreshPublishing(), refreshJobs(selected?.id ?? null)]);
  }

  async function connectDouyin() {
    const operationId = ++operationRevision.current;
    busyOperation.current = operationId;
    setBusy("connect"); setError(null); setNotice(null); setVerifiedAccountRef(null); setConfirmOpen(false); setConfirmation(null);
    try {
      const result = await connectPublishingAccount("douyin");
      if (operationId === operationRevision.current) setNotice(result.detail);
    } catch (cause) { if (operationId === operationRevision.current) setError(cause instanceof Error ? cause.message : "无法打开独立浏览器。"); }
    finally { if (busyOperation.current === operationId) { busyOperation.current = null; setBusy(null); } }
  }

  async function verifyDouyin() {
    const operationId = ++operationRevision.current;
    busyOperation.current = operationId;
    setBusy("verify"); setError(null); setNotice(null); setVerifiedAccountRef(null); setConfirmOpen(false); setConfirmation(null);
    try {
      const result = await verifyPublishingAccount("douyin");
      if (operationId !== operationRevision.current) return;
      if (result.connected && result.account_ref) {
        setVerifiedAccountRef(result.account_ref);
        setNotice(`已从独立浏览器读取账号 ${result.account_ref}；请提交前再次核对。`);
        await refreshPublishing();
      } else { setVerifiedAccountRef(null); setNotice(result.detail || "未读到当前登录账号，请先在独立浏览器完成登录。"); }
    } catch (cause) { if (operationId === operationRevision.current) setError(cause instanceof Error ? cause.message : "账号核验失败。"); }
    finally { if (busyOperation.current === operationId) { busyOperation.current = null; setBusy(null); } }
  }

  function updateDraft(field: keyof PublishDraft, value: string) {
    if (!draftKey) return;
    invalidatePendingOperation();
    if (confirmOpen) { setConfirmOpen(false); setConfirmation(null); }
    setDrafts((current) => ({ ...current, [draftKey]: { ...(current[draftKey] ?? emptyDraft), [field]: value } }));
  }

  function selectTask(taskId: string | null) {
    if (taskId === selectedId) return;
    invalidatePendingOperation();
    setSelectedId(taskId);
    setConfirmOpen(false); setConfirmation(null); setNotice(null); setError(null);
  }

  function closeConfirmation() {
    invalidatePendingOperation();
    setConfirmOpen(false); setConfirmation(null);
    setNotice(sendInFlight.current !== null
      ? "发布作业请求已发出；关闭确认卡不会撤回，请在对应任务的发布记录中查看结果。"
      : null);
  }

  function retryVersionDetail() {
    if (!selected || !versionId) return;
    invalidatePendingOperation();
    readVersionDetail(selected.id, versionId);
  }

  async function openPublishConfirmation() {
    if (!selected || !versionId || !currentOutputReady || !verifiedAccountRef || !douyinCapability?.browser_publish || busy !== null) return;
    const operationId = ++operationRevision.current;
    const target = { taskId: selected.id, versionId, accountRef: verifiedAccountRef };
    const copy = { ...currentDraft };
    busyOperation.current = operationId;
    setBusy("preflight"); setError(null); setNotice(null); setConfirmOpen(false); setConfirmation(null);
    try {
      const [taskResponse, accountCheck, detail] = await Promise.all([
        listCreationTasks(), verifyPublishingAccount("douyin"), getCreationTaskTimelineVersion(selected.id, versionId),
      ]);
      if (operationId !== operationRevision.current) return;
      const freshTask = taskResponse.items.find((task) => task.id === selected.id);
      setTasks(taskResponse.items);
      if (!freshTask?.output_ready || freshTask.current_timeline_version_id !== versionId
        || detail.task_id !== selected.id || detail.version.id !== versionId || detail.version.sequence !== freshTask.timeline_sequence || !detail.version.is_current
        || !detail.artifacts.some((artifact) => artifact.kind === "video" && artifact.status === "available" && artifact.url)
        || !accountCheck.connected || accountCheck.account_ref !== target.accountRef) {
        setVerifiedAccountRef(null);
        setNotice(accountCheck.connected && accountCheck.account_ref !== target.accountRef
          ? "当前账号与本轮选择不同；原确认已取消，请先手动核验并选择当前账号。"
          : "发布目标或账号状态已变化；请重新选择并核验后再编辑。");
        return;
      }
      setVersionDetail(detail);
      setConfirmation({ taskId: target.taskId, taskName: freshTask.name, versionId: target.versionId, accountRef: target.accountRef, ...copy });
      setConfirmOpen(true);
    } catch (cause) {
      if (operationId === operationRevision.current) { setVerifiedAccountRef(null); setError(cause instanceof Error ? cause.message : "发布预检未完成，请重新读取任务和账号。"); }
    } finally { if (busyOperation.current === operationId) { busyOperation.current = null; setBusy(null); } }
  }

  async function submitPublish() {
    if (!confirmation || !selected || !douyinCapability?.browser_publish || busy !== null) return;
    const operationId = ++operationRevision.current;
    const snapshot = { ...confirmation };
    busyOperation.current = operationId;
    setBusy("publish"); setError(null); setNotice(null);
    try {
      const [taskResponse, accountCheck, detail] = await Promise.all([
        listCreationTasks(), verifyPublishingAccount("douyin"), getCreationTaskTimelineVersion(snapshot.taskId, snapshot.versionId),
      ]);
      if (operationId !== operationRevision.current) return;
      const freshTask = taskResponse.items.find((task) => task.id === snapshot.taskId);
      setTasks(taskResponse.items);
      if (selected.id !== snapshot.taskId || selected.current_timeline_version_id !== snapshot.versionId
        || !freshTask?.output_ready || freshTask.current_timeline_version_id !== snapshot.versionId
        || detail.task_id !== snapshot.taskId || detail.version.id !== snapshot.versionId || detail.version.sequence !== freshTask.timeline_sequence || !detail.version.is_current
        || !detail.artifacts.some((artifact) => artifact.kind === "video" && artifact.status === "available" && artifact.url)
        || !accountCheck.connected || accountCheck.account_ref !== snapshot.accountRef) {
        setVerifiedAccountRef(null);
        setConfirmOpen(false); setConfirmation(null);
        setNotice(accountCheck.connected && accountCheck.account_ref !== snapshot.accountRef
          ? "当前账号与确认账号不同；未提交发布作业，请手动核验后重新确认。"
          : "确认期间任务版本或账号已变化，未提交发布作业；请重新核对。");
        return;
      }
      if (operationId !== operationRevision.current) return;
      const normalizedTopics = snapshot.topics.split(/[，,\n]/).map((topic) => topic.trim().replace(/^#/, "")).filter(Boolean);
      sendInFlight.current = operationId;
      const job = await createPlatformPublishJob(snapshot.taskId, {
        platform: "douyin", account_ref: snapshot.accountRef,
        timeline_version_id: snapshot.versionId,
        title: snapshot.title, description: snapshot.description, topics: normalizedTopics,
        idempotency_key: crypto.randomUUID(), confirmed: true,
      });
      sendInFlight.current = null;
      latestJobsRequest.current.set(snapshot.taskId, (latestJobsRequest.current.get(snapshot.taskId) ?? 0) + 1);
      setJobsByTask((current) => ({ ...current, [snapshot.taskId]: [job, ...(current[snapshot.taskId] ?? []).filter((item) => item.id !== job.id)] }));
      setJobsErrors(current=>({...current,[snapshot.taskId]:null}));
      setJobsReadAt(current=>({...current,[snapshot.taskId]:new Date().toISOString()}));
      setConfirmOpen(false); setConfirmation(null);
      if (operationId === operationRevision.current) { setCard(null); setView("jobs"); setNotice(`发布作业 ${job.id} 已写入任务“${snapshot.taskName}”的本机队列；实际平台结果以记录回读为准。`); }
    } catch (cause) {
      sendInFlight.current = null;
      if (operationId === operationRevision.current) { setVerifiedAccountRef(null); setConfirmOpen(false); setConfirmation(null); setError(cause instanceof Error ? cause.message : "发布作业未能提交。"); }
    } finally { if (busyOperation.current === operationId) { busyOperation.current = null; setBusy(null); } }
  }

  const selectedJob = jobs.find(job=>job.id===selectedJobId) ?? null;
  const jobFacts=selectedJob?.events.find(event=>event.type==='platform_publish_prepared')?.payload;
  const jobAccount=selectedJob?.events.find(event=>typeof event.payload.account_ref==='string')?.payload.account_ref;
  const jobPlatform=selectedJob?.events.find(event=>typeof event.payload.platform==='string')?.payload.platform;
  const platformNames:Record<string,string>={douyin:"抖音",wechat:"微信视频号",xiaohongshu:"小红书"};
  const activePlatform = platforms.find(platform=>platform.platform===selectedPlatform);
  const activeAccount = accounts.find(account=>account.platform===selectedPlatform);
  const visibleVideos = filtered.filter(task=>task.output_ready);
  const queueReasons:Record<string,string> = {resource_unknown:"本机资源条件当前不可确认，尚未开始提交。",resource_busy:"本机资源正被其他工作占用，发布仍在等待。",owner_unknown:"暂时不能复核任务归属，发布尚未执行。",input_stale:"当前成片版本已变化，请重新检查并确认。"};
  const queueReason = (job:PlatformPublishJob) => queueReasons[job.queue_wait_reason ?? ""] ?? null;
  function closeCard() { closeConfirmation(); setCard(null); }
  function changeView(next:typeof view) { closeCard(); setView(next); setQuery(""); }
  const feedback = <>{error ? <div className={styles.gate} role="alert"><strong>操作未完成</strong><details><summary>诊断信息</summary><p>{error}</p></details><button type="button" className={styles.secondaryButton} onClick={()=>void refreshAll()}>重新读取</button></div> : null}{notice ? <p role="status" className={styles.muted}>{notice}</p> : null}</>;
  const targetSelector = <label className={styles.field}>任务与成片版本<AppSelect aria-label="发布目标（任务与成片版本）" value={selected?.id ?? ""} onValueChange={newValue =>selectTask(newValue || null)}>
    <AppSelectOption value="" disabled>选择创作任务</AppSelectOption>{tasks.map(task=><AppSelectOption key={task.id} value={task.id}>{task.name} · {task.current_timeline_version_id ? `V${task.timeline_sequence}` : "无当前版本"} · {task.output_ready ? "待核对成片" : "未就绪"}</AppSelectOption>)}
  </AppSelect></label>;
  const confirmCard = confirmOpen && selected && confirmation ? <HomeCardDialog kind="confirmation" title="确认抖音发布内容" onClose={closeConfirmation} footer={<><button className={styles.secondaryButton} data-card-cancel onClick={closeConfirmation} type="button">返回修改</button><button className={styles.primaryButton} disabled={busy !== null || !douyinCapability?.browser_publish} onClick={()=>void submitPublish()} type="button">{busy === "publish" ? "正在重新核验…" : "我已核对，创建发布作业"}</button></>}>
    <dl className={styles.properties}><div><dt>平台</dt><dd>抖音</dd></div><div><dt>账号</dt><dd>账号：{confirmation.accountRef}</dd></div><div><dt>成片</dt><dd>{confirmation.taskName} · 当前版本 V{selected.timeline_sequence}</dd></div><div><dt>标题</dt><dd>{confirmation.title}</dd></div><div><dt>描述</dt><dd>{confirmation.description || "（空）"}</dd></div><div><dt>话题</dt><dd>{confirmation.topics || "（无）"}</dd></div></dl>
    <p className={styles.gate}>将视频和文案发送到上述平台账号。提交前再次复核版本与账号；创建本机发布作业后，以平台返回结果判断发布是否完成。</p><details><summary>版本身份</summary>{confirmation.versionId}</details>
  </HomeCardDialog> : null;

  return <AppShell creationTasks={tasks}>
    <div className={styles.page}>
      <div className={styles.toolbar}>
        <nav aria-label="发布视图" className={styles.tabs}>{([['videos','成片'],['jobs','发布记录'],['accounts','账号'],['data','外部数据']] as const).map(([id,label])=><button key={id} type="button" aria-pressed={view===id} onClick={()=>changeView(id)}>{label}</button>)}</nav>
        <button type="button" className={styles.iconButton} title="刷新本机记录" aria-label="刷新" disabled={loading || busy!==null} onClick={()=>void refreshAll()}><RefreshCw size={16}/></button>
        {view==='videos' || view==='jobs' ? <button type="button" className={styles.primaryButton} onClick={()=>setCard("prepare")}>准备发布</button> : null}
      </div>
      {card ? null : feedback}
      {view==='videos' ? <>
        <div className={styles.tools}><label data-input-group className={styles.search}><Search size={16}/><input aria-label="搜索成片任务名称" placeholder="搜索任务名称" value={query} onChange={event=>setQuery(event.target.value)}/>{query ? <button type="button" aria-label="清空搜索" onClick={event=>{setQuery("");const input=event.currentTarget.previousElementSibling;if(input instanceof HTMLInputElement)input.focus();}}><X size={14}/></button> : null}</label><span className={styles.muted}>{loading ? "正在读取…" : `${visibleVideos.length} 项`}</span></div>
        {visibleVideos.length ? <ol className={styles.records} aria-label="成片列表">{visibleVideos.map(task=><li key={task.id}><button type="button" className={styles.objectRow} aria-label={`检查成片 ${task.name}`} aria-pressed={selected?.id===task.id} onClick={()=>{selectTask(task.id);setCard('prepare');}}><Film size={18}/><span className={styles.recordBody}><strong>{task.name}</strong><small>V{task.timeline_sequence} · {currentOutputReady && selected?.id===task.id ? "视频文件可用" : "待核对视频文件"}</small></span><time>{formatTime(task.updated_at)}</time><ArrowRight size={15}/></button></li>)}</ol> : error && !tasks.length ? null : <div className={styles.emptyStage}><Film size={22}/><p>{loading ? "正在读取成片" : query ? "没有匹配的成片任务" : "暂无成片"}</p><Link href="/production">查看创作任务</Link></div>}
        {readAt && visibleVideos.length ? <p className={styles.source}>来源：当前工作区创作任务 · 读取于 {formatTime(readAt)}。视频文件在打开后核对。</p> : null}
      </> : null}
      {view==='jobs' ? <>
        <div className={styles.tools}>{targetSelector}<button className={styles.secondaryButton} type="button" disabled={!selected || busy!==null} onClick={()=>void refreshJobs(selected?.id ?? null)}>刷新结果</button></div>
        {selected && jobsErrors[selected.id] ? <div role="alert" className={styles.gate}>发布记录读取失败，当前结果未确认。<details><summary>诊断信息</summary>{jobsErrors[selected.id]}</details></div> : null}
        {jobs.length ? <ol className={styles.records} aria-label="发布记录列表">{jobs.map(job=><li key={job.id}><button className={styles.objectRow} type="button" onClick={()=>{setSelectedJobId(job.id);setCard('job');}}><Send size={18}/><span className={styles.recordBody}><strong>{statusLabels[job.status]}</strong><small>{selected?.name} · {job.events.length ? formatTime(job.events.at(-1)!.created_at) : "事件时间未知"}</small></span><ArrowRight size={15}/></button></li>)}</ol> : selected && jobsLoading[selected.id] ? <p role="status">正在读取发布记录…</p> : selected && jobsErrors[selected.id] ? null : <p className={styles.emptyStage}>{selected ? "当前任务没有已保存的发布作业。" : "选择任务查看发布记录。"}</p>}
        {selected && jobsReadAt[selected.id] ? <p className={styles.source}>来源：本机持久发布作业 · 读取于 {formatTime(jobsReadAt[selected.id])}。排队状态不代表平台已发布。</p> : null}
      </> : null}
      {view==='accounts' ? <>
        {publishingError ? <div role="alert" className={styles.gate}>账号信息读取失败。<details><summary>诊断信息</summary>{publishingError}</details><button type="button" onClick={()=>void refreshPublishing()}>重新读取账号</button></div> : null}
        <ol className={styles.records} aria-label="发布平台">{platforms.map(platform=>{const account=accounts.find(item=>item.platform===platform.platform);return <li key={platform.platform}><button className={styles.objectRow} type="button" onClick={()=>{invalidatePendingOperation();setSelectedPlatform(platform.platform);setCard('account');}}><UserRound size={18}/><span className={styles.recordBody}><strong>{platform.label}</strong><small>{account ? `${account.account_ref} · 上次核验 ${formatTime(account.verified_at)}` : platform.status==='candidate_unverified' ? "候选接入，未完成真实验收" : "尚未接通"}</small></span><ArrowRight size={15}/></button></li>;})}</ol>
        {!platforms.length && !publishingError ? <p className={styles.emptyStage}>平台信息尚未读取。</p> : null}
      </> : null}
      {view==='data' ? <div className={styles.emptyStage}><p>外部数据尚未接入</p><span>播放、互动和平台统计均未知；暂无平台数据来源与采集时间。</span></div> : null}
      {card==='prepare' ? <HomeCardDialog kind="config" title={selected ? `发布准备 · ${selected.name}` : "发布准备"} onClose={closeCard} footer={<>{selected ? <Link className={styles.secondaryButton} href={`/tasks/${encodeURIComponent(selected.id)}`}>打开任务检查</Link> : <Link href="/production">查看创作任务</Link>}{currentOutputReady && accountReady ? <button className={styles.primaryButton} type="button" disabled={busy!==null || !currentDraft.title.trim() || !douyinCapability?.browser_publish} title={!douyinCapability?.browser_publish ? "当前平台未接通发布" : undefined} onClick={()=>void openPublishConfirmation()}><Send size={14}/>{busy==='preflight' ? "正在复核…" : "检查并确认抖音发布"}</button> : null}</>}>
        {feedback}<div className={styles.publishFlow}>{targetSelector}
          {selected ? <p className={styles.targetSummary}>发布目标：{selected.name} · {versionId ? `V${selected.timeline_sequence}` : "无当前版本"} · {currentOutputReady ? "当前视频产物可用" : "尚未核对可用视频"}</p> : <p>暂无创作任务。</p>}
          {versionLoading ? <p role="status">正在核对成片文件…</p> : null}
          {versionError ? <div role="alert" className={styles.gate}>成片版本核对失败。<details><summary>诊断信息</summary>{versionError}</details><button className={styles.secondaryButton} type="button" onClick={retryVersionDetail}>重新核对</button></div> : null}
          {currentOutputReady && videoArtifact?.url ? <video className={styles.preview} src={videoArtifact.url} controls preload="metadata" aria-label="当前成片预览"/> : null}
          {!selected?.output_ready || !versionId ? <p className={styles.gate}>这个任务还没有当前可发布成片。请打开任务继续制作。</p> : !versionLoading && !versionError && !currentOutputReady ? <p className={styles.gate}>当前版本未提供可核验的视频文件，暂不能作为发布目标；请打开任务检查成片。</p> : null}
          <div><strong>抖音账号</strong><p>{verifiedAccountRef ? `本轮核验：${verifiedAccountRef}` : connectedDouyin ? `上次核验：${connectedDouyin.account_ref}；需读取当前账号。` : "尚无账号核验记录。"}</p></div>
          {publishingError ? <p role="alert">平台信息读取失败，请重新读取账号视图。</p> : douyinCapability && !douyinCapability.browser_connect ? <p className={styles.gate}>抖音账号接入当前不可用，请在账号视图核对接入状态。</p> : null}
          <button className={styles.secondaryButton} type="button" disabled={busy!==null || !platforms.some(item=>item.platform==='douyin' && item.browser_connect)} onClick={()=>void connectDouyin()}>打开专用浏览器</button>
          <button className={styles.secondaryButton} type="button" disabled={busy!==null || !douyinCapability?.browser_connect} onClick={()=>void verifyDouyin()}>{busy==='verify' ? "正在核验…" : "重新核验当前抖音账号"}</button>
          {currentOutputReady && accountReady ? <>
            {!douyinCapability?.browser_publish ? <p className={styles.gate}>当前平台未接通发布。</p> : null}<p className={styles.draftNote}>草稿仅保存在本轮页面内，并绑定 {selected?.name} · V{selected?.timeline_sequence}；切换目标保留各自草稿，刷新页面不会恢复。</p>
            <label className={styles.field}>发布标题<input aria-label="发布标题" value={currentDraft.title} maxLength={30} onChange={event=>updateDraft('title',event.target.value)}/></label>
            <label className={styles.field}>发布描述<textarea aria-label="发布描述" value={currentDraft.description} maxLength={1000} onChange={event=>updateDraft('description',event.target.value)}/></label>
            <label className={styles.field}>话题（逗号或换行分隔）<input aria-label="话题（逗号或换行分隔）" value={currentDraft.topics} onChange={event=>updateDraft('topics',event.target.value)}/></label>
          </> : null}
          <details><summary>发布能力与检查范围</summary><p>{douyinCapability?.requirement ?? "平台能力尚未读取，请在账号视图重试。"}</p><p>登录窗口、账号核验与本地排队均不代表平台已发布。</p><p>封面、尺寸、声音和检查结果请在任务中核对；账号核验与提交时再次检查真实当前成片版本。</p></details>
        </div>{confirmCard}
      </HomeCardDialog> : null}
      {card==='account' && activePlatform ? <HomeCardDialog kind="config" title={activePlatform.label} onClose={closeCard} footer={<span>账号来自成员专用浏览器核验记录</span>}>
        {feedback}<dl className={styles.properties}><div><dt>接入状态</dt><dd>{activePlatform.status==='candidate_unverified' ? "候选接入，未完成真实验收" : "尚未接通"}</dd></div><div><dt>上次账号</dt><dd>{activeAccount?.account_ref ?? "未知"}</dd></div><div><dt>核验时间</dt><dd>{activeAccount ? formatTime(activeAccount.verified_at) : "未知"}</dd></div></dl><p>{activePlatform.requirement}</p>
        {selectedPlatform==='douyin' && activePlatform.browser_connect ? <div className={styles.tools}>
          <button className={styles.secondaryButton} type="button" disabled={busy!==null} onClick={()=>void connectDouyin()}>打开专用浏览器</button>
          <button className={styles.secondaryButton} type="button" disabled={busy!==null} onClick={()=>void verifyDouyin()}>读取当前账号</button>
        </div> : null}
      </HomeCardDialog> : null}
      {card==='job' && selectedJob ? <HomeCardDialog kind="detail" title={statusLabels[selectedJob.status]} onClose={closeCard} footer={<button className={styles.secondaryButton} type="button" onClick={()=>void refreshJobs(selected?.id ?? null)}>刷新结果</button>}>
        <dl className={styles.properties}><div><dt>创作任务</dt><dd>{selected?.name}</dd></div><div><dt>平台</dt><dd>{typeof jobPlatform==='string' ? platformNames[jobPlatform] ?? jobPlatform : "未知"}</dd></div><div><dt>账号</dt><dd>{typeof jobAccount==='string' ? jobAccount : "未知"}</dd></div><div><dt>成片版本</dt><dd>{typeof jobFacts?.timeline_version_id==='string' ? "发布时固定版本" : "未知"}</dd></div></dl>
        {typeof jobFacts?.title==='string' ? <p>发布标题：{jobFacts.title}</p> : null}{queueReason(selectedJob) ? <p>{queueReason(selectedJob)}</p> : null}{selectedJob.impact ? <p>{selectedJob.impact}</p> : null}{selectedJob.repair_action ? <p>处理：{selectedJob.repair_action}</p> : null}
        {typeof selectedJob.events.find(event=>event.type==='platform_publish_succeeded')?.payload.remote_publish_id==='string' ? <p>平台返回编号：{String(selectedJob.events.find(event=>event.type==='platform_publish_succeeded')?.payload.remote_publish_id)}</p> : null}
        <p>来源：本机发布作业事件 · {selectedJob.events.length ? formatTime(selectedJob.events.at(-1)!.created_at) : "事件时间未知"}</p>
        <details><summary>诊断与事件</summary><p>作业：{selectedJob.id}</p><p>队列：{selectedJob.queue_state}</p>{selectedJob.queue_wait_reason ? <p>等待原因：{selectedJob.queue_wait_reason}</p> : null}{typeof jobFacts?.timeline_version_id==='string' ? <p>成片版本身份：{jobFacts.timeline_version_id}</p> : null}{selectedJob.failure_reason ? <p>{selectedJob.failure_reason}</p> : null}<ol>{selectedJob.events.map((event,index)=><li key={index}>{event.type} · {formatTime(event.created_at)}</li>)}</ol></details>
      </HomeCardDialog> : null}
    </div>
  </AppShell>;
}
