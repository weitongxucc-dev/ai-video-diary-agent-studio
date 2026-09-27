"use client";

import { AlertCircle, ArrowRight, BookOpen, FileText, Link2, LoaderCircle, RefreshCw, Search, Video } from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";
import { AppShell } from "@/components/AppShell";
import type { KnowledgeItem } from "@/lib/source-api";
import styles from "./KnowledgeWorkspace.module.css";

type KnowledgeDisplayItem = KnowledgeItem & {
  exampleCategory?: string;
  exampleSummary?: string;
  exampleParameters?: string;
  exampleIssue?: string;
};
const categories = ["产品知识", "公司文档", "菜单条目", "销售话术"] as const;

function itemIcon(type: string) {
  if (type === "video") return <Video size={21} />;
  if (type === "link") return <Link2 size={21} />;
  if (type === "text") return <BookOpen size={21} />;
  return <FileText size={21} />;
}
function itemType(type: string) {
  if (type === "text") return "文字";
  if (type === "video") return "视频";
  if (type === "file") return "文件";
  if (type === "link") return "链接";
  return type || "未分类格式";
}
function formatTime(value: string) {
  return new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit" }).format(new Date(value));
}

export function KnowledgeWorkspace({ items, loading, error, onRefresh, exampleMode = false, exampleLabel }: {
  items: KnowledgeDisplayItem[];
  loading: boolean;
  error: string | null;
  onRefresh: () => void;
  exampleMode?: boolean;
  exampleLabel?: string;
}) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState<string>("全部");
  const selected = items.find((item) => item.id === selectedId) ?? null;
  const visibleItems = useMemo(() => items.filter((item) => {
    const matchesCategory = category === "全部" || (exampleMode && item.exampleCategory === category);
    const searchable = [item.title, item.body, item.file_reference].filter(Boolean).join(" ").toLocaleLowerCase();
    return matchesCategory && searchable.includes(query.trim().toLocaleLowerCase());
  }), [items, category, query, exampleMode]);
  const inspector = selected ? <KnowledgeInspector exampleMode={exampleMode} item={selected} /> : undefined;

  return <AppShell inspector={inspector} inspectorTitle="来源详情">
    <div className={styles.page}>
      {exampleMode ? <div className={styles.exampleBanner}>{exampleLabel ?? "隔离视觉示例"} · 所有名称、分类、摘要和状态均非真实服务数据</div> : null}
      <div className={styles.top}>
        <nav aria-label="知识用途分类" className={styles.categories}>
          {categories.map((name, index) => <button aria-pressed={category === name} className={styles.category} key={name} onClick={() => setCategory(category === name ? "全部" : name)} type="button">
            <span className={styles.categoryIcon}>{[<BookOpen key="a" size={20} />, <FileText key="b" size={20} />, <FileText key="c" size={20} />, <BookOpen key="d" size={20} />][index]}</span>
            <strong>{name}</strong>
            <small>{exampleMode ? items.filter((item) => item.exampleCategory === name).length + " 条示例来源" : "用途待分类"}</small>
          </button>)}
        </nav>
        <div className={styles.actions}><Link href="/sources">添加内容 <Link2 size={14} /></Link></div>
      </div>
      <div className={styles.searchBar}>
        <label><Search size={16} /><input aria-label="查找来源" onChange={(event) => setQuery(event.target.value)} placeholder="搜索标题、正文或文件引用" value={query} /></label>
        <button aria-pressed={category === "全部"} onClick={() => setCategory("全部")} type="button">全部来源</button>
        <button aria-label="刷新知识库" disabled={loading} onClick={onRefresh} type="button"><RefreshCw className={loading ? styles.spinning : undefined} size={16} /></button>
      </div>
      {error ? <section className={styles.error} role="alert"><AlertCircle size={17} /><div><strong>{error}</strong><p>本地服务恢复或工作区授权确认后再刷新。</p></div></section> : null}
      <div className={styles.columns}>
        <section className={styles.card}>
          <div className={styles.cardHeading}><div><h2>来源封面</h2><small>{loading ? "正在读取" : error ? "读取失败" : category !== "全部" && !exampleMode ? "用途分类尚未接入" : visibleItems.length + " 条" + (exampleMode ? "示例" : "当前工作区") + "结果"} · 选来源查看内容</small></div></div>
          {loading ? <div className={styles.empty}><LoaderCircle className={styles.spinning} size={18} />正在读取知识库…</div>
            : category !== "全部" && !exampleMode ? <div className={styles.empty}>现有知识条目没有用途分类字段，不能按文件格式推断“{category}”。<button onClick={() => setCategory("全部")} type="button">查看全部来源</button></div>
            : visibleItems.length ? <div className={styles.grid}>{visibleItems.map((item) => <button className={styles.cover} data-open-details key={item.id} onClick={() => setSelectedId(item.id)} type="button">
                <span className={styles.coverArt}>{itemIcon(item.content_type)}<small>{itemType(item.content_type)}</small></span>
                <strong>{item.title}</strong><small>{exampleMode ? item.exampleCategory : "用途未分类"} · 版本 {item.version}</small>
                <span className={styles.coverStatus}>{exampleMode ? item.exampleIssue ?? "示例状态" : "核对状态未接入"}</span>
              </button>)}</div>
            : <div className={styles.empty}>{error ? "无法读取知识库" : items.length ? "没有匹配的来源" : "知识库还没有内容"}{!items.length && !error ? <Link href="/sources">去信息源添加内容</Link> : null}</div>}
        </section>
        <section className={styles.card}>
          <div className={styles.cardHeading}><div><h2>主题关系</h2><small>来源与创作引用</small></div></div>
          <div className={styles.relationEmpty}><BookOpen size={20} /><strong>主题与引用关系尚未接入</strong><p>现有条目记录了来源关联，但没有可核对的主题、参数冲突或任务引用关系。</p></div>
          <div className={styles.question}><h3>从来源中找答案</h3><p>问答和来源召回尚未接入。可先打开封面核对已保存正文。</p></div>
        </section>
      </div>
    </div>
  </AppShell>;
}

function KnowledgeInspector({ item, exampleMode }: { item: KnowledgeDisplayItem; exampleMode: boolean }) {
  return <div className={styles.inspector}>
    {exampleMode ? <div className={styles.exampleBanner}>示例详情 · 不会保存或引用到任务</div> : null}
    <header><strong>{item.title}</strong><small>{itemType(item.content_type)} · 版本 {item.version} · {formatTime(item.created_at)}</small></header>
    <div className={styles.compare}>
      <section><h3>{exampleMode ? "原始来源片段 · 示例" : "已保存正文"}</h3><p>{item.body || "这个条目没有可显示的正文。"}</p></section>
      <section><h3>{exampleMode ? "AI 摘要 · 示例" : "摘要"}</h3><p>{exampleMode ? item.exampleSummary ?? "示例暂无摘要" : "尚无可核对的摘要来源；不能把正文当作 AI 摘要。"}</p></section>
    </div>
    <section className={styles.detailSection}><h3>参数与出处</h3><p>{exampleMode ? item.exampleParameters ?? "示例暂无参数" : "结构化参数尚未接入。"}</p><dl><div><dt>来源</dt><dd>{item.source_item_id ? "已关联信息源" : "当前条目无信息源关联"}</dd></div><div><dt>文件引用</dt><dd>{item.file_reference || "无"}</dd></div><div><dt>归属</dt><dd>{item.ownership}</dd></div><div><dt>授权范围</dt><dd>{item.authorization}</dd></div><div><dt>版本</dt><dd>{item.version}</dd></div></dl></section>
    <section className={styles.detailSection}><h3>核对与人工纠正</h3><p>{exampleMode ? item.exampleIssue ?? "示例未标记问题" : "冲突检测与人工纠正保存尚未接入；请核对原始来源后再用于创作。"}</p><Link href="/sources">查看信息源 <ArrowRight size={14} /></Link></section>
  </div>;
}

