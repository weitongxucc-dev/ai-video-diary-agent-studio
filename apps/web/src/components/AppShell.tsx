"use client";

import Link from "next/link";
import Image from "next/image";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import {
  Activity,
  ArrowLeft,
  BookOpen,
  ChevronDown,
  CircleHelp,
  Clapperboard,
  FileStack,
  Home,
  Library,
  Menu,
  Moon,
  PanelLeft,
  Plus,
  Search,
  Settings,
  Sparkles,
  Sun,
  UserRound,
} from "lucide-react";
import {
  useEffect,
  useMemo,
  useState,
  Suspense,
  type ComponentType,
  type ReactNode,
  type CSSProperties,
} from "react";
import { HomeCommandComposer } from "@/components/HomeCommandComposer";
import { HomeCardDialog } from "@/components/HomeCardDialog";
import { HelpPanel } from "@/components/HelpPanel";
import { KeyboardShortcuts } from "@/components/KeyboardShortcuts";
import { CommandPalette, type CommandPaletteItem } from "@/components/CommandPalette";
import { listCreationTasks } from "@/lib/api";
import { creationTaskStatusPresentation } from "@/lib/creation-task-status";
import { toggleHelpPanel } from "@/lib/helpPanel";
import type { CreationTask } from "@/lib/types";

import { useEditorPanels } from "./useEditorPanels";
import editorShell from "./EditorShell.module.css";

type Icon = ComponentType<{ size?: number; strokeWidth?: number }>;
type NavItem = { href: string; label: string; icon: Icon; activePath?: string };
type ThemeMode = "system" | "light" | "dark";

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
  { href: "/knowledge", label: "内容资料", icon: BookOpen },
  { href: "/profiles", label: "人物声音", icon: UserRound },
  { href: "/templates", label: "视频模板", icon: FileStack },
  { href: "/production?view=automations", label: "定时任务", icon: Sparkles, activePath: "/automations" },
  { href: "/history", label: "发布记录", icon: Activity },
];

const routeLabels: Array<Pick<NavItem, "href" | "label" | "activePath">> = [
  ...primaryNav,
  { href: "/production", label: "创作任务" },
  { href: "/tasks", label: "创作任务" },
  { href: "/sources", label: "内容资料" },
  { href: "/history", label: "发布反馈" },
  { href: "/health", label: "运行情况" },
  { href: "/settings", label: "设置" },
  { href: "/onboarding", label: "启动检查" },
  { href: "/profile", label: "创作者画像" },
  { href: "/profiles", label: "人物声音" },
];

export type AppShellWorkspaceStatus = {
  kind: "unknown" | "connected" | "attention" | "failed";
  label: string;
};

export type AppShellProps = {
  children: ReactNode;
  creationTasks?: AppShellCreationTask[];
  inspector?: ReactNode;
  conversation?: ReactNode;
  autoFocus?: boolean;
  inspectorTitle?: string;
  workspaceStatus?: AppShellWorkspaceStatus;
  editorLayout?: boolean;
  toolbar?: ReactNode;
  toolbarActions?: ReactNode;
  inspectorDismissKey?: string;
};

function creationTaskState(task: CreationTask) {
  return creationTaskStatusPresentation(task.stage_status, task.stage);
}

function creationTaskHref(task: CreationTask) {
  return `/tasks/${encodeURIComponent(task.id)}`;
}

function mergeCreationTasks(...groups: CreationTask[][]): CreationTask[] {
  const seen = new Set<string>();
  return groups.flat().filter((task) => {
    if (seen.has(task.id)) return false;
    seen.add(task.id);
    return true;
  }).slice(0, 6);
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

function NavigationRows({ pathname }: { pathname: string }) {
  const searchParams = useSearchParams();
  return <NavigationRowsContent pathname={pathname} searchParams={searchParams} />;
}

function NavigationRowsContent({ pathname, searchParams }: { pathname: string; searchParams?: Pick<URLSearchParams, "get"> }) {
  return <>
    {primaryNav.map((item) => {
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

function applyTheme(mode: ThemeMode) {
  const resolved =
    mode === "system"
      ? window.matchMedia("(prefers-color-scheme: dark)").matches
        ? "dark"
        : "light"
      : mode;
  document.documentElement.dataset.theme = resolved;
  document.documentElement.dataset.themeMode = mode;
}

export function AppShell({
  children,
  creationTasks,
  inspector: providedInspector,
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
  const localAuthMode = process.env.NEXT_PUBLIC_AUTH_MODE === "local";
  const pathname = usePathname() ?? "";
  const router = useRouter();
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [themeMode, setThemeMode] = useState<ThemeMode>("system");
  const [searchOpen, setSearchOpen] = useState(false);
  const [recentTasks, setRecentTasks] = useState<CreationTask[]>(creationTasks ?? []);
  useEffect(() => { setDetailsOpen(false); }, [inspectorDismissKey]);

  useEffect(() => {
    const stored = window.localStorage.getItem("ai-video-diary-theme") as ThemeMode | null;
    const initial = stored === "light" || stored === "dark" || stored === "system" ? stored : "system";
    setThemeMode(initial);
    applyTheme(initial);
    const query = window.matchMedia("(prefers-color-scheme: dark)");
    const sync = () => initial === "system" && applyTheme("system");
    query.addEventListener("change", sync);
    return () => query.removeEventListener("change", sync);
  }, []);

  useEffect(() => {
    let active = true;
    listCreationTasks()
      .then((payload) => {
        if (!active) return;
        setRecentTasks((current) => mergeCreationTasks(payload.items, current));
      })
      .catch(() => undefined);
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    if (!creationTasks?.length) return;
    setRecentTasks((current) => mergeCreationTasks(creationTasks, current));
  }, [creationTasks]);

  useEffect(() => { setSidebarOpen(false); setDetailsOpen(false); }, [pathname]);

  function cycleTheme() {
    const next: ThemeMode = themeMode === "system" ? "light" : themeMode === "light" ? "dark" : "system";
    setThemeMode(next);
    window.localStorage.setItem("ai-video-diary-theme", next);
    applyTheme(next);
  }

  const ThemeIcon = themeMode === "dark" ? Moon : Sun;
  const commandItems = useMemo<CommandPaletteItem[]>(() => [
    { id: "new-production", label: "新建视频", description: "说说想法", href: "/?new=1", icon: Plus },
    ...primaryNav.map((item) => ({
      id: `nav-${item.href}`,
      label: item.label,
      description: item.label === "首页" ? "查看任务、阻断和通知" : `打开${item.label}`,
      href: item.href,
      icon: item.icon,
    })),
    ...recentTasks.map((task) => ({
      id: `task-${task.id}`,
      label: displayTaskName(task.name),
      description: `创作任务 · ${creationTaskState(task).label}`,
      href: creationTaskHref(task),
      icon: Clapperboard,
    })),
    { id: "health", label: "运行情况", description: "检查模型、本地服务和任务阻断", href: "/health", icon: Activity },
    { id: "settings", label: "设置", description: "配置模型、目录和外部连接", href: "/settings", icon: Settings },
  ], [recentTasks]);

  return (
    <div className={`desktop-shell ${editorShell.shell}${editorLayout ? " video-editor-shell" : ""}${panels.collapsed ? ` ${editorShell.collapsed}` : ""}`} style={{ "--editor-left": `${panels.leftWidth}px`, "--editor-right": `${panels.rightWidth}px` } as CSSProperties}>
      {sidebarOpen ? (
        <button
          aria-label="关闭侧栏"
          className="sidebar-backdrop"
          onClick={() => setSidebarOpen(false)}
          type="button"
        />
      ) : null}

      <aside className={`desktop-sidebar${sidebarOpen ? " is-open" : ""}`}>
        <div className="desktop-sidebar__traffic-spacer" aria-hidden="true" />
        <div className="desktop-sidebar__brand-row">
          <Link aria-label="返回首页" className="desktop-brand" href="/">
            <span className="desktop-brand__mark">
              <Image alt="" aria-hidden height={18} src="/favicon.svg" width={18} />
            </span>
            <span>智能视频日记</span>
          </Link>
          <button aria-label="搜索" aria-expanded={searchOpen} className="shell-icon-button" onClick={() => setSearchOpen(true)} type="button">
            <Search size={16} />
          </button>
        </div>

        <nav className="desktop-sidebar__scroll" aria-label="主要导航">
          <Link aria-label="新建视频" className="new-production-button" href="/?new=1">
            <Plus size={16} />
            <span>新建视频</span>

          </Link>

          <div className="desktop-nav-group">
            <Suspense fallback={<NavigationRowsContent pathname={pathname} />}>
              <NavigationRows pathname={pathname} />
            </Suspense>
          </div>

          <div className="desktop-nav-section desktop-nav-section--creation-tasks">
            <div className="desktop-nav-heading">创作任务</div>
            {recentTasks.length ? recentTasks.map((task) => {
              const taskState = creationTaskState(task);
              return (
                <Link aria-current={pathname === creationTaskHref(task) ? "page" : undefined} className="desktop-creation-task" href={creationTaskHref(task)} key={task.id}>
                  <span className={`creation-task-dot status-${taskState.tone}`} aria-hidden="true" />
                  <span className="desktop-creation-task__copy">
                    <strong>{displayTaskName(task.name)}</strong>
                    <small>{taskState.label}</small>
                  </span>
                </Link>
              );
            }) : (
              <p className="desktop-creation-tasks-empty">还没有创作任务</p>
            )}
          </div>
        </nav>

        <div className="desktop-sidebar__footer">
          <Link className={`desktop-nav-row${pathname.startsWith("/health") ? " is-active" : ""}`} href="/health" aria-label="运行情况">
            <Activity size={16} />
            <span>运行情况</span>
            {workspaceStatus ? <i className={`health-dot health-dot--${workspaceStatus.kind}`} title={workspaceStatus.label} /> : null}
          </Link>
          <Link className={`desktop-nav-row${pathname.startsWith("/settings") ? " is-active" : ""}`} href="/settings" aria-label="设置">
            <Settings size={16} />
            <span>设置</span>
          </Link>
          <div className="desktop-user-row">
            <span className="desktop-user-avatar"><UserRound size={14} /></span>
            <span>{localAuthMode ? "本机创作者" : "我的工作区"}</span>
            <button aria-label={`当前主题：${themeMode === "dark" ? "深色" : themeMode === "light" ? "浅色" : "跟随系统"}，点击切换`} className="shell-icon-button" onClick={cycleTheme} type="button">
              <ThemeIcon size={15} />
            </button>
          </div>
        </div>
        {!panels.collapsed ? <div className={`${editorShell.separator} ${editorShell.leftSeparator}`} {...panels.separator("left")} /> : null}
      </aside>

      <section className={`desktop-workspace${editorLayout ? " editor-workspace" : ""}`}>
        <header className="desktop-toolbar">
          <div className="desktop-toolbar__leading">
            <button aria-label="打开侧栏" className="shell-icon-button desktop-menu-button" onClick={() => setSidebarOpen(true)} type="button">
              <Menu size={17} />
            </button>
            {<button aria-label={panels.collapsed ? "展开导航栏" : "收起导航栏"} title={panels.collapsed ? "展开导航栏" : "收起导航栏"} className="shell-icon-button" onClick={panels.toggleLeft} type="button"><PanelLeft size={17} /></button>}
            {!editorLayout ? <button aria-label="返回" className="shell-icon-button" onClick={() => router.back()} type="button">
              <ArrowLeft size={17} />
            </button> : null}
            {toolbar ?? <Suspense fallback={<ToolbarLocationContent pathname={pathname} />}>
              <ToolbarLocation pathname={pathname} />
            </Suspense>}
          </div>
          <div className="desktop-toolbar__actions">
            {toolbarActions}
            <button aria-label="搜索" aria-expanded={searchOpen} className="shell-icon-button desktop-search-button" onClick={() => setSearchOpen(true)} type="button">
              <Search size={16} />

            </button>
            {providedInspector ? <button className={editorShell.detailsButton} onClick={() => setDetailsOpen(true)} type="button">查看详情</button> : null}
            <button aria-label="帮助" className="shell-icon-button" onClick={() => toggleHelpPanel()} type="button">
              <CircleHelp size={17} />
            </button>
            {!localAuthMode ? (
              <form action="/auth/signout" method="post">
                <button aria-label="退出登录" className="shell-icon-button" type="submit"><UserRound size={17} /></button>
              </form>
            ) : null}
          </div>
        </header>

        <div className={editorShell.contentGrid}>
          <section aria-label="创作助手" className={editorShell.conversation}>
            <header className={editorShell.conversationHeader}><span><Sparkles size={17} />创作助手</span><small>{pathname.startsWith("/tasks/") ? "当前视频" : "聊聊想法"}</small></header>
            <div className={editorShell.conversationBody}>
              {conversation || <HomeCommandComposer autoFocus={autoFocus} task={recentTasks.find(task => pathname === creationTaskHref(task)) ?? null} onTaskUpdated={() => router.refresh()} />}
            </div>
            <div className={`${editorShell.separator} ${editorShell.chatSeparator}`} {...panels.separator("right")} />
          </section>
          <main className="desktop-main-scroll" data-scroll-region="main-y" onClick={event => { if (event.target instanceof Element && event.target.closest("[data-open-details]")) setDetailsOpen(true); }}>{children}</main>
        </div>

      </section>
      <KeyboardShortcuts onOpenSearch={() => setSearchOpen(true)} />
      {detailsOpen && providedInspector ? <HomeCardDialog title={inspectorTitle} onClose={() => setDetailsOpen(false)}>{providedInspector}</HomeCardDialog> : null}
      <HelpPanel />
      <CommandPalette
        items={commandItems}
        onClose={() => setSearchOpen(false)}
        onNavigate={(href) => router.push(href)}
        open={searchOpen}
      />
    </div>
  );
}
