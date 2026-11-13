"use client";
import Link from "next/link";
import Image from "next/image";
import { createPortal } from "react-dom";
import { useActiveEditorDock } from "./workbench/EditorDock";
import { WorkspaceLayoutMenu } from "./workbench/WorkspaceLayoutMenu";
import { MediaVisibility } from "./ui/media-visibility";
import { WorkspaceInspector } from "./workbench/WorkspaceInspector";
import { DesktopHeader } from "./DesktopHeader";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import {
  Activity,
  AudioLines,
  ArrowLeft,
  BookOpen,
  LoaderCircle,
  Clapperboard,
  FileStack,
  Home,
  Library,
  MessageSquare,
  Music2,
  PanelRight,
  Plus,
  RefreshCw,
  Search,
  Settings,
  Sparkles,
  SunMoon,
  UserRound,
  Users,
  Wand2,
} from "lucide-react";
import {
  useEffect,
  useCallback, useContext,
  useMemo,
  useRef,
  useState,
  useLayoutEffect,
  Suspense,
  type ComponentType,
  type ReactNode,
  type CSSProperties,
} from "react";
import { HomeCommandComposer, type HomeCommandComposerProps } from "@/components/HomeCommandComposer";
import { Button } from "./ui/button";
import { IconButton } from "./ui/icon-button";
import { WorkbenchAssistantTargetContext, WorkspacePanelContext, useWorkspacePage } from "./workbench/context";
import { useConversationVisibility } from "./workbench/useConversationVisibility";
import { useWorkbenchTarget } from "./workbench/WorkbenchTarget";
import { ConversationTarget } from "./workbench/ConversationTarget";
import { useWorkspaceBoot } from "./WorkspaceBootContext";
import { PreflightDraftReturn } from "@/components/PreflightDraftReturn";
import { HomeCardDialog, homeCardParentForTarget } from "@/components/HomeCardDialog";
import { HelpPanel } from "@/components/HelpPanel";
import { KeyboardShortcuts } from "@/components/KeyboardShortcuts";
import { CommandPalette, type CommandPaletteItem } from "@/components/CommandPalette";
import { listCreationTasks, listLocalMembers, type LocalMember } from "@/lib/api";
import { initialTaskListFacts, applyTaskFactUpdates, applyTaskListSnapshot } from "./creation-task/task-list-facts";
import { creationTaskShortStatus } from "@/lib/creation-task-status";
import type { CreationTask } from "@/lib/types";

import { useEditorPanels } from "./useEditorPanels";
import editorShell from "./EditorShell.module.css";
import shellUi from "./AppShellCompact.module.css";

type Icon = ComponentType<{ size?: number; strokeWidth?: number }>;
type NavItem = { href: string; label: string; icon: Icon; activePath?: string };
export type ThemeMode = "system" | "light" | "dark";

function displayTaskName(name?: string) {
  return name || "未命名创作任务";
}

export type AppShellCreationTask = CreationTask;

const primaryNav: NavItem[] = [
  { href: "/", label: "首页", icon: Home },
  { href: "/library?mode=manage", label: "素材库", icon: Library },
  { href: "/knowledge?mode=manage", label: "知识库", icon: BookOpen },
  { href: "/profiles?mode=manage", label: "人物与声音", icon: UserRound },
  { href: "/templates?mode=manage", label: "视频模板", icon: FileStack },
  { href: "/history", label: "发布与数据", icon: Activity },
  { href: "/production?view=automations", label: "定时任务", icon: Sparkles },
];

// Functional destinations stay separate from the editor's asset-library panel.
// The panel can be opened beside a task with mode=panel and keeps unavailable
// catalogs explicit instead of presenting them as ready-to-use assets.
const resourceNav: NavItem[] = [
  { href: "/library?mode=panel&resource=materials", label: "素材", icon: Library, activePath: "/library" },
  { href: "/library?mode=panel&resource=skills", label: "技能", icon: Sparkles, activePath: "/library" },
  { href: "/library?mode=panel&resource=stickers", label: "贴纸", icon: Sparkles, activePath: "/library" },
  { href: "/library?mode=panel&resource=filters", label: "滤镜", icon: SunMoon, activePath: "/library" },
  { href: "/library?mode=panel&resource=effects", label: "特效", icon: Wand2, activePath: "/library" },
  { href: "/library?mode=panel&resource=transitions", label: "转场", icon: Clapperboard, activePath: "/library" },
  { href: "/templates?mode=manage", label: "模板", icon: FileStack, activePath: "/templates" },
  { href: "/library?mode=panel&resource=subtitles", label: "字幕", icon: FileStack, activePath: "/library" },
  { href: "/library?mode=panel&resource=text", label: "字幕 / 文本", icon: FileStack, activePath: "/library" },
  { href: "/profiles?mode=manage&view=voices", label: "配音", icon: AudioLines, activePath: "/profiles" },
  { href: "/library?mode=panel&resource=music", label: "音乐", icon: Music2, activePath: "/library" },
  { href: "/library?mode=panel&resource=sfx", label: "音效", icon: AudioLines, activePath: "/library" },
];

const routeLabels: Array<Pick<NavItem, "href" | "label" | "activePath">> = [
  ...primaryNav,
  { href: "/members", label: "成员与空间" },
  { href: "/production?view=automations", label: "定时任务" },
  { href: "/production", label: "创作任务" },
  { href: "/tasks", label: "创作任务" },
  { href: "/sources", label: "资料来源" },
  { href: "/history", label: "发布与数据" },
  { href: "/health", label: "运行情况" },
  { href: "/settings", label: "设置" },
  { href: "/onboarding", label: "启动检查" },
  { href: "/learning", label: "偏好学习" },
  { href: "/privacy", label: "隐私政策" },
  { href: "/terms", label: "使用条款" },
  { href: "/profile", label: "创作者画像" },
  { href: "/profiles", label: "人物与声音" },
];

export type AppShellWorkspaceStatus = {
  label: string;
};

export type AppShellProps = {
  children: ReactNode;
  creationTasks?: AppShellCreationTask[];
  activeCreationTaskId?: string;
  inspector?: ReactNode;
  inspectorConfirmation?: ReactNode;
  inspectorConfirmationKey?: string;
  inspectorOpenKey?: string;
  quickInspector?: (openDetails: () => void) => ReactNode;
  quickInspectorKey?: string;
  quickInspectorLabel?: string;
  conversation?: ReactNode;
  conversationActions?: ReactNode;
  autoFocus?: boolean;
  inspectorTitle?: string;
  inspectorLayout?: "properties" | "reading";
  workspaceStatus?: AppShellWorkspaceStatus;
  editorLayout?: boolean;
  editingPane?: string;
  toolbar?: ReactNode;
  toolbarActions?: ReactNode;
  inspectorDismissKey?: string;
  assistantContext?: HomeCommandComposerProps;
  workspaceTabs?: ReactNode;
  resourceOpen?: boolean;
  onReleaseResourcePane?: () => void;
  closeBlockedReason?: string;
};

function creationTaskHref(task: CreationTask) {
  return `/tasks/${encodeURIComponent(task.id)}`;
}

function isActive(pathname: string, href: string, activePath?: string, search?: Pick<URLSearchParams, "get">): boolean {
  if (href.includes("view=automations")) return pathname === "/production" && search?.get("view") === "automations";
  const route = activePath ?? href.split(/[?#]/)[0];
  return route === "/" ? pathname === "/" : pathname.startsWith(route);
}

function currentRouteFor(pathname: string, searchParams?: Pick<URLSearchParams, "get">) {
  return routeLabels
    .filter((item) => isActive(pathname, item.href, item.activePath, searchParams))
    .sort((a, b) => b.href.length - a.href.length)[0] ?? routeLabels[0];
}

function NavigationRows({ pathname, showMembers }: { pathname: string; showMembers: boolean }) {
  const searchParams = useSearchParams();
  return <NavigationRowsContent pathname={pathname} searchParams={searchParams} showMembers={showMembers} />;
}

function NavigationRowsContent({ pathname, searchParams, showMembers = true }: { pathname: string; searchParams?: Pick<URLSearchParams, "get">; showMembers?: boolean }) {
  return <>
    {primaryNav.filter((item) => showMembers || item.href !== "/members").map((item) => {
      const ItemIcon = item.icon;
      const active = isActive(pathname === "/sources" ? "/knowledge" : pathname, item.href, item.activePath, searchParams);
      return (
        <Link
          aria-label={item.label}
          aria-current={active ? "page" : undefined}
          className={`desktop-nav-row${active ? " is-active" : ""}`}
          href={item.href}
          title={item.label}
          key={item.href}
        >
          <ItemIcon size={16} strokeWidth={1.8} />
          <span>{item.label}</span>
        </Link>
      );
    })}
  </>;
}

function ResourceNavigationRows({ pathname, searchParams, collapsed }: { pathname: string; searchParams?: Pick<URLSearchParams, "get">; collapsed: boolean }) {
  return <div className="desktop-resource-nav" aria-label="Asset Library 资源面板">
    {!collapsed ? <span className="desktop-nav-heading desktop-nav-heading--resource">Asset Library</span> : null}
    {resourceNav.map(item => {
      const ItemIcon = item.icon;
      const resource = item.href.includes("resource=") ? item.href.split("resource=")[1] : null;
      const active = resource
        ? pathname === "/library" && searchParams?.get("mode") === "panel" && searchParams?.get("resource") === resource
        : pathname === (item.activePath ?? item.href.split(/[?#]/)[0]);
      return <Link aria-label={`资源面板：${item.label}`} aria-current={active ? "page" : undefined} className={`desktop-nav-row${active ? " is-active" : ""}`} href={item.href} title={`资源面板：${item.label}`} key={item.href}><ItemIcon size={16} strokeWidth={1.8} /><span>{item.label}</span></Link>;
    })}
  </div>;
}

function ToolbarLocation({ pathname }: { pathname: string }) {
  const searchParams = useSearchParams();
  return <ToolbarLocationContent pathname={pathname} searchParams={searchParams} />;
}

function ToolbarLocationContent({ pathname, searchParams }: { pathname: string; searchParams?: Pick<URLSearchParams, "get"> }) {
  const currentRoute = currentRouteFor(pathname, searchParams);
  return <div className="desktop-location">
    <span>{currentRoute.label}</span>
  </div>;
}

export function applyTheme(mode: ThemeMode) {
  const resolved =
    mode === "system"
      ? window.matchMedia("(prefers-color-scheme: dark)").matches
        ? "dark"
        : "light"
      : mode;
  document.documentElement.dataset.theme = resolved;
  document.documentElement.dataset.themeMode = mode;
  const colorScheme = document.head.querySelector<HTMLMetaElement>('meta[name="color-scheme"]')
    ?? (() => { const meta = document.createElement("meta"); meta.name = "color-scheme"; document.head.appendChild(meta); return meta; })();
  colorScheme.content = resolved === "dark" ? "dark light" : "light dark";
}

import { useLocalMemberIdentity } from "@/hooks/useLocalMemberIdentity";

export function AppShell(props: AppShellProps) {
  const page = useWorkspacePage();
  useLayoutEffect(() => { if (page) page.updateShell(page.id, props); }, [page, props]);
  return page ? props.children : <AppShellFrame {...props} />;
}

export function AppShellFrame({
  active = true,
  children,
  creationTasks,
  activeCreationTaskId,
  inspector: providedInspector,
  inspectorConfirmation,
  inspectorConfirmationKey,
  inspectorOpenKey,
  quickInspector,
  quickInspectorKey,
  quickInspectorLabel,
  conversation, conversationActions, assistantContext,
  autoFocus = false,
  inspectorDismissKey,
  inspectorTitle = "当前任务详情",
  inspectorLayout = "properties",
  workspaceStatus,
  editorLayout = false,
  editingPane,
  toolbar,
  toolbarActions,
  workspaceTabs,
  resourceOpen = false,
  onReleaseResourcePane,
}: AppShellProps & { active?: boolean }) {
  const sharedAssistantTarget = useContext(WorkbenchAssistantTargetContext);
  const assistantTarget = assistantContext ?? sharedAssistantTarget;
  const { clearTarget } = useWorkbenchTarget();
  const panels = useEditorPanels(true);
  const contentGrid = useRef<HTMLDivElement>(null);
  const [contentWidth, setContentWidth] = useState(0);
  useEffect(() => {
    const element = contentGrid.current;
    if (!element) return;
    const observer = new ResizeObserver(([entry]) => setContentWidth(entry.contentRect.width));
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  const workspaceBoot = useWorkspaceBoot();
  const workspaceReady = !workspaceBoot.blocked;
  const [nativeMac, setNativeMac] = useState(false);
  useEffect(() => {
    // These two read-only fields are provided by the existing desktop preload;
    // the older per-control Window declaration only describes its callable API.
    const desktop = window.electronAPI as (NonNullable<Window["electronAPI"]> & { isElectron: boolean; platform: string }) | undefined;
    setNativeMac(desktop?.isElectron === true && desktop.platform === "darwin");
  }, []);
  // 本地身份引导仅在 local 模式运行；正式设备角色由正式核验入口负责。
  // 角色未知（加载中）时先显示“成员与空间”，避免 owner 首屏看不到自己的页面。
  const { role: localMemberRole, ready: identityReady, token: identityToken, error: identityError } = useLocalMemberIdentity();
  const showMembersNav = localMemberRole !== "member";
  const localAuthMode = process.env.NEXT_PUBLIC_AUTH_MODE === "local";
  const pathname = usePathname() ?? "";
  const navSearchParams = useSearchParams();
  const standalone = ["/settings", "/health", "/onboarding", "/privacy", "/terms", "/members", "/profile", "/learning", "/sources"].some(route => pathname === route || pathname.startsWith(`${route}/`));
  const router = useRouter();
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [quickOpen, setQuickOpen] = useState(false);
  const [conversationFocused, setConversationFocused] = useState(false);
  useEffect(() => { setConversationFocused(false); }, [editingPane, pathname]);
  const editingPaneNeedsRoom = Boolean(editingPane) && contentWidth > 0 && contentWidth - panels.rightWidth < 720;
  const closeInspectorForReference = useCallback(() => { setDetailsOpen(false); setQuickOpen(false); setConversationFocused(true); onReleaseResourcePane?.(); }, [onReleaseResourcePane]);
  const chat = useConversationVisibility({ pathname, creation: editorLayout || pathname.startsWith("/tasks/") || autoFocus, enabled: active && !standalone, collapsed: panels.chatCollapsed, open: panels.openChat, toggle: panels.toggleChat, onReference: closeInspectorForReference });
  const workspaceHidden = editorLayout && panels.workspaceCollapsed && !chat.hidden;
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const [members, setMembers] = useState<LocalMember[]>([]);
  const [membersState, setMembersState] = useState<"loading" | "ready" | "error">("loading");
  useEffect(() => {
    let active = true;
    setMembers([]);
    if (!localAuthMode) { setMembersState("ready"); return; }
    if (!identityReady) { setMembersState("loading"); return; }
    if (identityError || !identityToken) { setMembersState("error"); return; }
    setMembersState("loading");
    void listLocalMembers().then(result => { if (active) { setMembers(result.members); setMembersState("ready"); } })
      .catch(() => { if (active) setMembersState("error"); });
    return () => { active = false; };
  }, [localAuthMode, identityReady, identityToken, identityError]);
  const [searchParentId, setSearchParentId] = useState<string | undefined>();
  const [taskListFacts, setTaskListFacts] = useState(() => initialTaskListFacts(creationTasks ?? []));
  const recentTasks = taskListFacts.items.slice(0, 6);
  const taskFactsRevision = useRef(0);
  const [taskReadState, setTaskReadState] = useState<"loading" | "ready" | "error">("loading");
  const [taskReadError, setTaskReadError] = useState<string | null>(null);
  const taskReadSequence = useRef(0);
  const previousTaskProps = useRef(creationTasks);
  useEffect(() => { setDetailsOpen(false); }, [inspectorDismissKey]);
  useEffect(() => {
    if (inspectorConfirmationKey && !quickOpen) setDetailsOpen(true);
  }, [inspectorConfirmationKey, quickOpen]);
  useEffect(() => {
    if (inspectorOpenKey) { setQuickOpen(false); setDetailsOpen(true); }
  }, [inspectorOpenKey]);
  const editorAuxiliaryHost = useActiveEditorDock("auxiliary");
  const quickAvailable = editorLayout && Boolean(quickInspector);
  const dockInspector = active && !standalone && Boolean(providedInspector) && detailsOpen && !quickOpen && !workspaceHidden;
  const dockQuickInspector = active && quickOpen && quickAvailable && !workspaceHidden;
  const revealTaskPanel = useCallback(() => { setDetailsOpen(false); setConversationFocused(false); onReleaseResourcePane?.(); }, [onReleaseResourcePane]);
  const openQuickInspector = useCallback(() => { setDetailsOpen(false); setQuickOpen(true); }, []);
  const focusConversation = editingPaneNeedsRoom && conversationFocused && !chat.hidden;
  const panelContext = useMemo(() => ({ inspectorOpen: dockInspector || dockQuickInspector, resourceOpen, conversationFocused: focusConversation, revealTaskPanel, openQuickInspector: quickAvailable ? openQuickInspector : undefined }), [dockInspector, dockQuickInspector, resourceOpen, focusConversation, revealTaskPanel, quickAvailable, openQuickInspector]);
  const resourceReplacesChat = !chat.hidden && (resourceOpen || (dockInspector || dockQuickInspector) && Boolean(editorAuxiliaryHost)) && contentWidth > 0 && contentWidth - panels.rightWidth - 280 < 440;
  const inspectorReplacesChat = !chat.hidden && dockInspector && !editorAuxiliaryHost && contentWidth > 0 && contentWidth - panels.rightWidth - 360 < 440;
  const conversationNode = useRef<HTMLElement>(null);
  const mainNode = useRef<HTMLElement>(null);
  const mainVisible = !workspaceHidden;
  const chatVisible = !chat.hidden && !inspectorReplacesChat && !resourceReplacesChat && (!editingPaneNeedsRoom || conversationFocused);
  const toggleConversation = () => {
    if (!chatVisible) closeInspectorForReference();
    if (chat.hidden || chatVisible) chat.toggle();
  };
  useEffect(() => {
    if (!chatVisible) conversationNode.current?.querySelectorAll("audio, video").forEach(media => (media as HTMLMediaElement).pause());
  }, [chatVisible]);
  useEffect(() => {
    if (!mainVisible) mainNode.current?.querySelectorAll("audio, video").forEach(media => (media as HTMLMediaElement).pause());
  }, [mainVisible]);
  useEffect(() => { setQuickOpen(false); }, [quickInspectorKey]);

  useEffect(() => {
    const stored = window.localStorage.getItem("ai-video-diary-theme") as ThemeMode | null;
    const initial = stored === "light" || stored === "dark" || stored === "system" ? stored : "system";
    applyTheme(initial);
    const query = window.matchMedia("(prefers-color-scheme: dark)");
    const sync = () => document.documentElement.dataset.themeMode === "system" && applyTheme("system");
    query.addEventListener("change", sync);
    return () => query.removeEventListener("change", sync);
  }, []);

  const refreshRecentTasks = useCallback(async (background = false) => {
    if (!workspaceReady) return;
    const request = ++taskReadSequence.current;
    const factsRevision = taskFactsRevision.current;
    if (!background) { setTaskReadState("loading"); setTaskReadError(null); }
    try {
      const payload = await listCreationTasks();
      if (request !== taskReadSequence.current) return;
      setTaskListFacts(current => applyTaskListSnapshot(current, payload.items, factsRevision !== taskFactsRevision.current));
      setTaskReadState("ready");
      setTaskReadError(null);
    } catch (cause) {
      if (request !== taskReadSequence.current) return;
      setTaskReadError(cause instanceof Error ? cause.message : "任务列表读取失败");
      setTaskReadState("error");
    }
  }, [workspaceReady]);

  useEffect(() => {
    void refreshRecentTasks();
    return () => { ++taskReadSequence.current; };
  }, [pathname, refreshRecentTasks]);

  const hasRunningTask = recentTasks.some(task => task.status === "running" || task.stage_status === "running");
  useEffect(() => {
    if (!workspaceReady || !hasRunningTask) return;
    let active = true;
    let reading = false;
    let timer: number | undefined;
    const schedule = () => {
      if (active && !document.hidden) timer = window.setTimeout(() => void read(), 15000);
    };
    async function read() {
      window.clearTimeout(timer);
      if (!active || reading || document.hidden) return;
      reading = true;
      await refreshRecentTasks(true);
      reading = false;
      schedule();
    }
    const onVisibility = () => {
      window.clearTimeout(timer);
      if (!document.hidden) void read();
    };
    schedule();
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      active = false;
      window.clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [hasRunningTask, refreshRecentTasks]);

  useEffect(() => {
    if (previousTaskProps.current === creationTasks) return;
    previousTaskProps.current = creationTasks;
    if (creationTasks === undefined || creationTasks.length === 0) return;
    ++taskFactsRevision.current;
    setTaskListFacts(current => applyTaskFactUpdates(current, creationTasks));
  }, [creationTasks]);

  useEffect(() => { setSidebarOpen(false); setDetailsOpen(false); }, [pathname]);

  function toggleTheme() {
    const next: ThemeMode = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    window.localStorage.setItem("ai-video-diary-theme", next);
    applyTheme(next);
  }

  const commandItems = useMemo<CommandPaletteItem[]>(() => [
    { id: "new-production", label: "新建视频", description: "说说想法", href: "/?new=1", icon: Plus },
    ...primaryNav.filter((item) => showMembersNav || item.href !== "/members").map((item) => ({
      id: `nav-${item.href}`,
      label: item.label,
      description: item.label === "首页" ? "查看任务、阻断和通知" : item.label === "定时任务" ? "安排到期时间，查看待确认步骤与执行记录" : `打开${item.label}`,
      href: item.href,
      icon: item.icon,
    })),
    ...recentTasks.map((task) => ({
      id: `task-${task.id}`,
      label: displayTaskName(task.name),
      description: `创作任务 · ${creationTaskShortStatus(task.stage_status, task.stage)}`,
      href: creationTaskHref(task),
      icon: Clapperboard,
    })),
    ...(showMembersNav ? [{ id: "members", label: "成员与空间", description: "管理成员与共享空间", href: "/settings?section=creators", icon: Users }] : []),
    { id: "health", label: "运行情况", description: "检查模型、本地服务和任务阻断", href: "/settings?section=runtime", icon: Activity },
    { id: "settings", label: "设置", description: "配置模型、目录和外部连接", href: "/settings", icon: Settings },
  ], [recentTasks, showMembersNav]);

  return (
    <div className={`desktop-shell ${editorShell.shell}${editorLayout ? " video-editor-shell" : ""}${panels.collapsed ? ` ${editorShell.collapsed}` : ""}`} style={{ "--editor-left": `${panels.leftWidth}px`, "--editor-right": `${panels.rightWidth}px` } as CSSProperties}>

      {sidebarOpen ? (
        <Button variant="ghost"
          aria-label="关闭侧栏"
          className="sidebar-backdrop"
          onClick={() => setSidebarOpen(false)}
          type="button"
        />
      ) : null}

      <DesktopHeader nativeMac={nativeMac} collapsed={panels.collapsed} onToggleNavigation={panels.toggleLeft} tabs={workspaceTabs} location={<>
            {!editorLayout && pathname.split("/").filter(Boolean).length > 1 ? <Button variant="ghost" aria-label="返回" className="shell-icon-button" onClick={() => router.back()} type="button">
              <ArrowLeft size={17} />
            </Button> : null}
            {toolbar ?? <Suspense fallback={<ToolbarLocationContent pathname={pathname} />}>
              <ToolbarLocation pathname={pathname} />
            </Suspense>}
      </>} actions={<>
            {workspaceReady ? <Suspense fallback={null}><PreflightDraftReturn /></Suspense> : null}
            {!standalone ? <IconButton label={chatVisible ? "收起助手" : "打开曜核哈力斯"} aria-pressed={chatVisible} onClick={toggleConversation}><MessageSquare size={16}/></IconButton> : null}
            {editorLayout ? <WorkspaceLayoutMenu conversationHidden={!chatVisible} workspaceHidden={workspaceHidden} onConversation={toggleConversation} onWorkspace={panels.toggleWorkspace}/> : null}
            {toolbarActions}
            <IconButton label="搜索" shortcut="⌘ K" aria-expanded={searchOpen} onClick={() => { setSearchParentId(undefined); setSearchOpen(true); }}><Search size={16} /></IconButton>
            {providedInspector ? <IconButton label="查看详情" description={detailsOpen ? "收起属性面板" : "查看当前对象属性"} aria-pressed={detailsOpen} onClick={() => { setQuickOpen(false); setDetailsOpen(value => !value); }}><PanelRight size={16} /></IconButton> : null}
            {panels.collapsed ? <><Button variant="ghost" type="button" aria-label="切换明暗主题" title="切换明暗主题" className="shell-icon-button" onClick={toggleTheme}><SunMoon size={16}/></Button><Link aria-label="设备设置" title="设备设置" className="shell-icon-button" href="/settings"><Settings size={16}/></Link></> : null}
            {!localAuthMode ? (
              <form action="/auth/signout" method="post">
                <Button variant="ghost" aria-label="退出登录" className="shell-icon-button" type="submit"><UserRound size={17} /></Button>
              </form>
            ) : null}
      </>} />
      <aside className={`desktop-sidebar${sidebarOpen ? " is-open" : ""}`}>
        <nav className="desktop-sidebar__scroll" aria-label="主要导航">
          <div className="desktop-nav-group">
            {!panels.collapsed ? <span className="desktop-nav-heading desktop-nav-heading--primary">功能导航</span> : null}
            <Suspense fallback={<NavigationRowsContent pathname={pathname} showMembers={showMembersNav} />}>
              <NavigationRows pathname={pathname} showMembers={showMembersNav} />
            </Suspense>
          </div>
          <Suspense fallback={null}>
            <ResourceNavigationRows pathname={pathname} searchParams={navSearchParams} collapsed={panels.collapsed} />
          </Suspense>

          {panels.collapsed ? <Link href="/?new=1" className="desktop-nav-row" aria-label="新建视频" title="新建视频"><Plus size={16}/></Link> : null}
          <div className="desktop-nav-section desktop-nav-section--creation-tasks">
            <div className="desktop-nav-heading"><Link href="/" title="查看全部创作任务">创作任务</Link><Link href="/?new=1" aria-label="新建视频" title="新建视频" className="shell-icon-button"><Plus size={16}/></Link></div>
            {!workspaceReady ? <p className="desktop-creation-tasks-empty" role="status">登录并完成首次设置后读取创作任务。</p> : null}
            {workspaceReady && taskReadState === "loading" ? <p className="desktop-creation-tasks-empty" role="status">{recentTasks.length ? "正在更新创作任务…" : "正在读取创作任务…"}</p> : null}
            {workspaceReady && taskReadState === "error" ? <div className="desktop-creation-tasks-empty" role="alert">
              <p>暂时无法读取创作任务。{recentTasks.length ? "已有任务仍可打开。" : ""}</p>
              <Button variant="ghost" className="shell-icon-button" aria-label="重新读取创作任务" title="重新读取创作任务" onClick={() => void refreshRecentTasks()} type="button"><RefreshCw size={16}/></Button>
              <details><summary>失败详情</summary><p>{taskReadError}</p></details>
            </div> : null}
            {workspaceReady && recentTasks.length ? recentTasks.map((task) => {
              const statusLabel = creationTaskShortStatus(task.stage_status, task.stage);
              return (
                <Link data-active-work={task.id === activeCreationTaskId || undefined} aria-current={pathname === creationTaskHref(task) ? "page" : undefined} className={`desktop-creation-task ${shellUi.task}`} aria-label={`${displayTaskName(task.name)} · ${statusLabel}`} title={`${displayTaskName(task.name)} · ${statusLabel}`} href={creationTaskHref(task)} key={task.id}>
                  <Clapperboard size={16} className={shellUi.stageIcon} />
                  <span className="desktop-creation-task__copy">
                    <strong>{displayTaskName(task.name)}</strong>
                    <small className={shellUi.taskState} data-status={task.stage_status}>{task.stage_status === "running" ? <LoaderCircle aria-hidden="true" className="is-spinning" size={12} strokeWidth={1.7} /> : null}{statusLabel}</small>
                  </span>
                </Link>
              );
            }) : workspaceReady && taskReadState === "ready" ? (
              <p className="desktop-creation-tasks-empty">还没有创作任务</p>
            ) : null}
            {taskListFacts.items.length > recentTasks.length ? <Link className={shellUi.allTasks} href="/">查看全部 {taskListFacts.items.length} 个任务</Link> : null}
          </div>
        </nav>

        <nav className={shellUi.creatorControls} aria-label="创作者与设备设置" hidden={panels.collapsed}>
          {members.length ? <Link className={shellUi.avatarGroup} data-avatar-group href="/settings?section=creators" aria-label={`${members.length}位创作者，打开成员设置`} title={`${members.map(member => member.name).join("、")}；不会切换当前身份`}>
            <Users size={16} aria-hidden="true"/><span>成员与空间</span>
          </Link> : <Link className={shellUi.memberStatus} href="/settings?section=creators" title={membersState === "loading" ? "正在读取成员名册" : membersState === "error" ? "成员名册暂不可读，打开设置处理" : localAuthMode ? "尚无成员，打开成员设置" : "打开成员设置"}>{membersState === "loading" ? "读取名册…" : membersState === "error" ? "名册暂不可读" : "成员设置"}</Link>}
          <div className={shellUi.quickControls}>
            <Button variant="ghost" className={shellUi.quickButton} type="button" aria-label="切换明暗主题" title="切换明暗主题" onClick={toggleTheme}><SunMoon size={16} aria-hidden="true"/></Button>
            <Link className={shellUi.quickButton} href="/settings" aria-label="设备设置" title={workspaceStatus ? `设备设置 · ${workspaceStatus.label}` : "设备设置"}><Settings size={16} aria-hidden="true"/></Link>
          </div>
        </nav>
        {!panels.collapsed ? <div className={`${editorShell.separator} ${editorShell.leftSeparator}`} {...panels.separator("left")} /> : null}
      </aside>

      <WorkspacePanelContext.Provider value={panelContext}>
      <section className={`desktop-workspace${editorLayout ? " editor-workspace" : ""}`}>
        <div ref={contentGrid} data-inspector-open={dockInspector && !editorAuxiliaryHost || undefined} className={`${editorShell.contentGrid}${dockInspector && !editorAuxiliaryHost ? ` ${editorShell.withInspector}` : ""}${!chatVisible ? ` ${editorShell.chatCollapsed}` : ""}${workspaceHidden ? ` ${editorShell.workspaceCollapsed}` : ""}${inspectorLayout === "reading" ? ` ${editorShell.readingInspector}` : ""}`} data-editor-toolbar-parent={editorLayout ? "" : undefined}>
          <section ref={conversationNode} data-conversation-pane onPlayCapture={event => { if (!chatVisible && event.target instanceof HTMLMediaElement) event.target.pause(); }} aria-label="曜核哈力斯" hidden={!chatVisible} className={editorShell.conversation}>
            <header className={editorShell.conversationHeader}><span><Image alt="" aria-hidden src="/yaohe-icon.png" width={22} height={22} unoptimized/>曜核哈力斯</span><div className={editorShell.conversationActions}><div data-assistant-header-actions className="assistant-header-actions"/><IconButton label="收起曜核哈力斯" onClick={chat.toggle}><PanelRight size={16}/></IconButton></div></header>
            <div className={editorShell.conversationContext}>{conversationActions ?? (assistantTarget?.task ? <ConversationTarget name={assistantTarget.task.name} onReturn={() => router.push(creationTaskHref(assistantTarget.task!))} onClear={clearTarget} /> : <span>新视频构思</span>)}</div>
            <div className={editorShell.conversationBody}><MediaVisibility visible={chatVisible}>
              {workspaceReady ? conversation || <HomeCommandComposer autoFocus={autoFocus} pageContext={assistantTarget?.task ? undefined : { pathname }} task={recentTasks.find(task => pathname === creationTaskHref(task)) ?? null} onTaskUpdated={() => router.refresh()} {...assistantTarget} /> : null}
            </MediaVisibility></div>
            <div className={`${editorShell.separator} ${editorShell.chatSeparator}`} {...panels.separator("right")} />
          </section>
          <main ref={mainNode} onPlayCapture={event => { if (!mainVisible && event.target instanceof HTMLMediaElement) event.target.pause(); }} className="desktop-main-scroll" data-scroll-region="main-y" onClick={event => { if (event.target instanceof Element && event.target.closest("[data-open-details]")) { setQuickOpen(false); setDetailsOpen(true); } }}>
            {!workspaceReady ? <div className={editorShell.bootNotice}><p role="status">{workspaceBoot.message}</p>{workspaceBoot.href ? <Link href={workspaceBoot.href}>{workspaceBoot.href === '/onboarding' ? '完成首次设置' : '登录账号'}</Link> : null}{workspaceBoot.retry ? <Button variant="outline" onClick={workspaceBoot.retry}>重新核验</Button> : null}</div> : null}
            <MediaVisibility visible={mainVisible}>{children}</MediaVisibility>
          </main>
          {providedInspector && !standalone ? (editorAuxiliaryHost ? createPortal(<WorkspaceInspector open={dockInspector} title={inspectorTitle} onClose={() => setDetailsOpen(false)}>{providedInspector}{dockInspector ? inspectorConfirmation : null}</WorkspaceInspector>, editorAuxiliaryHost) : <WorkspaceInspector open={dockInspector} title={inspectorTitle} onClose={() => setDetailsOpen(false)}>{providedInspector}{dockInspector ? inspectorConfirmation : null}</WorkspaceInspector>) : null}
          {quickInspector && editorAuxiliaryHost ? createPortal(<WorkspaceInspector open={dockQuickInspector} title={quickInspectorLabel ?? "镜头参数"} onClose={() => setQuickOpen(false)}>{dockQuickInspector ? quickInspector(() => { setQuickOpen(false); setDetailsOpen(true); }) : null}{dockQuickInspector ? inspectorConfirmation : null}</WorkspaceInspector>, editorAuxiliaryHost) : null}
        </div>

      </section>
      </WorkspacePanelContext.Provider>
      {active ? <KeyboardShortcuts onOpenSearch={target => { setSearchParentId(homeCardParentForTarget(target)); setSearchOpen(true); }} /> : null}
      {active && providedInspector && standalone ? <HomeCardDialog open={detailsOpen && !quickOpen && !panels.workspaceCollapsed} kind="detail" title={inspectorTitle} onClose={() => setDetailsOpen(false)}>
        {providedInspector}
        {detailsOpen && !quickOpen ? inspectorConfirmation : null}
      </HomeCardDialog> : null}
      {dockQuickInspector && quickInspector && !editorAuxiliaryHost ? <HomeCardDialog kind="detail" title={quickInspectorLabel ?? "镜头参数"} onClose={() => setQuickOpen(false)}>
        {quickInspector(() => { setQuickOpen(false); setDetailsOpen(true); })}
        {inspectorConfirmation}
      </HomeCardDialog> : null}
      {active ? <HelpPanel /> : null}
      <CommandPalette
        items={commandItems}
        onClose={() => setSearchOpen(false)}
        onNavigate={(href) => router.push(href)}
        open={active && searchOpen}
        parentLayerId={searchParentId}
      />
    </div>
  );
}
