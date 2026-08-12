"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { PixelIcon } from "@/components/PixelIcon";
import { toggleHelpPanel } from "@/lib/helpPanel";

const menuItems = [
  { href: "/", label: "创作" },
  { href: "/templates", label: "资源" },
  { href: "/knowledge", label: "知识" },
  { href: "/health", label: "运行" },
  { href: "/settings", label: "设置" },
];

export type RetroChromeWorkspaceStatusKind =
  | "unknown"
  | "connected"
  | "attention"
  | "failed";

export type RetroChromeWorkspaceStatus = {
  kind: RetroChromeWorkspaceStatusKind;
  label: string;
};

export type RetroChromeProps = {
  todayLabel: string;
  contextLabel: string;
  workspaceStatus?: RetroChromeWorkspaceStatus;
};

const unknownWorkspaceStatus: RetroChromeWorkspaceStatus = {
  kind: "unknown",
  label: "工作区状态未知",
};

export function RetroChrome({
  todayLabel,
  contextLabel,
  workspaceStatus = unknownWorkspaceStatus,
}: RetroChromeProps) {
  const router = useRouter();

  return (
    <>
      <header className="system-titlebar">
        <div className="system-title">
          <PixelIcon name="agent" />
          <strong>AI 视频日记</strong>
          <span>本地创作工作站</span>
        </div>
        <div
          aria-label="工作区状态"
          className={`system-title-status system-title-status--${workspaceStatus.kind}`}
          role="status"
        >
          <span aria-hidden="true" className="system-status-mark" />
          <span>{workspaceStatus.label}</span>
        </div>
      </header>

      <nav className="system-menubar" aria-label="应用菜单">
        {menuItems.map((item) => (
          <Link href={item.href} key={item.href}>
            {item.label}
          </Link>
        ))}
        <button onClick={() => toggleHelpPanel()} type="button">
          帮助
        </button>
      </nav>

      <div className="system-toolbar" aria-label="工作区工具栏">
        <div className="toolbar-navigation">
          <button
            className="toolbar-button"
            onClick={() => router.back()}
            type="button"
          >
            <span aria-hidden="true">←</span>
            <span>返回</span>
          </button>
          <Link className="toolbar-button" href="/">
            <PixelIcon name="desk" />
            <span>工作台</span>
          </Link>
        </div>

        <div className="workspace-location" aria-label="当前位置">
          <span className="workspace-location__label">当前工作区</span>
          <strong>{contextLabel}</strong>
        </div>

        <div className="toolbar-date" suppressHydrationWarning>
          <PixelIcon name="calendar" />
          <span>{todayLabel}</span>
        </div>
      </div>
    </>
  );
}
