"use client";
import { WorkspaceVideo, WorkspaceAudio } from "../workbench/WorkspaceMedia";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  advanceMaterialDiscovery, controlMaterialDiscovery, discoveryPreviewUrl,
  getDiscoveryRoots, getLatestDiscovery, getMaterialDiscovery,
  receiveDiscoveryCandidates, startMaterialDiscovery,
  scanMaterialIntake,
} from "@/lib/api/materials";
import type { DiscoveryCandidate, DiscoveryRoots, MaterialDiscovery } from "@/lib/types/materials";
import styles from "./MaterialDiscoveryPanel.module.css";
import { useMaterialIntake } from "./useMaterialIntake";
import { useLocalMaterialImport } from "./useLocalMaterialImport";
import { MaterialIntakePanel } from "./MaterialIntakePanel";

const statusLabel = { scanning: "正在扫描", paused: "扫描已暂停", cancelled: "扫描已取消", completed: "本次扫描已结束" };
function message(error: unknown) { return error instanceof Error ? error.message : "读取失败，请重试。"; }
function size(bytes: number) { return bytes < 1024 * 1024 ? `${Math.round(bytes / 1024)} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB`; }

export function MaterialDiscoveryPanel({ onReceived }: { onReceived?: () => void }) {
  const { intake, intakeError, intakeBusy, refreshIntakeStatus, bindIntake, confirmCopy } = useMaterialIntake();
  const intakeReady = Boolean(intake?.bound_to_current_workspace && intake.directory_state === "ready");
  const { importBusy, importResult, runImport } = useLocalMaterialImport(intakeReady);
  const [roots, setRoots] = useState<DiscoveryRoots | null>(null);
  const [rootIds, setRootIds] = useState<string[]>([]);
  const [session, setSession] = useState<MaterialDiscovery | null>(null);
  const [exclusions, setExclusions] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const [preview, setPreview] = useState<DiscoveryCandidate | null>(null);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [autoAdvance, setAutoAdvance] = useState(false);
  const revision = useRef(0);
  const mounted = useRef(false);
  const load = useCallback(async () => {
    setLoading(true); setError("");
    try {
      const [inventory, latest] = await Promise.all([getDiscoveryRoots(), getLatestDiscovery()]);
      if (!mounted.current) return;
      setRoots(inventory);
      setRootIds(inventory.roots.filter(r => r.available).map(r => r.id));
      setSession(latest);
      setExclusions((latest?.exclusions ?? inventory.exclusions).join("\n"));
    } catch (e) { if (mounted.current) setError(message(e)); }
    finally { if (mounted.current) setLoading(false); }
  }, []);
  useEffect(() => {
    mounted.current = true;
    void load();
    return () => { mounted.current = false; revision.current += 1; };
  }, [load]);

  const sessionId = session?.id;
  const state = session?.state;
  useEffect(() => {
    if (!sessionId || state !== "scanning" || !autoAdvance || busy) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    const version = revision.current;
    async function advance() {
      try {
        const next = await advanceMaterialDiscovery(sessionId!);
        if (!active || version !== revision.current) return;
        setSession(next);
        if (next.state === "scanning") timer = setTimeout(() => void advance(), 350);
      } catch (e) {
        if (active) { setError(message(e)); setAutoAdvance(false); }
      }
    }
    timer = setTimeout(() => void advance(), 100);
    return () => { active = false; clearTimeout(timer); };
  }, [sessionId, state, autoAdvance, busy]);

  async function action(run: () => Promise<MaterialDiscovery>, afterward?: () => void) {
    const version = ++revision.current;
    setBusy(true); setError("");
    try {
      const next = await run();
      if (!mounted.current || version !== revision.current) return;
      setSession(next); afterward?.();
    } catch (e) { if (mounted.current && version === revision.current) setError(message(e)); }
    finally { if (mounted.current && version === revision.current) setBusy(false); }
  }
  const excludedPaths = () => exclusions.split("\n").map(p => p.trim()).filter(Boolean);
  const selectable = session?.candidates.filter(c => !c.excluded && c.state !== "received") ?? [];
  const selectedVisible = selected.filter(id => selectable.some(c => c.id === id));
  const canStart = !session || session.state === "cancelled" || session.state === "completed";

  return <section className={styles.panel} aria-label="扫描电脑素材">
    <header><h2>扫描电脑素材</h2><p>主动查找本机和已连接磁盘中的视频、图片与音频。先预览和勾选，加入素材库后才开始接收与分析。</p></header>
    {loading ? <p role="status">正在读取扫描位置…</p> : null}
    {error ? <div role="alert"><p>{error}</p>{!roots ? <button type="button" className="ui-button secondary" onClick={() => void load()}>重新读取</button> : null}</div> : null}
    {roots ? <>
      <fieldset disabled={busy || !canStart}><legend>扫描位置</legend>
        {roots.roots.map(root => <label className={styles.root} key={root.id}><input type="checkbox" checked={rootIds.includes(root.id)} disabled={!root.available} onChange={e => setRootIds(ids => e.target.checked ? [...ids, root.id] : ids.filter(id => id !== root.id))} /><span>{root.label}<small>{root.display_path}{!root.available ? " · 当前不可访问" : ""}</small></span></label>)}
      </fieldset>
      <details><summary>排除目录</summary><p>默认跳过系统、应用、依赖、缓存和本软件产物。新增排除立即阻止后续读取，已入库的素材保留。</p>
        <label>每行一个完整目录路径<textarea rows={3} value={exclusions} onChange={e => setExclusions(e.target.value)} /></label>
        {session ? <button className="ui-button secondary" type="button" disabled={busy} onClick={() => { setPreview(null); setSelected([]); void action(() => controlMaterialDiscovery(session.id, "exclude", excludedPaths())); }}>保存排除目录</button> : null}
      </details>
      <div className={styles.actions}>
        {canStart ? <button type="button" className="ui-button" disabled={busy || !rootIds.length} onClick={() => void action(() => startMaterialDiscovery(rootIds, excludedPaths()), () => { setSelected([]); setPreview(null); setAutoAdvance(true); })}>{session ? "重新扫描" : "开启扫描"}</button> : null}
        {session?.state === "scanning" && !autoAdvance ? <button className="ui-button" type="button" disabled={busy} onClick={() => setAutoAdvance(true)}>继续本次扫描</button> : null}
        {session?.state === "scanning" && autoAdvance ? <button className="ui-button secondary" type="button" disabled={busy} onClick={() => { setAutoAdvance(false); void action(() => controlMaterialDiscovery(session.id, "pause")); }}>暂停</button> : null}
        {session?.state === "paused" ? <button className="ui-button" type="button" disabled={busy} onClick={() => void action(() => controlMaterialDiscovery(session.id, "resume"), () => setAutoAdvance(true))}>恢复扫描</button> : null}
        {session && session.state !== "cancelled" ? <button className="ui-button secondary" type="button" onClick={() => { setAutoAdvance(false); void action(() => controlMaterialDiscovery(session.id, "cancel")); }}>取消本次扫描</button> : null}
      </div>
    </> : null}
    {session ? <>
      <p role="status">{statusLabel[session.state]} · 已检查 {session.visited_count} 项 · 发现 {session.total_count} 个候选{session.skipped_count ? ` · ${session.skipped_count} 项未能读取` : ""}</p>
      {session.state === "scanning" && !autoAdvance ? <p>上次进度已保留；点击继续后恢复读取。</p> : null}
      {session.issues.length ? <details><summary>查看未扫描位置</summary><ul>{session.issues.map((issue, i) => <li key={i}>{issue.location}：{issue.message}</li>)}</ul><p>仅表示可访问范围已检查，未读取位置仍需恢复系统权限或连接磁盘后重扫。</p></details> : null}
      {session.total_count === 0 ? <p>还没有发现候选素材。未扫描或排除的位置不会出现在这里。</p> : <>
        {!intakeReady ? <><p>候选已保留。先确认素材接收位置，再把勾选文件加入素材库。</p><MaterialIntakePanel intake={intake} intakeError={intakeError} intakeBusy={intakeBusy} busy={null} importBusy={importBusy} importResult={importResult} onRefresh={() => void refreshIntakeStatus()} onBind={() => void bindIntake()} onLocalImport={() => void runImport()} onScan={() => void action(async () => { await scanMaterialIntake(); return getMaterialDiscovery(session.id); })} onConfirmCopy={file => void confirmCopy(file)} mediaLinkImport={null} /></> : null}
        <div className={styles.actions}><label><input type="checkbox" checked={selectable.length > 0 && selectable.every(c => selected.includes(c.id))} disabled={busy || !selectable.length || session.state === "cancelled"} onChange={e => setSelected(e.target.checked ? selectable.map(c => c.id) : [])} />选择本页可接收素材</label>
          <button type="button" className="ui-button" disabled={busy || !intakeReady || !selectedVisible.length || session.state === "paused" || session.state === "cancelled"} onClick={() => { setAutoAdvance(false); void action(() => receiveDiscoveryCandidates(session.id, selectedVisible), () => { setSelected([]); onReceived?.(); }); }}>{busy ? "正在处理…" : `加入素材库${selectedVisible.length ? `（${selectedVisible.length}）` : ""}`}</button>
        </div>
        <ul className={styles.candidates}>{session.candidates.map(candidate => <li key={candidate.id}>
          <label><input type="checkbox" aria-label={`选择 ${candidate.file_name}`} checked={selected.includes(candidate.id)} disabled={busy || candidate.excluded || candidate.state === "received" || session.state === "cancelled"} onChange={e => setSelected(ids => e.target.checked ? [...ids, candidate.id] : ids.filter(id => id !== candidate.id))} /><span><strong>{candidate.file_name}</strong><small>{candidate.location}</small><small>{{video:"视频", audio:"音频", image:"图片"}[candidate.media_type]} · {size(candidate.size_bytes)}{candidate.media_type !== "image" ? " · 时长在预览后显示" : ""}</small></span></label>
          <div><button type="button" className="ui-button secondary" disabled={candidate.excluded} onClick={() => setPreview(candidate)}>预览</button><small>{candidate.excluded ? "已排除" : candidate.state === "received" ? "已接收" : candidate.state === "receiving" ? "接收未完成，可重新提交核对" : candidate.state === "failed" ? candidate.error : candidate.duplicate_asset_id ? "曾接收过，选中后核对去重" : "未入库"}</small></div>
        </li>)}</ul>
        <div className={styles.actions}><button type="button" className="ui-button secondary" disabled={busy || session.offset === 0} onClick={() => { setAutoAdvance(false); setSelected([]); void action(() => getMaterialDiscovery(session.id, Math.max(0, session.offset - 100))); }}>上一页</button><span>{session.offset + 1}–{session.offset + session.candidates.length} / {session.total_count}</span><button type="button" className="ui-button secondary" disabled={busy || !session.has_more} onClick={() => { setAutoAdvance(false); setSelected([]); void action(() => getMaterialDiscovery(session.id, session.offset + 100)); }}>下一页</button></div>
      </>}
      {preview ? <div className={styles.preview} aria-label={`预览 ${preview.file_name}`}><div className={styles.actions}><strong>{preview.file_name}</strong><button type="button" className="ui-button secondary" onClick={() => setPreview(null)}>关闭预览</button></div><CandidatePreview key={preview.id} candidate={preview} sessionId={session.id} /></div> : null}
    </> : null}
  </section>;
}

function CandidatePreview({ candidate, sessionId }: { candidate: DiscoveryCandidate; sessionId: string }) {
  const [failed, setFailed] = useState(false);
  const [duration, setDuration] = useState<number | null>(null);
  const src = discoveryPreviewUrl(sessionId, candidate.id);
  if (failed) return <p role="alert">无法预览：文件可能已变化、目录权限已撤销，或格式无法播放。请重新扫描或在系统中检查。</p>;
  return <>
    {candidate.media_type === "image"
      // The authenticated local endpoint validates the candidate before serving bytes.
      // eslint-disable-next-line @next/next/no-img-element
      ? <img src={src} alt={candidate.file_name} onError={() => setFailed(true)} />
      : candidate.media_type === "audio" ? <WorkspaceAudio src={src} controls preload="metadata" onError={() => setFailed(true)} onLoadedMetadata={e => setDuration(e.currentTarget.duration)} />
      : <WorkspaceVideo src={src} controls preload="metadata" onError={() => setFailed(true)} onLoadedMetadata={e => setDuration(e.currentTarget.duration)} />}
    {duration !== null && Number.isFinite(duration) ? <p>时长 {duration.toFixed(1)} 秒</p> : null}
  </>;
}
