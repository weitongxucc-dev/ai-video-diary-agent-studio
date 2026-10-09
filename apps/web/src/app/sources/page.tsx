"use client";

import { AlertCircle, CheckCircle2, ExternalLink, FileText, Link2, LoaderCircle, Plus, RefreshCw, Save, Upload } from "lucide-react";
import Link from "next/link";
import { FormEvent, type ReactNode, useEffect, useMemo, useState } from "react";
import { AppShell } from "@/components/AppShell";
import styles from "./SourcesPage.module.css";
import {
  createManualSourceItem,
  createSourceConnector,
  captureWebSnapshot,
  importKnowledgeFile,
  listSourceConnectors,
  listSourceItems,
  saveSourceItemToKnowledge,
  sourceErrorMessage,
  SourceApiError,
  type ContentUseState,
  SourceConnector,
  SourceItem,
  SourceRun,
  syncSourceConnector,
} from "@/lib/source-api";

const platforms = [
  { key: "douyin", label: "抖音", available: false },
  { key: "xiaohongshu", label: "小红书", available: false },
  { key: "bilibili", label: "哔哩哔哩", available: false },
  { key: "youtube", label: "油管", available: false },
  { key: "product_hunt", label: "产品社区", available: true },
  { key: "external_urls", label: "网页订阅", available: true },
];

function platformLabel(value: string) {
  if (value === "manual") return "手工添加";
  return platforms.find((platform) => platform.key === value)?.label ?? "其他信息源";
}

function sourceLabel(value: string) {
  return value === "manual" ? "手工添加" : platformLabel(value);
}

function useStateLabel(value: ContentUseState) {
  return ({ available: "可用于创作", reference_only: "仅供参考", excluded: "不使用", not_for_automation: "不用于自动任务", not_for_publish: "不用于发布" })[value];
}

function frequencyLabel(value: string) {
  return value === "on_demand" ? "按需" : value;
}

function permissionLabel(value: string) {
  if (value === "write:source_items") return "可添加信息条目";
  if (value === "read:source_items") return "可读取信息条目";
  return "已授权访问";
}

function connectorState(connector: SourceConnector) {
  if (["ready", "connected"].includes(connector.connection_status)) return { label: "已连接", tone: "success" };
  if (connector.connection_status === "capability_unavailable") return { label: "尚未支持", tone: "warning" };
  if (connector.connection_status === "authentication_required") return { label: "需要登录", tone: "danger" };
  if (connector.connection_status === "access_denied") return { label: "无权限", tone: "danger" };
  if (connector.connection_status === "rate_limited") return { label: "被限流", tone: "warning" };
  if (connector.connection_status === "configuration_required") return { label: "需要配置", tone: "warning" };
  if (connector.connection_status === "failed") return { label: "同步失败", tone: "danger" };
  return { label: "待同步", tone: "idle" };
}

function formatTime(value: string | null) {
  if (!value) return "暂无记录";
  return new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }).format(new Date(value));
}

// 抓取时刻要精确到秒：同一天的两次抓取（V1/V2）只有这样才分得开。
function formatFetchTime(value: string | null) {
  if (!value) return "无抓取记录";
  return new Intl.DateTimeFormat("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(new Date(value));
}

export default function SourcesPage() {
  const [entryMode, setEntryMode] = useState<"text" | "file" | "web">("text");
  const [manualType, setManualType] = useState("text");
  const [subscriptionOpen, setSubscriptionOpen] = useState(false);
  const [subscriptionPlatform, setSubscriptionPlatform] = useState("external_urls");
  const [fileName, setFileName] = useState<string | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);
  const [savedReceipt, setSavedReceipt] = useState<{ sourceItemId: string; knowledgeItemId: string; useState: ContentUseState } | null>(null);
  const [connectors, setConnectors] = useState<SourceConnector[]>([]);
  const [items, setItems] = useState<SourceItem[]>([]);
  const [selectedConnectorId, setSelectedConnectorId] = useState<string | null>(null);
  const [selectedItemId, setSelectedItemId] = useState<string | null>(null);
  const [lastRuns, setLastRuns] = useState<Record<string, SourceRun>>({});
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const selectedConnector = connectors.find((connector) => connector.id === selectedConnectorId) ?? null;
  const selectedItem = items.find((item) => item.id === selectedItemId) ?? null;

  async function refresh(connectorId: string | null = selectedConnectorId) {
    setLoading(true);
    setError(null);
    try {
      const [connectorResponse, itemResponse] = await Promise.all([listSourceConnectors(), listSourceItems(connectorId ?? undefined)]);
      setConnectors(connectorResponse.items);
      setItems(itemResponse.items);
      if (!connectorId && connectorResponse.items[0]) setSelectedConnectorId(connectorResponse.items[0].id);
    } catch (cause) {
      setError(sourceErrorMessage(cause));
      setConnectors([]);
      setItems([]);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { void refresh(selectedConnectorId); }, [selectedConnectorId]);

  const platformEntries = useMemo(() => platforms.map((platform) => ({ ...platform, connector: connectors.find((connector) => connector.platform === platform.key) ?? null })), [connectors]);

  async function addConnector(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    setBusy("create-connector"); setError(null); setNotice(null); setSavedReceipt(null);
    try {
      const connector = await createSourceConnector({ platform: String(form.get("platform") ?? ""), protocol: "https", account_or_rule: String(form.get("account_or_rule") ?? ""), frequency: String(form.get("frequency") ?? "每天"), permissions: ["读取公开内容"] });
      setConnectors((current) => [connector, ...current.filter((item) => item.id !== connector.id)]);
      setSelectedConnectorId(connector.id); setNotice("信息源已保存，可执行同步。");
      formElement.reset();
    } catch (cause) { setError(sourceErrorMessage(cause)); }
    finally { setBusy(null); }
  }

  async function addManualItem(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    setBusy("manual-item"); setError(null); setNotice(null); setSavedReceipt(null);
    try {
      const license = String(form.get("license") ?? "").trim();
      const item = await createManualSourceItem({ title: String(form.get("title") ?? ""), content_type: String(form.get("content_type") ?? "text"), source_url: String(form.get("source_url") ?? "") || null, body: String(form.get("body") ?? "") || null, subtitles: null, media_references: [], file_reference: String(form.get("file_reference") ?? "") || null, author: null, ...(license ? { license } : {}), use_state: String(form.get("use_state") ?? "available") as ContentUseState });
      setSelectedConnectorId(item.connector_id); setItems((current) => [item, ...current]); setSelectedItemId(item.id); setNotice("信息条目已添加，可查看正文并保存到知识库。"); formElement.reset(); setManualType("text");
    } catch (cause) { setError(sourceErrorMessage(cause)); }
    finally { setBusy(null); }
  }

  async function captureWebPage(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    // 先记住表单：await 之后 currentTarget 已经失效，reset 会报错。
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    setBusy("web-snapshot"); setError(null); setNotice(null); setSavedReceipt(null);
    try {
      const item = await captureWebSnapshot({ url: String(form.get("url") ?? ""), use_state: String(form.get("use_state") ?? "available") as ContentUseState });
      // 选中快照自己的连接器，否则列表刷新会跳回第一个旧信息源，快照立刻被刷掉。
      setSelectedConnectorId(item.connector_id); setItems((current) => [item, ...current]); setSelectedItemId(item.id);
      setNotice("网页正文已存为快照，可查看正文并保存到知识库。");
      formElement.reset();
    } catch (cause) { setError(sourceErrorMessage(cause)); }
    finally { setBusy(null); }
  }

  async function importFile(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    const input = formElement.elements.namedItem("file") as HTMLInputElement;
    const file = input.files?.[0];
    const validation = validateKnowledgeFile(file);
    if (!file || validation) { setFileError(validation); return; }
    setFileError(null);
    setBusy("import-file"); setError(null); setNotice(null); setSavedReceipt(null);
    try {
      const buffer = await file.arrayBuffer();
      let binary = "";
      const bytes = new Uint8Array(buffer);
      const chunk = 0x8000;
      for (let index = 0; index < bytes.length; index += chunk) {
        binary += String.fromCharCode(...bytes.subarray(index, index + chunk));
      }
      const item = await importKnowledgeFile({ filename: file.name, content_base64: btoa(binary), use_state: String(form.get("use_state") ?? "available") as ContentUseState });
      setItems((current) => [item, ...current]);
      // 列表按当前平台筛选，导入后要落在文件所属的来源上。
      setSelectedConnectorId(item.connector_id);
      setSelectedItemId(item.id);
      setNotice(`已解析《${item.title}》：全文 ${(item.body ?? "").length} 字，出处 ${item.sections.length} 处，参数 ${item.parameters.length} 项。可查看解析结果并保存到知识库。`);
      formElement.reset(); setFileName(null);
    } catch (cause) { setError(sourceErrorMessage(cause)); }
    finally { setBusy(null); }
  }

  async function sync(connector: SourceConnector) {
    setBusy(`sync-${connector.id}`); setError(null); setNotice(null); setSavedReceipt(null);
    try {
      const response = await syncSourceConnector(connector.id);
      setLastRuns((current) => ({ ...current, [connector.id]: response.run }));
      setConnectors((current) => current.map((item) => item.id === connector.id ? response.connector : item)); setNotice(`同步完成，获取 ${response.run.item_count} 条内容。`);
      const itemResponse = await listSourceItems(selectedConnectorId ?? undefined); setItems(itemResponse.items);
    } catch (cause) {
      const run = cause instanceof SourceApiError ? cause.run : null;
      if (run) setLastRuns((current) => ({ ...current, [connector.id]: run }));
      setError(sourceErrorMessage(cause)); void refresh(selectedConnectorId);
    } finally { setBusy(null); }
  }

  async function saveToKnowledge(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (!selectedItem) return;
    const source = selectedItem;
    setBusy(`save-${source.id}`); setError(null); setNotice(null); setSavedReceipt(null);
    try {
      const saved = await saveSourceItemToKnowledge(source.id, {});
      if (!saved.id || !saved.version || saved.saved_source_item_id !== source.id ||
          !["available", "reference_only", "excluded", "not_for_automation", "not_for_publish"].includes(saved.use_state)) {
        throw new Error("保存回执与当前来源不一致；原信息条目仍保留，可重试。");
      }
      setSavedReceipt({ sourceItemId: source.id, knowledgeItemId: saved.id, useState: saved.use_state });
      setNotice(saved.use_state === "excluded"
        ? `已登记“${source.title}”并回读确认；当前标记为不使用，因此不会进入知识检索。`
        : `已回读确认“${source.title}”保存到知识库，使用范围：${useStateLabel(saved.use_state)}。`);
    }
    catch (cause) { setError(cause instanceof Error ? cause.message : sourceErrorMessage(cause)); }
    finally { setBusy(null); }
  }

  return (
    <AppShell inspector={selectedItem ? <SourceItemInspector item={selectedItem} busy={busy} onSave={saveToKnowledge} /> : selectedConnector ? <SourceInspector connector={selectedConnector} lastRun={lastRuns[selectedConnector.id] ?? null} /> : undefined} inspectorTitle={selectedItem ? "信息条目详情" : "信息源详情"}>
      <div className="source-workspace">
        {error ? <section className="source-error" role="alert"><AlertCircle size={17} /><div><strong>{error}</strong><p>已有信息不会被改写；修复连接、登录或权限后再重试。</p></div></section> : null}
        {notice ? <p className="source-notice" role="status">{notice} {savedReceipt?.sourceItemId === selectedItemId && savedReceipt.useState !== "excluded" ? <Link href="/knowledge">查看我的资料</Link> : null}</p> : null}
        <div className="source-content-bar"><nav className="source-content-tabs" aria-label="内容资料视图"><Link href="/knowledge">我的资料</Link><span aria-current="page">资料来源</span></nav><button className="modern-primary-button" disabled={loading} onClick={() => void refresh(selectedConnectorId)} type="button"><RefreshCw className={loading ? "is-spinning" : ""} size={16} />刷新</button></div>
        <section className={styles.entry} aria-label="添加内容">
          <nav className={styles.methods} aria-label="添加方式">
            {([['text', '粘贴文字'], ['file', '导入文件'], ['web', '保存网页']] as const).map(([mode, label]) => <button aria-pressed={entryMode === mode} key={mode} type="button" onClick={() => setEntryMode(mode)}>{label}</button>)}
          </nav>
          <section hidden={entryMode !== "text"} className="source-manual-entry">
            <header><div><h2>粘贴文字</h2><p>保存已有笔记或产品资料，核对后加入知识库。</p></div></header>
            <form className={styles.entryForm} onSubmit={addManualItem}>
              <label><span>标题</span><input name="title" placeholder="给这条内容一个名称" required /></label>
              <label><span>正文</span><textarea name="body" placeholder="粘贴已有资料正文" required={manualType === "text"} rows={5} /></label>
              <details className={styles.more}><summary>出处与使用范围</summary>
                <label><span>类型</span><select name="content_type" value={manualType} onChange={event => setManualType(event.target.value)}><option value="text">文字</option><option value="link">链接</option><option value="video">视频</option><option value="file">文件引用</option></select></label>
                <label><span>原文链接（可选）</span><input name="source_url" placeholder="https://" type="url" /></label>
                <label><span>文件引用（可选）</span><input name="file_reference" placeholder="已有文件的路径" /></label>
                <label><span>来源许可记录（可选）</span><input name="license" placeholder="记录来源中已有的许可说明" /></label>
                <UseStateField />
              </details>
              <button disabled={busy === "manual-item"} type="submit"><FileText size={15} />添加信息条目</button>
            </form>
          </section>
          <section hidden={entryMode !== "file"} className="source-manual-entry">
            <header><div><h2>上传知识文件</h2><p>支持 Markdown、纯文本和带文本层的 PDF。</p></div></header>
            <form className={styles.entryForm} onSubmit={importFile}>
              <label><span>选择文件</span><input accept=".md,.markdown,.txt,.pdf" name="file" required type="file" aria-describedby="knowledge-file-state" aria-invalid={Boolean(fileError)} onInvalid={event => { event.preventDefault(); setFileError("请先选择一个 Markdown、文本或 PDF 文件。"); }} onChange={event => {
                const file = event.target.files?.[0];
                setFileName(file?.name ?? null);
                setFileError(file ? validateKnowledgeFile(file) : null);
              }} /></label>
              <p id="knowledge-file-state" className={styles.fieldState} role={fileError ? "alert" : "status"}>{fileError ?? (fileName ? `已选择 ${fileName}，可解析并导入。` : "请先选择文件，再解析并导入。")}</p>
              <UseStateField />
              <button disabled={!fileName || Boolean(fileError) || busy === "import-file"} type="submit"><Upload size={15} />解析并导入</button>
            </form>
          </section>
          <section hidden={entryMode !== "web"} className="source-manual-entry">
            <header><div><h2>网页快照</h2><p>保存当时的正文、原网址与抓取时间；需登录或无法访问时会说明原因。</p></div></header>
            <form className={styles.entryForm} onSubmit={captureWebPage}><label><span>网页地址</span><input name="url" placeholder="https://example.com/article" required type="url" /></label><UseStateField /><button disabled={busy === "web-snapshot"} type="submit"><Link2 size={15} />抓取网页快照</button></form>
          </section>
        </section>
        <details className={styles.subscriptions} open={subscriptionOpen} onToggle={event => setSubscriptionOpen(event.currentTarget.open)}>
          <summary>持续订阅与信息源 · 高级</summary>
          <section className="source-platforms" aria-label="可连接平台">{platformEntries.map(({ key, label, available, connector }) => available ? <button className={`source-platform${subscriptionPlatform === key ? " is-selected" : ""}`} key={key} onClick={() => { setSubscriptionPlatform(key); setSelectedItemId(null); if (connector) setSelectedConnectorId(connector.id); }} type="button"><span>{label}</span><small className="status-success">{connector ? connectorState(connector).label : "可配置"}</small></button> : <div className="source-platform" key={key}><span>{label}</span><small className="status-warning">尚未支持</small></div>)}</section>
          <div className="source-workspace__grid">
            <section className="source-panel"><header><h2>已配置信息源</h2><small>{connectors.length} 个</small></header>{loading ? <p className="source-panel__state"><LoaderCircle className="is-spinning" size={16} />正在读取本地服务…</p> : connectors.length ? <div className="source-connector-list">{connectors.map((connector) => { const state = connectorState(connector); const run = lastRuns[connector.id]; return <article className={`source-connector${selectedConnectorId === connector.id ? " is-selected" : ""}`} key={connector.id}><button onClick={() => { setSelectedConnectorId(connector.id); setSelectedItemId(null); }} type="button"><span className={`source-state-dot status-${state.tone}`} /><div><strong>{platformLabel(connector.platform)}</strong><p>{connector.account_or_rule === "user-provided content" ? "由你手工添加的内容" : connector.account_or_rule}</p><small>{state.label} · 最近运行 {run ? formatTime(run.finished_at) : connector.latest_run_id ? "有记录" : "暂无记录"}</small></div></button><button aria-label={`同步 ${platformLabel(connector.platform)}`} className="source-sync-button" disabled={busy === `sync-${connector.id}`} onClick={() => void sync(connector)} type="button"><RefreshCw className={busy === `sync-${connector.id}` ? "is-spinning" : ""} size={14} />同步</button></article>; })}</div> : <Empty icon={<Link2 size={20} />} title="还没有信息源" text="选择平台并填写可访问的地址或规则。" />}</section>
            <section className="source-panel source-panel--form"><header><h2>添加信息源</h2></header><form className={styles.entryForm} onSubmit={addConnector}>
              <label><span>平台</span><select name="platform" value={subscriptionPlatform} onChange={event => setSubscriptionPlatform(event.target.value)}>{platforms.filter(platform => platform.available).map(platform => <option key={platform.key} value={platform.key}>{platform.label}</option>)}</select></label>
              <label><span>地址或规则</span><input name="account_or_rule" placeholder="粘贴网页地址" required /></label>
              <label><span>同步频率</span><select defaultValue="每天" name="frequency"><option>每天</option><option>每周</option><option>手动</option></select></label>
              <button disabled={busy === "create-connector"} type="submit"><Plus size={15} />保存信息源</button>
            </form></section>
          </div>
        </details>
        <section className="source-items"><header><h2>{selectedConnector ? `${platformLabel(selectedConnector.platform)}最近获取` : "最近获取"}</h2><small>{items.length} 条</small></header>{loading ? null : items.length ? <div className="source-item-list">{items.map((item) => <button data-open-details className={`source-item${selectedItemId === item.id ? " is-selected" : ""}`} key={item.id} onClick={() => setSelectedItemId(item.id)} type="button"><span className="source-item__type">{{text:"文字",video:"视频",file:"文件",link:"链接"}[item.content_type] ?? "内容"}</span><div><strong>{item.title}</strong><p>{item.body || item.subtitles || item.source_url || item.file_reference || "没有可预览的正文。"}</p><small>{sourceLabel(item.source_label)} · {formatTime(item.created_at)} · {item.license}</small></div><Save size={15} /></button>)}</div> : <Empty icon={<FileText size={20} />} title="还没有信息条目" text="同步已配置的信息源，或手工添加已有内容。" />}</section>
      </div>
    </AppShell>
  );
}

function validateKnowledgeFile(file: File | undefined): string | null {
  if (!file) return "请先选择一个 Markdown、文本或 PDF 文件。";
  if (!/\.(md|markdown|txt|pdf)$/i.test(file.name)) return "请选择 Markdown、文本或 PDF 文件。";
  if (file.size === 0) return "文件为空，请选择有正文的文件。";
  return null;
}

function Empty({ icon, title, text }: { icon: ReactNode; title: string; text: string }) { return <div className="source-panel__empty">{icon}<strong>{title}</strong><p>{text}</p></div>; }

function UseStateField() {
  return <label><span>保存后的使用范围</span><select defaultValue="available" name="use_state"><option value="available">可正常使用</option><option value="reference_only">仅作参考，不直接进入成片</option><option value="excluded">排除，不用于检索、生成或成片</option><option value="not_for_automation">不进入自动选片，可由我手动选择</option><option value="not_for_publish">可制作预览，不进入发布产物</option></select></label>;
}

function SourceInspector({ connector, lastRun }: { connector: SourceConnector | null; lastRun: SourceRun | null }) {
  if (!connector) return <p className="source-inspector-empty">选择一个信息源或信息条目，查看来源、授权与可执行操作。</p>;
  const state = connectorState(connector);
  return <div className="source-inspector"><span className={`source-inspector__status status-${state.tone}`}>{state.label}</span><h3>{platformLabel(connector.platform)}</h3><dl><div><dt>地址或规则</dt><dd>{connector.account_or_rule === "user-provided content" ? "由你手工添加的内容" : connector.account_or_rule}</dd></div><div><dt>同步频率</dt><dd>{frequencyLabel(connector.frequency)}</dd></div><div><dt>权限范围</dt><dd>{connector.permissions.map(permissionLabel).join("、") || "未声明"}</dd></div></dl>{lastRun ? <section><h4>本次同步结果</h4><p>获取 {lastRun.item_count} 条 · {formatTime(lastRun.finished_at)}</p>{lastRun.reason ? <p>{lastRun.reason}</p> : null}{lastRun.impact ? <p>影响：{lastRun.impact}</p> : null}{lastRun.repair_action ? <p>处理：{lastRun.repair_action}</p> : null}</section> : <section><h4>最近结果</h4><p>{connector.latest_run_id ? "服务只返回了最近运行标识；结果详情需在下一次同步后显示。" : "尚未同步。"}</p></section>}</div>;
}

function SourceItemInspector({ item, busy, onSave }: { item: SourceItem; busy: string | null; onSave: (event: FormEvent<HTMLFormElement>) => Promise<void> }) {
  return <div className="source-inspector"><span className="source-inspector__status">信息条目</span><h3>{item.title}</h3><dl><div><dt>来源</dt><dd>{sourceLabel(item.source_label)}</dd></div><div><dt>原始地址</dt><dd>{item.source_url ? <a href={item.source_url} rel="noreferrer" target="_blank">打开原文 <ExternalLink size={12} /></a> : "无"}</dd></div><div><dt>文件引用</dt><dd>{item.file_reference || "无"}</dd></div>{item.fetched_at ? <div><dt>抓取时间</dt><dd>{formatFetchTime(item.fetched_at)}</dd></div> : null}<div><dt>来源许可记录</dt><dd>{item.license === "unknown" ? "未记录" : item.license}</dd></div></dl><form className="source-save-form" onSubmit={(event) => void onSave(event)}><h4>保存到知识库</h4><button disabled={busy === `save-${item.id}`} type="submit"><CheckCircle2 size={14} />保存到知识库</button></form></div>;
}
