"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  finalizeDevicePreparation, getDevicePreparation, getDeviceResourceStatus, prepareNarrationResources,
  type DevicePreparationReceipt, type DeviceResourceStatus, type NarrationPreparationRequest,
} from "@/lib/api/device-preparation";
import { getVoiceProfile, listPronunciationEntries } from "@/lib/media-profiles-api";
import { ApiRequestError } from "@/lib/api/transport";
import { LOCAL_MEMBER_TOKEN_STORAGE_KEY, readLocalMemberToken } from "@/lib/local-member-auth";

type Voice = { profileId: string; version: string; language: "zh" | "en" };
type Attempt = {
  payload: NarrationPreparationRequest;
  workId?: string;
  settlement?: "ready" | "failed" | "rejected";
};

const terminal = new Set(["done", "failed", "cancelled"]);
const deadline = () => AbortSignal.timeout(30_000);
const scopeChanged = "当前账号、工作区或设备已变化。原准备请求仍保留。";

function readAttempt(key: string, voice: Voice): Attempt | null {
  const raw = localStorage.getItem(key);
  if (!raw) return null;
  try {
    const attempt = JSON.parse(raw) as Attempt;
    const shape = attempt.payload?.shape;
    if (attempt.payload?.kind === "narration"
        && /^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(attempt.payload.request_id)
        && shape?.voice_profile_id === voice.profileId && shape.voice_version === voice.version
        && shape.language === voice.language && typeof shape.text === "string" && Boolean(shape.text.trim())
        && Number.isFinite(shape.output_duration_seconds) && shape.output_duration_seconds > 0
        && Array.isArray(shape.pronunciation_references)
        && shape.pronunciation_references.every(entry => entry && typeof entry.id === "string" && typeof entry.version === "string")
        && (attempt.workId === undefined || typeof attempt.workId === "string" && Boolean(attempt.workId))
        && (attempt.settlement === undefined || attempt.settlement === "rejected" && !attempt.workId
          || Boolean(attempt.workId) && ["ready", "failed"].includes(attempt.settlement))) return attempt;
  } catch { /* A damaged local record must not be replaced by a new submission. */ }
  throw new Error("本机保存的原准备记录无法核对。");
}

function storeAttempt(key: string, attempt: Attempt) {
  localStorage.setItem(key, JSON.stringify(attempt));
}

function errorDetail(error: unknown): string {
  if (error instanceof ApiRequestError) {
    try {
      const detail: unknown = JSON.parse(error.message)?.detail;
      if (typeof detail === "string") return detail;
    } catch { /* Legacy services can return plain text. */ }
    return error.status === 422 ? "当前样句无法用于准备，请检查文字后再试。" : error.message;
  }
  return error instanceof Error && error.name !== "TimeoutError"
    ? error.message : "暂时无法确认设备准备状态，请核对原请求。";
}

async function narrationShape(voice: Voice, text: string): Promise<NarrationPreparationRequest["shape"]> {
  const [profile, dictionary] = await Promise.all([
    getVoiceProfile(voice.profileId, voice.version), listPronunciationEntries(),
  ]);
  if (profile.id !== voice.profileId || profile.version !== voice.version) {
    throw new Error("无法核对选定的声线版本。");
  }
  const seconds = (text.match(/[\u4e00-\u9fff]/g)?.length ?? 0) / 4
    + (text.match(/[A-Za-z0-9]+/g)?.length ?? 0) / 2.5;
  return {
    voice_profile_id: voice.profileId, voice_version: voice.version, language: voice.language,
    text, output_duration_seconds: Math.max(6, Math.ceil(seconds * 1.5 + 2)),
    // Standalone voice preview uses workspace and selected person's terms.
    pronunciation_references: dictionary.items.filter(entry => entry.workspace_id === profile.workspace_id &&
      (entry.scope === "workspace" && entry.scope_reference === null
        || entry.scope === "person" && entry.scope_reference === profile.person_profile_id))
      .map(({ id, version }) => ({ id, version })),
  };
}

export function useVoiceSamplePreparation({ profileId, version, language, sourceKey }: Voice & { sourceKey?: string }) {
  const [status, setStatus] = useState<DeviceResourceStatus | null>(null);
  const [attempt, setAttempt] = useState<Attempt | null>(null);
  const [receipt, setReceipt] = useState<DevicePreparationReceipt | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [identityChanged, setIdentityChanged] = useState(false);
  const mounted = useRef(false);
  const operation = useRef(false);
  const lastScope = useRef<string | null>(null);

  const keyFor = useCallback((scope: string) =>
    "yaohe:voice-preparation:v1:" + JSON.stringify([scope, profileId, version, language, ...(sourceKey ? [sourceKey] : [])]),
    [profileId, version, language, sourceKey]);

  const refresh = useCallback(async (expected?: string) => {
    const token = readLocalMemberToken();
    const value = await getDeviceResourceStatus(deadline());
    if (!mounted.current || token !== readLocalMemberToken()) throw new Error(scopeChanged);
    if (lastScope.current !== value.actor_scope) {
      if (lastScope.current !== null) setIdentityChanged(true);
      lastScope.current = value.actor_scope;
      setAttempt(null);
      setReceipt(null);
    }
    setStatus(value);
    if (expected && value.actor_scope !== expected) throw new Error(scopeChanged);
    return value;
  }, []);

  const run = useCallback(async (work: () => Promise<void>) => {
    if (operation.current) return;
    operation.current = true;
    setBusy(true);
    setError("");
    try { await work(); }
    catch (cause) { if (mounted.current) setError(errorDetail(cause)); }
    finally {
      operation.current = false;
      if (mounted.current) setBusy(false);
    }
  }, []);

  const follow = useCallback(async (saved: Attempt, scope: string, key: string) => {
    if (!saved.workId || saved.settlement) return;
    const token = readLocalMemberToken();
    const current = await getDevicePreparation(saved.workId, scope, deadline());
    if (!mounted.current || readLocalMemberToken() !== token || lastScope.current !== scope) return;
    setReceipt(current);
    if (!terminal.has(current.state)) return;
    await refresh(scope);
    const result = await finalizeDevicePreparation(saved.workId, scope, deadline());
    if (result.state !== current.state || result.normal_work_started !== false
        || result.profile_published !== (result.state === "done")) {
      throw new Error("本次准备尚未取得有效的启用回执，请继续核对原请求。");
    }
    const settled: Attempt = { ...saved, settlement: result.profile_published ? "ready" : "failed" };
    // Retain verified outcomes under the original scope even if the view closed.
    if (readAttempt(key, { profileId, version, language })?.payload.request_id === saved.payload.request_id) {
      storeAttempt(key, settled);
    }
    if (!mounted.current || readLocalMemberToken() !== token || lastScope.current !== scope) return;
    setAttempt(settled);
    setReceipt(result);
    await refresh(scope);
  }, [language, profileId, refresh, version]);

  const read = useCallback(() => run(async () => {
    const current = await refresh();
    const key = keyFor(current.actor_scope);
    const saved = readAttempt(key, { profileId, version, language });
    setAttempt(previous => previous?.payload.request_id === saved?.payload.request_id
      && previous?.workId === saved?.workId && previous?.settlement === saved?.settlement ? previous : saved);
    if (saved) await follow(saved, current.actor_scope, key);
  }), [follow, keyFor, language, profileId, refresh, run, version]);

  useEffect(() => {
    mounted.current = true;
    void read();
    const onFocus = () => void read();
    const onStorage = (event: StorageEvent) => {
      if (!event.key || event.key === LOCAL_MEMBER_TOKEN_STORAGE_KEY || event.key === keyFor(lastScope.current ?? "")) void read();
    };
    window.addEventListener("focus", onFocus);
    window.addEventListener("storage", onStorage);
    return () => {
      mounted.current = false;
      window.removeEventListener("focus", onFocus);
      window.removeEventListener("storage", onStorage);
    };
  }, [keyFor, read]);

  useEffect(() => {
    if (busy || error || !attempt?.workId || attempt.settlement) return;
    const timer = window.setTimeout(() => void read(), 1500);
    return () => window.clearTimeout(timer);
  }, [attempt, busy, error, read, receipt]);

  const submit = (text: string, newAttempt = false) => run(async () => {
    const current = await refresh(status?.actor_scope);
    const scope = current.actor_scope;
    const key = keyFor(scope);
    const voice = { profileId, version, language };
    const previousRequestId = newAttempt ? attempt?.payload.request_id : undefined;
    const perform = async () => {
      await refresh(scope);
      let saved = readAttempt(key, voice);
      const replaceSettled = Boolean(saved?.settlement && saved.payload.request_id === previousRequestId);
      let firstSubmission = false;
      if (saved?.workId && !replaceSettled) {
        setAttempt(saved);
        await follow(saved, scope, key);
        return;
      }
      if (!saved || replaceSettled) {
        if (!current.preparation_configured || current.profile_status !== "available") {
          throw new Error("请在桌面设置完成本机资源准备后再试。");
        }
        const shape = await narrationShape(voice, text);
        await refresh(scope);
        saved = { payload: { kind: "narration", request_id: crypto.randomUUID(), shape } };
        storeAttempt(key, saved);
        firstSubmission = true;
        setReceipt(null);
      }
      if (saved.settlement) return;
      setAttempt(saved);
      const token = readLocalMemberToken();
      let result: DevicePreparationReceipt;
      try { result = await prepareNarrationResources(saved.payload, scope, deadline()); }
      catch (cause) {
        if (firstSubmission && cause instanceof ApiRequestError && cause.status === 422
            && readAttempt(key, voice)?.payload.request_id === saved.payload.request_id) {
          const rejected: Attempt = { ...saved, settlement: "rejected" };
          storeAttempt(key, rejected);
          if (mounted.current && token === readLocalMemberToken() && lastScope.current === scope) setAttempt(rejected);
        }
        throw cause;
      }
      const submitted = { ...saved, workId: result.id };
      if (readAttempt(key, voice)?.payload.request_id === saved.payload.request_id) storeAttempt(key, submitted);
      if (!mounted.current || token !== readLocalMemberToken() || lastScope.current !== scope) return;
      setAttempt(submitted);
      setReceipt(result);
      if (terminal.has(result.state)) await follow(submitted, scope, key);
    };
    // Multiple mounted workbench panels/tabs can name the same audition.
    if (navigator.locks) await navigator.locks.request(key, perform);
    else await perform();
  });

  return { status, attempt, receipt, busy, error, identityChanged, read, submit, refresh };
}
