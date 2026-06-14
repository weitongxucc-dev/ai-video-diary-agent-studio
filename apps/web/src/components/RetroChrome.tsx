"use client";

import Link from "next/link";
import type { KeyboardEvent } from "react";
import { GlobalSearchBox } from "@/components/GlobalSearchBox";
import { PixelIcon, type PixelIconName } from "@/components/PixelIcon";

type ChromeMenuLink = {
  href: string;
  label: string;
  note: string;
  icon: PixelIconName;
};

type ChromeMenuGroup = {
  label: string;
  summary: string;
  links: ChromeMenuLink[];
};

const chromeMenuGroups: ChromeMenuGroup[] = [
  {
    label: "内容库",
    summary: "内容模板、知识库和外部信息源",
    links: [
      { href: "/templates", label: "内容模板", note: "维护视频日记、工具测评和教程演示模板", icon: "product" },
      { href: "/knowledge", label: "知识库", note: "浏览本地 Obsidian 知识上下文", icon: "folder" },
      { href: "/sources", label: "信息源", note: "同步外部素材和手动链接", icon: "link" },
    ],
  },
  {
    label: "画像",
    summary: "创作者声音、偏好和反馈记忆",
    links: [
      { href: "/profile", label: "创作者画像", note: "编辑长期口吻、边界和样本", icon: "person" },
      { href: "/learning", label: "反馈学习", note: "查看 thumbs 反馈形成的偏好记忆", icon: "spark" },
    ],
  },
  {
    label: "运行",
    summary: "依赖健康、内测日志和问题报告",
    links: [
      { href: "/health", label: "运行健康", note: "检查 DeepSeek、ffmpeg、路径和来源", icon: "check" },
      { href: "/logs", label: "内测日志", note: "查看最近错误并导出问题报告包", icon: "code" },
    ],
  },
  {
    label: "设置",
    summary: "本地路径、密钥和生产参数",
    links: [
      { href: "/setup", label: "配置向导", note: "首次配置依赖、路径和模型能力", icon: "settings" },
      { href: "/settings", label: "运行设置", note: "编辑 API key、本地路径和视频输出目录", icon: "settings" },
    ],
  },
];

type RetroChromeProps = {
  todayLabel: string;
};

function WindowButton({ label, title }: { label: string; title: string }) {
  return (
    <button className="window-button" type="button" aria-label={title} disabled>
      {label}
    </button>
  );
}

function closeOpenSystemMenu(event: KeyboardEvent<HTMLElement>) {
  if (event.key !== "Escape") {
    return;
  }

  const openMenu = event.currentTarget.querySelector<HTMLDetailsElement>(
    ".system-menu-group[open]"
  );
  if (!openMenu) {
    return;
  }

  event.preventDefault();
  openMenu.open = false;
  openMenu.querySelector<HTMLElement>("summary")?.focus();
}

export function RetroChrome({ todayLabel }: RetroChromeProps) {
  return (
    <>
      <header className="system-titlebar">
        <div className="system-title">
          <PixelIcon name="agent" />
          <strong>AI视频日记 Agent Studio</strong>
          <span>v2.0-beta</span>
        </div>
        <div className="window-controls" aria-label="窗口控制">
          <WindowButton label="-" title="最小化" />
          <WindowButton label="□" title="最大化" />
          <WindowButton label="X" title="关闭" />
        </div>
      </header>

      <nav className="system-menubar" aria-label="系统菜单" onKeyDown={closeOpenSystemMenu}>
        {chromeMenuGroups.map((group) => (
          <details className="system-menu-group" key={group.label} name="system-menu">
            <summary>{group.label}</summary>
            <div className="system-menu-popover" role="menu">
              <div className="menu-panel-edit">
                <strong>{group.label}工作区</strong>
                <span>{group.summary}</span>
              </div>
              <div className="menu-panel-links">
                {group.links.map((link) => (
                  <Link
                    className="menu-panel-link"
                    href={link.href}
                    key={link.href}
                    role="menuitem"
                  >
                    <PixelIcon name={link.icon} />
                    <span>
                      <strong>{link.label}</strong>
                      <em>{link.note}</em>
                    </span>
                  </Link>
                ))}
              </div>
            </div>
          </details>
        ))}
      </nav>

      <div className="system-toolbar" aria-label="工作区工具栏">
        <div className="toolbar-navigation">
          <button className="toolbar-button" type="button" aria-label="返回" disabled>
            <span aria-hidden="true">←</span>
          </button>
          <button className="toolbar-button" type="button" aria-label="前进" disabled>
            <span aria-hidden="true">→</span>
          </button>
          <button className="toolbar-button" type="button" aria-label="主页" disabled>
            <span aria-hidden="true">⌂</span>
          </button>
        </div>

        <div className="address-box" aria-label="本地地址">
          <span>127.0.0.1:3000</span>
          <button type="button" aria-label="展开地址" disabled>▾</button>
        </div>

        <GlobalSearchBox
          className="toolbar-search"
          inputClassName="toolbar-search-input"
          placeholder="搜索页面、选题、素材..."
          showIcon
        />

        <div className="toolbar-date">
          <PixelIcon name="calendar" />
          <span>{todayLabel}</span>
        </div>
      </div>
    </>
  );
}
