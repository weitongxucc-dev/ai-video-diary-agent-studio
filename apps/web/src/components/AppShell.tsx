"use client";

import Link from "next/link";
import Image from "next/image";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import {
  Activity,
  ArrowLeft,
  BookOpen,
  ChevronDown,
  LoaderCircle,
  Clapperboard,
  Columns2,
  FileStack,
  Home,
  Library,
  Menu,
  PanelLeft,
  PanelRight,
  Plus,
  RefreshCw,
  Search,
  Settings,
  Sparkles,
  SunMoon,
  UserRound,
  Users,
} from "lucide-react";
import {
  useEffect,
  useCallback,
  useMemo,
  useRef,
  useState,
  Suspense,
  type ComponentType,
  type ReactNode,
  type CSSProperties,
} from "react";
import { HomeCommandComposer } from "@/components/HomeCommandComposer";
import { PreflightDraftReturn } from "@/components/PreflightDraftReturn";
import { HomeCardDialog, homeCardParentForTarget } from "@/components/HomeCardDialog";
import { HelpPanel } from "@/components/HelpPanel";
import { KeyboardShortcuts } from "@/components/KeyboardShortcuts";
import { CommandPalette, type CommandPaletteItem } from "@/components/CommandPalette";
import { listCreationTasks, listLocalMembers, type LocalMember } from "@/lib/api";
import { initialTaskListFacts, applyTaskFactUpdates, applyTaskListSnapshot } from "./creation-task/task-list-facts";
import { creationTaskShortStatus } from "@/lib/creation-task-status";
import { CreationTaskStageIcon } from "@/components/CreationTaskStageIcon";
import type { CreationTask } from "@/lib/types";

import { useEditorPanels } from "./useEditorPanels";
import editorShell from "./EditorShell.module.css";
import shellUi from "./AppShellCompact.module.css";

type Icon = ComponentType<{ size?: number; strokeWidth?: number }>;
type NavItem = { href: string; label: string; icon: Icon; activePath?: string };
export type ThemeMode = "system" | "light" | "dark";

function displayTaskName(name?: string) {
  return (name || "未命名创作任务")
    .replace(/\bb-?roll\b/gi, "补充画面")
    .replace(/\bbatch\b/gi, "创作任务")
    .replace(/批次/g, "创作任务")
    .replace(/\bepisode\b/gi, "内容")
    .replace(/\brecipe\b/gi, "制作方案")
    .replace(/\bprovider\b/gi, "服务")
    .replace(/\bagent\b/gi, "智能助手")
    .replace(/混剪/g, "视频剪辑")
    .replace(/\bSRT\s+字幕/gi, "字幕");
}

export type AppShellCreationTask = CreationTask;

const primaryNav: NavItem[] = [
  { href: "/", label: "首页", icon: Home },
  { href: "/library", label: "素材库", icon: Library },
  { href: "/knowledge", label: "知识库", icon: BookOpen },
  { href: "/profiles", label: "人物与声音", icon: UserRound },
  { href: "/templates", label: "视频模板", icon: FileStack },
  { href: "/history", label: "发布与数据", icon: Activity },
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
  kind: "unknown" | "connected" | "attention" | "failed";
  label: string;
};

export type AppShellProps = {
  children: ReactNode;
  creationTasks?: AppShellCreationTask[];
  inspector?: ReactNode;
  inspectorConfirmation?: ReactNode;
  inspectorConfirmationKey?: string;
  quickInspector?: (openDetails: () => void) => ReactNode;
  quickInspectorKey?: string;
  quickInspectorLabel?: string;
  conversation?: ReactNode;
  autoFocus?: boolean;
  inspectorTitle?: string;
  workspaceStatus?: AppShellWorkspaceStatus;
  editorLayout?: boolean;
  toolbar?: ReactNode;
  toolbarActions?: ReactNode;
  inspectorDismissKey?: string;
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
      const active = isActive(pathname, item.href, item.activePath, searchParams);
      return (
        <Link
          aria-label={item.label}
          aria-current={active ? "page" : undefined}
          className={`desktop-nav-row${active ? " is-active" : ""}`}
          href={item.href}
          key={item.href}
        >
          <ItemIcon size={16} strokeWidth={1.8} />
          <span>{item.label}</span>
        </Link>
      );
    })}
  </>;
}

function ToolbarLocation({ pathname }: { pathname: string }) {
  const searchParams = useSearchParams();
  return <ToolbarLocationContent pathname={pathname} searchParams={searchParams} />;
}

function ToolbarLocationContent({ pathname, searchParams }: { pathname: string; searchParams?: Pick<URLSearchParams, "get"> }) {
  const currentRoute = currentRouteFor(pathname, searchParams);
  return <div className="desktop-location">
    <span>{currentRoute.label}</span>
    {pathname.startsWith("/production") ? <ChevronDown size={13} /> : null}
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
}

import { useLocalMemberIdentity } from "@/hooks/useLocalMemberIdentity";

export function AppShell({
  children,
  creationTasks,
  inspector: providedInspector,
  inspectorConfirmation,
  inspectorConfirmationKey,
  quickInspector,
  quickInspectorKey,
  quickInspectorLabel,
  conversation,
  autoFocus = false,
  inspectorDismissKey,
  inspectorTitle = "当前任务详情",
  workspaceStatus,
  editorLayout = false,
  toolbar,
  toolbarActions,
}: AppShellProps) {
  const panels = useEditorPanels(true);
  const [nativeMac, setNativeMac] = useState(false);
  useEffect(() => {
    // These two read-only fields are provided by the existing desktop preload;
    // the older per-control Window declaration only describes its callable API.
    const desktop = window.electronAPI as (NonNullable<Window["electronAPI"]> & { isElectron: boolean; platform: string }) | undefined;
    setNativeMac(desktop?.isElectron === true && desktop.platform === "darwin");
  }, []);
  // 身份引导：挂载时拉一次 whoami 并把令牌写进 localStorage（hook 内完成）。
  // 角色未知（加载中）时先显示“成员与空间”，避免 owner 首屏看不到自己的页面。
  const { role: localMemberRole, ready: identityReady, token: identityToken, error: identityError } = useLocalMemberIdentity();
  const showMembersNav = localMemberRole !== "member";
  const localAuthMode = process.env.NEXT_PUBLIC_AUTH_MODE === "local";
  const pathname = usePathname() ?? "";
  const router = useRouter();
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [quickOpen, setQuickOpen] = useState(false);
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
    if (inspectorConfirmationKey) setDetailsOpen(true);
  }, [inspectorConfirmationKey]);
  const quickAvailable = editorLayout && Boolean(quickInspector);
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
  }, []);

  useEffect(() => {
    void refreshRecentTasks();
    return () => { ++taskReadSequence.current; };
  }, [pathname, refreshRecentTasks]);

  const hasRunningTask = recentTasks.some(task => task.status === "running" || task.stage_status === "running");
  useEffect(() => {
    if (!hasRunningTask) return;
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
      description: item.label === "首页" ? "查看任务、阻断和通知" : `打开${item.label}`,
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
    { id: "scheduled-tasks", label: "定时任务", description: "安排到期时间，查看待确认步骤与执行记录", href: "/production?view=automations", icon: Sparkles },
    ...(showMembersNav ? [{ id: "members", label: "成员与空间", description: "管理成员与共享空间", href: "/settings?section=creators", icon: Users }] : []),
    { id: "health", label: "运行情况", description: "检查模型、本地服务和任务阻断", href: "/settings?section=runtime", icon: Activity },
    { id: "settings", label: "设置", description: "配置模型、目录和外部连接", href: "/settings", icon: Settings },
  ], [recentTasks, showMembersNav]);

  return (
    <div className={`desktop-shell ${editorShell.shell}${nativeMac ? ` ${editorShell.nativeMac}` : ""}${editorLayout ? " video-editor-shell" : ""}${panels.collapsed ? ` ${editorShell.collapsed}` : ""}`} style={{ "--editor-left": `${panels.leftWidth}px`, "--editor-right": `${panels.rightWidth}px` } as CSSProperties}>
      {nativeMac ? <div className={editorShell.nativeTitlebar} aria-hidden /> : null}
      {sidebarOpen ? (
        <button
          aria-label="关闭侧栏"
          className="sidebar-backdrop"
          onClick={() => setSidebarOpen(false)}
          type="button"
        />
      ) : null}

      <aside className={`desktop-sidebar${sidebarOpen ? " is-open" : ""}`}>
        <div className="desktop-sidebar__brand-row">
          <Link aria-label="返回首页" className="desktop-brand" href="/">
            <span className="desktop-brand__mark">
              <Image alt="" aria-hidden height={18} src="/yaohe-icon.png" unoptimized width={18} />
            </span>
            <span>曜核1.0</span>
          </Link>
          <button aria-label={panels.collapsed ? "展开导航栏" : "收起导航栏"} title={panels.collapsed ? "展开导航栏" : "收起导航栏"} className="shell-icon-button" onClick={panels.toggleLeft} type="button"><PanelLeft size={16} /></button>
        </div>

        <nav className="desktop-sidebar__scroll" aria-label="主要导航">
          <Link aria-label="新建视频" className="new-production-button" href="/?new=1">
            <Plus size={16} />
            <span>新建视频</span>

          </Link>

          <div className="desktop-nav-group">
            <Suspense fallback={<NavigationRowsContent pathname={pathname} showMembers={showMembersNav} />}>
              <NavigationRows pathname={pathname} showMembers={showMembersNav} />
            </Suspense>
          </div>

          <div className="desktop-nav-section desktop-nav-section--creation-tasks">
            <div className="desktop-nav-heading">创作任务</div>
            {taskReadState === "loading" ? <p className="desktop-creation-tasks-empty" role="status">{recentTasks.length ? "正在更新创作任务…" : "正在读取创作任务…"}</p> : null}
            {taskReadState === "error" ? <div className="desktop-creation-tasks-empty" role="alert">
              <p>暂时无法读取创作任务。{recentTasks.length ? "已有任务仍可打开。" : ""}</p>
              <button className="shell-icon-button" aria-label="重新读取创作任务" title="重新读取创作任务" onClick={() => void refreshRecentTasks()} type="button"><RefreshCw size={16}/></button>
              <details><summary>失败详情</summary><p>{taskReadError}</p></details>
            </div> : null}
            {recentTasks.length ? recentTasks.map((task) => {
              const statusLabel = creationTaskShortStatus(task.stage_status, task.stage);
              return (
                <Link aria-current={pathname === creationTaskHref(task) ? "page" : undefined} className={`desktop-creation-task ${shellUi.task}`} aria-label={`${displayTaskName(task.name)} · ${statusLabel}`} title={`${displayTaskName(task.name)} · ${statusLabel}`} href={creationTaskHref(task)} key={task.id}>
                  <CreationTaskStageIcon stage={task.stage} className={shellUi.stageIcon} />
                  <span className="desktop-creation-task__copy">
                    <strong>{displayTaskName(task.name)}</strong>
                    <small className={shellUi.taskState} data-status={task.stage_status}>{task.stage_status === "running" ? <LoaderCircle aria-hidden="true" className="is-spinning" size={12} strokeWidth={1.7} /> : null}{statusLabel}</small>
                  </span>
                </Link>
              );
            }) : taskReadState === "ready" ? (
              <p className="desktop-creation-tasks-empty">还没有创作任务</p>
            ) : null}
          </div>
        </nav>

        <nav className={shellUi.creatorControls} aria-label="创作者与设备设置" hidden={panels.collapsed}>
          {members.length ? <Link className={shellUi.avatarGroup} data-avatar-group href="/settings?section=creators" aria-label={`${members.length}位创作者，打开成员设置`} title={`${members.map(member => member.name).join("、")}；不会切换当前身份`}>
            {members.slice(0, 3).map(member => <span className={shellUi.avatar} data-avatar key={member.member_id} title={member.name} aria-hidden="true">{Array.from(member.name)[0]}</span>)}
            {members.length > 3 ? <span className={shellUi.avatarCount} data-avatar-count aria-hidden="true">+{members.length - 3}</span> : null}
          </Link> : <Link className={shellUi.memberStatus} href="/settings?section=creators" title={membersState === "loading" ? "正在读取成员名册" : membersState === "error" ? "成员名册暂不可读，打开设置处理" : localAuthMode ? "尚无成员，打开成员设置" : "打开成员设置"}>{membersState === "loading" ? "读取名册…" : membersState === "error" ? "名册暂不可读" : "成员设置"}</Link>}
          <div className={shellUi.quickControls}>
            <button className={shellUi.quickButton} type="button" aria-label="切换明暗主题" title="切换明暗主题" onClick={toggleTheme}><SunMoon size={16} aria-hidden="true"/></button>
            <Link className={shellUi.quickButton} href="/settings" aria-label="设备设置" title={workspaceStatus ? `设备设置 · ${workspaceStatus.label}` : "设备设置"}><Settings size={16} aria-hidden="true"/></Link>
          </div>
        </nav>
        {!panels.collapsed ? <div className={`${editorShell.separator} ${editorShell.leftSeparator}`} {...panels.separator("left")} /> : null}
      </aside>

      <section className={`desktop-workspace${editorLayout ? " editor-workspace" : ""}`}>
        <div className={`${editorShell.contentGrid}${panels.chatCollapsed ? ` ${editorShell.chatCollapsed}` : ""}${panels.workspaceCollapsed ? ` ${editorShell.workspaceCollapsed}` : ""}`} data-editor-toolbar-parent={editorLayout ? "" : undefined}>
        <header className="desktop-toolbar">
          <div className="desktop-toolbar__leading">
            <button aria-label="打开侧栏" className="shell-icon-button desktop-menu-button" onClick={() => setSidebarOpen(true)} type="button">
              <Menu size={17} />
            </button>
            {!editorLayout ? <button aria-label="返回" className="shell-icon-button" onClick={() => router.back()} type="button">
              <ArrowLeft size={17} />
            </button> : null}
            {toolbar ?? <Suspense fallback={<ToolbarLocationContent pathname={pathname} />}>
              <ToolbarLocation pathname={pathname} />
            </Suspense>}
            <Suspense fallback={null}><PreflightDraftReturn /></Suspense>
          </div>
          <div className="desktop-toolbar__actions">
            <button aria-label={panels.workspaceCollapsed ? "展开工作区" : "收起工作区"} title={panels.workspaceCollapsed ? "展开工作区" : "收起工作区"} className="shell-icon-button" onClick={panels.toggleWorkspace} type="button"><Columns2 size={16} /></button>
            {toolbarActions}
            {quickAvailable ? <button aria-label="编辑镜头参数" className={editorShell.detailsButton} onClick={() => { setDetailsOpen(true); setQuickOpen(true); }} type="button">镜头参数</button> : null}
            <button aria-label="搜索" aria-expanded={searchOpen} className="shell-icon-button desktop-search-button" onClick={() => { setSearchParentId(undefined); setSearchOpen(true); }} type="button">
              <Search size={16} />

            </button>
            {providedInspector ? <button className={editorShell.detailsButton} onClick={() => setDetailsOpen(true)} type="button">查看详情</button> : null}
            {panels.collapsed ? <><button type="button" aria-label="切换明暗主题" title="切换明暗主题" className="shell-icon-button" onClick={toggleTheme}><SunMoon size={16}/></button><Link aria-label="设备设置" title="设备设置" className="shell-icon-button" href="/settings"><Settings size={16}/></Link></> : null}
            {!localAuthMode ? (
              <form action="/auth/signout" method="post">
                <button aria-label="退出登录" className="shell-icon-button" type="submit"><UserRound size={17} /></button>
              </form>
            ) : null}
          </div>
        </header>

          <section aria-label="创作助手" className={editorShell.conversation}>
            <header className={editorShell.conversationHeader}><span><Sparkles size={17} />创作助手</span><div className={editorShell.conversationActions}><div data-assistant-header-actions className="assistant-header-actions"/><button aria-label={panels.chatCollapsed ? "展开对话栏" : "收起对话栏"} title={panels.chatCollapsed ? "展开对话栏" : "收起对话栏"} className="shell-icon-button" onClick={panels.toggleChat} type="button"><PanelRight size={16} /></button></div></header>
            <div className={editorShell.conversationBody}>
              {conversation || <HomeCommandComposer autoFocus={autoFocus} pageContext={{ pathname }} task={recentTasks.find(task => pathname === creationTaskHref(task)) ?? null} onTaskUpdated={() => router.refresh()} />}
            </div>
            <div className={`${editorShell.separator} ${editorShell.chatSeparator}`} {...panels.separator("right")} />
          </section>
          <main className="desktop-main-scroll" data-scroll-region="main-y" onClick={event => { if (event.target instanceof Element && event.target.closest("[data-open-details]")) setDetailsOpen(true); }}>
            {children}
          </main>
        </div>

      </section>
      <KeyboardShortcuts onOpenSearch={target => { setSearchParentId(homeCardParentForTarget(target)); setSearchOpen(true); }} />
      {providedInspector ? <HomeCardDialog open={detailsOpen && !panels.workspaceCollapsed} kind="detail" title={inspectorTitle} onClose={() => setDetailsOpen(false)}>
        {providedInspector}
        {quickOpen && quickInspector ? <HomeCardDialog kind="config" title={quickInspectorLabel ?? "镜头参数"} onClose={() => setQuickOpen(false)}>
          {quickInspector(() => { setQuickOpen(false); setDetailsOpen(true); })}
          {inspectorConfirmation}
        </HomeCardDialog> : null}
        {!quickOpen ? inspectorConfirmation : null}
      </HomeCardDialog> : null}
      <HelpPanel />
      <CommandPalette
        items={commandItems}
        onClose={() => setSearchOpen(false)}
        onNavigate={(href) => router.push(href)}
        open={searchOpen}
        parentLayerId={searchParentId}
      />
    </div>
  );
}
