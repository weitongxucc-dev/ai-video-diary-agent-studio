"use client";
import { useEffect, useRef, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { boundedPreflightReturn, loadPreflightDraft, PREFLIGHT_RESOURCE_PATHS, readPreflightDraftScope } from "@/lib/preflight-draft";
import ui from "./CreateProductionTaskForm.module.css";

export function PreflightDraftReturn() {
  const pathname = usePathname();
  const params = useSearchParams();
  const router = useRouter();
  const raw = params?.get("return_to");
  const revision = useRef(0);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let active = true; ++revision.current;
    setReady(false); setError(null);
    if (!raw || !PREFLIGHT_RESOURCE_PATHS.has(pathname ?? "")) return;
    const target = boundedPreflightReturn(raw, window.location.origin);
    if (!target) { setError("返回创作地址无效，没有打开草稿。"); return; }
    void readPreflightDraftScope().then(scope => {
      if (!active) return;
      const draft = loadPreflightDraft(window.sessionStorage, scope, target.draftId);
      if (!draft || JSON.stringify(draft.template) !== JSON.stringify(target.template)) throw new Error("当前成员与工作区没有这份创作草稿，无法返回。");
      if (active) setReady(true);
    }).catch(cause => { if (active) setError(`不能读取临时草稿：${cause instanceof Error ? cause.message : "读取失败"}`); });
    return () => { active = false; ++revision.current; };
  }, [raw, pathname]);
  if (!raw || !PREFLIGHT_RESOURCE_PATHS.has(pathname ?? "")) return null;
  async function goBack() {
    setReady(false);
    const request = revision.current;
    try {
      const target = boundedPreflightReturn(raw!, window.location.origin);
      if (!target) throw new Error("返回地址无效。");
      const scope = await readPreflightDraftScope();
      if (request !== revision.current) return;
      const draft = loadPreflightDraft(window.sessionStorage, scope, target.draftId);
      if (!draft || JSON.stringify(draft.template) !== JSON.stringify(target.template)) throw new Error("成员或工作区已改变，没有读取其他人的草稿。");
      router.push(target.href);
    } catch (cause) { if (request === revision.current) setError(`不能返回创作：${cause instanceof Error ? cause.message : "读取失败"}`); }
  }
  return <span className={ui.returnEntry}>{error ? <span role="alert">{error}</span> : <button type="button" disabled={!ready} onClick={() => void goBack()}>返回创作</button>}</span>;
}
