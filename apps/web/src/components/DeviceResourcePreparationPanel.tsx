"use client";

import { useCallback, useEffect, useRef, useState } from 'react';
import { AppSelect, AppSelectOption } from './AppSelect';
import {
  finalizeDevicePreparation, getDevicePreparation, getDeviceResourceStatus, prepareCpuResources,
  type CpuPreparationRequest, type CpuPreparationShape, type DevicePreparationReceipt,
  type DeviceResourceStatus,
} from '@/lib/api/device-preparation';
import styles from './DeviceResourcePreparationPanel.module.css';
import { ApiRequestError } from '@/lib/api/transport';

type Attempt = { scope: string; payload: CpuPreparationRequest; workId?: string; finalized?: boolean; inputRejected?: boolean };
const storageKey = (scope: string) => `yaohe:cpu-preparation:v1:${scope}`;
const terminal = new Set(['done', 'failed', 'cancelled']);
const active = new Set(['waiting', 'blocked', 'granted', 'yield_requested']);
const shapeKeys = ['width_px', 'height_px', 'frame_rate', 'duration_seconds', 'clip_count', 'audio_input_count', 'sample_rate', 'channels'];

function readAttempt(scope: string): Attempt | null {
  const raw = localStorage.getItem(storageKey(scope));
  if (!raw) return null;
  const value = JSON.parse(raw) as Attempt;
  if (value.scope !== scope || value.payload?.kind !== 'cpu'
      || !/^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(value.payload.request_id)
      || !value.payload.shape || Object.keys(value.payload.shape).length !== shapeKeys.length
      || shapeKeys.some(key => {
        const item = value.payload.shape[key as keyof CpuPreparationShape];
        return typeof item !== 'number' || !Number.isFinite(item) || item <= 0
          || key !== 'duration_seconds' && !Number.isInteger(item);
      })
      || value.payload.shape.width_px % 2 !== 0 || value.payload.shape.height_px % 2 !== 0
      || value.workId !== undefined && (typeof value.workId !== 'string' || !value.workId)
      || value.finalized !== undefined && typeof value.finalized !== 'boolean'
      || value.inputRejected !== undefined && (typeof value.inputRejected !== 'boolean' || Boolean(value.workId))) {
    throw new Error('本机保存的原准备记录无法核对；未创建替代请求。');
  }
  return value;
}

function saveAttempt(attempt: Attempt) {
  // Persist before submitting: losing a response must never generate a new UUID.
  localStorage.setItem(storageKey(attempt.scope), JSON.stringify(attempt));
}

function failureDetail(error: unknown): string {
  if (!(error instanceof Error)) return '无法读取本机服务的回复。';
  try {
    const value = JSON.parse(error.message) as { detail?: unknown };
    return typeof value.detail === 'string' ? value.detail : error.message;
  } catch { return error.message; }
}

function receiptLabel(receipt: DevicePreparationReceipt): string {
  return ({ waiting: '已排队，等待设备', blocked: '正在等待准备条件', granted: '正在准备基础媒体能力',
    yield_requested: '正在等待安全停止', done: '准备已完成，等待核验启用', failed: '本次准备未完成',
    cancelled: '本次准备已取消' } as Record<string, string>)[receipt.state] ?? '原准备结果尚不能确认';
}

export function DeviceResourcePreparationPanel() {
  const [status, setStatus] = useState<DeviceResourceStatus | null>(null);
  const [attempt, setAttempt] = useState<Attempt | null>(null);
  const [receipt, setReceipt] = useState<DevicePreparationReceipt | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [storageError, setStorageError] = useState(false);
  const [ratio, setRatio] = useState('9:16');
  const [resolution, setResolution] = useState('1080');
  const [fps, setFps] = useState('30');
  const [duration, setDuration] = useState('30');
  const [clips, setClips] = useState('6');
  const [audioInputs, setAudioInputs] = useState('2');
  const busyRef = useRef(false);
  const mounted = useRef(false);
  const scope = useRef<string | null>(null);
  const attemptRef = useRef<Attempt | null>(null);
  const rememberAttempt = useCallback((value: Attempt | null) => {
    attemptRef.current = value;
    setAttempt(value);
  }, []);

  const adoptStatus = useCallback((current: DeviceResourceStatus) => {
    setStatus(current);
    if (scope.current !== current.actor_scope) {
      scope.current = current.actor_scope;
      setReceipt(null);
      setStorageError(false);
      try {
        const saved = readAttempt(current.actor_scope);
        rememberAttempt(saved);
        if (saved) {
          const shape = saved.payload.shape;
          setRatio(shape.width_px === shape.height_px ? '1:1' : shape.width_px < shape.height_px ? '9:16' : '16:9');
          setResolution(String(Math.min(shape.width_px, shape.height_px)));
          setFps(String(shape.frame_rate)); setDuration(String(shape.duration_seconds));
          setClips(String(shape.clip_count)); setAudioInputs(String(shape.audio_input_count));
        }
      }
      catch (cause) { rememberAttempt(null); setStorageError(true); setError(failureDetail(cause)); }
    }
  }, [rememberAttempt]);

  const run = useCallback(async (action: () => Promise<void>) => {
    if (busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    setError('');
    try { await action(); }
    catch (cause) { if (mounted.current) setError(failureDetail(cause)); }
    finally { busyRef.current = false; if (mounted.current) setBusy(false); }
  }, []);

  const currentStatus = useCallback(async (expected?: string) => {
    const current = await getDeviceResourceStatus();
    if (!mounted.current) throw new Error('设置页已关闭，原请求保留。');
    adoptStatus(current);
    if (expected && current.actor_scope !== expected) {
      throw new Error('当前账号、工作区或主机已变化；没有继续另一身份的准备。');
    }
    return current;
  }, [adoptStatus]);

  const refresh = useCallback(() => run(async () => {
    await currentStatus();
    const saved = attemptRef.current;
    if (saved?.workId) {
      const result = await getDevicePreparation(saved.workId, saved.scope);
      if (scope.current === saved.scope && mounted.current) setReceipt(result);
    }
  }), [currentStatus, run]);

  useEffect(() => {
    mounted.current = true;
    void refresh();
    const onFocus = () => { void refresh(); };
    const onStorage = () => { void refresh(); };
    window.addEventListener('focus', onFocus);
    window.addEventListener('storage', onStorage);
    return () => {
      mounted.current = false;
      window.removeEventListener('focus', onFocus);
      window.removeEventListener('storage', onStorage);
    };
  }, [refresh]);

  useEffect(() => {
    if (!receipt || !active.has(receipt.state) || busy || error) return;
    const timer = window.setTimeout(() => { void refresh(); }, 1500);
    return () => window.clearTimeout(timer);
  }, [receipt, busy, error, refresh]);

  const submit = () => run(async () => {
    const current = await currentStatus(status?.actor_scope);
    if (!current.cpu_preparation_supported || !current.preparation_configured || storageError) return;
    let original = attempt;
    if (!original || original.finalized) {
      const short = Number(resolution), long = Math.round(short * 16 / 9);
      const dimensions = ratio === '1:1' ? [short, short] : ratio === '9:16' ? [short, long] : [long, short];
      const shape: CpuPreparationShape = { width_px: dimensions[0], height_px: dimensions[1], frame_rate: Number(fps),
        duration_seconds: Number(duration), clip_count: Number(clips), audio_input_count: Number(audioInputs), sample_rate: 48000, channels: 2 };
      if (Object.values(shape).some(value => !Number.isFinite(value) || value <= 0)
          || !Number.isInteger(shape.clip_count) || !Number.isInteger(shape.audio_input_count)) {
        throw new Error('请填写大于零的时长、镜头数量和声音数量，数量须为整数。');
      }
      original = { scope: current.actor_scope, payload: { request_id: crypto.randomUUID(), kind: 'cpu', shape } };
      try { saveAttempt(original); }
      catch { throw new Error('无法保存原准备记录，尚未发送请求。请恢复应用存储后重试。'); }
      rememberAttempt(original);
      setReceipt(null);
    }
    // A known queue identity is always observed, never submitted again.
    if (original.workId) {
      const result = await getDevicePreparation(original.workId, original.scope);
      if (mounted.current && scope.current === original.scope) setReceipt(result);
      return;
    }
    let result: DevicePreparationReceipt;
    try { result = await prepareCpuResources(original.payload, original.scope); }
    catch (cause) {
      // This endpoint's 422 rejects the typed request before preparation runs.
      // Other failures can follow submission and must retain the fixed input.
      if (cause instanceof ApiRequestError && cause.status === 422) {
        const rejected = { ...original, inputRejected: true };
        saveAttempt(rejected);
        if (mounted.current && scope.current === original.scope) rememberAttempt(rejected);
      }
      throw cause;
    }
    const saved = { ...original, workId: result.id };
    if (mounted.current && scope.current === saved.scope) { rememberAttempt(saved); setReceipt(result); }
    saveAttempt(saved);
  });

  const finalize = () => run(async () => {
    if (!attempt?.workId) return;
    await currentStatus(attempt.scope);
    const result = await finalizeDevicePreparation(attempt.workId, attempt.scope);
    if (result.id !== attempt.workId || result.normal_work_started !== false || !terminal.has(result.state)
        || typeof result.profile_published !== 'boolean' || result.state === 'done' && !result.profile_published) {
      throw new Error('尚未取得有效的核验启用回执，原请求保留。');
    }
    const saved = { ...attempt, finalized: true };
    saveAttempt(saved);
    if (mounted.current && scope.current === saved.scope) { rememberAttempt(saved); setReceipt(result); }
    await currentStatus(attempt.scope);
  });

  const correctInput = () => run(async () => {
    if (!attempt?.inputRejected || attempt.workId) return;
    await currentStatus(attempt.scope);
    localStorage.removeItem(storageKey(attempt.scope));
    rememberAttempt(null);
    setReceipt(null);
  });

  const locked = Boolean(attempt && !attempt.finalized) || busy || !status?.cpu_preparation_supported || !status.preparation_configured || storageError;
  const recorded = status?.profile_status === 'available' && ['cpu_render', 'cpu_audio', 'clip_preview'].every(item => status.available_operations.includes(item));
  const profileLabel = !status ? error ? '暂时无法读取资源档' : '正在读取资源档' : recorded ? '基础媒体准备已记录'
    : status.profile_status === 'invalid' ? '资源档需要重新准备' : status.profile_status === 'available' ? '资源档已就绪，基础媒体仍需准备' : '基础媒体尚未准备';
  const closed = attempt?.finalized;

  return <section className={styles.panel} aria-label="基础媒体准备">
    <header className={styles.header}><h3>基础媒体准备</h3><span className={recorded ? styles.ready : undefined}>{profileLabel}</span></header>
    <p className={styles.secondary}>按你使用的视频规格检查本机画面与音频处理条件，不会制作已有作品。配音模型等能力另行检查。</p>
    {status && !status.preparation_configured ? <p>请从桌面应用打开设置，绑定本机设备配置后再准备。</p>
      : status && !status.cpu_preparation_supported ? <p>当前系统尚不支持基础媒体准备；请使用已适配的 macOS 桌面版本。</p> : null}
    <fieldset className={styles.fields} disabled={locked}>
      <legend>本次准备的视频规格</legend>
      <label><span>画面比例</span><AppSelect aria-label="画面比例" value={ratio} onValueChange={setRatio}>
        <AppSelectOption value="9:16">竖屏 9:16</AppSelectOption><AppSelectOption value="16:9">横屏 16:9</AppSelectOption><AppSelectOption value="1:1">方形 1:1</AppSelectOption>
      </AppSelect></label>
      <label><span>分辨率</span><AppSelect aria-label="分辨率" value={resolution} onValueChange={setResolution}>
        <AppSelectOption value="720">720p 预览</AppSelectOption><AppSelectOption value="1080">1080p 成片</AppSelectOption>
      </AppSelect></label>
      <label><span>帧率</span><AppSelect aria-label="帧率" value={fps} onValueChange={setFps}>
        <AppSelectOption value="24">24 帧/秒</AppSelectOption><AppSelectOption value="25">25 帧/秒</AppSelectOption><AppSelectOption value="30">30 帧/秒</AppSelectOption>
      </AppSelect></label>
      <label><span>处理时长（秒）</span><input aria-label="处理时长（秒）" type="number" min="0" step="any" value={duration} onChange={event => setDuration(event.target.value)}/></label>
      <label><span>镜头数量</span><input aria-label="镜头数量" type="number" min="1" step="1" value={clips} onChange={event => setClips(event.target.value)}/></label>
      <label><span>同时使用的声音</span><input aria-label="同时使用的声音" type="number" min="1" step="1" value={audioInputs} onChange={event => setAudioInputs(event.target.value)}/></label>
    </fieldset>
    <p className={styles.secondary}>音频使用 48 kHz 立体声。准备结果只记录所选规格的条件，不代表整条视频制作已通过。</p>
    {attempt && !closed ? <p role="status">{attempt.inputRejected ? '输入尚未被接受，未开始准备。请修改规格后再提交。' : receipt ? receiptLabel(receipt) : '尚未取得原请求的队列回执，请沿原输入继续核对。'}</p> : null}
    {closed ? <p role="status">{receipt?.state === 'done' ? '已取得核验启用回执，已有作品没有自动开始制作。' : '原准备已收束，资源档是否可用以当前读取结果为准。'}</p> : null}
    {receipt?.state === 'failed' && receipt.failure_reason ? <div role="alert">
      <p className={styles.error}>{receipt.failure_reason}</p>
      {receipt.repair_action ? <p>{receipt.repair_action}</p> : null}
    </div> : null}
    <div className={styles.actions}>
      {attempt?.workId && receipt && terminal.has(receipt.state) && !closed ? <button type="button" disabled={busy} onClick={() => void finalize()}>{busy ? '正在核验…' : receipt.state === 'done' ? '核验并启用' : '收束原准备'}</button>
        : <button type="button" disabled={busy || !status?.cpu_preparation_supported || !status.preparation_configured || storageError} onClick={() => void (attempt?.inputRejected ? correctInput() : submit())}>
          {busy ? '正在处理…' : attempt && !closed ? attempt.inputRejected ? '修改准备规格' : attempt.workId ? '刷新原准备进度' : '继续核对原请求' : recorded || closed ? '重新准备基础媒体' : '开始准备基础媒体'}
        </button>}
      <button type="button" disabled={busy} onClick={() => void refresh()}>重新读取状态</button>
    </div>
    {error ? <div role="alert"><p className={styles.error}>本次操作未完成，原准备记录保留。</p><details className={styles.details}><summary>原因与处理</summary><p>{error}</p></details></div> : null}
    {attempt ? <details className={styles.details}><summary>本次准备记录</summary>
      <p>规格：{attempt.payload.shape.width_px} × {attempt.payload.shape.height_px} · {attempt.payload.shape.frame_rate} 帧/秒 · {attempt.payload.shape.duration_seconds} 秒 · {attempt.payload.shape.clip_count} 个镜头 · {attempt.payload.shape.audio_input_count} 路声音</p>
      <p>请求：{attempt.payload.request_id}{attempt.workId ? `；队列：${attempt.workId}` : ''}</p>
      {receipt?.wait_reason && !receipt.failure_reason ? <p>设备等待原因：{receipt.wait_reason}</p> : null}
    </details> : null}
  </section>;
}
