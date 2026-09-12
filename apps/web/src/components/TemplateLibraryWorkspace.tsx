"use client";

import Link from "next/link";
import { ArrowRight, History, Layers3, Library } from "lucide-react";
import { useState } from "react";
import { AppShell } from "@/components/AppShell";
import type { VideoTemplate } from "@/lib/types";
import styles from "./ProductWorkspace.module.css";

type TemplateView = "mine" | "built-in" | "versions";
const views = [
  { id: "mine", label: "我的模板", icon: Layers3 },
  { id: "built-in", label: "内置模板", icon: Library },
  { id: "versions", label: "版本记录", icon: History },
] as const;

export function TemplateLibraryWorkspace({ offline, templates }: { offline: boolean; templates: VideoTemplate[] }) {
  const [view, setView] = useState<TemplateView>("mine");
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const candidates = templates.filter((item) => view === "built-in" ? item.origin === "built_in" : item.origin === "user");
  const visible = view === "versions" ? candidates : candidates.filter((item) => !candidates.some((other) => other.id === item.id && other.version > item.version));
  const selected = visible.find((item) => `${item.id}:${item.version}` === selectedKey) ?? visible[0];
  const settings = selected?.settings;
  const inspector = selected && settings ? <div className={styles.inspector}>
    <h3>{selected.name} · v{selected.version}</h3>
    <dl>
      <div><dt>来源</dt><dd>{selected.source_task_id ? <Link href={`/tasks/${encodeURIComponent(selected.source_task_id)}`}>查看来源任务</Link> : "内置制作结构"}</dd></div>
      <div><dt>文案风格</dt><dd>{settings.style_instruction || "未指定额外风格"}</dd></div>
      <div><dt>文案结构</dt><dd>{settings.script_structure?.join(" → ") || (settings.script_section_count ? `${settings.script_section_count} 段，按新主题重新撰写` : "按新主题生成")}</dd></div>
      <div><dt>声音</dt><dd>{settings.voice ? `${settings.voice.profile_id} · ${settings.voice.version}` : "预检时选择当前可用声音"}</dd></div>
      <div><dt>画幅与时长</dt><dd>{settings.aspect_ratio} · {settings.duration_target_seconds} 秒 · {settings.language === "zh-CN" ? "中文" : settings.language}</dd></div>
      <div><dt>画面适配</dt><dd>{settings.fit_mode === "fit" ? "完整显示（可能留边）" : "填满画面（居中裁切）"}</dd></div>
      <div><dt>字幕</dt><dd>复用中文样式；正文与时轴重新生成</dd></div>
      <div><dt>素材与审核</dt><dd>按新主题检索；选题、文案和生成仍需确认</dd></div>
    </dl>
    <section className={styles.inspectorNotice}><strong>当前未复用</strong><p>人物口型、封面、双语字幕、模型与成本偏好、发布规则及自动任务。</p></section>
  </div> : null;

  return <AppShell inspector={inspector} inspectorTitle="模板详情"><div className={styles.page}>
    <div className={styles.viewBar}><nav className={styles.tabs} aria-label="模板视图">{views.map(({ id, label, icon: Icon }) => <button type="button" key={id} aria-pressed={view === id} className={view === id ? styles.activeTab : undefined} onClick={() => { setView(id); setSelectedKey(null); }}><Icon size={15} />{label}</button>)}</nav>
      {selected ? <Link className={styles.primaryButton} href={`/production?new=1&template_id=${encodeURIComponent(selected.id)}&template_version=${selected.version}`}>使用模板创作 <ArrowRight size={15} /></Link> : null}
    </div>
    {offline ? <section className={styles.emptyStage}><p>暂时无法读取模板，请检查本地服务后刷新。</p><Link href="/health">检查运行情况</Link></section> : !visible.length ? <section className={styles.emptyStage}><p>还没有保存的模板。从满意成片的“检查与输出”中保存后，即可在这里选择版本复用。</p><Link href="/production">查看创作任务</Link></section> : <div className={styles.templateGrid}>{visible.map((item) => <button type="button" key={`${item.id}:${item.version}`} aria-pressed={selected === item} onClick={() => setSelectedKey(`${item.id}:${item.version}`)} className={`${styles.templateCard}${selected === item ? ` ${styles.selectedCard}` : ""}`}><span className={styles.templateCopy}><strong>{item.name} · v{item.version}</strong><small>{item.settings.style_instruction || "复用制作设置，用新主题创作"}</small><span>{item.settings.duration_target_seconds} 秒 · {item.settings.fit_mode === "fit" ? "完整显示" : "填满画面"}</span></span></button>)}</div>}
  </div></AppShell>;
}
