"use client";

import Link from "next/link";
import { ArrowRight, History, Layers3, Library, Search, WifiOff } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { boundedPreflightReturn, readPreflightDraftScope, resolvePreflightRepairContext, replacePreflightDraftTemplate, sameDraftScope, savePreflightRepairContext, type PreflightRepairContext } from "@/lib/preflight-draft";
import { AppShell } from "@/components/AppShell";
import { HomeCardDialog } from "@/components/HomeCardDialog";
import { setVideoTemplateVisibility } from "@/lib/api";
import type { ContentTemplate, ContentVisibility, VideoTemplate } from "@/lib/types";
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

export function TemplateLibraryWorkspace({ offline, templates, catalog = [] }: { offline: boolean; templates: VideoTemplate[]; catalog?: ContentTemplate[] }) {
  const router = useRouter();
  const rawReturn = useSearchParams()?.get("return_to") ?? null;
  const repairRevision = useRef(0);
  const [repairContext, setRepairContext] = useState<PreflightRepairContext | null>(null);
  const [repairReady, setRepairReady] = useState(false);
  const [repairBusy, setRepairBusy] = useState(false);
  const [repairError, setRepairError] = useState<string | null>(null);
  useEffect(() => {
    let active = true; const request = ++repairRevision.current;
    setRepairContext(null); setRepairReady(false); setRepairError(null); setRepairBusy(false);
    if (rawReturn !== null && !boundedPreflightReturn(rawReturn, window.location.origin)) { setRepairError("返回创作地址无效，没有回退其他草稿。"); return; }
    void readPreflightDraftScope().then(scope => {
      if (!active) return;
      const value = resolvePreflightRepairContext(window.sessionStorage, scope, rawReturn, window.location.origin);
      if (value && rawReturn !== null) savePreflightRepairContext(window.sessionStorage, scope, value.target.draftId, value.draft.template);
      setRepairContext(value); setRepairReady(true);
    }).catch(cause => { if (active) setRepairError(`不能读取修复上下文：${cause instanceof Error ? cause.message : "读取失败"}`); });
    return () => { active = false; repairRevision.current = request + 1; };
  }, [rawReturn]);
  async function handleUseDraftTemplate(item: VideoTemplate) {
    if (!repairContext || !repairReady || repairBusy) return;
    const request = repairRevision.current;
    setRepairBusy(true); setRepairError(null);
    try {
      const scope = await readPreflightDraftScope();
      if (request !== repairRevision.current) return;
      if (!sameDraftScope(scope, repairContext.scope)) throw new Error("成员或工作区已变化，没有修改原草稿。");
      const href = replacePreflightDraftTemplate(window.sessionStorage, repairContext, { id: item.id, version: item.version }, window.location.origin);
      router.push(href);
    } catch (cause) { if (request === repairRevision.current) setRepairError(`草稿模板未更换：${cause instanceof Error ? cause.message : "存储不可用"}`); }
    finally { if (request === repairRevision.current) setRepairBusy(false); }
  }
  const [detailsOpen, setDetailsOpen] = useState(false);
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
  const creationHref = selected ? `/production?new=1&template_id=${encodeURIComponent(selected.id)}&template_version=${selected.version}` : "";
  const useLabel = selected?.origin === "built_in" ? "使用此制作结构" : "使用此模板";
  const useAction = selected ? !repairReady || repairError
    ? <button className={styles.primaryButton} type="button" disabled>用这个做 <ArrowRight size={15} /></button>
    : repairContext
    ? <button className={styles.primaryButton} type="button" disabled={repairBusy || !repairReady || Boolean(repairError)} onClick={() => void handleUseDraftTemplate(selected)}>换用此{selected.origin === "built_in" ? "制作结构" : "模板"}并返回创作 <ArrowRight size={15} /></button>
    : <Link className={styles.primaryButton} href={creationHref}>{useLabel} <ArrowRight size={15} /></Link>
    : null;
  const hasQuery = haystack.length > 0;
  const empty = visible.length === 0;
  const inspector = canAct && selected && settings ? <div className={styles.inspector}>
    <h3>{selected.name} · 第{selected.version}版</h3>
    <TemplateStructurePreview template={selected} contentTemplate={catalog.find(item => `builtin_${item.template_id}` === selected.id)} />
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
    const body = <span className={styles.templateCopy}><strong>{item.name} · 第{item.version}版</strong><span className={own.structureLabel}>{item.origin === "built_in" ? "制作结构" : "制作模板"}</span><small>{item.settings.style_instruction || "复用制作设置，用新主题创作"}</small><span>{item.settings.duration_target_seconds} 秒 · {item.settings.fit_mode === "fit" ? "完整显示" : "填满画面"}</span><span className={own.structureStrip} aria-label="文案结构预览">{item.settings.script_structure?.join(" → ") || (item.settings.script_section_count ? `${item.settings.script_section_count} 段，按新主题重新撰写` : "按新主题生成")}</span></span>;
    if (!canAct) {
      return <div key={key} className={styles.templateCard} data-offline-inert>{body}</div>;
    }
    const isSelected = selected === item;
    return <button type="button" key={key} aria-pressed={isSelected} onClick={() => { setSelectedKey(key); setDetailsOpen(true); }} className={`${styles.templateCard}${isSelected ? ` ${styles.selectedCard}` : ""}`}>{body}</button>;
  })}</div>;

  const emptyStage = hasQuery
    ? <section className={styles.emptyStage}><p>没有找到名称匹配“{trimmed}”的模板。</p><button type="button" onClick={() => setQuery("")}>清除搜索</button></section>
    : <section className={styles.emptyStage}><p>{emptyCopy[view].title} {emptyCopy[view].hint}</p><Link href={emptyCopy[view].href}>{emptyCopy[view].action}</Link></section>;

  return <AppShell><div className={styles.page}>
    {offline ? <section className={styles.capabilityBanner}><span><WifiOff size={16} /></span><div><strong>暂时无法读取模板</strong><p>请检查本地服务后刷新；已保存的模板暂不可操作。</p></div><Link href="/health">检查运行情况</Link></section> : null}
    <div className={styles.viewBar}><nav className={styles.tabs} aria-label="模板视图">{views.map(({ id, label, icon: Icon }) => <button type="button" key={id} aria-pressed={view === id} className={view === id ? styles.activeTab : undefined} onClick={() => { setView(id); setSelectedKey(null); setQuery(""); setDetailsOpen(false); }}><Icon size={15} />{label}</button>)}</nav>
      <label className={own.search}><Search size={15} /><input type="search" aria-label="搜索模板名称" placeholder="搜索模板名称…" value={query} onChange={(event) => setQuery(event.target.value)} /></label>
      {canAct && selected ? <><button className={styles.secondaryButton} type="button" onClick={() => setDetailsOpen(true)}>查看详情</button>{!repairReady || repairError || repairContext ? useAction : <Link className={styles.primaryButton} href={creationHref}>用这个做 <ArrowRight size={15} /></Link>}</> : null}
    </div>
    {repairError ? <p role="alert">{repairError}</p> : null}
    {repairContext ? <p>换用模板保留原目标、时长和人物声音选择，返回后重新检查；不会自动创建任务。</p> : null}
    {empty ? (offline ? null : emptyStage) : grid}
  </div>
    <HomeCardDialog kind="detail" title="模板详情" open={detailsOpen && canAct && Boolean(selected)} onClose={() => setDetailsOpen(false)} footer={selected ? <div className={own.detailFooter}><span>第{selected.version}版</span>{useAction}</div> : null}>{inspector}</HomeCardDialog>
  </AppShell>;
}

// 文案仅演示已保存的段落结构，不引用用户资料，也不代表预览成片。
const structureExamples: Record<string, string[]> = {
  builtin_video_diary: ["今天整理资料时，我发现一个反复出现的问题。", "我记录过程，对比了两种处理方式。", "真正影响效率的是先明确要保留什么。", "下一次先列出目标，再开始整理。"],
  builtin_tool_review: ["这款工具适合快速整理初稿。", "它把零散笔记整理成可编辑的提纲。", "我用同一份测试资料比较了整理前后的结果。", "重要参数仍需要回到原资料核对。", "需要保留来源、再人工审核的团队可以尝试。"],
  builtin_product_intro: ["面对这个使用问题，先明确需要解决什么。", "这款产品用于处理资料中描述的场景。", "展示一项已有资料支持的能力与操作过程。", "说明适用对象、条件和限制。", "准备自己的资料，再选择下一步。"],
  builtin_customer_faq: ["客户问：这个功能应该怎样使用？", "先给出已确认的简短答复。", "展示说明书中的依据和对应条件。", "按已验证的步骤操作，再核对结果。", "遇到例外情况时，补充信息后再判断。"],
  builtin_customer_case: ["这个案例从一个具体的使用问题开始。", "团队按确认方案完成了这些步骤。", "展示可公开的过程资料与记录。", "引用有出处的结果，并说明限制。", "具备相同条件时，才考虑复用做法。"],
  builtin_tutorial_demo: ["把一份已有资料整理成可复用的内容。", "先准备资料和明确的目标。", "按目标整理正文与关键参数。", "核对出处，再保存当前版本。", "不要把示例参数当作真实产品事实。", "保存后用新主题重新创作。"],
};

function TemplateStructurePreview({ template, contentTemplate }: { template: VideoTemplate; contentTemplate?: ContentTemplate }) {
  const { settings } = template;
  const sections = settings.script_structure?.length ? settings.script_structure : Array.from({ length: settings.script_section_count ?? 0 }, (_, index) => `第${index + 1}段`);
  return <section className={own.preview} aria-label="制作结构预览">
    <h4>制作结构 · 第{template.version}版</h4>
    <p>{settings.style_instruction || "按本次主题重新撰写。"}</p>
    {sections.length ? <><small>示例文案 · 展示结构，不是成片</small><ol>{sections.map((section, index) => <li key={`${index}:${section}`}><strong>{section}</strong><p>{structureExamples[template.id]?.[index] ?? `围绕「本次主题」填写${section}的内容，并核对事实出处。`}</p></li>)}</ol></> : <p>此版本未保存固定段落结构，创作时按新主题生成。</p>}
    {contentTemplate ? <section aria-label="必要输入"><h4>需要准备</h4><ul>{contentTemplate.material_requirements.map(requirement => <li key={requirement}>{requirement}</li>)}</ul></section> : null}
    <p>需要准备本次主题、可用资料与素材；声音、人物和制作条件将在新任务中核对。</p>
  </section>;
}
