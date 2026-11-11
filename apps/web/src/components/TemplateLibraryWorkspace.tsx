"use client";

import Link from "next/link";
import { ArrowRight, ChevronRight, History, Layers3, Library, Pencil, Search, WifiOff } from "lucide-react";
import { useEffect, useId, useLayoutEffect, useRef, useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { boundedPreflightReturn, readPreflightDraftScope, resolvePreflightRepairContext, replacePreflightDraftTemplate, sameDraftScope, savePreflightRepairContext, type PreflightRepairContext } from "@/lib/preflight-draft";
import { useWorkspacePage, useWorkspaceSearchParams } from "./workbench/context";
import { AppShell } from "@/components/AppShell";
import { HomeCardDialog } from "@/components/HomeCardDialog";
import { createVideoTemplateVersion } from "@/lib/video-template-api";
import { setVideoTemplateVisibility } from "@/lib/api";
import type { ContentTemplate, ContentVisibility, VideoTemplate } from "@/lib/types";
import styles from "./ProductWorkspace.module.css";
import own from "./TemplateLibraryWorkspace.module.css";
import {VisualTemplatePreview} from "./VisualTemplatePreview";
import {VisualTemplateLibraryEditor} from './VisualTemplateEditor';

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
  const pageActive = useWorkspacePage()?.active ?? true;
  const activeRef = useRef(pageActive);
  useLayoutEffect(() => { activeRef.current = pageActive; }, [pageActive]);
  const rawReturn = useWorkspaceSearchParams().get("return_to");
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
    if (!activeRef.current || !repairContext || !repairReady || repairBusy) return;
    const request = repairRevision.current;
    setRepairBusy(true); setRepairError(null);
    try {
      const scope = await readPreflightDraftScope();
      if (request !== repairRevision.current || !activeRef.current) return;
      if (!sameDraftScope(scope, repairContext.scope)) throw new Error("成员或工作区已变化，没有修改原草稿。");
      const href = replacePreflightDraftTemplate(window.sessionStorage, repairContext, { id: item.id, version: item.version }, window.location.origin);
      router.push(href);
    } catch (cause) { if (request === repairRevision.current) setRepairError(`草稿模板未更换：${cause instanceof Error ? cause.message : "存储不可用"}`); }
    finally { if (request === repairRevision.current) setRepairBusy(false); }
  }
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [savedVersions, setSavedVersions] = useState<VideoTemplate[]>([]);
  const [editing, setEditing] = useState<VideoTemplate | null>(null);
  const editorRevision = useRef(0);
  const [saveNotice, setSaveNotice] = useState<string | null>(null);
  const allTemplates = [...templates.filter(item => !savedVersions.some(saved => saved.id === item.id && saved.version === item.version)), ...savedVersions];
  const [view, setView] = useState<TemplateView>("mine");
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  // 以服务端回读的值为准：保存成功才改这里，失败保持原值不动。
  const [resourceCategory,setResourceCategory] = useState("all");
  const [visibilityByTemplate, setVisibilityByTemplate] = useState<Record<string, ContentVisibility>>({});
  const [visibilityBusy, setVisibilityBusy] = useState(false);
  const [visibilityError, setVisibilityError] = useState<{ id: string; message: string } | null>(null);
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
      setVisibilityError({id:item.id, message:err instanceof Error ? err.message : "共享设置没有保存成功，请再试一次。"});
    } finally {
      setVisibilityBusy(false);
    }
  }
  const canAct = !offline;
  const candidates = allTemplates.filter((item) => view === "built-in" ? item.origin === "built_in" : item.origin === "user");
  const byVersion = view === "versions" ? candidates : candidates.filter((item) => !candidates.some((other) => other.id === item.id && other.version > item.version));
  const trimmed = query.trim();
  const haystack = trimmed.toLowerCase();
  const categories: Record<string,string> = {structure:'制作结构',composition:'整片组合',title:'标题与姓名条',chapter:'章节',callout:'标注',data:'数据与知识',product:'产品参数',ending:'结尾',social:'互动引导',reaction:'综艺',chat:'对话',news:'新闻',location:'时间地点',festival:'节日'};
  const categoryOf = (item:VideoTemplate) => item.settings.visual?.category ?? (item.settings.kind==='composition'?'composition':'structure');
  const availableCategories = [...new Set(byVersion.map(categoryOf))];
  const visible = byVersion.filter(item=>(resourceCategory==='all'||categoryOf(item)===resourceCategory)&&(!haystack||item.name.toLowerCase().includes(haystack)));
  const selected = selectedKey ? allTemplates.find(item => `${item.id}:${item.version}` === selectedKey) : visible[0];
  const settings = selected?.settings;
  const creationHref = selected ? `/production?new=1&template_id=${encodeURIComponent(selected.id)}&template_version=${selected.version}` : "";
  const useLabel = selected?.origin === "built_in" ? "使用此制作结构" : "使用此模板";
  const useAction = selected?.settings.kind === 'visual_component'
    ? <Link className={styles.primaryButton} href="/production">到作品中使用 <ArrowRight size={15}/></Link>
    : selected ? !repairReady
    ? <button className={styles.primaryButton} type="button" disabled>用这个做 <ArrowRight size={15} /></button>
    : repairContext
    ? <button className={styles.primaryButton} type="button" disabled={repairBusy || !repairReady} onClick={() => void handleUseDraftTemplate(selected)}>换用此{selected.origin === "built_in" ? "制作结构" : "模板"}并返回创作 <ArrowRight size={15} /></button>
    : <Link className={styles.primaryButton} href={creationHref}>{useLabel} <ArrowRight size={15} /></Link>
    : null;
  const hasQuery = haystack.length > 0;
  const empty = visible.length === 0;
  const inspector = canAct && selected && settings ? selected.settings.visual
    ? <VisualTemplateLibraryEditor key={`${selected.id}:${selected.version}`} template={selected} onSaved={saved=>{
      setSavedVersions(current=>[...current.filter(item=>!(item.id===saved.id&&item.version===saved.version)),saved]);
      setSelectedKey(`${saved.id}:${saved.version}`);
    }}/>
    : <div className={styles.inspector}>
    <h3>{selected.name} · 第{selected.version}版</h3>
    <p>{settings.visual_components?.length ? `已保存 ${settings.visual_components.length} 个动画组件，使用时填写本次主题的输入。` : null}</p>
    <TemplateStructurePreview template={selected} contentTemplate={catalog.find(item => `builtin_${item.template_id}` === selected.id)} />
    <dl>
      <div><dt>来源</dt><dd>{selected.source_task_id ? <Link href={`/tasks/${encodeURIComponent(selected.source_task_id)}`}>查看原片</Link> : selected.origin === "built_in" ? "内置制作结构" : "来源任务未记录"}</dd></div>
      <div><dt>文案风格</dt><dd>{settings.style_instruction || "未指定额外风格"}</dd></div>
      <div><dt>文案结构</dt><dd>{settings.script_structure?.join(" → ") || (settings.script_section_count ? `${settings.script_section_count} 段，按新主题重新撰写` : "按新主题生成")}</dd></div>
      <div><dt>声音</dt><dd>{settings.voice ? "无法读取原声音名称" : "制作前选择可用声音"}</dd></div>
      <div><dt>画幅与时长</dt><dd>{settings.aspect_ratio} · {settings.duration_target_seconds} 秒 · {{"zh-CN":"中文","en-US":"英文","zh-CN/en-US":"中英混读"}[settings.language]}</dd></div>
      <div><dt>画面适配</dt><dd>{settings.fit_mode === "fit" ? "完整显示（可能留边）" : "填满画面（居中裁切）"}</dd></div>
      <div><dt>调色</dt><dd>{settings.color_style ? `曝光 ${settings.color_style.exposure} · 对比度 ${settings.color_style.contrast} · 饱和度 ${settings.color_style.saturation} · 冷暖 ${settings.color_style.temperature}` : settings.color_style === null ? "原镜头调色不同，不统一复用" : "未保存调色，使用新素材原色"}</dd></div>
      <div><dt>字幕</dt><dd>复用字幕样式；正文与时轴重新生成</dd></div>
      <div><dt>素材与确认</dt><dd>按新主题检索；选题、文案和生成仍需确认</dd></div>
      <div><dt>可见范围</dt><dd>{visibilityOf(selected) === "shared" ? "团队共享" : "仅自己可见"}</dd></div>
    </dl>
    {selected.origin === "user" ? selected.can_edit === true ? <button type="button" className={own.editButton} onClick={() => { ++editorRevision.current; setEditing(selected); setSaveNotice(null); }}><Pencil size={15}/>编辑名称与文案风格</button> : <p className={own.readOnly}>此模板只读；可用固定版本创作。</p> : null}
    {saveNotice ? <p role="status">{saveNotice}</p> : null}
    {editing ? <TemplateVersionEditor key={`${editing.id}:${editing.version}`} open={pageActive && detailsOpen} template={editing} onClose={() => { ++editorRevision.current;setEditing(null); }} onSaved={(saved, ticket) => {
      setSavedVersions(current => [...current.filter(item => !(item.id === saved.id && item.version === saved.version)), saved]);
      if (ticket === editorRevision.current) { setSelectedKey(`${saved.id}:${saved.version}`);setSaveNotice(`已保存「${saved.name}」第${saved.version}版。`);setEditing(null); }
    }} ticket={editorRevision.current}/> : null}
    {selected.origin === "user" && selected.can_edit === true ? <section className={own.visibilitySection}>
      {visibilityOf(selected) === "shared"
        ? <button disabled={visibilityBusy} onClick={() => void changeTemplateVisibility(selected, "private")} type="button">收回为仅自己可见</button>
        : <button disabled={visibilityBusy} onClick={() => void changeTemplateVisibility(selected, "shared")} type="button">共享给团队</button>}
      {visibilityError?.id === selected.id ? <p role="alert">{visibilityError.message}</p> : null}
    </section> : selected.origin === "built_in" ? <section className={own.visibilitySection}><p>内置模板由系统提供，不单独设置可见范围。</p></section> : null}
    <section className={styles.inspectorNotice}><strong>当前未复用</strong><p>逐镜头调色、美颜、音轨编排、转场、静态图文、人物口型、封面、双语字幕、模型与成本偏好、发布规则及自动任务。</p></section>
  </div> : null;

  const grid = <div className={own.templateList} data-gallery={visible.some(item=>Boolean(item.settings.visual)) || undefined}>{visible.map((item) => {
    const key = `${item.id}:${item.version}`;
    const structure = item.settings.script_structure?.join(" → ") || (item.settings.script_section_count ? `${item.settings.script_section_count} 段，按新主题重新撰写` : "按新主题生成");
    const body = <><span className={own.previewMark} aria-label={item.settings.visual?"动画组件":"文案结构预览"} title={item.settings.visual?categories[item.settings.visual.category]:structure}><Layers3 size={19}/></span><span className={own.rowCopy}><strong>{item.name} · 第{item.version}版{item.settings.visual ? " · 动画组件" : item.settings.kind === "composition" ? " · 整片组合" : " · 制作结构"}</strong><small>{item.settings.visual ? `${categories[item.settings.visual.category]} · ${item.settings.visual.width}×${item.settings.visual.height} · ${item.settings.visual.duration_seconds} 秒 · ${item.settings.visual.provenance[0].license}` : `${item.settings.aspect_ratio} · ${item.settings.duration_target_seconds} 秒 · ${item.settings.script_section_count ?? item.settings.script_structure?.length ?? "未固定"} 段`}</small></span><ChevronRight size={16} aria-hidden="true"/></>;
    if (!canAct) return <div key={key} className={own.templateRow} data-offline-inert>{body}</div>;
    return <article className={own.templateCard} key={key}>{item.settings.visual ? <div className={own.effectPreview}><VisualTemplatePreview definition={item.settings.visual} compact/></div> : null}<button type="button" aria-pressed={selected?.id === item.id && selected.version === item.version} onClick={() => { ++editorRevision.current;setEditing(null);setSaveNotice(null);setSelectedKey(key);setDetailsOpen(true); }} className={own.templateRow}>{body}</button></article>;
  })}</div>;

  const emptyStage = hasQuery
    ? <section className={styles.emptyStage}><p>没有找到名称匹配“{trimmed}”的模板。</p><button type="button" onClick={() => setQuery("")}>清除搜索</button></section>
    : <section className={styles.emptyStage}><p>{emptyCopy[view].title} {emptyCopy[view].hint}</p><Link href={emptyCopy[view].href}>{emptyCopy[view].action}</Link></section>;

  return <AppShell><div className={styles.page}>
    {offline ? <section className={styles.capabilityBanner}><span><WifiOff size={16} /></span><div><strong>暂时无法读取模板</strong><p>请检查本地服务后刷新；已保存的模板暂不可操作。</p></div><Link href="/health">检查运行情况</Link></section> : null}
    <div className={`${styles.viewBar} ${own.toolbar}`}><nav className={styles.tabs} aria-label="模板视图">{views.map(({ id, label, icon: Icon }) => <button type="button" key={id} aria-pressed={view === id} className={view === id ? styles.activeTab : undefined} onClick={() => { ++editorRevision.current;setEditing(null);setSaveNotice(null);setView(id); setResourceCategory("all"); setSelectedKey(null); setQuery(""); setDetailsOpen(false); }}><Icon size={15} />{label}</button>)}</nav>
      <label data-input-group className={own.search}><Search size={15} /><input type="search" aria-label="搜索模板名称" placeholder="搜索模板名称…" value={query} onChange={(event) => setQuery(event.target.value)} /></label>
      {canAct && selected ? <><button className={styles.secondaryButton} type="button" onClick={() => { setSelectedKey(`${selected.id}:${selected.version}`);setDetailsOpen(true); }}>查看详情</button>{selected.settings.visual || !repairReady || repairError || repairContext ? useAction : <Link className={styles.primaryButton} href={creationHref}>用这个做 <ArrowRight size={15} /></Link>}</> : null}
    </div>
    {!offline && availableCategories.length>1 ? <label className={own.resourceFilter}>资源类型 <select aria-label="资源类型" value={resourceCategory} onChange={event=>{setResourceCategory(event.target.value);setSelectedKey(null);}}><option value="all">全部资源（{byVersion.length}）</option>{availableCategories.map(key=><option value={key} key={key}>{categories[key]}</option>)}</select></label> : null}
    {repairError ? <p role="alert">{repairError}</p> : null}
    {repairContext ? <p>换用模板保留原目标、时长和人物声音选择，返回后重新检查；不会自动创建任务。</p> : null}
    {empty ? (offline ? null : emptyStage) : grid}
  </div>
    <HomeCardDialog kind="detail" title="模板详情" open={pageActive && detailsOpen && canAct && Boolean(selected)} onClose={() => { ++editorRevision.current;setEditing(null);setDetailsOpen(false); }} footer={selected ? <div className={own.detailFooter}><span>第{selected.version}版</span>{useAction}</div> : null}>{inspector}</HomeCardDialog>
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

function TemplateVersionEditor({ template, ticket, open, onClose, onSaved }: {
  open: boolean; template: VideoTemplate; ticket: number; onClose: () => void; onSaved: (saved: VideoTemplate, ticket: number) => void;
}) {
  const formId = useId();
  const [name,setName] = useState(template.name);
  const [style,setStyle] = useState(template.settings.style_instruction);
  const [pending,setPending] = useState(false);
  const [error,setError] = useState<string | null>(null);
  async function submit(event: FormEvent) {
    event.preventDefault();if (pending || !name.trim()) return;
    setPending(true);setError(null);
    try { const saved=await createVideoTemplateVersion(template.id,{expected_version:template.version,name:name.trim(),style_instruction:style.trim()});onSaved(saved,ticket); }
    catch(cause) { setError(cause instanceof Error ? cause.message : "模板新版本没有保存，请重试。"); }
    finally { setPending(false); }
  }
  return <HomeCardDialog kind="config" title="编辑模板" open={open} onClose={onClose} footer={<><button type="button" onClick={onClose}>取消</button><button className={styles.primaryButton} type="submit" form={formId} disabled={pending || !name.trim() || name.trim().length > 80}>{pending ? "正在保存…" : "保存新版本"}</button></>}>
    <form id={formId} onSubmit={submit} className={own.editForm} aria-label="编辑模板字段">
      <label>模板名称<input aria-label="模板名称" required maxLength={80} disabled={pending} value={name} onChange={event=>setName(event.target.value)}/></label>
      <label>新文案的风格要求<textarea aria-label="新文案的风格要求" maxLength={500} disabled={pending} value={style} onChange={event=>setStyle(event.target.value)}/></label>
      <p>名称 1–80 字；风格要求最多 500 字，可留空。</p>
      <p>从第{template.version}版保存新版本，保留这一版的其他设置和来源。风格要求供新文案参考；使用时仍需核对本次资料与制作条件。</p>
      {error ? <p role="alert">{error} 名称和风格草稿已保留。</p> : null}
    </form>
  </HomeCardDialog>;
}
