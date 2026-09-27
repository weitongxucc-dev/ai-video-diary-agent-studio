"use client";

import { useEffect, useRef, useState } from "react";
import { Mic, RotateCcw, Square, X } from "lucide-react";
import styles from "./DesktopVoiceRecorder.module.css";

export type RecordedVoiceMetadata = {
  mimeType: string;
  durationMs: number;
  deviceLabel?: string;
};

export type DesktopVoiceRecorderProps = {
  scriptText?: string;
  onRecorded: (blob: Blob, metadata: RecordedVoiceMetadata) => void | Promise<void>;
  onCancel: () => void;
};

type Phase = "idle" | "requesting" | "countdown" | "recording" | "stopping" | "review" | "submitting" | "submitted";

function recordingError(error: unknown): string {
  if (error instanceof DOMException) {
    if (error.name === "NotAllowedError" || error.name === "PermissionDeniedError" || error.name === "SecurityError") return "没有麦克风权限。请在系统或浏览器设置中允许后重试。";
    if (error.name === "NotFoundError" || error.name === "DevicesNotFoundError" || error.name === "OverconstrainedError") return "没有找到可用麦克风。请连接设备后重试。";
    if (error.name === "NotReadableError" || error.name === "TrackStartError") return "麦克风暂时无法使用。请检查是否被其他应用占用。";
  }
  return error instanceof Error && error.message ? `录音失败：${error.message}` : "录音失败，请重试。";
}

function stopTracks(stream: MediaStream | null) {
  stream?.getTracks().forEach((track) => track.stop());
}

function recordingOptions(): MediaRecorderOptions | undefined {
  const supported = ["audio/webm", "audio/mp4", "audio/ogg"].find((type) => MediaRecorder.isTypeSupported?.(type));
  return supported ? { mimeType: supported } : undefined;
}

export function DesktopVoiceRecorder({ scriptText, onRecorded, onCancel }: DesktopVoiceRecorderProps) {
  const [phase, setPhase] = useState<Phase>("idle");
  const [error, setError] = useState<string | null>(null);
  const [devices, setDevices] = useState<MediaDeviceInfo[]>([]);
  const [deviceId, setDeviceId] = useState("");
  const [countdown, setCountdown] = useState(3);
  const [elapsedMs, setElapsedMs] = useState(0);
  const [recording, setRecording] = useState<{ blob: Blob; metadata: RecordedVoiceMetadata } | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const startedAtRef = useRef(0);
  const stopRequestedRef = useRef(false);
  const tickRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const countdownTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const requestRef = useRef(0);
  const submitPendingRef = useRef(false);
  const mountedRef = useRef(true);
  const previewUrlRef = useRef<string | null>(null);

  function clearTick() {
    if (tickRef.current) clearInterval(tickRef.current);
    tickRef.current = null;
  }

  function clearCountdown() {
    if (countdownTimerRef.current) clearTimeout(countdownTimerRef.current);
    countdownTimerRef.current = null;
  }

  function releaseRecorder() {
    const recorder = recorderRef.current;
    recorderRef.current = null;
    if (recorder) {
      recorder.ondataavailable = null;
      recorder.onerror = null;
      recorder.onstop = null;
      if (recorder.state !== "inactive") {
        try { recorder.stop(); } catch { /* The device tracks are still released below. */ }
      }
    }
    stopTracks(streamRef.current);
    streamRef.current = null;
    clearTick();
    clearCountdown();
  }

  function clearPreview() {
    if (previewUrlRef.current) URL.revokeObjectURL(previewUrlRef.current);
    previewUrlRef.current = null;
    setPreviewUrl(null);
    setRecording(null);
  }

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      requestRef.current += 1;
      const recorder = recorderRef.current;
      recorderRef.current = null;
      if (recorder) {
        recorder.ondataavailable = null;
        recorder.onerror = null;
        recorder.onstop = null;
        if (recorder.state !== "inactive") {
          try { recorder.stop(); } catch { /* The device tracks are still released below. */ }
        }
      }
      stopTracks(streamRef.current);
      streamRef.current = null;
      if (tickRef.current) clearInterval(tickRef.current);
      if (countdownTimerRef.current) clearTimeout(countdownTimerRef.current);
      if (previewUrlRef.current) URL.revokeObjectURL(previewUrlRef.current);
    };
  }, []);

  useEffect(() => {
    if (!navigator.mediaDevices?.enumerateDevices) return;
    let active = true;
    void navigator.mediaDevices.enumerateDevices().then((available) => {
      if (active) setDevices(available.filter((device) => device.kind === "audioinput"));
    }).catch(() => { /* Device selection is optional; getUserMedia reports capture errors. */ });
    return () => { active = false; };
  }, []);

  async function start() {
    if (phase !== "idle" && phase !== "review") return;
    clearPreview();
    setError(null);
    setElapsedMs(0);
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
      setError("当前环境不能使用麦克风录音。请在支持录音的浏览器或桌面版中打开。");
      return;
    }
    const request = ++requestRef.current;
    setPhase("requesting");
    let stream: MediaStream | null = null;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: deviceId ? { deviceId: { exact: deviceId } } : true });
      if (!mountedRef.current || request !== requestRef.current) {
        stopTracks(stream);
        return;
      }
      streamRef.current = stream;
      const recorder = new MediaRecorder(stream, recordingOptions());
      recorderRef.current = recorder;
      chunksRef.current = [];
      stopRequestedRef.current = false;
      const label = stream.getAudioTracks()[0]?.label || undefined;
      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) chunksRef.current.push(event.data);
      };
      recorder.onerror = (event) => {
        releaseRecorder();
        if (mountedRef.current) {
          setError(recordingError((event as Event & { error?: Error }).error));
          setPhase("idle");
        }
      };
      recorder.onstop = () => {
        const durationMs = Math.max(0, Date.now() - startedAtRef.current);
        const blob = new Blob(chunksRef.current, { type: recorder.mimeType || chunksRef.current[0]?.type || "application/octet-stream" });
        const requested = stopRequestedRef.current;
        releaseRecorder();
        if (!mountedRef.current || request !== requestRef.current) return;
        if (!requested) {
          setError("录音意外中断，请检查麦克风后重录。");
          setPhase("idle");
          return;
        }
        if (!blob.size) {
          setError("没有录到声音，请检查麦克风后重试。");
          setPhase("idle");
          return;
        }
        const url = URL.createObjectURL(blob);
        previewUrlRef.current = url;
        setPreviewUrl(url);
        setRecording({ blob, metadata: { mimeType: blob.type, durationMs, deviceLabel: label } });
        setElapsedMs(durationMs);
        setPhase("review");
      };
      setCountdown(3);
      setPhase("countdown");
      const advance = (seconds: number) => {
        countdownTimerRef.current = setTimeout(() => {
          if (!mountedRef.current || request !== requestRef.current) return;
          if (seconds > 1) {
            setCountdown(seconds - 1);
            advance(seconds - 1);
            return;
          }
          countdownTimerRef.current = null;
          try {
            recorder.start();
            startedAtRef.current = Date.now();
            setPhase("recording");
            tickRef.current = setInterval(() => setElapsedMs(Date.now() - startedAtRef.current), 250);
          } catch (cause) {
            releaseRecorder();
            setError(recordingError(cause));
            setPhase("idle");
          }
        }, 1000);
      };
      advance(3);
      if (navigator.mediaDevices.enumerateDevices) {
        void navigator.mediaDevices.enumerateDevices().then((available) => {
          if (mountedRef.current) setDevices(available.filter((device) => device.kind === "audioinput"));
        }).catch(() => { /* Recording continues with the granted device. */ });
      }
    } catch (cause) {
      stopTracks(stream);
      if (request === requestRef.current) releaseRecorder();
      if (mountedRef.current && request === requestRef.current) {
        setError(recordingError(cause));
        setPhase("idle");
      }
    }
  }

  function stop() {
    if (phase !== "recording" || !recorderRef.current) return;
    setPhase("stopping");
    stopRequestedRef.current = true;
    setElapsedMs(Date.now() - startedAtRef.current);
    clearTick();
    try {
      recorderRef.current.stop();
    } catch (cause) {
      releaseRecorder();
      setError(recordingError(cause));
      setPhase("idle");
    }
  }

  function reset() {
    requestRef.current += 1;
    releaseRecorder();
    clearPreview();
    setElapsedMs(0);
    setError(null);
    setPhase("idle");
  }

  async function submitRecording() {
    if (!recording || phase !== "review" || submitPendingRef.current) return;
    const request = requestRef.current;
    submitPendingRef.current = true;
    setPhase("submitting");
    setError(null);
    try {
      await onRecorded(recording.blob, recording.metadata);
      submitPendingRef.current = false;
      if (mountedRef.current && request === requestRef.current) setPhase("submitted");
    } catch (cause) {
      submitPendingRef.current = false;
      if (mountedRef.current && request === requestRef.current) {
        setError(cause instanceof Error && cause.message ? `保存录音失败：${cause.message}` : "保存录音失败，请重试。");
        setPhase("review");
      }
    }
  }

  function cancel() {
    if (submitPendingRef.current) return;
    reset();
    onCancel();
  }

  const busy = phase === "requesting" || phase === "countdown" || phase === "stopping" || phase === "submitting";
  return <section aria-label="电脑录音" className={styles.recorder}>
    <div className={styles.heading}><div><span className={styles.eyebrow}>本机麦克风</span><h2>为这条视频录音</h2></div><button aria-label={phase === "submitted" ? "关闭录音" : "取消录音"} className={styles.iconButton} disabled={phase === "submitting"} onClick={cancel} type="button"><X size={16} /></button></div>
    {scriptText ? <p className={styles.script}>{scriptText}</p> : <p className={styles.help}>录完先试听，确认后再交给当前任务。</p>}
    {devices.length > 1 ? <div className={styles.devices} role="group" aria-label="选择麦克风"><span>录音设备</span><div><button aria-pressed={!deviceId} className={styles.deviceButton} disabled={phase === "recording" || busy || phase === "submitted"} onClick={() => setDeviceId("")} type="button">系统默认</button>{devices.map((device, index) => <button aria-pressed={deviceId === device.deviceId} className={styles.deviceButton} disabled={phase === "recording" || busy || phase === "submitted"} key={device.deviceId || index} onClick={() => setDeviceId(device.deviceId)} type="button">{device.label || `麦克风 ${index + 1}`}</button>)}</div></div> : null}
    <div aria-live="polite" className={styles.state}>{phase === "requesting" ? "正在连接麦克风…" : phase === "countdown" ? `${countdown} 秒后开始录音` : phase === "recording" ? `正在录音 · ${Math.floor(elapsedMs / 1000)} 秒` : phase === "stopping" ? "正在整理录音…" : phase === "submitting" ? "正在保存录音，请等待结果…" : phase === "review" || phase === "submitted" ? `已录制 ${elapsedMs < 1000 ? "不足 1 秒" : `${Math.round(elapsedMs / 1000)} 秒`}` : "麦克风将在开始后启用"}</div>
    {previewUrl ? <audio aria-label="试听本次录音" controls onError={() => setError("试听失败，请重新录音。")} preload="metadata" src={previewUrl} /> : null}
    {error ? <p className={styles.error} role="alert">{error}</p> : null}
    {phase === "submitted" ? <p className={styles.notice} role="status">录音已交给当前任务，保存结果以任务提示为准。</p> : null}
    <div className={styles.actions}>
      {phase === "idle" ? <button className={styles.primaryButton} onClick={() => void start()} type="button"><Mic size={16} />开始录音</button> : null}
      {phase === "recording" ? <button className={styles.primaryButton} onClick={stop} type="button"><Square size={14} fill="currentColor" />停止录音</button> : null}
      {phase === "review" ? <><button className={styles.primaryButton} onClick={() => void submitRecording()} type="button">使用录音</button><button className={styles.secondaryButton} onClick={() => void start()} type="button"><RotateCcw size={15} />重新录音</button></> : null}
      {busy ? <button className={styles.primaryButton} disabled type="button">{phase === "requesting" ? "等待麦克风…" : phase === "countdown" ? "准备录音…" : phase === "stopping" ? "正在整理…" : "正在保存…"}</button> : null}
      <button className={styles.secondaryButton} disabled={phase === "submitting"} onClick={cancel} type="button">{phase === "submitted" ? "关闭" : "取消"}</button>
    </div>
  </section>;
}
