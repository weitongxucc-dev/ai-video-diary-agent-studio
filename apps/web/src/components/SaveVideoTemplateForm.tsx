"use client";

import Link from "next/link";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { listVideoTemplates, saveVideoTemplate } from "@/lib/api";
import type { VideoTemplate } from "@/lib/types";
import styles from "./CreationTaskWorkspace.module.css";

export function SaveVideoTemplateForm({ taskId, onEditingChange }: { taskId: string; onEditingChange: (editing: boolean) => void }) {
  const [opened, setOpened] = useState(false);
  const [templates, setTemplates] = useState<VideoTemplate[]>([]);
  const [target, setTarget] = useState("");
  const [name, setName] = useState("");
  const [style, setStyle] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState<VideoTemplate | null>(null);
  const formRef = useRef<HTMLFormElement>(null);
  useEffect(() => { if (opened) formRef.current?.focus(); }, [opened]);
  async function open() {
    setOpened(true); onEditingChange(true); setPending(true); setError("");
    try { setTemplates((await listVideoTemplates()).items.filter((item) => item.origin === "user")); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "无法读取已有模板，请重试。"); }
    finally { setPending(false); }
  }
  async function save(event: FormEvent) {
    event.preventDefault(); if (pending) return;
    setPending(true); setError("");
    const previous = templates.find((item) => item.id === target);
    try { setSaved(await saveVideoTemplate(taskId, { name: name.trim(), style_instruction: style.trim(), ...(previous ? { template_id: previous.id, expected_version: previous.version } : {}) })); onEditingChange(false); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "模板保存失败，请重试。"); }
    finally { setPending(false); }
  }
  if (!opened) return <button className={styles.secondaryButton} type="button" onClick={() => void open()}>保存为视频模板</button>;
  if (saved) return <p role="status">已保存“{saved.name}”v{saved.version}。<Link href="/templates">前往我的模板</Link></p>;
  const latest = templates.filter((item) => !templates.some((other) => other.id === item.id && other.version > item.version));
  return <form ref={formRef} tabIndex={-1} onSubmit={save} className="creation-plan-form" aria-label="保存视频模板">
    <label><span>保存方式</span><select disabled={pending} value={target} onChange={(event) => { const value = event.target.value; setTarget(value); const item = latest.find((item) => item.id === value); if (item) { setName(item.name); setStyle(item.settings.style_instruction); } }}><option value="">新建我的模板</option>{latest.map((item) => <option key={item.id} value={item.id}>{item.name} · 保存为 v{item.version + 1}</option>)}</select></label>
    <label><span>模板名称</span><input required maxLength={120} value={name} disabled={pending} onChange={(event) => setName(event.target.value)} /></label>
    <label><span>新文案的风格要求（可选）</span><textarea maxLength={500} value={style} disabled={pending} onChange={(event) => setStyle(event.target.value)} placeholder="例如：短句，先讲结论，再给操作步骤" /></label>
    <p>保存声音版本、文案段数、语言、画幅、时长、画面适配与字幕样式。所有镜头调色一致时保存调色；逐镜头差异不合并。混合适配将统一为填满画面。不复制当前内容、裁切位置、封面或完成状态。</p>
    {error ? <p role="alert">{error}</p> : null}
    <div className="creation-plan-form__actions"><button className="modern-primary-button" disabled={pending || !name.trim()} type="submit">{pending ? "正在处理…" : "保存模板设置"}</button><button className={styles.secondaryButton} disabled={pending} type="button" onClick={() => { setOpened(false); onEditingChange(false); }}>取消</button></div>
  </form>;
}
