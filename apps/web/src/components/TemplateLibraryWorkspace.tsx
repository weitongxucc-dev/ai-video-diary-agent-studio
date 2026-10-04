"use client";

import Link from "next/link";
import { ArrowRight, History, Layers3, Library, Search, WifiOff } from "lucide-react";
import { useState } from "react";
import { AppShell } from "@/components/AppShell";
import { setVideoTemplateVisibility } from "@/lib/api";
import type { ContentVisibility, VideoTemplate } from "@/lib/types";
import styles from "./ProductWorkspace.module.css";
import own from "./TemplateLibraryWorkspace.module.css";

type TemplateView = "mine" | "built-in" | "versions";
const views = [
  { id: "mine", label: "我的模板", icon: Layers3 },
  { id: "built-in", label: "内置模板", icon: Library },
  { id: "versions", label: "版本记录", icon: History },
] as const;

const emptyCopy: Record<TemplateView, { title: string; hint: string; href: string; action: string }> = {
  mine: {
    title: "还没有保存的模板。",
    hint: "从满意成片的“检查与输出”中保存后，即可在这里选择版本复用。",
    href: "/production",
    action: "查看创作任务",
  },
  "built-in": {
    title: "暂无可用的内置模板。",
    hint: "内置模板由本地服务提供，请检查本地服务后刷新。",
    href: "/health",
    action: "检查运行情况",
  },
  versions: {
    title: "还没有模板版本记录。",
    hint: "保存过模板后，各版本会在这里列出，可回到任一版本复用。",
    href: "/production",
    action: "查看创作任务",
  },
};

export function TemplateLibraryWorkspace({ offline, templates }: { offline: boolean; templates: VideoTemplate[] }) {
  const [view, setView] = useState<TemplateView>("mine");
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  // 以服务端回读的值为准：保存成功才改这里，失败保持原值不动。
  const [visibilityByTemplate, setVisibilityByTemplate] = useState<Record<string, ContentVisibility>>({});
  const [visibilityBusy, setVisibilityBusy] = useState(false);
  const [visibilityError, setVisibilityError] = useState<string | null>(null);
  const visibilityOf = (item: VideoTemplate): ContentVisibility =>
    visibilityByTemplate[item.id] ?? item.visibility;

  async function changeTemplateVisibility(item: VideoTemplate, next: ContentVisibility) {
    setVisibilityBusy(true);
    setVisibilityError(null);
    try {
      const saved = await setVideoTemplateVisibility(item.id, next);
      // 用接口返回值，不猜：服务端说存成什么，界面就显示什么。
      setVisibilityByTemplate((current) => ({
        ...current,
        [item.id]: saved.item.visibility,
      }));
    } catch (err) {
      setVisibilityError(err instanceof Error ? err.message : "共享设置没有保存成功，请再试一次。");
    } finally {
      setVisibilityBusy(false);
    }
  }
  const canAct = !offline;
  const candidates = templates.filter((item) => view === "built-in" ? item.origin === "built_in" : item.origin === "user");
  const byVersion = view === "versions" ? candidates : candidates.filter((item) => !candidates.some((other) => other.id === item.id && other.version > item.version));
  const trimmed = query.trim();
  const haystack = trimmed.toLowerCase();
  const visible = haystack ? byVersion.filter((item) => item.name.toLowerCase().includes(haystack)) : byVersion;
  const selected = visible.find((item) => `${item.id}:${item.version}` === selectedKey) ?? visible[0];
  const settings = selected?.settings;
  const hasQuery = haystack.length > 0;
  const empty = visible.length === 0;
  const inspector = canAct && selected && settings ? <div className={styles.inspector}>
    <h3>{selected.name} · 第{selected.version}版</h3>
    <dl>
      <div><dt>来源</dt><dd>{selected.source_task_id ? <Link href={`/tasks/${encodeURIComponent(selected.source_task_id)}`}>查看原片</Link> : "内置制作结构"}</dd></div>
      <div><dt>文案风格</dt><dd>{settings.style_instruction || "未指定额外风格"}</dd></div>
      <div><dt>文案结构</dt><dd>{settings.script_structure?.join(" → ") || (settings.script_section_count ? `${settings.script_section_count} 段，按新主题重新撰写` : "按新主题生成")}</dd></div>
      <div><dt>声音</dt><dd>{settings.voice ? "无法读取原声音名称" : "制作前选择可用声音"}</dd></div>
      <div><dt>画幅与时长</dt><dd>{settings.aspect_ratio} · {settings.duration_target_seconds} 秒 · {settings.language === "zh-CN" ? "中文" : settings.language}</dd></div>
      <div><dt>画面适配</dt><dd>{settings.fit_mode === "fit" ? "完整显示（可能留边）" : "填满画面（居中裁切）"}</dd></div>
      <div><dt>调色</dt><dd>{settings.color_style ? `曝光 ${settings.color_style.exposure} · 对比度 ${settings.color_style.contrast} · 饱和度 ${settings.color_style.saturation} · 冷暖 ${settings.color_style.temperature}` : settings.color_style === null ? "原镜头调色不同，不统一复用" : "未保存调色，使用新素材原色"}</dd></div>
      <div><dt>字幕</dt><dd>复用字幕样式；正文与时轴重新生成</dd></div>
      <div><dt>素材与审核</dt><dd>按新主题检索；选题、文案和生成仍需确认</dd></div>
      <div><dt>可见范围</dt><dd>{visibilityOf(selected) === "shared" ? "团队共享" : "仅自己可见"}</dd></div>
    </dl>
    {selected.origin === "user" ? <section className={own.visibilitySection}>
      {visibilityOf(selected) === "shared"
        ? <button disabled={visibilityBusy} onClick={() => void changeTemplateVisibility(selected, "private")} type="button">收回为仅自己可见</button>
        : <button disabled={visibilityBusy} onClick={() => void changeTemplateVisibility(selected, "shared")} type="button">共享给团队</button>}
      {visibilityError ? <p role="alert">{visibilityError}</p> : null}
    </section> : <section className={own.visibilitySection}><p>内置模板由系统提供，不单独设置可见范围。</p></section>}
    <section className={styles.inspectorNotice}><strong>当前未复用</strong><p>逐镜头调色、美颜、音轨编排、转场、图文叠加、人物口型、封面、双语字幕、模型与成本偏好、发布规则及自动任务。</p></section>
  </div> : null;

  const grid = <div className={styles.templateGrid}>{visible.map((item) => {
    const key = `${item.id}:${item.version}`;
    const body = <span className={styles.templateCopy}><strong>{item.name} · 第{item.version}版</strong><small>{item.settings.style_instruction || "复用制作设置，用新主题创作"}</small><span>{item.settings.duration_target_seconds} 秒 · {item.settings.fit_mode === "fit" ? "完整显示" : "填满画面"}</span></span>;
    if (!canAct) {
      return <div key={key} className={styles.templateCard} data-offline-inert>{body}</div>;
    }
    const isSelected = selected === item;
    return <button type="button" key={key} aria-pressed={isSelected} data-open-details onClick={() => setSelectedKey(key)} className={`${styles.templateCard}${isSelected ? ` ${styles.selectedCard}` : ""}`}>{body}</button>;
  })}</div>;

  const emptyStage = hasQuery
    ? <section className={styles.emptyStage}><p>没有找到名称匹配“{trimmed}”的模板。</p><button type="button" onClick={() => setQuery("")}>清除搜索</button></section>
    : <section className={styles.emptyStage}><p>{emptyCopy[view].title} {emptyCopy[view].hint}</p><Link href={emptyCopy[view].href}>{emptyCopy[view].action}</Link></section>;

  return <AppShell inspector={inspector} inspectorTitle="模板详情"><div className={styles.page}>
    {offline ? <section className={styles.capabilityBanner}><span><WifiOff size={16} /></span><div><strong>暂时无法读取模板</strong><p>请检查本地服务后刷新；已保存的模板暂不可操作。</p></div><Link href="/health">检查运行情况</Link></section> : null}
    <div className={styles.viewBar}><nav className={styles.tabs} aria-label="模板视图">{views.map(({ id, label, icon: Icon }) => <button type="button" key={id} aria-pressed={view === id} className={view === id ? styles.activeTab : undefined} onClick={() => { setView(id); setSelectedKey(null); setQuery(""); }}><Icon size={15} />{label}</button>)}</nav>
      <label className={own.search}><Search size={15} /><input type="search" aria-label="搜索模板名称" placeholder="搜索模板名称…" value={query} onChange={(event) => setQuery(event.target.value)} /></label>
      {canAct && selected ? <Link className={styles.primaryButton} href={`/production?new=1&template_id=${encodeURIComponent(selected.id)}&template_version=${selected.version}`}>用这个做 <ArrowRight size={15} /></Link> : null}
    </div>
    {empty ? (offline ? null : emptyStage) : grid}
  </div></AppShell>;
}
