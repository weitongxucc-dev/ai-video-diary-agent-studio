"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { VoiceRecorderState } from "./types";

type UseVoiceRecorderResult = {
  state: VoiceRecorderState;
  startRecording: () => Promise<void>;
  stopRecording: () => Promise<Blob | null>;
  reset: () => void;
  audioBlob: Blob | null;
  error: string | null;
  duration: number;
};

const PREFERRED_MIME_TYPE = "audio/webm";

function pickSupportedMimeType(): string | undefined {
  if (typeof MediaRecorder === "undefined") {
    return undefined;
  }
  if (MediaRecorder.isTypeSupported(PREFERRED_MIME_TYPE)) {
    return PREFERRED_MIME_TYPE;
  }
  const fallbacks = ["audio/ogg", "audio/mp4", "audio/wav"];
  for (const candidate of fallbacks) {
    if (MediaRecorder.isTypeSupported(candidate)) {
      return candidate;
    }
  }
  return undefined;
}

function getMediaRecorderCtor(): typeof MediaRecorder | null {
  if (typeof window === "undefined") {
    return null;
  }
  if (typeof window.MediaRecorder === "undefined") {
    return null;
  }
  return window.MediaRecorder;
}

function getGetUserMedia(): typeof navigator.mediaDevices.getUserMedia | null {
  if (typeof navigator === "undefined") {
    return null;
  }
  const mediaDevices = navigator.mediaDevices;
  if (!mediaDevices || typeof mediaDevices.getUserMedia !== "function") {
    return null;
  }
  return mediaDevices.getUserMedia.bind(mediaDevices);
}

function describeError(err: unknown): string {
  if (err instanceof Error && err.message) {
    return err.message;
  }
  if (typeof err === "string") {
    return err;
  }
  return "麦克风录音失败";
}

export function useVoiceRecorder(): UseVoiceRecorderResult {
  const [state, setState] = useState<VoiceRecorderState>("idle");
  const [audioBlob, setAudioBlob] = useState<Blob | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [duration, setDuration] = useState(0);

  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const resolveStopRef = useRef<((blob: Blob | null) => void) | null>(null);
  const tickIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const startedAtRef = useRef<number | null>(null);

  const clearTick = useCallback(() => {
    if (tickIntervalRef.current) {
      clearInterval(tickIntervalRef.current);
      tickIntervalRef.current = null;
    }
  }, []);

  const stopStream = useCallback(() => {
    const stream = streamRef.current;
    if (stream) {
      for (const track of stream.getTracks()) {
        try {
          track.stop();
        } catch {
          // ignore
        }
      }
    }
    streamRef.current = null;
  }, []);

  const finalizeStop = useCallback(
    (errMessage: string | null, blob: Blob | null) => {
      clearTick();
      stopStream();
      const recorder = mediaRecorderRef.current;
      if (recorder && recorder.state !== "inactive") {
        try {
          recorder.stop();
        } catch {
          // ignore
        }
      }
      mediaRecorderRef.current = null;
      startedAtRef.current = null;
      chunksRef.current = [];
      const resolver = resolveStopRef.current;
      resolveStopRef.current = null;
      if (resolver) {
        resolver(blob);
      }
      if (errMessage) {
        setError(errMessage);
        setAudioBlob(null);
        setState("idle");
        setDuration(0);
      } else if (blob) {
        setAudioBlob(blob);
        setError(null);
        setState("done");
      } else {
        setAudioBlob(null);
        setState("idle");
        setDuration(0);
      }
    },
    [clearTick, stopStream],
  );

  const startRecording = useCallback(async () => {
    if (state === "recording") {
      return;
    }
    setError(null);
    setAudioBlob(null);
    setDuration(0);

    const MediaRecorderCtor = getMediaRecorderCtor();
    const getUserMedia = getGetUserMedia();
    if (!MediaRecorderCtor || !getUserMedia) {
      const message = "当前浏览器不支持 MediaRecorder 或 getUserMedia";
      setError(message);
      setState("idle");
      return;
    }

    try {
      const stream = await getUserMedia({ audio: true });
      streamRef.current = stream;
      const mimeType = pickSupportedMimeType();
      const recorder = mimeType
        ? new MediaRecorderCtor(stream, { mimeType })
        : new MediaRecorderCtor(stream);
      mediaRecorderRef.current = recorder;
      chunksRef.current = [];

      recorder.ondataavailable = (event) => {
        if (event.data && event.data.size > 0) {
          chunksRef.current.push(event.data);
        }
      };
      recorder.onerror = (event) => {
        const message = (event as unknown as { error?: Error }).error?.message
          ?? "录音过程出现错误";
        finalizeStop(message, null);
      };
      recorder.onstop = () => {
        const blob = new Blob(chunksRef.current, {
          type: recorder.mimeType || mimeType || "audio/webm",
        });
        finalizeStop(null, blob);
      };

      recorder.start();
      startedAtRef.current = Date.now();
      setState("recording");
      tickIntervalRef.current = setInterval(() => {
        if (startedAtRef.current !== null) {
          setDuration(Math.floor((Date.now() - startedAtRef.current) / 1000));
        }
      }, 250);
    } catch (err) {
      finalizeStop(describeError(err), null);
    }
  }, [finalizeStop, state]);

  const stopRecording = useCallback((): Promise<Blob | null> => {
    if (state !== "recording") {
      return Promise.resolve(audioBlob);
    }
    return new Promise<Blob | null>((resolve) => {
      resolveStopRef.current = resolve;
      const recorder = mediaRecorderRef.current;
      if (recorder && recorder.state !== "inactive") {
        try {
          recorder.stop();
        } catch (err) {
          finalizeStop(describeError(err), null);
        }
      } else {
        finalizeStop(null, null);
      }
    });
  }, [audioBlob, finalizeStop, state]);

  const reset = useCallback(() => {
    setState("idle");
    setAudioBlob(null);
    setError(null);
    setDuration(0);
    chunksRef.current = [];
  }, []);

  useEffect(() => {
    return () => {
      clearTick();
      stopStream();
      const recorder = mediaRecorderRef.current;
      if (recorder && recorder.state !== "inactive") {
        try {
          recorder.stop();
        } catch {
          // ignore
        }
      }
      mediaRecorderRef.current = null;
    };
  }, [clearTick, stopStream]);

  return {
    state,
    startRecording,
    stopRecording,
    reset,
    audioBlob,
    error,
    duration,
  };
}
