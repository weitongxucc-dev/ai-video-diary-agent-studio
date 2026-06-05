import Link from "next/link";
import { AppShell } from "@/components/AppShell";
import { MetricCard } from "@/components/MetricCard";
import { PixelIcon } from "@/components/PixelIcon";
import { getKnowledgeNotes, getSystemStatus } from "@/lib/api";
import type { KnowledgeNote, SystemStatus } from "@/lib/types";

export const dynamic = "force-dynamic";

type KnowledgePageProps = {
  searchParams?: Promise<{ q?: string }>;
};

async function loadKnowledge(query?: string): Promise<{
  notes: KnowledgeNote[];
  status: SystemStatus | null;
  offline: boolean;
}> {
  try {
    const [notes, status] = await Promise.all([
      getKnowledgeNotes(query),
      getSystemStatus(),
    ]);
    return { notes, status, offline: false };
  } catch {
    return { notes: [], status: null, offline: true };
  }
}

function formatDate(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", {
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    month: "2-digit",
    timeZone: "Asia/Shanghai",
  }).format(new Date(value));
}

export default async function KnowledgePage({ searchParams }: KnowledgePageProps) {
  const params = await searchParams;
  const query = params?.q?.trim();
  const { notes, status, offline } = await loadKnowledge(query);

  return (
    <AppShell>
      <div className="workspace-page">
        <section className="workspace-main pixel-card">
          <div className="workspace-header">
            <div>
              <span className="section-kicker">知识库</span>
              <h1>Obsidian 本地索引</h1>
              <p>
                {offline
                  ? "暂时无法连接本地 API。"
                  : `当前索引 ${status?.obsidian.markdown_count ?? notes.length} 篇 Markdown。`}
              </p>
            </div>
            <Link className="pixel-button secondary" href="/">
              返回工作台
            </Link>
          </div>

          <form className="workspace-search" action="/knowledge">
            <input name="q" placeholder="搜索标题、路径、摘要" defaultValue={query ?? ""} />
            <button className="pixel-button" type="submit">
              搜索
            </button>
          </form>

          <div className="workspace-list">
            {notes.length > 0 ? (
              notes.map((note) => (
                <article className="workspace-row" key={note.ref}>
                  <PixelIcon name="doc" />
                  <div>
                    <strong>{note.title}</strong>
                    <span>{note.ref} · {formatDate(note.modified_at)}</span>
                    {note.tags.length > 0 ? (
                      <span>标签：{note.tags.join(" / ")}</span>
                    ) : null}
                    {note.wikilinks.length > 0 ? (
                      <span>双链：{note.wikilinks.slice(0, 4).join(" / ")}</span>
                    ) : null}
                    <p>{note.excerpt || "这篇笔记暂无正文摘要。"}</p>
                  </div>
                </article>
              ))
            ) : (
              <div className="workspace-empty">
                <PixelIcon name="folder" />
                <strong>{offline ? "API 未连接" : "没有匹配笔记"}</strong>
                <p>{status?.obsidian.path ?? "请在设置里检查 Obsidian 路径。"}</p>
              </div>
            )}
          </div>
        </section>

        <aside className="workspace-side">
          <MetricCard icon="folder" value={String(status?.obsidian.markdown_count ?? 0)} label="Markdown" tone="blue" />
          <MetricCard icon="check" value={status?.obsidian.exists ? "正常" : "异常"} label="Vault 状态" tone="green" />
          <div className="pixel-card side-note">
            <h2>路径</h2>
            <code>{status?.obsidian.path ?? "--"}</code>
          </div>
        </aside>
      </div>
    </AppShell>
  );
}
