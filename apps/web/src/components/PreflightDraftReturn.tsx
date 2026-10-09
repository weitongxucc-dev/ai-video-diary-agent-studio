"use client";
import { useEffect, useRef, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { boundedPreflightReturn, PREFLIGHT_RESOURCE_PATHS, readPreflightDraftScope, resolvePreflightRepairContext, sameDraftScope, savePreflightRepairContext, type PreflightRepairContext } from "@/lib/preflight-draft";
import ui from "./CreateProductionTaskForm.module.css";

export function PreflightDraftReturn() {
  const pathname = usePathname();
  const params = useSearchParams();
  const router = useRouter();
  const raw = params?.get("return_to") ?? null;
  const revision = useRef(0);
  const [context, setContext] = useState<PreflightRepairContext | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let active = true; const request = ++revision.current;
    setContext(null); setError(null); setPending(false);
    if (!PREFLIGHT_RESOURCE_PATHS.has(pathname ?? "")) return;
    if (raw !== null && !boundedPreflightReturn(raw, window.location.origin)) { setError("返回创作地址无效，没有回退其他草稿。"); return; }
    void readPreflightDraftScope().then(scope => {
      if (!active) return;
      const value = resolvePreflightRepairContext(window.sessionStorage, scope, raw, window.location.origin);
      if (value && raw !== null) savePreflightRepairContext(window.sessionStorage, scope, value.target.draftId, value.draft.template);
      setContext(value);
    }).catch(cause => { if (active) setError(`不能读取临时草稿：${cause instanceof Error ? cause.message : "读取失败"}`); });
    return () => { active = false; revision.current = request + 1; };
  }, [raw, pathname]);
  if (!PREFLIGHT_RESOURCE_PATHS.has(pathname ?? "") || (!context && !error)) return null;
  async function goBack() {
    setPending(true);
    const request = revision.current;
    try {
      const scope = await readPreflightDraftScope();
      if (request !== revision.current) return;
      if (!context || !sameDraftScope(scope, context.scope)) throw new Error("成员或工作区已改变，没有读取其他人的草稿。");
      const current = resolvePreflightRepairContext(window.sessionStorage, scope, raw, window.location.origin);
      if (!current || current.target.href !== context.target.href) throw new Error("成员、工作区或修复定位已改变，没有读取其他人的草稿。");
      router.push(current.target.href);
    } catch (cause) { if (request === revision.current) setError(`不能返回创作：${cause instanceof Error ? cause.message : "读取失败"}`); }
    finally { if (request === revision.current) setPending(false); }
  }
  return <span className={ui.returnEntry}>{error ? <span role="alert">{error}</span> : <button type="button" disabled={pending} onClick={() => void goBack()}>返回创作</button>}</span>;
}
