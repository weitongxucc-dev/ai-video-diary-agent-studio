"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import {
  Activity,
  ArrowLeft,
  BookOpen,
  ChevronDown,
  ChevronRight,
  CircleHelp,
  FileStack,
  FolderKanban,
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
  type ComponentType,
  type ReactNode,
} from "react";
import { HelpPanel } from "@/components/HelpPanel";
import { KeyboardShortcuts } from "@/components/KeyboardShortcuts";
import { VoiceCommandConsole } from "@/components/VoiceCommandConsole";
import { toggleHelpPanel } from "@/lib/helpPanel";

type Icon = ComponentType<{ size?: number; strokeWidth?: number }>;
type NavItem = { href: string; label: string; icon: Icon };
type ThemeMode = "system" | "light" | "dark";

const primaryNav: NavItem[] = [
  { href: "/", label: "首页", icon: Home },
  { href: "/production", label: "生产队列", icon: FolderKanban },
  { href: "/library", label: "素材库", icon: Library },
];

const resourceNav: NavItem[] = [
  { href: "/templates", label: "内容模板", icon: FileStack },
  { href: "/knowledge", label: "知识库", icon: BookOpen },
  { href: "/sources", label: "信息源", icon: Sparkles },
];

const routeLabels = [
  ...primaryNav,
  ...resourceNav,
  { href: "/topics", label: "选题" },
  { href: "/script", label: "脚本" },
  { href: "/video", label: "素材处理" },
  { href: "/edit-plan", label: "混剪审核" },
  { href: "/draft", label: "导出" },
  { href: "/history", label: "发布反馈" },
  { href: "/health", label: "运行健康" },
  { href: "/settings", label: "设置" },
  { href: "/onboarding", label: "启动检查" },
  { href: "/profile", label: "创作者画像" },
];

export type AppShellWorkspaceStatus = {
  kind: "unknown" | "connected" | "attention" | "failed";
  label: string;
};

export type AppShellProps = {
  children: ReactNode;
  inspector?: ReactNode;
  inspectorTitle?: string;
  workspaceStatus?: AppShellWorkspaceStatus;
};

function isActive(pathname: string, href: string): boolean {
  return href === "/" ? pathname === "/" : pathname.startsWith(href);
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
  inspector,
  inspectorTitle = "当前任务详情",
  workspaceStatus,
}: AppShellProps) {
  const localAuthMode = process.env.NEXT_PUBLIC_AUTH_MODE === "local";
  const pathname = usePathname() ?? "";
  const router = useRouter();
  const [inspectorOpen, setInspectorOpen] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [resourcesOpen, setResourcesOpen] = useState(true);
  const [themeMode, setThemeMode] = useState<ThemeMode>("system");
  const [recentBatches, setRecentBatches] = useState<Array<{ id: string; name: string }>>([]);
  const inspectorTriggerRef = useRef<HTMLButtonElement>(null);

  const currentRoute =
    routeLabels
      .filter((item) => isActive(pathname, item.href))
      .sort((a, b) => b.href.length - a.href.length)[0] ?? routeLabels[0];

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
    fetch("/api/production/foundation", { cache: "no-store" })
      .then((response) => (response.ok ? response.json() : null))
      .then((payload) => {
        if (!active || !payload?.recent_batches) return;
        setRecentBatches(payload.recent_batches.slice(0, 6));
      })
      .catch(() => undefined);
    return () => {
      active = false;
    };
  }, []);

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
            <span className="desktop-brand__mark"><Sparkles size={15} /></span>
            <span>AI 视频日记</span>
          </Link>
          <button aria-label="搜索" className="shell-icon-button" type="button">
            <Search size={16} />
          </button>
        </div>

        <nav className="desktop-sidebar__scroll" aria-label="主要导航">
          <Link className="new-production-button" href="/production?new=1">
            <Plus size={16} />
            <span>新建生产</span>
            <kbd>⌘N</kbd>
          </Link>

          <div className="desktop-nav-group">
            {primaryNav.map((item) => {
              const ItemIcon = item.icon;
              return (
                <Link
                  aria-current={isActive(pathname, item.href) ? "page" : undefined}
                  className={`desktop-nav-row${isActive(pathname, item.href) ? " is-active" : ""}`}
                  href={item.href}
                  key={item.href}
                >
                  <ItemIcon size={16} strokeWidth={1.8} />
                  <span>{item.label}</span>
                </Link>
              );
            })}
          </div>

          {recentBatches.length ? (
            <div className="desktop-nav-section">
              <div className="desktop-nav-heading">最近批次</div>
              {recentBatches.map((batch) => (
                <Link className="desktop-nav-row desktop-nav-row--recent" href={`/production?batch=${batch.id}`} key={batch.id}>
                  <FolderKanban size={15} strokeWidth={1.7} />
                  <span>{batch.name || "未命名批次"}</span>
                </Link>
              ))}
            </div>
          ) : null}

          <div className="desktop-nav-section">
            <button className="desktop-nav-heading desktop-nav-heading--button" onClick={() => setResourcesOpen((value) => !value)} type="button">
              <span>资源</span>
              {resourcesOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
            </button>
            {resourcesOpen
              ? resourceNav.map((item) => {
                  const ItemIcon = item.icon;
                  return (
                    <Link
                      aria-current={isActive(pathname, item.href) ? "page" : undefined}
                      className={`desktop-nav-row${isActive(pathname, item.href) ? " is-active" : ""}`}
                      href={item.href}
                      key={item.href}
                    >
                      <ItemIcon size={16} strokeWidth={1.8} />
                      <span>{item.label}</span>
                    </Link>
                  );
                })
              : null}
          </div>
        </nav>

        <div className="desktop-sidebar__footer">
          <Link className={`desktop-nav-row${pathname.startsWith("/health") ? " is-active" : ""}`} href="/health">
            <Activity size={16} />
            <span>运行健康</span>
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
            <div className="desktop-location">
              <span>{currentRoute.label}</span>
              {pathname.startsWith("/production") ? <ChevronDown size={13} /> : null}
            </div>
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
                  <div><span>详情</span><h2>{inspectorTitle}</h2></div>
                  <button aria-label="关闭详情" className="shell-icon-button" onClick={() => setInspectorOpen(false)} type="button"><X size={16} /></button>
                </header>
                <div className="desktop-inspector__body">{inspector}</div>
              </aside>
            </>
          ) : null}
        </div>

        {pathname !== "/" ? <VoiceCommandConsole contextLabel={currentRoute.label} /> : null}
      </section>
      <KeyboardShortcuts />
      <HelpPanel />
    </div>
  );
}
