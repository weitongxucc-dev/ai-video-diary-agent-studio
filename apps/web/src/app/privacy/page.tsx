import type { ReactNode } from "react";
import Link from "next/link";
import { AppShell } from "@/components/AppShell";
import { PixelIcon } from "@/components/PixelIcon";

export const dynamic = "force-dynamic";

type Section = {
  title: string;
  icon: "archive" | "settings" | "video" | "agent" | "doc";
  paragraphs: ReactNode[];
};

const SECTIONS: Section[] = [
  {
    title: "本地优先，不收集个人视频",
    icon: "archive",
    paragraphs: [
      "AI 视频日记是本地优先的桌面工具。所有视频、口播稿、选题和反馈都保存在你的本机资料目录、视频目录和数据库中。",
      "我们（即项目作者与后端进程）不会把你的视频、音频或文字稿上传到任何第三方服务器，也没有任何形式的远程索引或转售。",
      "只有在你显式点击「标记已发布」并填写第三方平台 URL 后，发布链接才会写入本地发布记录；这一步也不会上传视频本体。",
    ],
  },
  {
    title: "API Key 与本机 .env 存储",
    icon: "settings",
    paragraphs: [
      <>
        DeepSeek、Product Hunt、腾讯云 ASR 等第三方服务的密钥仅保存在本机运行时配置文件中（通常是{" "}
        <code>~/.ai-video-diary/studio.db</code> 内嵌或同级 .env 文件），从不发送到我们的服务器。
      </>,
      "设置页密钥字段在保存前显示「已配置/未配置」徽标，永不回显明文；输入框留空时也不会修改现有值。",
      "如果你的电脑多人共用，建议使用操作系统级权限（chmod 600 / 加密磁盘）保护这些文件。我们不为此提供任何云端托管密钥的能力。",
    ],
  },
  {
    title: "DeepSeek 与腾讯云 ASR 数据使用",
    icon: "agent",
    paragraphs: [
      "调用文字模型时，本机会把选题素材摘要、当前创作内容的脚本骨架和反馈偏好作为上下文发送至已配置的模型服务。我们仅在生成内容时使用最小必要的上下文，不持久化任何用户数据。",
      "调用腾讯云 ASR（语音转写）时，音频文件直传至腾讯云 ASR 服务，结果回写到本地 SQLite。该音频文件不会保存在腾讯云侧，处理完成后即被丢弃。",
      "可选的本地 Whisper 模式完全离线运行，音频不离开你的电脑。",
    ],
  },
  {
    title: "Hermes 雷达与外链 URL",
    icon: "video",
    paragraphs: [
      "信息源页会读取你已配置的信息源以及粘贴的外链 URL。这些内容仅用于辅助系统提炼选题，不会自动发布到任何外部平台。",
      <>
        你随时可以在 <Link href="/sources">信息源</Link> 页查看、删除或归档任一条目；本地 Obsidian Vault 的笔记也是同样的逻辑。
      </>,
    ],
  },
  {
    title: "日志仅用于本地调试",
    icon: "doc",
    paragraphs: [
      <>
        所有 backend 日志输出到本机 stdout 或 <code>~/.ai-video-diary/logs/</code> 目录，仅用于排查脚本生成失败、视频处理报错等本地问题。
      </>,
      "默认不会上传日志；如果你选择导出日志粘贴给我们协助排查，请先脱敏任何密钥与可识别信息。",
    ],
  },
];

export default function PrivacyPage() {
  return (
    <AppShell>
      <div className="legal-page">
        <section className="legal-main ui-panel">
          <header className="legal-header">
            <span className="section-kicker">隐私政策</span>
            <h1>AI 视频日记 · 隐私与数据使用</h1>
            <p>
              这份文档说明 AI 视频日记
              如何处理你的数据。它是产品的一部分，不依赖任何外部 cookie
              横幅或第三方追踪脚本。
            </p>
            <div className="legal-meta">
              <span>最后更新：项目内置版本</span>
              <Link href="/terms">查看使用条款 →</Link>
            </div>
          </header>

          {SECTIONS.map((section) => (
            <section className="legal-section" key={section.title}>
              <h2>
                <span className="legal-section-icon" aria-hidden="true">
                  <PixelIcon name={section.icon} />
                </span>
                {section.title}
              </h2>
              {section.paragraphs.map((paragraph, index) => (
                <p key={`${section.title}-${index}`}>{paragraph}</p>
              ))}
            </section>
          ))}

          <footer className="legal-footer">
            <Link className="ui-button" href="/">
              返回工作台
            </Link>
            <Link className="ui-button secondary" href="/terms">
              阅读使用条款
            </Link>
          </footer>
        </section>
      </div>
    </AppShell>
  );
}
