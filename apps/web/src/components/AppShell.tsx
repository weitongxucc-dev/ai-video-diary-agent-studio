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
  FileStack,
  Home,
  Library,
  Menu,
  Moon,
  PanelRight,
  Plus,
  Search,
  Settings,
  Sparkles,
  Sun,
  UserRound,
  X,
} from "lucide-react";
import {
  useEffect,
  useRef,
  useState,
  Suspense,
  type ComponentType,
  type ReactNode,
} from "react";
import { HelpPanel } from "@/components/HelpPanel";
import { KeyboardShortcuts } from "@/components/KeyboardShortcuts";
import { listCreationTasks } from "@/lib/api";
import { creationTaskStatusPresentation } from "@/lib/creation-task-status";
import { toggleHelpPanel } from "@/lib/helpPanel";
import type { CreationTask } from "@/lib/types";

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
  { href: "/profiles", label: "人物与声音", icon: UserRound },
  { href: "/templates", label: "视频模板", icon: FileStack },
  { href: "/production?view=automations", label: "自动任务", icon: Sparkles, activePath: "/automations" },
  { href: "/history", label: "发布与数据", icon: Activity },
];

const routeLabels: Array<Pick<NavItem, "href" | "label" | "activePath">> = [
  ...primaryNav,
  { href: "/production", label: "创作任务" },
  { href: "/sources", label: "内容资料" },
  { href: "/topics", label: "选题" },
  { href: "/script", label: "脚本" },
  { href: "/draft", label: "导出" },
  { href: "/history", label: "发布反馈" },
  { href: "/health", label: "运行健康" },
  { href: "/settings", label: "设置" },
  { href: "/onboarding", label: "启动检查" },
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
  inspectorTitle?: string;
  workspaceStatus?: AppShellWorkspaceStatus;
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
  inspector,
  inspectorTitle = "当前任务详情",
  workspaceStatus,
}: AppShellProps) {
  const localAuthMode = process.env.NEXT_PUBLIC_AUTH_MODE === "local";
  const pathname = usePathname() ?? "";
  const router = useRouter();
  const [inspectorOpen, setInspectorOpen] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [themeMode, setThemeMode] = useState<ThemeMode>("system");
  const [recentTasks, setRecentTasks] = useState<CreationTask[]>(creationTasks ?? []);
  const inspectorTriggerRef = useRef<HTMLButtonElement>(null);

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

  useEffect(() => {
    if (!inspector) {
      setInspectorOpen(false);
      return;
    }
    const desktopQuery = window.matchMedia("(min-width: 1280px)");
    const syncInspector = () => setInspectorOpen(desktopQuery.matches);
    syncInspector();
    desktopQuery.addEventListener("change", syncInspector);
    return () => desktopQuery.removeEventListener("change", syncInspector);
  }, [inspector]);

  useEffect(() => {
    setSidebarOpen(false);
  }, [pathname]);

  useEffect(() => {
    if (!inspectorOpen) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape" && window.innerWidth < 1280) {
        setInspectorOpen(false);
        inspectorTriggerRef.current?.focus();
      }
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [inspectorOpen]);

  function cycleTheme() {
    const next: ThemeMode = themeMode === "system" ? "light" : themeMode === "light" ? "dark" : "system";
    setThemeMode(next);
    window.localStorage.setItem("ai-video-diary-theme", next);
    applyTheme(next);
  }

  const ThemeIcon = themeMode === "dark" ? Moon : Sun;

  return (
    <div className="desktop-shell">
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
            <span>AI 视频日记</span>
          </Link>
          <button aria-label="搜索" className="shell-icon-button" type="button">
            <Search size={16} />
          </button>
        </div>

        <nav className="desktop-sidebar__scroll" aria-label="主要导航">
          <Link className="new-production-button" href="/production?new=1">
            <Plus size={16} />
            <span>开始制作新视频</span>
            <kbd>⌘N</kbd>
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
                <Link className="desktop-creation-task" href={creationTaskHref(task)} key={task.id}>
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
          <Link className={`desktop-nav-row${pathname.startsWith("/health") ? " is-active" : ""}`} href="/health">
            <Activity size={16} />
            <span>运行情况</span>
            {workspaceStatus ? <i className={`health-dot health-dot--${workspaceStatus.kind}`} title={workspaceStatus.label} /> : null}
          </Link>
          <Link className={`desktop-nav-row${pathname.startsWith("/settings") ? " is-active" : ""}`} href="/settings">
            <Settings size={16} />
            <span>设置</span>
          </Link>
          <div className="desktop-user-row">
            <span className="desktop-user-avatar"><UserRound size={14} /></span>
            <span>{localAuthMode ? "本机创作者" : "我的工作区"}</span>
            <button aria-label={`当前主题：${themeMode}，点击切换`} className="shell-icon-button" onClick={cycleTheme} type="button">
              <ThemeIcon size={15} />
            </button>
          </div>
        </div>
      </aside>

      <section className="desktop-workspace">
        <header className="desktop-toolbar">
          <div className="desktop-toolbar__leading">
            <button aria-label="打开侧栏" className="shell-icon-button desktop-menu-button" onClick={() => setSidebarOpen(true)} type="button">
              <Menu size={17} />
            </button>
            <button aria-label="返回" className="shell-icon-button" onClick={() => router.back()} type="button">
              <ArrowLeft size={17} />
            </button>
            <Suspense fallback={<ToolbarLocationContent pathname={pathname} />}>
              <ToolbarLocation pathname={pathname} />
            </Suspense>
          </div>
          <div className="desktop-toolbar__actions">
            <button aria-label="搜索" className="shell-icon-button desktop-search-button" type="button">
              <Search size={16} />
              <kbd>⌘K</kbd>
            </button>
            {inspector ? (
              <button
                aria-controls="agent-inspector"
                aria-expanded={inspectorOpen}
                aria-label={inspectorOpen ? "关闭详情" : "打开详情"}
                className={`shell-icon-button${inspectorOpen ? " is-active" : ""}`}
                onClick={() => setInspectorOpen((open) => !open)}
                ref={inspectorTriggerRef}
                type="button"
              >
                <PanelRight size={17} />
              </button>
            ) : null}
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

        <div className={`desktop-content-grid${inspector ? " has-inspector" : ""}${inspectorOpen ? " inspector-open" : ""}`}>
          <main className="desktop-main-scroll" data-scroll-region="main-y">{children}</main>

          {inspector ? (
            <>
              {inspectorOpen ? <button aria-label="关闭详情" className="inspector-backdrop" onClick={() => setInspectorOpen(false)} type="button" /> : null}
              <aside aria-label={inspectorTitle} className="desktop-inspector" hidden={!inspectorOpen} id="agent-inspector">
                <header className="desktop-inspector__header">
                  <h2>{inspectorTitle}</h2>
                  <button aria-label="关闭详情" className="shell-icon-button" onClick={() => setInspectorOpen(false)} type="button"><X size={16} /></button>
                </header>
                <div className="desktop-inspector__body">{inspector}</div>
              </aside>
            </>
          ) : null}
        </div>

      </section>
      <KeyboardShortcuts />
      <HelpPanel />
    </div>
  );
}
