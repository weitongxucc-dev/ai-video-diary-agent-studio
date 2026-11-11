"use client";
import { WorkspaceAudio, WorkspaceVideo } from "../workbench/WorkspaceMedia";
import { useWorkspacePage } from "../workbench/context";
import type { TaskMediaJob, TaskMediaJobPage, TaskMediaResultMap } from "@/lib/types";
import NextImage from "next/image";
import { creationTaskFailureReason, creationTaskRepairAction } from "@/lib/creation-task-status";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { HomeCardDialog } from "@/components/HomeCardDialog";
import { apiUrl, listCreationTaskMediaJobs, taskMediaJobMatchesContext, readCreationTaskGraphicSource, cancelCreationTaskMediaJob, taskMediaJobLabel, getTaskJobProgress } from "@/lib/api";
import type { CreationTask, TaskJobProgress } from "@/lib/types";
import styles from "../CreationTaskWorkspace.module.css";
import { errorDetails } from './request-errors';
import { formatDuration, jobStatusLabel, jobKindLabel } from './execution-labels';
import { Button } from '../ui/button';
import { IconButton } from '../ui/icon-button';
import { Activity } from 'lucide-react';
import { createPortal } from 'react-dom';
import { useActiveEditorDock } from '../workbench/EditorDock';
import { readCreationTaskNativeOperations, readCreationTaskNativeArtifact } from '@/lib/api/task-media';
import type { CreationTaskNativeArtifact, CreationTaskNativeOperations } from '@/lib/types/creation-tasks';

export function NativeOperationsPanel({taskId,adoptedRefs,onPreviewImage,canPrepare}:{taskId:string;adoptedRefs:string[];
    onPreviewImage:(artifact:CreationTaskNativeArtifact)=>void;canPrepare:boolean}) {
    const viewActive=useWorkspacePage()?.active ?? true;
    const [historyOpen,setHistoryOpen]=useState(false);
    const [page,setPage]=useState<CreationTaskNativeOperations|null>(null),[offset,setOffset]=useState(0);
    const [error,setError]=useState<string|null>(null),[retry,setRetry]=useState(0);
    const [selected,setSelected]=useState<CreationTaskNativeArtifact|null>(null),[url,setUrl]=useState<string|null>(null);
    const [previewError,setPreviewError]=useState<string|null>(null);
    useEffect(()=>{
        if (!viewActive) return;
        const controller=new AbortController();let reading=false;
        async function read() {
            if (reading) return;reading=true;
            try {
                const value=await readCreationTaskNativeOperations(taskId,offset,controller.signal);
                if (!controller.signal.aborted) {
                    if (value.task_id!==taskId) throw new Error('工作产物回执不属于当前作品');
                    setPage(value);setError(null);
                }
            } catch(cause) {if (!controller.signal.aborted) setError(cause instanceof Error?cause.message:'工作产物状态读取失败');}
            finally {reading=false;}
        }
        void read();const timer=setInterval(()=>void read(),5000);
        return ()=>{controller.abort();clearInterval(timer);};
    },[taskId,offset,retry,viewActive]);
    useEffect(()=>{
        setUrl(null);setPreviewError(null);
        if (!selected || !viewActive) return;
        const controller=new AbortController();let objectUrl:string|null=null;
        void readCreationTaskNativeArtifact(taskId,selected.artifact_id,controller.signal).then(blob=>{
            if (controller.signal.aborted) return;
            objectUrl=URL.createObjectURL(blob);setUrl(objectUrl);
        }).catch(cause=>{if (!controller.signal.aborted) setPreviewError(cause instanceof Error?cause.message:'预览读取失败');});
        return ()=>{controller.abort();if (objectUrl) URL.revokeObjectURL(objectUrl);};
    },[taskId,selected,viewActive]);
    const current=page?.task_id===taskId?page:null;
    if (!error && (!current || current.total_count===0)) return null;
    const statuses:Record<string,string>={queued:'等待执行',waiting:'等待资源',running:'正在执行',succeeded:'执行成功',failed:'执行失败',cancel_requested:'正在停止',cancelled:'已取消',interrupted:'已中断'};
    const processStates:Record<string,string>={not_started:'未启动',running:'运行中',exited:'已退出',unknown:'待核对'};
    const releaseStates:Record<string,string>={not_acquired:'未占用',released:'已释放',unknown:'待核对'};
    return <section aria-label="任务执行与工作产物">
        {error?<p role="alert">{error}<Button variant="outline" onClick={()=>setRetry(value=>value+1)}>重新读取产物</Button></p>:null}
        <Button variant="ghost" aria-haspopup="dialog" aria-expanded={historyOpen} onClick={()=>setHistoryOpen(true)}>工作产物与执行记录{current ? ` · ${current.total_count} 条` : ""}{current?.items.some(item=>['failed','interrupted'].includes(item.status)) ? " · 记录含失败或中断" : ""}</Button>
        <HomeCardDialog kind="detail" title="工作产物与执行记录" open={historyOpen} onClose={()=>setHistoryOpen(false)}>
        <div className={styles.sectionHeading}><span>工作产物候选</span><Button variant="outline" onClick={()=>setRetry(value=>value+1)}>刷新产物</Button></div>
        {selected?<div className={styles.segmentVideoPreview}>
            {url && selected.media_type.startsWith('image/')?<NextImage unoptimized width={640} height={360} style={{width:'100%',height:'auto',objectFit:'contain'}} src={url} alt="实际登记的工作产物候选"/>:null}
            {url && selected.media_type.startsWith('video/')?<WorkspaceVideo controls preload="metadata" src={url} aria-label="实际登记的视频候选"/>:null}
            {url && selected.media_type.startsWith('audio/')?<WorkspaceAudio controls preload="metadata" src={url} aria-label="实际登记的音频候选"/>:null}
            {!url && !previewError?<p role="status">正在读取原产物…</p>:null}{previewError?<p role="alert">{previewError}</p>:null}
            <p>{adoptedRefs.includes(selected.source_ref)?'已在当前时间线使用；最终合成状态另行核对':'当前是登记候选，尚未采用'}</p>
        </div>:null}
        <div className={styles.segmentProductionList}>{current?.items.map(item=><article key={item.job_id}>
            <strong>{item.operation==='register_task_artifacts'?'产物登记':item.operation==='native_task_file'?'工作文件':'程序执行'} · {statuses[item.status]??item.status}</strong>
            <p>进程：{processStates[item.process_state]??item.process_state} · 资源：{releaseStates[item.release_state]??item.release_state}</p>
            {item.failure_reason?<p>失败原因：{item.failure_reason}</p>:null}
            {item.waiting_reason?<p>等待原因：{item.waiting_reason}</p>:null}
            {item.repair_action?<p>恢复方式：{item.repair_action}</p>:null}
            {item.artifacts.map(artifact=><div key={artifact.artifact_id}>
                <p>{artifact.role} · {artifact.media_type} · {(artifact.size_bytes/1024).toFixed(1)} KB</p>
                {/^(image|video|audio)\//.test(artifact.media_type)?<Button variant="outline" onClick={()=>setSelected(artifact)}>预览候选</Button>:null}
                {artifact.media_type.startsWith('image/')?<Button variant="outline" disabled={!canPrepare} title={canPrepare?'准备原作品中的图片图层':'当前作品时间线尚未准备完成'} onClick={()=>onPreviewImage(artifact)}>准备图片图层</Button>:null}
                {adoptedRefs.includes(artifact.source_ref)?<span>已在当前时间线使用</span>:<span>登记候选</span>}
            </div>)}
        </article>)}</div>
        {current && (current.offset>0 || current.truncated)?<div><p>当前显示 {current.offset+1}–{current.offset+current.items.length} / {current.total_count} 条</p><Button variant="outline" disabled={offset===0} onClick={()=>setOffset(Math.max(0,offset-30))}>上一页</Button><Button variant="outline" disabled={!current.truncated} onClick={()=>setOffset(offset+30)}>下一页</Button></div>:null}
        </HomeCardDialog>
    </section>;
}
/**
 * 作业进度读后台已经落库的作业行：重开应用读到同一批记录，页面关闭不改变它们。
 * 作业还在排队或执行时按固定间隔重新读取，结束后停止；切换任务时丢弃旧结果。
 * 读不到就明说并给出重试，不静默当成没有作业。
 */
export function JobProgressPanel({ graphicEditor, task, taskId, refreshToken, pendingEditJobId, onEditSettled, showLiveStatus, mediaJob, onCancelMedia, selectedRefs, restoreBusy, onRestoreMedia, recoveredMedia }: {
    selectedRefs: string[];
    restoreBusy: boolean;
    onRestoreMedia: (job: TaskMediaJob) => void;
    recoveredMedia: {
        job: TaskMediaJob;
        result: TaskMediaResultMap[keyof TaskMediaResultMap];
    } | null;
    graphicEditor: ReactNode;
    mediaJob: TaskMediaJob | null;
    onCancelMedia: () => void;
    showLiveStatus: boolean;
    task: CreationTask;
    taskId: string;
    refreshToken: string;
    pendingEditJobId: string | null;
    onEditSettled: (jobId: string, status: string) => Promise<void>;
}) {
    const viewActive = useWorkspacePage()?.active ?? true;
    const toolsHost = useActiveEditorDock("tools");
    const settledCallback = useRef(onEditSettled);
    settledCallback.current = onEditSettled;
    const [progress, setProgress] = useState<TaskJobProgress | null>(null);
    const [readError, setReadError] = useState<string | null>(null);
    const [retryTick, setRetryTick] = useState(0);
    const [detailsOpen, setDetailsOpen] = useState(false);
    const [mediaPage, setMediaPage] = useState<TaskMediaJobPage | null>(null);
    const [mediaReadError, setMediaReadError] = useState<string | null>(null);
    const [mediaCursor, setMediaCursor] = useState<string | null>(null);
    const [mediaRetry, setMediaRetry] = useState(0);
    const [mediaLoading, setMediaLoading] = useState(false);
    useEffect(() => {
        if (!viewActive || !detailsOpen)
            return;
        const controller = new AbortController();
        setMediaLoading(true);
        setMediaReadError(null);
        void listCreationTaskMediaJobs(taskId, mediaCursor, controller.signal).then(page => {
            if (!controller.signal.aborted) {
                setMediaPage(page);
                setMediaLoading(false);
            }
        }).catch(cause => {
            if (!controller.signal.aborted) {
                setMediaReadError(errorDetails(cause).message);
                setMediaLoading(false);
            }
        });
        return () => controller.abort();
    }, [viewActive, taskId, detailsOpen, mediaCursor, mediaRetry]);
    useEffect(() => {
        if (!viewActive) return;
        let active = true;
        let timer: number | undefined;
        setReadError(null);
        async function read() {
            window.clearTimeout(timer);
            try {
                const result = await getTaskJobProgress(taskId);
                if (!active || result.task_id !== taskId)
                    return;
                setProgress(result);
                setReadError(null);
                const edit = result.items.find(item => item.id === pendingEditJobId);
                if (edit && ["succeeded", "failed", "cancelled"].includes(edit.status)) {
                    await settledCallback.current(edit.id, edit.status);
                    if (!active)
                        return;
                }
                // 只有还在排队或执行的作业才继续读；结束或失败后停止轮询。
                const status = result.current?.status;
                if (status === "queued" || status === "running" || status === "interrupted" || edit?.status === "queued" || edit?.status === "running" || edit?.status === "interrupted") {
                    timer = window.setTimeout(() => void read(), 2500);
                }
            }
            catch (cause) {
                if (!active)
                    return;
                setReadError(errorDetails(cause).message);
            }
        }
        void read();
        return () => { active = false; window.clearTimeout(timer); };
        // 开始/重试制作后作业事实会变，重新读一次；空态不会一直轮询。
    }, [viewActive, taskId, refreshToken, retryTick, pendingEditJobId]);
    async function cancelCurrentMedia() {
        if (!progress?.current)
            return;
        try {
            await cancelCreationTaskMediaJob(taskId, progress.current.id);
            setRetryTick(value => value + 1);
        }
        catch (cause) {
            setReadError(errorDetails(cause).message);
        }
    }
    const recoveryCandidates = mediaPage?.items.filter(item => taskMediaJobMatchesContext(item, task.current_timeline_version_id, selectedRefs)) ?? [];
    const current = progress?.current ?? null;
    const live = current?.status === "running" || current?.status === "queued";
    return <section aria-label="作业进度" className={styles.jobProgress}>
    {mediaJob && mediaJob.status !== "succeeded" ? <span role="status">{taskMediaJobLabel(mediaJob)}{["queued", "running", "interrupted"].includes(mediaJob.status) ? <button type="button" onClick={onCancelMedia}>取消预览</button> : null}</span> : null}
    {showLiveStatus && live ? <span>{jobKindLabel(current.kind)} · {jobStatusLabel(current.status)} · 进度 {current.progress}%</span> : showLiveStatus && (current?.status === "failed" || current?.status === "interrupted") ? <span>后台有步骤需要处理</span> : null}
    {readError ? <span role="status">后台记录更新未完成</span> : null}
    {toolsHost && viewActive ? createPortal(<IconButton label="运行详情" description="查看后台作业、预览与原请求" aria-haspopup="dialog" aria-expanded={detailsOpen} onClick={() => setDetailsOpen(true)}><Activity/></IconButton>, toolsHost) : <button type="button" aria-haspopup="dialog" aria-expanded={detailsOpen} onClick={() => setDetailsOpen(true)}>运行详情</button>}
    <HomeCardDialog kind="detail" title="运行详情" open={detailsOpen} onClose={() => setDetailsOpen(false)}>
      {readError ? <p role="alert" className={styles.jobRefreshError}>最新一次刷新失败：{readError} <button onClick={() => setRetryTick(value => value + 1)} type="button">重试</button></p> : null}
      <section aria-label="可恢复的媒体预览">
        <h3>恢复媒体预览</h3>
        {mediaLoading ? <p role="status">正在读取预览记录…</p> : null}
        {mediaReadError ? <p role="alert">预览记录未读取：{mediaReadError}<button type="button" onClick={() => setMediaRetry(value => value + 1)}>重读预览记录</button></p> : null}
        {!mediaLoading && !mediaReadError && mediaPage ? <>
          {recoveryCandidates.length ? <ol>{recoveryCandidates.map(item => <li key={item.job_id}>
            <span>{({ edit_preview: "修改预览", audio_listen: "音频试听", waveform: "音频波形", recording: "本机录音", audio_source: "导入音频", graphic_source: "图片来源", observation: "输出检查" })[item.result_kind]} · <time dateTime={item.created_at}>{new Date(item.created_at).toLocaleString("zh-CN")}</time> · {taskMediaJobLabel(mediaJob?.job_id === item.job_id ? mediaJob : item)}</span>
            <button type="button" disabled={restoreBusy} onClick={() => onRestoreMedia(item)}>恢复这次预览</button>
          </li>)}</ol> : <p>这一页没有符合当前版本与选择的预览记录。</p>}
          {mediaPage.next_cursor ? <button type="button" onClick={() => setMediaCursor(mediaPage.next_cursor)}>查看更多预览记录</button> : null}
          {mediaCursor ? <button type="button" onClick={() => setMediaCursor(null)}>回到最新预览记录</button> : null}
        </> : null}
        {recoveredMedia ? <RecoveredMediaResult taskId={taskId} graphicEditor={graphicEditor} job={recoveredMedia.job} result={recoveredMedia.result}/> : null}
      </section>
      {mediaJob ? <p>{taskMediaJobLabel(mediaJob)}{mediaJob.repair_action ? ` · ${mediaJob.repair_action}` : ""}</p> : null}
      {current ? <p>{jobKindLabel(current.kind)} · {jobStatusLabel(current.status)} · 进度 {current.progress}% · 已耗时 {formatDuration(current.elapsed_seconds)} · 更新于 {new Date(current.updated_at).toLocaleString("zh-CN")}</p> : <p>{progress ? "这个任务还没有后台作业记录。" : "正在读取后台记录…"}</p>}
      {live && current.kind === "creation_task_media" && current.id !== mediaJob?.job_id ? <button type="button" onClick={() => void cancelCurrentMedia()}>取消媒体处理</button> : null}
      {live ? <p>预计等待：{formatDuration(current.wait_estimate_seconds)} · 预计处理：{formatDuration(current.processing_estimate_seconds)}{current.estimate_basis ? `（依据：${current.estimate_basis}）` : current.wait_estimate_seconds === null && current.processing_estimate_seconds === null ? "（尚无可信样本，无法预计）" : ""}</p> : null}
      {current && (current.status === "failed" || current.status === "interrupted" || current.status === "cancelled" && Boolean(current.failure_reason || current.repair_action)) ? <div role="alert" className={styles.jobFailure} aria-label="当前作业失败信息"><strong>{current.status === "failed" ? "当前作业失败" : current.status === "interrupted" ? "当前作业已中断" : "当前作业未完成"}</strong>{current.failure_reason ? <p>失败原因：{creationTaskFailureReason(current.failure_reason, current.kind)}{current.impact ? ` · ${current.impact}` : ""}</p> : null}{current.repair_action ? <p>下一步：{creationTaskRepairAction(current.repair_action)}</p> : null}{current.failure_reason ? <details><summary>后台原始记录</summary><p>{current.failure_reason}</p></details> : null}</div> : null}
      {progress && progress.items.length > 1 ? <details><summary>查看历史作业（{progress.items.length} 条）</summary><ol>{progress.items.map(item => <li key={item.id}>{jobKindLabel(item.kind)} · {jobStatusLabel(item.status)} · 进度 {item.progress}% · 已耗时 {formatDuration(item.elapsed_seconds)} · 更新于 {new Date(item.updated_at).toLocaleString("zh-CN")}{item.failure_reason ? ` · 失败原因：${item.failure_reason}${item.repair_action ? ` · 下一步：${item.repair_action}` : ""}` : ""}</li>)}</ol></details> : null}
      <details className={styles.traceDetails}><summary>任务版本与时间</summary><p>版本 {task.timeline_sequence}</p><p>最近更新：<time dateTime={task.updated_at}>{new Date(task.updated_at).toLocaleString("zh-CN")}</time></p><p>恢复记录：{task.recovery_point}</p></details>
    </HomeCardDialog>
  </section>;
}
export function RecoveredMediaResult({ taskId, graphicEditor, job, result }: {
    taskId: string;
    graphicEditor: ReactNode;
    job: TaskMediaJob;
    result: TaskMediaResultMap[keyof TaskMediaResultMap];
}) {
    if (job.result_kind === "graphic_source")
        return <RecoveredGraphicSource key={`${taskId}:${(result as TaskMediaResultMap["graphic_source"]).artifact_id}`} taskId={taskId} source={result as TaskMediaResultMap["graphic_source"]} graphicEditor={graphicEditor}/>;
    if (job.result_kind === "audio_listen")
        return <WorkspaceAudio aria-label="恢复的音频试听" controls preload="metadata" src={apiUrl((result as TaskMediaResultMap["audio_listen"]).audio_url)}/>;
    if (job.result_kind === "audio_source") {
        const source = result as TaskMediaResultMap["audio_source"];
        return <p role="status">已读取音频：{source.name} · {(source.duration_ms / 1000).toFixed(1)} 秒；尚未添加到音轨。</p>;
    }
    if (job.result_kind === "recording")
        return <p role="status">录音结果已读取；请在已有录音列表查看，尚未采用。</p>;
    if (job.result_kind === "waveform") {
        const wave = result as TaskMediaResultMap["waveform"];
        const stride = Math.max(1, Math.ceil(wave.peaks.length / 240));
        const points: string[] = [];
        for (let i = 0; i < wave.peaks.length; i += stride) {
            const peak = Math.max(...wave.peaks.slice(i, i + stride));
            const x = i / Math.max(1, wave.peaks.length) * 240;
            points.push(`M${x},${16 - peak * 15}V${16 + peak * 15}`);
        }
        return <div aria-label="恢复的音频波形"><svg viewBox="0 0 240 32" role="img" aria-label="实际音频波形"><path d={points.join(" ")} stroke="currentColor"/></svg><p>每个采样点覆盖 {wave.seconds_per_peak} 秒。</p></div>;
    }
    return null;
}
export function RecoveredGraphicSource({ taskId, source, graphicEditor }: {
    taskId: string;
    source: TaskMediaResultMap["graphic_source"];
    graphicEditor: ReactNode;
}) {
    const [url, setUrl] = useState<string | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [preparing, setPreparing] = useState(false);
    useEffect(() => {
        const controller = new AbortController();
        let active = true;
        let objectUrl: string | null = null;
        void readCreationTaskGraphicSource(taskId, source.artifact_id, controller.signal).then(blob => {
            if (!active)
                return;
            objectUrl = URL.createObjectURL(blob);
            setUrl(objectUrl);
        }).catch(cause => { if (active)
            setError(cause instanceof Error ? cause.message : "图片来源未读取。"); });
        return () => { active = false; controller.abort(); if (objectUrl)
            URL.revokeObjectURL(objectUrl); };
    }, [taskId, source.artifact_id]);
    return <section aria-label="恢复的图片来源">
    <p>{source.name} · {source.width}×{source.height}；尚未应用到草稿或生成成片。</p>
    {url ? <NextImage src={url} alt="实际登记的图片来源" width={source.width} height={source.height} unoptimized/> : error ? <p role="alert">{error}</p> : <p role="status">正在读取图片来源…</p>}
    <button type="button" disabled={!url} onClick={() => setPreparing(true)}>准备图片图层</button>
    {preparing ? graphicEditor : null}
  </section>;
}
