import Link from "next/link";
import { AppShell } from "@/components/AppShell";
import { PixelIcon } from "@/components/PixelIcon";
import { PublishFeedbackPanel } from "@/components/PublishFeedbackPanel";
import { getTodayEpisode } from "@/lib/api";
import type { Episode } from "@/lib/types";

export const dynamic = "force-dynamic";

async function loadPublishFeedbackPage(): Promise<{
  episode: Episode | null;
  offline: boolean;
}> {
  try {
    const episode = await getTodayEpisode();
    return { episode, offline: false };
  } catch {
    return { episode: null, offline: true };
  }
}

export default async function PublishFeedbackPage() {
  const { episode, offline } = await loadPublishFeedbackPage();

  return (
    <AppShell>
      <div className="workspace-page publish-feedback-page">
        <section className="workspace-main pixel-card">
          <div className="workspace-header">
            <div>
              <span className="section-kicker">发布反馈</span>
              <h1>发布后的偏好回收</h1>
              <p>
                {offline
                  ? "暂时无法连接本地 API。"
                  : "把链接、首轮数据和评论反应记录回来，让下一轮选题与口播更贴近真实反馈。"}
              </p>
            </div>
            <Link className="pixel-button secondary" href="/draft">
              回到剪映草稿
            </Link>
          </div>
          <PublishFeedbackPanel episode={episode} />
        </section>

        <aside className="workspace-side">
          <div className="pixel-card side-note">
            <h2>发布域</h2>
            <p>{episode ? episode.status : "--"}</p>
            <span>登记链接、播放、点赞、评论和明确原因。</span>
          </div>
          <div className="pixel-card side-note">
            <h2>学习去向</h2>
            <p>反馈学习</p>
            <span>发布反馈会写入 Episode，并在偏好记忆里沉淀。</span>
          </div>
          <div className="pixel-card side-note">
            <h2>下一步影响</h2>
            <p>
              <PixelIcon name="publish" /> 选题 / 口播
            </p>
            <span>后续生成会优先参考这批发布后的真实表现。</span>
          </div>
        </aside>
      </div>
    </AppShell>
  );
}
