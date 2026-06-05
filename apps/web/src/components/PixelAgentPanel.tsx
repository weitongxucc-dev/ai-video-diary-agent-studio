import { PixelIcon, type PixelIconName } from "@/components/PixelIcon";
import { PixelMascot } from "@/components/PixelMascot";
import { PixelBlockMeter } from "@/components/PixelBlockMeter";
import type { Episode, SystemStatus } from "@/lib/types";

type AgentTask = {
  label: string;
  progress: number;
  icon: PixelIconName;
  status: string;
};

type PixelAgentPanelProps = {
  episode: Episode | null;
  systemStatus: SystemStatus | null;
};

export function PixelAgentPanel({ episode, systemStatus }: PixelAgentPanelProps) {
  const llmConfigured = systemStatus?.llm.configured ?? false;
  const obsidianReady = systemStatus?.obsidian.exists ?? false;
  const obsidianCount = systemStatus?.obsidian.markdown_count ?? 0;
  const hermesRadar = systemStatus?.hermes_radar;
  const hermesCount = hermesRadar?.total_count ?? 0;
  const hermesReady = Boolean(hermesRadar?.exists && hermesRadar.total_count > 0);
  const productHuntConfigured = systemStatus?.product_hunt.configured ?? false;
  const externalSourceCount = systemStatus?.external_sources.urls.length ?? 0;
  const videoPendingCount = systemStatus?.video.pending_count ?? 0;
  const ffmpegAvailable = systemStatus?.video.ffmpeg_available ?? false;
  const topicsReady = (episode?.topics.length ?? 0) > 0;
  const scriptReady = Boolean(episode?.script);
  const processedVideoReady = Boolean(episode?.video_processed_file_name);
  const draftReady = Boolean(episode?.draft_package_name);
  const tasks: AgentTask[] = [
    { label: "DeepSeek 文案", progress: llmConfigured ? 100 : 0, icon: "agent", status: llmConfigured ? systemStatus?.llm.model ?? "已配置" : "未配置" },
    { label: "Obsidian 索引", progress: obsidianReady ? 100 : 0, icon: "folder", status: obsidianReady ? `${obsidianCount} 篇` : "未配置" },
    { label: "Hermes 雷达", progress: hermesReady ? 100 : 0, icon: "ingest", status: hermesReady ? `${hermesCount} 条` : "无日报" },
    { label: "Product Hunt", progress: productHuntConfigured ? 100 : 0, icon: "product", status: productHuntConfigured ? "已配置" : "缺 token" },
    { label: "外链素材源", progress: externalSourceCount > 0 ? 80 : 0, icon: "link", status: `${externalSourceCount} 个 URL` },
    { label: "选题生成", progress: topicsReady ? 100 : 0, icon: "spark", status: topicsReady ? `${episode?.topics.length ?? 0} 个` : "待生成" },
    { label: "口播稿生成", progress: scriptReady ? 100 : 0, icon: "script", status: scriptReady ? "已生成" : "待确认" },
    { label: "视频处理", progress: processedVideoReady ? 100 : ffmpegAvailable ? 60 : 0, icon: "video", status: processedVideoReady ? "已处理" : ffmpegAvailable ? `${videoPendingCount} 个待处理` : "ffmpeg 不可用" },
    { label: "剪映草稿", progress: draftReady ? 100 : 30, icon: "clip", status: draftReady ? "素材包已生成" : "等待视频" }
  ];

  return (
    <aside className="agent-panel pixel-card">
      <h2><PixelIcon name="agent" /> Agent 状态</h2>
      <div className="agent-robot">
        <PixelMascot />
      </div>
      <p className="agent-copy">
        DeepSeek 文案生成已{llmConfigured ? "接通" : "待配置"}。
        Obsidian 已发现 <strong>{obsidianCount}</strong> 篇笔记。
        Hermes 雷达已读取 <strong>{hermesCount}</strong> 条素材。
        今日候选选题 <strong>{episode?.topics.length ?? 0}</strong> 个，
        口播稿{scriptReady ? "已生成" : "等待人工确认后生成"}。
        外链素材源 <strong>{externalSourceCount}</strong> 个，
        待处理视频 <strong>{videoPendingCount}</strong> 个。
      </p>
      <div className="agent-task-list">
        <div className="agent-task agent-task-head" aria-hidden="true">
          <span>模块</span>
          <span>状态</span>
          <span>进度</span>
        </div>
        {tasks.map((task) => (
          <div className="agent-task" key={task.label}>
            <span className="agent-module">
              <PixelIcon name={task.icon} />
              <span>{task.label}</span>
            </span>
            <strong>{task.status}</strong>
            <PixelBlockMeter value={task.progress} />
          </div>
        ))}
      </div>
      <div className="preference-summary">
        <h3>偏好学习摘要</h3>
        <p>
          Hermes：PH {hermesRadar?.product_hunt_count ?? 0} / 观猹 {hermesRadar?.watcha_count ?? 0}。
          反馈记录已写入 Episode，并会在反馈学习页沉淀为偏好记忆。
        </p>
      </div>
    </aside>
  );
}
