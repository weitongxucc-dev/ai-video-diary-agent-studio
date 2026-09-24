"use client";

import { AlertCircle, FileText, Link2, LoaderCircle, RefreshCw, Video } from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { AppShell } from "@/components/AppShell";
import { KnowledgeItem, listKnowledgeItems, sourceErrorMessage } from "@/lib/source-api";

function itemIcon(type: string) {
  if (type === "video") return <Video size={16} />;
  if (type === "link") return <Link2 size={16} />;
  return <FileText size={16} />;
}

function itemType(type: string) {
  if (["text", "video", "file", "link"].includes(type)) return ({ text: "文字", video: "视频", file: "文件", link: "链接" } as Record<string, string>)[type];
  return type || "未分类";
}

function formatTime(value: string) {
  return new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }).format(new Date(value));
}

export default function KnowledgePage() {
  const [items, setItems] = useState<KnowledgeItem[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [kind, setKind] = useState("all");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const selected = items.find((item) => item.id === selectedId) ?? null;

  async function refresh() {
    setLoading(true); setError(null);
    try {
      const response = await listKnowledgeItems();
      setItems(response.items);
      if (!selectedId && response.items[0]) setSelectedId(response.items[0].id);
    } catch (cause) {
      setItems([]); setError(sourceErrorMessage(cause));
    } finally { setLoading(false); }
  }

  useEffect(() => { void refresh(); }, []);
  const visibleItems = useMemo(() => items.filter((item) => {
    const matchesKind = kind === "all" || item.content_type === kind;
    const searchable = `${item.title} ${item.body ?? ""} ${item.file_reference ?? ""}`.toLocaleLowerCase();
    return matchesKind && searchable.includes(query.trim().toLocaleLowerCase());
  }), [items, kind, query]);

  return (
    <AppShell inspector={selected ? <KnowledgeInspector item={selected} /> : undefined} inspectorTitle="资料详情">
      <div className="knowledge-workspace">
        <div className="source-content-bar"><nav className="source-content-tabs" aria-label="内容资料视图"><span aria-current="page">我的资料</span><Link href="/sources">资料来源</Link></nav><Link className="modern-primary-button" href="/sources">加资料</Link><button className="shell-icon-button" aria-label="刷新资料" disabled={loading} onClick={() => void refresh()} type="button"><RefreshCw className={loading ? "is-spinning" : ""} size={16} /></button></div>
        {error ? <section className="source-error" role="alert"><AlertCircle size={17} /><div><strong>{error}</strong><p>本地服务恢复、登录或授权完成后再刷新；不会把空结果当作知识库内容。</p></div></section> : null}
        <section className="knowledge-toolbar"><label><span>搜索</span><input onChange={(event) => setQuery(event.target.value)} placeholder="搜索标题、正文或文件引用" value={query} /></label><div role="group" aria-label="内容类型">{[["all", "全部"], ["text", "文字"], ["video", "视频"], ["file", "文件"], ["link", "链接"]].map(([value, label]) => <button className={kind === value ? "is-active" : ""} key={value} onClick={() => setKind(value)} type="button">{label}</button>)}</div></section>
        <section className="knowledge-items"><header><h2>已保存内容</h2><small>{visibleItems.length} 条</small></header>{loading ? <p className="source-panel__state"><LoaderCircle className="is-spinning" size={16} />正在读取本地知识库…</p> : visibleItems.length ? <div className="knowledge-item-list">{visibleItems.map((item) => <button className={`knowledge-item${selectedId === item.id ? " is-selected" : ""}`} key={item.id} data-open-details onClick={() => setSelectedId(item.id)} type="button"><span className="knowledge-item__icon">{itemIcon(item.content_type)}</span><div><strong>{item.title}</strong><p>{item.body || item.file_reference || "没有可预览的正文。"}</p><small>{itemType(item.content_type)} · {item.source_item_id ? "来自信息源" : "我的内容"} · 版本 {item.version}</small></div><time>{formatTime(item.created_at)}</time></button>)}</div> : <div className="knowledge-empty"><FileText size={22} /><div><strong>{error ? "无法读取知识库" : "知识库还没有内容"}</strong><p>{error ? "检查本地服务、登录状态或工作区权限。" : "在“信息源”添加文字、链接、视频或文件引用，再确认保存。"}</p></div></div>}</section>
      </div>
    </AppShell>
  );
}

function KnowledgeInspector({ item }: { item: KnowledgeItem }) {
  return <div className="source-inspector"><span className="source-inspector__status">{itemType(item.content_type)}</span><h3>{item.title}</h3><dl><div><dt>内容来源</dt><dd>{item.source_item_id ? "从信息源确认保存" : "用户已有内容"}</dd></div><div><dt>归属</dt><dd>{item.ownership}</dd></div><div><dt>授权/使用范围</dt><dd>{item.authorization}</dd></div><div><dt>版本</dt><dd>{item.version}</dd></div><div><dt>文件引用</dt><dd>{item.file_reference || "无"}</dd></div></dl><section><h4>内容摘要</h4><p>{item.body || "没有正文摘要；请通过文件引用查看原始内容。"}</p></section></div>;
}
