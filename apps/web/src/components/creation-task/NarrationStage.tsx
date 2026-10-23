"use client";
import { WorkspaceVideo, WorkspaceAudio } from "../workbench/WorkspaceMedia";
import { LoaderCircle, UserRound } from "lucide-react";
import { apiUrl } from "@/lib/api";
import type { CreationTaskPersonRender, CreationTaskWorkspace as WorkspaceState } from "@/lib/types";
import styles from "../CreationTaskWorkspace.module.css";
import { narrationSegmentStatus, segmentRevisionStatus, segmentAdoptionReason } from './stage-labels';
import { useCallback, useEffect, useRef, useState } from 'react';
import { Button } from '../ui/button';
import { readCreationTaskSegments, readCreationTaskBudget, readCreationTaskSegmentRevision, resumeCreationTaskSegmentRevision } from '@/lib/api/task-media';
import type { CreationTaskSegments, CreationTaskSegment, CreationTaskBudget, CreationTaskSegmentRevision } from '@/lib/types/creation-tasks';

export function SegmentProductionPanel({ taskId }: { taskId: string }) {
    const [segments,setSegments]=useState<CreationTaskSegments|null>(null);
    const [budget,setBudget]=useState<CreationTaskBudget|null>(null);
    const [preview,setPreview]=useState<CreationTaskSegment|null>(null);
    const [error,setError]=useState<string|null>(null);
    const [budgetError,setBudgetError]=useState<string|null>(null);
    const [reading,setReading]=useState(false);
    const [revision,setRevision]=useState(0);
    const [offset,setOffset]=useState(0);
    const [receipt,setReceipt]=useState<CreationTaskSegmentRevision|null>(null);
    const [readingReceipt,setReadingReceipt]=useState(false);
    const [receiptUnknown,setReceiptUnknown]=useState(false);
    const receiptScope=useRef({taskId,sequence:0});
    if (receiptScope.current.taskId!==taskId) receiptScope.current={taskId,sequence:0};
    useEffect(()=>{const scope=receiptScope.current;setReadingReceipt(false);setReceiptUnknown(false);
        return()=>{scope.sequence++;};},[taskId]);
    async function inspectReceipt(requestId:string, recover=false) {
        const scope=receiptScope.current,sequence=++scope.sequence;
        const owns=()=>receiptScope.current===scope && scope.sequence===sequence;
        setReadingReceipt(true);
        try {
            let value=await readCreationTaskSegmentRevision(taskId,requestId);
            if (value.task_id!==taskId || value.request_id!==requestId) throw new Error('修改回执身份不一致');
            if (!owns()) return;
            if (recover && value.recovery?.action==='resume_original') {
                const originalJob=value.job_id;
                value=await resumeCreationTaskSegmentRevision(taskId,requestId);
                if (value.task_id!==taskId || value.request_id!==requestId || value.job_id!==originalJob)
                    throw new Error('恢复回执未保留原请求和作业，请先查询原结果');
            }
            if (owns()) {setReceipt(value);setReceiptUnknown(false);setError(null);refresh();}
        } catch(cause) {if (owns()) {setReceiptUnknown(true);setError(cause instanceof Error?cause.message:'修改回执读取失败');}}
        finally {if (owns()) setReadingReceipt(false);}
    }
    const refresh=useCallback(()=>setRevision(value=>value+1),[]);
    useEffect(()=>{
        let active=true,readingNow=false;
        async function read() {
            if (readingNow) return;
            readingNow=true; setReading(true);
            const [media,usage]=await Promise.allSettled([readCreationTaskSegments(taskId,offset),readCreationTaskBudget(taskId)]);
            if (!active) return;
            if (media.status==='fulfilled' && media.value.task_id===taskId) {setSegments(media.value);setError(null);}
            else setError(media.status==='rejected' && media.reason instanceof Error?media.reason.message:'段落回执身份不一致');
            if (usage.status==='fulfilled' && usage.value.task_id===taskId) {setBudget(usage.value);setBudgetError(null);}
            else setBudgetError('用量回执暂不可读取');
            readingNow=false;setReading(false);
        }
        void read();
        const timer=setInterval(()=>void read(),5000);
        return ()=>{active=false;clearInterval(timer);};
    },[taskId,revision,offset]);
    const selected=preview?.task_id===taskId?preview:null;
    const current=segments?.task_id===taskId?segments:null;
    const currentSelected=current?.segments.find(item=>item.section_id===selected?.section_id);
    const newer=Boolean(selected && currentSelected?.video && currentSelected.segment_version!==selected.segment_version);
    const usage=budget?.task_id===taskId?budget:null;
    const paid=usage?.actual_cost;
    const actualKnown=paid?.amount!==null && paid?.amount!==undefined && paid.supplier_usage_refs.length>0 && paid.pricing_or_invoice_refs.length>0;
    const statuses:Record<string,string>={queued:'等待执行',running:'正在制作',succeeded:'制作完成',failed:'制作失败',cancelled:'已取消',interrupted:'已中断',blocked:'等待恢复'};
    return <section className={styles.workSection} aria-label="分段预览与修改">
        <div className={styles.sectionHeading}><div><h2>分段预览</h2><p>{current?`${current.available_video_count} / ${current.segments.length} 段有声视频可播放${current.total_count>current.segments.length?`（本页，共 ${current.total_count} 段）`:""}`:'正在读取段落产物'}</p></div><Button variant="outline" onClick={refresh} disabled={reading}>刷新状态</Button></div>
        {error?<p role="alert">{error}</p>:null}
        {selected?.video?<div className={styles.segmentVideoPreview}>
            <WorkspaceVideo key={selected.video.url} controls preload="metadata" src={apiUrl(selected.video.url)} aria-label={`第 ${selected.ordinal} 段有声预览`}/>
            <p>第 {selected.ordinal} 段 · {selected.video.duration_seconds.toFixed(1)} 秒 · {currentSelected?.adopted && currentSelected.segment_version===selected.segment_version?'当前采用的段版本':'历史或候选段版本'}</p>
            {newer && currentSelected?<div role="status"><p>本段已有新版本，当前预览保持原版本。</p><Button variant="outline" onClick={()=>setPreview(currentSelected)}>查看新版本</Button></div>:null}
        </div>:null}
        <div className={styles.segmentProductionList}>
            {current?.segments.map(segment=><article key={segment.section_id}>
                <div><strong>第 {segment.ordinal} 段 · {segment.title}</strong><span>{segment.video?(segment.adopted?'段视频已采用':'历史或候选段视频'):segment.audio?.status==='failed'?'配音失败':segment.audio?.status==='generating'?'正在配音':'等待段产物'}</span></div>
                <p>{segment.text}</p>
                {segment.video?<Button variant="outline" aria-pressed={selected?.section_id===segment.section_id && selected.segment_version===segment.segment_version} onClick={()=>setPreview(segment)}>播放本段</Button>:null}
                {!segment.video && segment.historical_video?<Button variant="outline" onClick={()=>setPreview({...segment,video:segment.historical_video!})}>查看历史段视频</Button>:null}
                {current.revision_requests.items.filter(request=>request.section_id===segment.section_id).map(request=><div className={styles.segmentRevisionReceipt} key={request.request_id}>
                    <strong>本段修改 · {statuses[request.status]??request.status}</strong>
                    {request.waiting_reason?<p>{request.waiting_reason}</p>:null}
                    <Button variant="outline" disabled={readingReceipt} onClick={()=>void inspectReceipt(request.request_id)}>查看修改回执</Button>
                    <small>请求 {request.request_id} · 作业 {request.job_id}</small>
                </div>)}
            </article>)}
        </div>
        {current && (current.offset>0 || current.truncated)?<div><Button variant="outline" disabled={reading || offset===0} onClick={()=>setOffset(Math.max(0,offset-30))}>上一页</Button><Button variant="outline" disabled={reading || !current.truncated} onClick={()=>setOffset(offset+30)}>下一页</Button></div>:null}
        {current?.revision_requests.truncated?<p>当前显示 {current.revision_requests.items.length} / {current.revision_requests.total_count} 条修改回执。</p>:null}
        {receipt?.task_id===taskId?<div className={styles.segmentRevisionReceipt} aria-label="修改回执详情"><strong>{segmentRevisionStatus(receipt)}</strong><p>规则检查：{receipt.rules_check.status==='passed'?'通过':'未通过'} · Pi 语义对照已记录</p><small>语义复核记录 {receipt.semantic_review_ref}</small>{receipt.waiting_reason?<p>{receipt.waiting_reason}</p>:null}{receipt.adoption?.reason?<p>{segmentAdoptionReason(receipt.adoption.reason)}</p>:null}
            {receiptUnknown?<p role="status">原结果尚未核实，请先查询修改回执。</p>:null}
            <Button variant="outline" disabled={readingReceipt} onClick={()=>void inspectReceipt(receipt.request_id)}>查询原修改结果</Button>
            {receipt.recovery?.action==='resume_original' && !receiptUnknown?<Button variant="outline" disabled={readingReceipt} onClick={()=>void inspectReceipt(receipt.request_id,true)}>恢复原段修改</Button>:null}
        </div>:null}
        <details className={styles.segmentUsage}><summary>费用与资源记录</summary>
            <p>实际费用：{actualKnown?`${paid!.amount} ${paid!.currency??''}`:'待核对供应商用量或账单'}</p>
            <p>费用估算：{typeof usage?.estimates.cost==='number'?usage.estimates.cost:'暂无估算'} · 完成时间估算：{typeof usage?.estimates.eta_seconds==='number'?`${usage.estimates.eta_seconds} 秒`:'暂无估算'}</p>
            {usage?<><p>已记录执行 {usage.resources.settled_execution_seconds.toFixed(1)} 秒 · 生成 {(usage.resources.settled_generated_bytes/1048576).toFixed(1)} MB</p><p>{usage.resources.coverage==='complete'?'原执行用量记录齐全':'用量记录尚待核对，当前数字为已记录部分'}</p>{usage.status==='unit_budget_blocked'?<p role="status">执行因系统资源保护暂停。请查看制作状态并按原回执恢复。</p>:null}</>:<p>资源消耗：待核对</p>}
            {budgetError?<p role="status">{budgetError}</p>:null}
        </details>
    </section>;
}
export function PersonRenderPanel({ busy, onGenerate, selectedRender, selectedSegmentId, hasPerson }: {
    busy: boolean;
    onGenerate: (segmentId: string) => void;
    selectedRender: CreationTaskPersonRender | null;
    selectedSegmentId: string | null;
    hasPerson: boolean;
}) {
    return (<div className={styles.personRenderPanel}>
      <div>
        <UserRound size={18}/>
        <div><strong>人物口型预览</strong><p>选择一段配音，用当前人物版本检查口型效果；确认编辑后才会进入成片时间线。</p></div>
      </div>
      {!hasPerson ? <span role="status">当前方案没有选择人物版本；请先在制作方案中选择人物。</span> : null}
      {!selectedSegmentId ? <span>先选择上方一段配音</span> : null}
      {selectedSegmentId && !selectedRender && hasPerson ? <button className="modern-primary-button" disabled={busy} onClick={() => onGenerate(selectedSegmentId)} type="button">生成人物口型预览</button> : null}
      {selectedRender?.status === "running" || selectedRender?.status === "pending" ? <span><LoaderCircle className="is-spinning" size={15}/>正在生成人物口型预览</span> : null}
      {selectedRender?.status === "failed" ? <div className={styles.personRenderFailure}><strong>生成没有完成</strong><p>{selectedRender.impact ?? selectedRender.failure_reason}</p>{selectedRender.repair_action ? <small>{selectedRender.repair_action}</small> : null}<button className="modern-primary-button" disabled={busy} onClick={() => onGenerate(selectedSegmentId!)} type="button">重新生成</button></div> : null}
      {selectedRender?.status === "ready" && selectedRender.media_url ? <div className={styles.personRenderPreview}><WorkspaceVideo controls preload="metadata" src={apiUrl(selectedRender.media_url)}/><p>{selectedRender.model_id} · {selectedRender.duration_seconds?.toFixed(1)} 秒 · 人物 {selectedRender.person_version}</p></div> : null}
    </div>);
}
export function NarrationResultView({ busy, onGeneratePersonRender, onSelect, selectedSegmentId, hasPerson, taskId, narration, personRenders }: {
    busy: boolean;
    onGeneratePersonRender: (segmentId: string) => void;
    taskId: string;
    narration: WorkspaceState["narration"];
    personRenders: WorkspaceState["person_renders"];
    onSelect: (segmentId: string) => void;
    selectedSegmentId: string | null;
    hasPerson: boolean;
}) {
    if (!narration)
        return null;
    const ready = narration.segments.length > 0 && narration.segments.every(segment => segment.status === "ready");
    const selectedRender = personRenders.find((render) => render.narration_segment_id === selectedSegmentId) ?? null;
    return (<section className={styles.workSection}>
      <div className={styles.sectionHeading}><div><h2>试听当前声音来源</h2><p>{narration.model_id ?? "本机录音"} · {narration.duration_seconds.toFixed(1)} 秒{narration.sample_rate ? ` · ${narration.sample_rate} Hz` : ""}</p></div><span className={styles.artifactStatus}>{ready ? "已采用" : narration.segments.some(segment => segment.status === "failed") ? "有段落未完成" : "等待配音完成"}</span></div>
      {ready ? <WorkspaceAudio className={styles.audioPreview} controls preload="metadata" src={apiUrl(`/api/creation-tasks/${taskId}/narration/audio`)}/> : null}
      <div className={styles.narrationSegments}>
        {narration.segments.map((segment, index) => (<article className={selectedSegmentId === segment.id ? styles.selectedSegment : ""} key={segment.id}>
            <button aria-label={`选择配音 ${index + 1}`} aria-pressed={selectedSegmentId === segment.id} disabled={busy} onClick={() => onSelect(segment.id)} type="button">
              <span>第 {index + 1} 段</span>
              <strong>{segment.text}</strong>
              <small>{segment.duration_seconds !== null ? `${segment.duration_seconds.toFixed(1)} 秒 · ` : ""}{narrationSegmentStatus(segment.status)}</small>
            </button>
            {segment.audio_url ? <WorkspaceAudio aria-label={`试听配音 ${index + 1}`} controls preload="metadata" src={apiUrl(segment.audio_url)}/> : null}
          </article>))}
      </div>
      <PersonRenderPanel busy={busy} onGenerate={onGeneratePersonRender} selectedRender={selectedRender} selectedSegmentId={selectedSegmentId} hasPerson={hasPerson}/>
    </section>);
}
