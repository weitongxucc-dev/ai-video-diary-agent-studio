"use client";

import { createContext, memo, Suspense, useCallback, useContext, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Clapperboard, FileStack, X } from "lucide-react";
import { AppShellFrame, type AppShellProps } from "../AppShell";
import { HomeCommandComposer } from "../HomeCommandComposer";
import { useLocalMemberIdentity } from "@/hooks/useLocalMemberIdentity";
import { getCreationTaskWorkspace } from "@/lib/api";
import { useAssistantSelection } from "../AssistantConversationProvider";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "../ui/tabs";
import { Button } from "../ui/button";
import { ScrollArea } from "../ui/scroll-area";
import { WorkbenchRegistry, WorkspacePageContext, workspacePageId, type WorkspacePage } from "./context";
import styles from "./Workbench.module.css";

const WorkbenchViewStates = createContext(new Map<string, Map<string, unknown>>());

// This cache owns only view lifetime. Business objects, task progress, versions,
// conversation drafts and receipts keep their existing API/repository owners.
const PageMount = memo(function PageMount({ page, active, updateShell }: {
  page: WorkspacePage; active: boolean; updateShell: (id: string, props: AppShellProps) => void;
}) {
  const node = useRef<HTMLDivElement>(null);
  const viewStates = useContext(WorkbenchViewStates);
  const viewState = viewStates.get(page.id) ?? new Map<string, unknown>();
  viewStates.set(page.id, viewState);
  const context = useMemo(() => ({ id: page.id, href: page.href, active, updateShell, viewState }), [page.id, page.href, active, updateShell, viewState]);
  useLayoutEffect(() => {
    if (!active) return;
    node.current?.querySelectorAll<HTMLElement>("[data-workspace-scroll]").forEach(element => {
      const saved = viewState.get(`scroll:${element.dataset.workspaceScroll}`) as { top: number; left: number } | undefined;
      if (saved) { element.scrollTop = saved.top; element.scrollLeft = saved.left; }
    });
    const saved = viewState.get("scroll:page") as { top: number; left: number } | undefined;
    if (saved && node.current) { node.current.scrollTop = saved.top; node.current.scrollLeft = saved.left; }
  }, [active, viewState]);
  useEffect(() => {
    const pause = () => {
      if (!active || document.hidden) node.current?.querySelectorAll("audio, video").forEach(media => (media as HTMLMediaElement).pause());
    };
    pause();
    document.addEventListener("visibilitychange", pause);
    return () => document.removeEventListener("visibilitychange", pause);
  }, [active]);
  return <TabsContent value={page.id} keepMounted hidden={!active} className={styles.page} ref={node}
    data-workspace-scroll="page" onScrollCapture={event => {
      const element = event.target;
      if (element instanceof HTMLElement && element.dataset.workspaceScroll) viewState.set(`scroll:${element.dataset.workspaceScroll}`, { top: element.scrollTop, left: element.scrollLeft });
    }}
    onPlayCapture={event => { if (!active && event.target instanceof HTMLMediaElement) event.target.pause(); }}>
    <WorkspacePageContext.Provider value={context}>{page.content}</WorkspacePageContext.Provider>
  </TabsContent>;
});

function MemberWorkbench({ children }: { children: ReactNode }) {
  const router = useRouter();
  const pathname = usePathname() ?? "/";
  const params = useSearchParams();
  const activeId = workspacePageId(pathname, params ?? new URLSearchParams());
  const [pages, setPages] = useState<WorkspacePage[]>([]);
  const [shells, setShells] = useState<Record<string, AppShellProps>>({});
  const [targetId, setTargetId] = useState<string | null>(null);
  const [closeError, setCloseError] = useState<string | null>(null);
  const viewStates = useRef(new Map<string, Map<string, unknown>>());
  const selection = useAssistantSelection();
  const currentSelection = useRef(selection);
  currentSelection.current = selection;
  const register = useCallback((page: Omit<WorkspacePage, "open">) => setPages(current => {
    const existing = current.find(item => item.id === page.id);
    return existing
      ? current.map(item => item.id === page.id ? { ...item, ...page, open: true } : item)
      : [...current, { ...page, open: true }];
  }), []);
  const registry = useMemo(() => ({ register }), [register]);
  const updateShell = useCallback((id: string, props: AppShellProps) => setShells(current => current[id] === props ? current : { ...current, [id]: props }), []);
  useEffect(() => { if (activeId.startsWith("/tasks/")) setTargetId(activeId); }, [activeId]);
  const newVideo = pathname === "/" && params?.get("new") === "1";
  useEffect(() => {
    if (!newVideo) return;
    setTargetId(null);
    const selected = currentSelection.current;
    if (selected?.items.some(item => "task_id" in item)) selected.select(selected.items.filter(item => !("task_id" in item)));
  }, [newVideo]);
  const activePage = pages.find(page => page.id === activeId && page.open);
  const managed = pathname === "/" || pathname === "/library" || pathname.startsWith("/tasks/");
  const shell = shells[activeId] ?? {};
  const activeViewState = viewStates.current.get(activeId);
  const pageScope = activePage && activeViewState ? { id: activeId, href: activePage.href, active: managed, updateShell, viewState: activeViewState } : null;
  const scoped = (content: ReactNode) => pageScope ? <WorkspacePageContext.Provider value={pageScope}>{content}</WorkspacePageContext.Provider> : content;
  const scopedShell: AppShellProps = { ...shell, children: null, inspector: shell.inspector && scoped(shell.inspector),
    inspectorConfirmation: shell.inspectorConfirmation && scoped(shell.inspectorConfirmation), toolbar: shell.toolbar && scoped(shell.toolbar),
    toolbarActions: shell.toolbarActions && scoped(shell.toolbarActions), quickInspector: shell.quickInspector ? openDetails => scoped(shell.quickInspector?.(openDetails)) : undefined };
  const target = targetId ? shells[targetId]?.assistantContext : undefined;
  const resource = shell.assistantContext;
  const tabs = pages.filter(page => page.open);
  const activate = (id: string) => {
    const page = pages.find(item => item.id === id);
    if (page) { setPages(current => current.map(item => item.id === id ? { ...item, open: true } : item)); router.push(page.href); }
  };
  const close = (id: string) => {
    if (shells[id]?.closeBlockedReason) { setCloseError(shells[id].closeBlockedReason ?? null); return; }
    const context = shells[id]?.assistantContext;
    const closed: AppShellProps = { children: null };
    if (context?.task) {
      const playhead = context.taskContext?.getPlayheadMs() ?? null;
      closed.assistantContext = { task: context.task, taskContext: context.taskContext ? {
        stage: context.taskContext.stage, selectedObjectRefs: context.taskContext.selectedObjectRefs,
        getPlayheadMs: () => playhead,
      } : undefined, onTaskUpdated: () => {
        void getCreationTaskWorkspace(context.task!.id).then(facts => setShells(current => current[id]?.assistantContext?.onTaskUpdated === closed.assistantContext?.onTaskUpdated
          ? { ...current, [id]: { ...closed, assistantContext: { ...closed.assistantContext, task: facts.task } } } : current))
          .catch(() => setCloseError("修改结果尚未回读，请重新打开对应作品核对当前版本。"));
      } };
    }
    setShells(current => ({ ...current, [id]: closed }));
    setPages(current => current.map(page => page.id === id ? { ...page, content: null, open: false } : page));
    if (activeId === id) {
      const index = tabs.findIndex(page => page.id === id);
      const next = tabs[index - 1] ?? tabs[index + 1];
      router.push(next?.href ?? "/");
    }
  };
  const tabstrip = <ScrollArea className={styles.tabScroll}><TabsList variant="line" aria-label="工作区页签" className={styles.tabList}>
    {tabs.map(page => <div key={page.id} className={styles.tab} data-active={page.id === activeId || undefined}>
      <TabsTrigger value={page.id} title={page.label} className={styles.tabTrigger}>
        {page.id.startsWith("/tasks/") ? <Clapperboard aria-hidden /> : <FileStack aria-hidden />}<span>{page.label}</span>
      </TabsTrigger>
      <Button variant="ghost" size="icon" className={styles.close} aria-label={`关闭${page.label}页签`} title={`关闭${page.label}页签`} onClick={() => close(page.id)}><X /></Button>
    </div>)}
  </TabsList></ScrollArea>;
  return <WorkbenchRegistry.Provider value={registry}><WorkbenchViewStates.Provider value={viewStates.current}>
    {children}
    <div hidden={!managed} className={styles.host}>
      <Tabs value={activePage?.id ?? null} onValueChange={value => { if (typeof value === "string") activate(value); }} className={styles.root}>
        <AppShellFrame {...scopedShell} workspaceTabs={tabstrip} conversation={<>
          <div className={styles.target} aria-label="当前操作目标">
            {target?.task ? <><Clapperboard size={14} /><button type="button" title={target.task.name} onClick={() => targetId && activate(targetId)}>{target.task.name}</button><Button variant="ghost" size="icon" aria-label="取消当前作品目标" title="取消当前作品目标" onClick={() => { setTargetId(null); selection?.select(selection.items.filter(item => !("task_id" in item))); }}><X /></Button></> : <span>工作区对话</span>}
          </div>
          <HomeCommandComposer {...resource} {...target} pageContext={undefined} assignLocation={href => router.push(href)} />
        </>}>
          {pages.filter(page => page.open && page.content !== null).map(page => <PageMount key={page.id} page={page} active={managed && page.id === activeId} updateShell={updateShell} />)}
          {closeError ? <div role="alert" className={styles.closeError}>{closeError}<Button variant="ghost" onClick={() => setCloseError(null)}>知道了</Button></div> : null}
          {!activePage?.content ? <p className={styles.loading} role="status">正在打开工作区…</p> : null}
        </AppShellFrame>
      </Tabs>
    </div>
  </WorkbenchViewStates.Provider></WorkbenchRegistry.Provider>;
}

export function Workbench({ children }: { children: ReactNode }) {
  const identity = useLocalMemberIdentity();
  return <Suspense fallback={children}><MemberWorkbench key={identity.token ?? "identity-pending"}>{children}</MemberWorkbench></Suspense>;
}
