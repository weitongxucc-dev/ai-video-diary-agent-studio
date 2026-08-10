import type { ReactNode } from "react";
import Link from "next/link";
import { AppShell } from "@/components/AppShell";
import { PixelIcon } from "@/components/PixelIcon";

export const dynamic = "force-dynamic";

type Section = {
  title: string;
  icon: "doc" | "person" | "bell" | "agent";
  paragraphs: ReactNode[];
};

const SECTIONS: Section[] = [
  {
    title: "MIT 协议",
    icon: "doc",
    paragraphs: [
      "本项目以 MIT 协议开源。你可以自由使用、复制、修改、合并、发布、分发、再授权和销售本软件的副本，前提是在所有副本中保留版权声明和许可声明。",
      "本软件按「原样」提供，不附带任何形式的明示或暗示保证。",
    ],
  },
  {
    title: "仅供个人创作使用",
    icon: "person",
    paragraphs: [
      "AI 视频日记 Agent Studio 是一个辅助个人创作者整理每日选题、生成口播稿、剪辑视频并发布到第三方平台的本地工具。",
      "请勿将本项目用于：批量生产低质量内容、伪造权威人物言论、规避平台审核、或任何违反所在地法律法规的活动。",
      "你对自己发布到第三方平台（抖音、B 站、YouTube 等）的内容承担全部责任，包括版权、内容合规和受众影响。",
    ],
  },
  {
    title: "不提供可用性保证",
    icon: "bell",
    paragraphs: [
      "本软件不提供任何 SLA（服务等级协议）或可用性承诺。后端依赖 DeepSeek、腾讯云 ASR、剪映、ffmpeg 等多个第三方，任何上游故障都可能影响使用。",
      <>
        当 API Key 失效、视频目录权限不足、磁盘空间耗尽、网络中断等环境问题时，你应当能在{" "}
        <Link href="/health">运行健康</Link> 页看到明确指引，并自行恢复。
      </>,
      "我们不为任何因使用本软件造成的直接、间接、偶然或后果性损失承担责任。",
    ],
  },
  {
    title: "支持与反馈",
    icon: "agent",
    paragraphs: [
      "问题反馈：GitHub Issues（项目仓库）。",
      "功能建议：在 Issue 里以 [Feature] 前缀提交。",
      <>
        本地排查：阅读 <Link href="/privacy">隐私政策</Link> 了解日志位置后导出相关片段。
      </>,
    ],
  },
];

export default function TermsPage() {
  return (
    <AppShell>
      <div className="legal-page">
        <section className="legal-main pixel-card">
          <header className="legal-header">
            <span className="section-kicker">使用条款</span>
            <h1>AI 视频日记 · 使用条款</h1>
            <p>
              使用本项目即视为同意以下条款。如果你不同意，请停止使用并删除本地数据。
            </p>
            <div className="legal-meta">
              <span>协议：MIT License</span>
              <Link href="/privacy">查看隐私政策 →</Link>
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
            <Link className="pixel-button" href="/">
              返回工作台
            </Link>
            <Link className="pixel-button secondary" href="/privacy">
              阅读隐私政策
            </Link>
          </footer>
        </section>
      </div>
    </AppShell>
  );
}