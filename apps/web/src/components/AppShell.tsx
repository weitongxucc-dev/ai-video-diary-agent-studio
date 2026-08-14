"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { HelpPanel } from "@/components/HelpPanel";
import { KeyboardShortcuts } from "@/components/KeyboardShortcuts";
import { PixelIcon, type PixelIconName } from "@/components/PixelIcon";
import { PixelButton, PixelStatus } from "@/components/PixelPrimitives";
import {
  RetroChrome,
  type RetroChromeWorkspaceStatus,
} from "@/components/RetroChrome";
import { VoiceCommandConsole } from "@/components/VoiceCommandConsole";
import { toggleHelpPanel } from "@/lib/helpPanel";

type NavItem = {
  href: string;
  label: string;
  icon: PixelIconName;
};

const navItems: NavItem[] = [
  { href: "/", label: "今日工作台", icon: "desk" },
  { href: "/topics", label: "选题", icon: "note" },
  { href: "/script", label: "脚本", icon: "script" },
  { href: "/video", label: "视频", icon: "video" },
  { href: "/edit-plan", label: "剪辑计划", icon: "clip" },
  { href: "/draft", label: "剪映草稿", icon: "cover" },
  { href: "/history", label: "发布反馈", icon: "publish" },
  { href: "/health", label: "运行状态", icon: "check" },
];

const routeLabels = [
  ...navItems,
  { href: "/templates", label: "内容模板" },
  { href: "/knowledge", label: "知识库" },
  { href: "/sources", label: "信息源" },
  { href: "/learning", label: "反馈学习" },
  { href: "/settings", label: "设置" },
  { href: "/onboarding", label: "启动检查" },
  { href: "/profile", label: "创作者画像" },
  { href: "/privacy", label: "隐私与数据" },
  { href: "/terms", label: "使用条款" },
  { href: "/ui-preview", label: "阶段 A 组件预览" },
];

export type AppShellProps = {
  children: ReactNode;
  inspector?: ReactNode;
  inspectorTitle?: string;
  workspaceStatus?: RetroChromeWorkspaceStatus;
};

export function AppShell({
  children,
  inspector,
  inspectorTitle = "当前任务详情",
  workspaceStatus,
}: AppShellProps) {
  const localAuthMode = process.env.NEXT_PUBLIC_AUTH_MODE === "local";
  const pathname = usePathname() ?? "";
  const [inspectorOpen, setInspectorOpen] = useState(false);
  const inspectorTriggerRef = useRef<HTMLButtonElement>(null);
  const todayLabel = new Intl.DateTimeFormat("zh-CN", {
    day: "2-digit",
    month: "2-digit",
    timeZone: "Asia/Shanghai",
    weekday: "short",
    year: "numeric",
  }).format(new Date());

  const currentRoute =
    routeLabels.find((item) =>
      item.href === "/" ? pathname === "/" : pathname.startsWith(item.href),
    ) ?? routeLabels[0];

  useEffect(() => {
    if (!inspector) {
      setInspectorOpen(false);
      return;
    }

    const desktopQuery = window.matchMedia("(min-width: 1180px)");
    const syncInspector = () => setInspectorOpen(desktopQuery.matches);
    syncInspector();
    desktopQuery.addEventListener("change", syncInspector);
    return () => desktopQuery.removeEventListener("change", syncInspector);
  }, [inspector]);

  useEffect(() => {
    if (!inspectorOpen) {
      return;
    }
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape" && window.innerWidth < 1180) {
        setInspectorOpen(false);
        inspectorTriggerRef.current?.focus();
      }
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [inspectorOpen]);

  function closeInspector() {
    setInspectorOpen(false);
    inspectorTriggerRef.current?.focus();
  }

  return (
    <div className="retro-desktop-shell">
      <div className="retro-window-frame">
        <RetroChrome
          contextLabel={currentRoute.label}
          todayLabel={todayLabel}
          workspaceStatus={workspaceStatus}
        />
        <div
          className={`app-shell${inspector ? " has-inspector" : ""}${inspectorOpen ? " inspector-open" : ""}`}
        >
          <aside className="sidebar" data-scroll-region="sidebar-y">
            <Link aria-label="返回今日工作台" className="brand" href="/">
              <span className="pixel-logo">
                <PixelIcon name="agent" />
              </span>
              <span className="brand-copy">
                <strong>AI 视频日记</strong>
                <span>生产流程</span>
              </span>
            </Link>
            <nav aria-label="Episode 生产导航">
              {navItems.map((item) => {
                const active =
                  item.href === "/"
                    ? pathname === "/"
                    : pathname.startsWith(item.href);

                return (
                  <Link
                    aria-current={active ? "page" : undefined}
                    aria-label={item.label}
                    className={`nav-item${active ? " active" : ""}`}
                    href={item.href}
                    key={item.href}
                    title={item.label}
                  >
                    <PixelIcon name={item.icon} />
                    <span className="nav-label">{item.label}</span>
                  </Link>
                );
              })}
            </nav>
            <div className="sidebar-context">
              <span>当前流程</span>
              <strong>{currentRoute.label}</strong>
              <PixelStatus kind="idle" label="等待你的下一步" />
            </div>
          </aside>

          <section className="shell-workspace">
            <header className="topbar">
              <div className="breadcrumb">
                <span>当前任务</span>
                <strong>{currentRoute.label}</strong>
              </div>
              <div className="topbar-actions">
                {inspector ? (
                  <PixelButton
                    aria-controls="agent-inspector"
                    aria-expanded={inspectorOpen}
                    onClick={() => setInspectorOpen((open) => !open)}
                    ref={inspectorTriggerRef}
                    size="compact"
                    variant="secondary"
                  >
                    {inspectorOpen ? "关闭详情" : "查看详情"}
                  </PixelButton>
                ) : null}
                <PixelButton
                  onClick={() => toggleHelpPanel()}
                  size="compact"
                  variant="secondary"
                >
                  帮助
                </PixelButton>
                {!localAuthMode ? (
                  <form action="/auth/signout" method="post">
                    <button className="avatar" title="退出登录" type="submit">
                      <PixelIcon name="person" />
                      <span className="sr-only">退出登录</span>
                    </button>
                  </form>
                ) : null}
              </div>
            </header>

            <div className="shell-content-grid">
              <main className="shell-main-scroll" data-scroll-region="main-y">
                {children}
              </main>

              {inspector ? (
                <>
                  {inspectorOpen ? (
                    <button
                      aria-label="关闭任务详情"
                      className="inspector-backdrop"
                      onClick={closeInspector}
                      type="button"
                    />
                  ) : null}
                  <aside
                    aria-label={inspectorTitle}
                    className="shell-inspector"
                    data-scroll-region="inspector-y"
                    hidden={!inspectorOpen}
                    id="agent-inspector"
                  >
                    <header className="shell-inspector__header">
                      <div>
                        <span>INSPECTOR</span>
                        <h2>{inspectorTitle}</h2>
                      </div>
                      <button
                        aria-label="关闭任务详情"
                        className="inspector-close"
                        onClick={closeInspector}
                        type="button"
                      >
                        ×
                      </button>
                    </header>
                    <div className="shell-inspector__body">{inspector}</div>
                  </aside>
                </>
              ) : null}
            </div>

            <VoiceCommandConsole contextLabel={currentRoute.label} />
          </section>
          <KeyboardShortcuts />
          <HelpPanel />
        </div>
      </div>
    </div>
  );
}
