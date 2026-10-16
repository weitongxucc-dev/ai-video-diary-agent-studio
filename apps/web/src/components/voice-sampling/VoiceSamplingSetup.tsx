"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { AppSelect, AppSelectOption } from "@/components/AppSelect";
import { DesktopVoiceRecorder } from "@/components/DesktopVoiceRecorder";
import {
  createVoiceSamplingTask, getVoiceSamplingTask, listVoiceSamplingTasks, submitVoiceSample,
  getMediaCapability, createVoicePreview, listMediaPreviews, getVoiceProfile,
  mediaProfilesErrorMessage, MediaProfilesApiError,
  type MediaCapability, type MediaPreview, type VoiceSamplingTask,
} from "@/lib/media-profiles-api";
import { recordedVoiceWav } from "./pcm-wav";
import styles from "./VoiceSamplingSetup.module.css";

export type VoiceSamplingSetupProps = {
  onSkip: () => void;
  onReceived?: (task: VoiceSamplingTask) => void;
};

const errorMessage = (cause: unknown) => cause instanceof Error ? cause.message : mediaProfilesErrorMessage(cause);

export function VoiceSamplingSetup({ onSkip, onReceived }: VoiceSamplingSetupProps) {
  const [task, setTask] = useState<VoiceSamplingTask | null>(null);
  const [tasks, setTasks] = useState<VoiceSamplingTask[]>([]);
  const [name, setName] = useState("我的声音");
  const [language, setLanguage] = useState<VoiceSamplingTask["language"]>("zh");
  const [mode, setMode] = useState<"desktop" | "import" | "mobile">("desktop");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [importFile, setImportFile] = useState<File | null>(null);
  const [importUrl, setImportUrl] = useState<string | null>(null);
  const [textConfirmed, setTextConfirmed] = useState(false);
  const [transmission, setTransmission] = useState<string | null>(null);
  const createRequest = useRef<{ key: string; id: string } | null>(null);
  const submission = useRef<{ source: Blob; audio: Blob; requestId: string } | null>(null);
  const pending = useRef(false);

  useEffect(() => {
    let active = true;
    void listVoiceSamplingTasks().then(result => {
      if (active) { setTasks(result.items); setTask(result.items[0] ?? null); }
    }).catch(cause => { if (active) setError(errorMessage(cause)); }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (!importFile) { setImportUrl(null); return; }
    const url = URL.createObjectURL(importFile);
    setImportUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [importFile]);

  async function start() {
    if (pending.current || !name.trim()) return;
    pending.current = true; setBusy(true); setError(null);
    const key = JSON.stringify([name.trim(), language]);
    if (createRequest.current?.key !== key) createRequest.current = { key, id: crypto.randomUUID() };
    try {
      const created = await createVoiceSamplingTask({ request_id: createRequest.current.id, display_name: name.trim(), language, purpose: "voice_profile" });
      setTask(created); setTasks(items => [created, ...items.filter(item => item.id !== created.id)]);
      createRequest.current = null;
    } catch (cause) { setError(errorMessage(cause)); }
    finally { pending.current = false; setBusy(false); }
  }

  function received(value: VoiceSamplingTask) {
    setTask(value); setTasks(items => [value, ...items.filter(item => item.id !== value.id)]);
    setTransmission(null); setImportFile(null); submission.current = null;
    onReceived?.(value);
  }

  async function submit(blob: Blob, source: "desktop" | "import") {
    if (!task || pending.current) return;
    pending.current = true; setBusy(true); setError(null); setTransmission("正在准备声音样本，尚未发送…");
    try {
      if (submission.current?.source !== blob) {
        const originalWav = source === "import" && blob instanceof File && blob.name.toLowerCase().endsWith(".wav");
        const audio = originalWav ? blob : await recordedVoiceWav(blob);
        submission.current = { source: blob, audio, requestId: crypto.randomUUID() };
      }
      const frozen = submission.current;
      setTransmission("正在发送；主机接收结果尚未确认…");
      let result: VoiceSamplingTask;
      try {
        result = await submitVoiceSample(task, { request_id: frozen.requestId, source_device: source, audio: frozen.audio });
      } catch (cause) {
        // A lost reply is not a failed reception. Read the original task before any retry.
        if (!(cause instanceof MediaProfilesApiError) || (cause.status !== 0 && cause.status < 500)) throw cause;
        setTransmission("发送回执暂未确认，正在查询原任务…");
        const original = await getVoiceSamplingTask(task.id);
        if (original.status !== "received" || original.submission_request_id !== frozen.requestId) throw cause;
        result = original;
      }
      received(result);
    } catch (cause) {
      setTransmission("尚未确认主机接收。录音保留在本页，可用原请求重试。");
      setError(errorMessage(cause));
      throw cause;
    } finally { pending.current = false; setBusy(false); }
  }

  function newSample() {
    setTask(null); setImportFile(null); setTextConfirmed(false); setTransmission(null); setError(null);
    submission.current = null;
  }

  return <section className={styles.setup} aria-label="我的声音采样">
    <header><h2>制作我的声音</h2><p>保存自己的声音参考，需要时用于视频旁白。此项可跳过，之后可从“人物与声音”继续。</p></header>
    {error ? <p role="alert" className={styles.error}>{error}</p> : null}
    {loading ? <p role="status">正在读取声音采样任务…</p> : !task ? <>
      <label>声音名称<input value={name} maxLength={120} disabled={busy} onChange={event => setName(event.target.value)}/></label>
      <label>朗读语言<AppSelect value={language} disabled={busy} onValueChange={value => setLanguage(value as VoiceSamplingTask["language"])}><AppSelectOption value="zh">中文</AppSelectOption><AppSelectOption value="en">英文</AppSelectOption><AppSelectOption value="zh-en">中英混合</AppSelectOption></AppSelect></label>
      <button type="button" disabled={busy || !name.trim()} onClick={() => void start()}>开始声音采样</button>
      {tasks.length ? <div className={styles.history}><p>继续已有采样</p>{tasks.map(item => <button type="button" key={item.id} disabled={busy} onClick={() => { setTask(item); setError(null); }}>{item.display_name} · {item.status === "received" ? "主机已接收" : "待提交样本"}</button>)}</div> : null}
    </> : <>
      <div className={styles.reference}><strong>{task.display_name} · 朗读参考</strong><p>{task.reference_text}</p><small>文本版本：{task.text_version}</small></div>
      {task.status === "received" ? <>
        <p role="status">主机已接收 · {task.duration_seconds?.toFixed(1)} 秒。声音参考已保存，尚不代表声音克隆就绪。</p>
        <VoiceSamplePreview key={task.id} task={task}/>
        <button type="button" onClick={newSample}>录制另一份声音样本</button>
      </> : <>
        {task.last_error ? <p role="status">上次样本未接收：{task.last_error}</p> : null}
        <div className={styles.methods} role="group" aria-label="采样方式">{(["desktop", "import", "mobile"] as const).map(value => <button type="button" key={value} aria-pressed={mode === value} disabled={busy} onClick={() => { setMode(value); setError(null); }}>{({desktop:"在电脑录制",import:"导入已有录音",mobile:"用手机录制"})[value]}</button>)}</div>
        {mode === "desktop" ? <DesktopVoiceRecorder key={task.id} purpose="voice-sample" onRecorded={blob => submit(blob, "desktop")} onCancel={onSkip}/> : mode === "import" ? <div className={styles.import}>
          <p>选择 1–120 秒的录音，支持 WAV 和本机另存的录音。录音须与上方参考文字逐字一致；其他录音可在人物与声音页登记并填写实际文字。压缩录音在本机转成 PCM WAV 后提交。</p>
          <label>声音样本文件<input type="file" accept=".wav,.webm,.m4a,.mp4,.ogg,audio/*" disabled={busy} onChange={event => { setImportFile(event.target.files?.[0] ?? null); setTextConfirmed(false); submission.current = null; }}/></label>
          {importUrl ? <><audio aria-label="试听导入的声音样本" controls src={importUrl}/><label className={styles.check}><input type="checkbox" checked={textConfirmed} disabled={busy} onChange={event => setTextConfirmed(event.target.checked)}/>我已试听，录音与本任务朗读文字一致</label><button type="button" disabled={busy || !textConfirmed} onClick={() => { if (importFile) void submit(importFile, "import").catch(() => {}); }}>提交声音样本</button><p>文件已在电脑中选择，尚未发送。</p></> : null}
        </div> : <p role="status">手机录制尚未接通。后续已连接手机会使用此任务和同一文本版本；现在可在电脑录制或导入录音。</p>}
        {transmission ? <p role="status">{transmission}</p> : null}
      </>}
    </>}
    <footer><button type="button" disabled={busy} onClick={onSkip}>{task?.status === "received" ? "完成，继续使用" : "稍后设置"}</button><Link href="/profiles?view=voices">打开人物与声音</Link></footer>
  </section>;
}

function VoiceSamplePreview({ task }: { task: VoiceSamplingTask }) {
  const [capability, setCapability] = useState<MediaCapability | null>(null);
  const [preview, setPreview] = useState<MediaPreview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [referenceUrl, setReferenceUrl] = useState<string | null>(null);
  const [voiceModel, setVoiceModel] = useState<string | null>(null);
  const [referenceRunnable, setReferenceRunnable] = useState(false);
  const [text, setText] = useState(task.language === "en" ? "This is a short preview of my voice." : "你好，这是用我的声音生成的短试听。");
  const selectPreview = useCallback((items: MediaPreview[]) => items.find(item => item.voice_profile_id === task.voice_profile_id && item.voice_version === task.voice_version) ?? null, [task.voice_profile_id, task.voice_version]);
  useEffect(() => {
    let active = true;
    if (!task.voice_profile_id || !task.voice_version) return;
    void Promise.all([getMediaCapability("voice-clone"), listMediaPreviews("voice"), getVoiceProfile(task.voice_profile_id, task.voice_version)]).then(([available, runs, voice]) => {
      if (active) { setCapability(available); setPreview(selectPreview(runs.items)); setReferenceUrl(voice.reference_url ?? null); setVoiceModel(voice.model_id); setReferenceRunnable(voice.runnable); }
    }).catch(cause => { if (active) setError(errorMessage(cause)); });
    return () => { active = false; };
  }, [selectPreview, task.voice_profile_id, task.voice_version]);
  useEffect(() => {
    if (preview?.status !== "queued" && preview?.status !== "running") return;
    let active = true;
    const timer = setInterval(() => { void listMediaPreviews("voice").then(result => { if (active) setPreview(selectPreview(result.items)); }).catch(cause => { if (active) setError(errorMessage(cause)); }); }, 1500);
    return () => { active = false; clearInterval(timer); };
  }, [preview?.status, selectPreview]);
  async function generate() {
    if (!task.voice_profile_id || !task.voice_version || busy) return;
    setBusy(true); setError(null);
    try { setPreview(await createVoicePreview({ voice_profile_id: task.voice_profile_id, voice_version: task.voice_version, preview_text: text, retry: Boolean(preview && ["failed", "blocked", "unavailable", "cancelled"].includes(preview.status)) })); }
    catch (cause) { setError(errorMessage(cause)); }
    finally { setBusy(false); }
  }
  const running = preview?.status === "queued" || preview?.status === "running";
  const modelMismatch = capability?.available && voiceModel !== capability.model_id;
  return <section className={styles.preview} aria-label="采样声音试听">
    {referenceUrl ? <><p>已接收的原始样本</p><audio aria-label="试听主机收到的声音样本" controls src={referenceUrl} onError={() => setError("原始样本暂时无法播放，请重新打开声音档案检查参考。")}/></> : null}
    {capability && !capability.available ? <p>声音参考已保留，暂不可生成试听。{capability.message} <Link href="/health">查看运行条件</Link></p> : null}
    {modelMismatch ? <p>此声音参考尚未绑定当前模型。请在<Link href="/profiles?view=voices">人物与声音</Link>编辑参考，选择已配置模型后再试听。</p> : null}
    {capability && !referenceRunnable ? <p>当前参考需要检查，请打开人物与声音确认来源和使用限制。</p> : null}
    <label>短试听文字<textarea value={text} disabled={busy || running} maxLength={300} onChange={event => setText(event.target.value)}/></label>
    <p>生成只在本机执行；样本保存不会自动启动模型。</p>
    <button type="button" disabled={busy || running || !capability?.available || modelMismatch || !referenceRunnable || !text.trim()} onClick={() => void generate()}>生成声音短试听</button>
    {error ? <p role="alert" className={styles.error}>{error}</p> : null}
    {preview ? <div role="status"><p>{({queued:"声音试听已排队，等待设备许可",running:"正在制作声音试听",succeeded:preview.output_url ? "此版本声音已生成，可试听实际结果" : "试听回执已完成，但生成文件暂时不可读取",failed:"声音制作失败，原始样本已保留",blocked:"声音制作被阻止，原始样本已保留",unavailable:"声音生成暂不可用，原始样本已保留",cancelled:"声音试听已取消，原始样本已保留"})[preview.status]}</p>{preview.reason ? <p>{preview.reason}</p> : null}{preview.repair_action ? <p>{preview.repair_action}</p> : null}{preview.status === "succeeded" && preview.output_url ? <><p>本次试听文字：{preview.preview_text}</p><audio aria-label="试听生成的声音" controls src={preview.output_url} onError={() => setError("生成文件暂时无法播放，请在人物与声音页检查这条试听回执。")}/></> : null}</div> : null}
  </section>;
}
