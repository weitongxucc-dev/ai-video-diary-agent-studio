"use client";

import Link from "next/link";
import {
  ArrowRight, BookOpenCheck, Clock3, GraduationCap, History, Layers3,
  Library, ListChecks, Sparkles, Video, Wrench,
} from "lucide-react";
import { type ComponentType, useMemo, useState } from "react";
import { AppShell } from "@/components/AppShell";
import type { ContentTemplate } from "@/lib/types";
import styles from "./ProductWorkspace.module.css";

type TemplateView = "built-in" | "mine" | "versions";
type Icon = ComponentType<{ size?: number; strokeWidth?: number }>;

const viewLabels: Array<{ id: TemplateView; label: string; icon: Icon }> = [
  { id: "built-in", label: "内置模板", icon: Library },
  { id: "mine", label: "我的模板", icon: Layers3 },
  { id: "versions", label: "版本记录", icon: History },
];

function templateIcon(templateId: string): Icon {
  if (templateId === "tool_review") return Wrench;
  if (templateId === "tutorial_demo") return GraduationCap;
  return Video;
}

function templateTone(templateId: string): string {
  if (templateId === "tool_review") return styles.toneAmber;
  if (templateId === "tutorial_demo") return styles.toneGreen;
  return styles.toneBlue;
}

function join(items: string[], limit = items.length): string {
  return items.slice(0, limit).join(" · ");
}

function templateGoal(template: ContentTemplate): string {
  return `按照“${template.name}”的表达方向，制作一条${template.recommended_duration}的中文视频。目标：${template.scenario}`;
}

function UnavailableLibrary({ view }: { view: Exclude<TemplateView, "built-in"> }) {
  const mine = view === "mine";
  return (
    <section className={styles.emptyStage}>
      <span className={styles.emptyIcon}>{mine ? <Layers3 size={22} /> : <History size={22} />}</span>
      <div>
        <h2>{mine ? "还没有可复用的视频模板" : "模板版本记录尚未建立"}</h2>
        <p>{mine ? "当前可以查看内置制作结构，但只有真实创作任务跑通并保存后，才会出现在这里。" : "模板保存与版本事实源尚未接入；这里不会用内置模板假装历史版本。"}</p>
      </div>
      <Link className={styles.secondaryButton} href={mine ? "/production" : "/templates"}>{mine ? "查看创作任务" : "返回内置模板"}</Link>
    </section>
  );
}

export function TemplateLibraryWorkspace({ offline, templates }: { offline: boolean; templates: ContentTemplate[] }) {
  const [view, setView] = useState<TemplateView>("built-in");
  const [selectedId, setSelectedId] = useState(templates[0]?.template_id ?? null);
  const selected = useMemo(() => templates.find((template) => template.template_id === selectedId) ?? templates[0] ?? null, [selectedId, templates]);

  const inspector = selected ? (
    <div className={styles.inspector}>
      <span className={styles.objectEyebrow}>当前模板</span>
      <h3>{selected.name}</h3>
      <p>{selected.scenario}</p>
      <dl>
        <div><dt>推荐时长</dt><dd>{selected.recommended_duration}</dd></div>
        <div><dt>选题结构</dt><dd>{join(selected.topic_structure)}</dd></div>
        <div><dt>文案结构</dt><dd>{join(selected.script_structure)}</dd></div>
        <div><dt>素材需要</dt><dd>{join(selected.material_requirements)}</dd></div>
        <div><dt>字幕方式</dt><dd>{selected.subtitle_style}</dd></div>
      </dl>
      <section className={styles.inspectorNotice}><strong>当前能力边界</strong><p>此操作会把模板方向带入新任务目标；固定模板保存、版本记录和自动任务尚未接入。</p></section>
      <Link className={styles.primaryButton} href={`/production?new=1&goal=${encodeURIComponent(templateGoal(selected))}`}>以此方向开始 <ArrowRight size={15} /></Link>
    </div>
  ) : (
    <div className={styles.inspectorEmpty}><BookOpenCheck size={20} /><strong>没有可检查的模板</strong><p>连接本地服务后，模板的结构与素材要求会显示在这里。</p></div>
  );

  return (
    <AppShell inspector={inspector} inspectorTitle="模板详情">
      <div className={styles.page}>
        <div className={styles.viewBar}><nav aria-label="模板视图" className={styles.tabs}>
          {viewLabels.map((item) => { const ItemIcon = item.icon; return <button aria-pressed={view === item.id} className={view === item.id ? styles.activeTab : undefined} key={item.id} onClick={() => setView(item.id)} type="button"><ItemIcon size={15} /> {item.label}</button>; })}
        </nav><Link className={styles.primaryButton} href="/production?new=1"><Sparkles size={16} />开始创作</Link></div>

        {view === "built-in" ? (
          offline || templates.length === 0 ? (
            <section className={styles.emptyStage}>
              <span className={styles.emptyIcon}><BookOpenCheck size={22} /></span>
              <div><h2>{offline ? "暂时无法读取模板" : "还没有内置模板"}</h2><p>{offline ? "本地 API 恢复后会显示真实模板定义；页面没有替换成演示数据。" : "当前服务没有返回可用的制作结构。"}</p></div>
              <Link className={styles.secondaryButton} href="/health">检查运行情况</Link>
            </section>
          ) : (
            <div className={styles.templateCatalog}>
              <div className={styles.sectionHeading}><h2>选择制作结构</h2><p>{templates.length} 种 · 选择后查看详情</p></div>
              <div className={styles.templateGrid}>
                {templates.map((template) => {
                  const TemplateIcon = templateIcon(template.template_id);
                  const selectedTemplate = selected?.template_id === template.template_id;
                  return (
                    <button aria-pressed={selectedTemplate} className={`${styles.templateCard} ${templateTone(template.template_id)}${selectedTemplate ? ` ${styles.selectedCard}` : ""}`} key={template.template_id} onClick={() => setSelectedId(template.template_id)} type="button">
                      <span className={styles.templateVisual} aria-hidden="true"><span className={styles.templateGlyph}><TemplateIcon size={22} strokeWidth={1.7} /></span><i /><i /><i /></span>
                      <span className={styles.templateCopy}><span className={styles.templateMeta}><Clock3 size={13} /> {template.recommended_duration}</span><strong>{template.name}</strong><small>{template.scenario}</small><span className={styles.structureLine}><ListChecks size={14} /> {join(template.script_structure, 3)}</span></span>
                      <ArrowRight className={styles.cardArrow} size={16} />
                    </button>
                  );
                })}
              </div>
            </div>
          )
        ) : <UnavailableLibrary view={view} />}
      </div>
    </AppShell>
  );
}
