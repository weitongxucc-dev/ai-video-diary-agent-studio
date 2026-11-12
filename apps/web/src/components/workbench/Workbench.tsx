"use client";

import { createContext, memo, Suspense, useCallback, useContext, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode, type SyntheticEvent } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Clapperboard, FileStack, Home, X } from "lucide-react";
import { AppShellFrame, type AppShellProps } from "../AppShell";
import { HomeCommandComposer } from "../HomeCommandComposer";
import { useLocalMemberIdentity } from "@/hooks/useLocalMemberIdentity";
import { getCreationTaskWorkspace } from "@/lib/api";
import { useWorkbenchTarget } from "./WorkbenchTarget";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "../ui/tabs";
import { Button } from "../ui/button";
import { ConversationTarget } from "./ConversationTarget";
import { MediaVisibility } from "../ui/media-visibility";
import { ScrollArea } from "../ui/scroll-area";
import { WorkbenchAssistantTargetContext, WorkbenchRegistry, WorkspacePanelContext, WorkspacePageContext, workspacePageId, workspaceResourcePaths, isWorkbenchManagedPath, type WorkspacePage } from "./context";
import { EditorDockProvider } from "./EditorDock";
import { EditorResourcePane } from "./EditorResourcePane";
import styles from "./Workbench.module.css";

const WorkbenchViewStates = createContext(new Map<string, Map<string, unknown>>());

// This cache owns only view lifetime. Business objects, task progress, versions,
// conversation drafts and receipts keep their existing API/repository owners.
const PageMount = memo(function PageMount({ page, active, auxiliary, updateShell }: {
  page: WorkspacePage; active: boolean; auxiliary: boolean; updateShell: (id: string, props: AppShellProps) => void;
}) {
  const panel = useContext(WorkspacePanelContext);
  const visible = active && !(auxiliary && panel.inspectorOpen);
  const node = useRef<HTMLDivElement>(null);
  const viewStates = useContext(WorkbenchViewStates);
  const viewState = viewStates.get(page.id) ?? new Map<string, unknown>();
  viewStates.set(page.id, viewState);
  const context = useMemo(() => ({ id: page.id, href: page.href, active, auxiliary, updateShell, viewState }), [page.id, page.href, active, auxiliary, updateShell, viewState]);
  useLayoutEffect(() => {
    if (!visible) return;
    node.current?.querySelectorAll<HTMLElement>("[data-workspace-scroll]").forEach(element => {
      const saved = viewState.get(`scroll:${element.dataset.workspaceScroll}`) as { top: number; left: number } | undefined;
      if (saved) { element.scrollTop = saved.top; element.scrollLeft = saved.left; }
    });
    const saved = viewState.get("scroll:page") as { top: number; left: number } | undefined;
    if (saved && node.current) { node.current.scrollTop = saved.top; node.current.scrollLeft = saved.left; }
  }, [visible, viewState]);
  useEffect(() => {
    const pause = () => {
      if (!visible || document.hidden) node.current?.querySelectorAll("audio, video").forEach(media => (media as HTMLMediaElement).pause());
    };
    pause();
    document.addEventListener("visibilitychange", pause);
    return () => document.removeEventListener("visibilitychange", pause);
  }, [visible]);
  const content = <div role="region" aria-label={page.label} hidden={!visible} className={`${styles.page}${auxiliary ? ` ${styles.resourcePage}` : ""}`} ref={node}
    data-workspace-scroll="page" onScrollCapture={event => {
      const element = event.target;
      if (element instanceof HTMLElement && element.dataset.workspaceScroll) viewState.set(`scroll:${element.dataset.workspaceScroll}`, { top: element.scrollTop, left: element.scrollLeft });
    }}
    onPlayCapture={event => {
      if (!(event.target instanceof HTMLMediaElement)) return;
      if (!visible) event.target.pause();
    }}>
    <WorkspacePageContext.Provider value={context}><MediaVisibility visible={visible}>{page.content}</MediaVisibility></WorkspacePageContext.Provider>
  </div>;
  return page.id.startsWith("/tasks/") || page.id === "/"
    ? <TabsContent value={page.id} keepMounted className={styles.page}>{content}</TabsContent>
    : content;
});

function MemberWorkbench({ children }: { children: ReactNode }) {
  const router = useRouter();
  const pathname = usePathname() ?? "/";
  const params = useSearchParams();
  const activeId = workspacePageId(pathname, params ?? new URLSearchParams());
  const [pages, setPages] = useState<WorkspacePage[]>([]);
  const [shells, setShells] = useState<Record<string, AppShellProps>>({});
  const { targetId, clearTarget } = useWorkbenchTarget();
  const [closeError, setCloseError] = useState<string | null>(null);
  const playingMedia = useRef<HTMLMediaElement | null>(null);
  const releasePlaying = (event: SyntheticEvent) => {
    if (playingMedia.current === event.target) playingMedia.current = null;
  };
  const viewStates = useRef(new Map<string, Map<string, unknown>>());
  const register = useCallback((page: Omit<WorkspacePage, "open">) => setPages(current => {
    const existing = current.find(item => item.id === page.id);
    return existing
      ? current.map(item => item.id === page.id ? { ...item, ...page, open: true } : item)
      : [...current, { ...page, open: true }];
  }), []);
  const registry = useMemo(() => ({ register }), [register]);
  const updateShell = useCallback((id: string, props: AppShellProps) => setShells(current => current[id] === props ? current : { ...current, [id]: props }), []);
  const newVideo = pathname === "/" && params?.get("new") === "1";
  useEffect(() => {
    if (!newVideo) return;
    clearTarget();
  }, [newVideo, clearTarget]);
  const activePage = pages.find(page => page.id === activeId && page.open);
  const resourceIds = workspaceResourcePaths;
  const managed = isWorkbenchManagedPath(pathname);
  const dockedTask = resourceIds.includes(activeId) && params?.get("mode") !== "manage" && targetId ? pages.find(page => page.id === targetId && page.open) : undefined;
  const dockedHref = dockedTask?.href;
  const releaseResourcePane = useCallback(() => { if (dockedHref) router.push(dockedHref); }, [dockedHref, router]);
  const shell = shells[dockedTask?.id ?? activeId] ?? {};
  const resourceShell = shells[activeId] ?? {};
  const shellPage = dockedTask ?? activePage;
  const activeViewState = viewStates.current.get(shellPage?.id ?? activeId);
  const pageScope = shellPage && activeViewState ? { id: shellPage.id, href: shellPage.href, active: managed, auxiliary: false, updateShell, viewState: activeViewState } : null;
  const scoped = (content: ReactNode) => pageScope ? <WorkspacePageContext.Provider value={pageScope}>{content}</WorkspacePageContext.Provider> : content;
  const scopedShell: AppShellProps = { ...shell, children: null, inspector: shell.inspector && scoped(shell.inspector),
    inspectorConfirmation: shell.inspectorConfirmation && scoped(shell.inspectorConfirmation), toolbar: shell.toolbar && scoped(shell.toolbar),
    toolbarActions: shell.toolbarActions && scoped(shell.toolbarActions), quickInspector: shell.quickInspector ? openDetails => scoped(shell.quickInspector?.(openDetails)) : undefined };
  const target = targetId ? shells[targetId]?.assistantContext : undefined;
  const resource = resourceShell.assistantContext;
  const tabs = pages.filter(page => page.open && (!resourceIds.includes(page.id) || page.id === activeId && !dockedTask));
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
  const tabstrip = tabs.length ? <ScrollArea className={styles.tabScroll}><TabsList variant="workspace" aria-label="工作区页签" className={styles.tabList}>
    {tabs.map(page => <div key={page.id} className={styles.tab} data-active={page.id === activeId || undefined}>
      <TabsTrigger value={page.id} title={page.label} className={styles.tabTrigger}>
        {page.id.startsWith("/tasks/") ? <Clapperboard aria-hidden /> : page.id === "/" ? <Home aria-hidden /> : <FileStack aria-hidden />}<span>{page.id === "/" && newVideo ? "新视频" : page.label}</span>
      </TabsTrigger>
      {page.id.startsWith("/tasks/") ? <Button variant="ghost" size="icon" className={styles.close} aria-label={`关闭${page.label}页签`} title={`关闭${page.label}页签`} onClick={() => close(page.id)}><X /></Button> : null}
    </div>)}
  </TabsList></ScrollArea> : undefined;
  return <WorkbenchAssistantTargetContext.Provider value={target}><WorkbenchRegistry.Provider value={registry}><WorkbenchViewStates.Provider value={viewStates.current}><EditorDockProvider activePageId={shellPage?.id}>
    {children}
    <div hidden={!managed} className={styles.host} onPlayCapture={event => {
      if (!(event.target instanceof HTMLMediaElement)) return;
      if (playingMedia.current && playingMedia.current !== event.target) playingMedia.current.pause();
      playingMedia.current = event.target;
    }} onPauseCapture={releasePlaying} onEndedCapture={releasePlaying}>
      <Tabs value={dockedTask?.id ?? activePage?.id ?? null} onValueChange={value => { if (typeof value === "string") activate(value); }} className={styles.root}>
        <AppShellFrame {...scopedShell} active={managed} activeCreationTaskId={shellPage?.id.startsWith("/tasks/") ? shellPage.id.slice("/tasks/".length) : undefined} resourceOpen={Boolean(dockedTask)} onReleaseResourcePane={releaseResourcePane} inspectorDismissKey={activeId} inspector={dockedTask && activePage ? <WorkspacePageContext.Provider value={{ id: activePage.id, href: activePage.href, active: managed, auxiliary: true, updateShell, viewState: viewStates.current.get(activePage.id)! }}>{resourceShell.inspector}</WorkspacePageContext.Provider> : scopedShell.inspector} inspectorTitle={dockedTask ? resourceShell.inspectorTitle : scopedShell.inspectorTitle} workspaceTabs={tabstrip} conversationActions={target?.task ? <ConversationTarget name={target.task.name} onReturn={() => targetId && activate(targetId)} onClear={clearTarget} /> : <span className="conversation-workspace-target">{newVideo ? "新视频构思" : "工作区对话"}</span>} conversation={<>
          <HomeCommandComposer {...resource} {...target} autoFocus={newVideo} pageContext={undefined} assignLocation={href => router.push(href)} />
        </>}>
          <div data-workbench-pages className={`${styles.pages}${dockedTask ? ` ${styles.withResource}` : ""}`}>
            {pages.filter(page => page.open && page.content !== null).map(page => {
              const auxiliary = Boolean(dockedTask && page.id === activeId);
              const view = <PageMount key={page.id} page={page} active={managed && (page.id === activeId || page.id === dockedTask?.id)} auxiliary={auxiliary} updateShell={updateShell} />;
              return resourceIds.includes(page.id) ? <EditorResourcePane key={page.id} title={page.label} active={managed && page.id === activeId} docked={auxiliary} onClose={() => { if (dockedTask) activate(dockedTask.id); }}>{view}</EditorResourcePane> : view;
            })}
          </div>
          {closeError ? <div role="alert" className={styles.closeError}>{closeError}<Button variant="ghost" onClick={() => setCloseError(null)}>知道了</Button></div> : null}
          {!activePage?.content ? <p className={styles.loading} role="status">正在打开工作区…</p> : null}
        </AppShellFrame>
      </Tabs>
    </div>
  </EditorDockProvider></WorkbenchViewStates.Provider></WorkbenchRegistry.Provider></WorkbenchAssistantTargetContext.Provider>;
}

export function Workbench({ children }: { children: ReactNode }) {
  const identity = useLocalMemberIdentity();
  return <Suspense fallback={children}><MemberWorkbench key={identity.token ?? "identity-pending"}>{children}</MemberWorkbench></Suspense>;
}
