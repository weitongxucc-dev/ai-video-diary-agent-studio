"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { PixelIcon, type PixelIconName } from "@/components/PixelIcon";
import { RetroChrome } from "@/components/RetroChrome";
import { VoiceCommandConsole } from "@/components/VoiceCommandConsole";

type NavItem = {
  href: string;
  label: string;
  icon: PixelIconName;
  disabled?: boolean;
};

const navItems: NavItem[] = [
  { href: "/", label: "今日工作台", icon: "desk" },
  { href: "/templates", label: "内容模板", icon: "product" },
  { href: "/profile", label: "创作者画像", icon: "person" },
  { href: "/topics", label: "选题池", icon: "note" },
  { href: "/script", label: "口播稿", icon: "script" },
  { href: "/video", label: "视频处理", icon: "video" },
  { href: "/edit-plan", label: "剪辑计划", icon: "clip" },
  { href: "/draft", label: "剪映草稿", icon: "clip" },
  { href: "/knowledge", label: "知识库", icon: "folder" },
  { href: "/sources", label: "信息源", icon: "link" },
  { href: "/learning", label: "反馈学习", icon: "spark" },
  { href: "/health", label: "运行健康", icon: "check" },
  { href: "/settings", label: "设置", icon: "settings" }
];

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const currentNavItem =
    navItems.find((item) =>
      item.href === "/" ? pathname === "/" : pathname.startsWith(item.href)
    ) ?? navItems[0];
  const todayLabel = new Intl.DateTimeFormat("zh-CN", {
    day: "2-digit",
    month: "2-digit",
    timeZone: "Asia/Shanghai",
    weekday: "short",
    year: "numeric"
  }).format(new Date());

  return (
    <div className="retro-desktop-shell">
      <div className="retro-window-frame">
        <RetroChrome todayLabel={todayLabel} />
        <div className="app-shell">
          <aside className="sidebar">
            <div className="brand">
              <span className="pixel-logo">
                <PixelIcon name="agent" />
              </span>
              <div>
                <strong>AI视频日记</strong>
                <span>Agent Studio</span>
              </div>
            </div>
            <nav>
              {navItems.map((item) => {
                const active =
                  item.href === "/"
                    ? pathname === "/"
                    : pathname.startsWith(item.href);

                if (item.disabled) {
                  return (
                    <span
                      key={item.href}
                      className="nav-item disabled"
                      title="后续阶段开放"
                      aria-disabled="true"
                    >
                      <PixelIcon name={item.icon} />
                      <span>{item.label}</span>
                    </span>
                  );
                }

                return (
                  <Link
                    key={item.href}
                    href={item.href}
                    className={`nav-item${active ? " active" : ""}`}
                    aria-current={active ? "page" : undefined}
                  >
                    <PixelIcon name={item.icon} />
                    <span>{item.label}</span>
                  </Link>
                );
              })}
            </nav>
            <div className="sidebar-note">
              <strong>专注创作，</strong>
              <span>记录每一步一点点</span>
              <span>- Agent Studio</span>
            </div>
          </aside>

          <main className="main-area">
            <header className="topbar">
              <div className="breadcrumb">{currentNavItem.label}</div>
              <div className="topbar-center" suppressHydrationWarning>
                <PixelIcon name="calendar" />
                <span>今日 · {todayLabel}</span>
              </div>
              <div className="topbar-actions">
                <input
                  className="search-box"
                  type="search"
                  placeholder="搜索 Episode、选题、素材..."
                  aria-label="搜索 Episode、选题、素材"
                  disabled
                />
                <button className="icon-button" type="button" aria-label="通知" disabled>
                  <PixelIcon name="bell" />
                </button>
                <span className="avatar">
                  <PixelIcon name="person" />
                </span>
              </div>
            </header>
            {children}
            <VoiceCommandConsole contextLabel={currentNavItem.label} />
          </main>
        </div>
      </div>
    </div>
  );
}
