import { PixelIcon, type PixelIconName } from "@/components/PixelIcon";
import type { Episode } from "@/lib/types";

const steps = [
  { key: "ingest", label: "信息摄入", icon: "ingest" },
  { key: "topics", label: "选题生成", icon: "spark" },
  { key: "review", label: "选题复核", icon: "note" },
  { key: "script", label: "口播稿", icon: "script" },
  { key: "video", label: "视频等待", icon: "upload" },
  { key: "process", label: "AI处理", icon: "agent" },
  { key: "cover", label: "封面", icon: "image" },
  { key: "draft", label: "剪映草稿", icon: "clip" },
  { key: "publish", label: "发布反馈", icon: "publish" }
] satisfies Array<{ key: string; label: string; icon: PixelIconName }>;

function completedSteps(episode: Episode | null): Set<string> {
  const completed = new Set<string>();
  if (!episode) {
    return completed;
  }
  completed.add("ingest");
  if (episode.topics.length > 0) completed.add("topics");
  if (episode.selected_topic_id) completed.add("review");
  if (episode.script) completed.add("script");
  if (episode.video_source_file_name) completed.add("video");
  if (episode.video_processed_file_name) completed.add("process");
  if (episode.draft_package_name) {
    completed.add("cover");
    completed.add("draft");
  }
  if (episode.status === "published") completed.add("publish");
  return completed;
}

function stepState(stepKey: string, completed: Set<string>): "done" | "active" | "pending" {
  if (completed.has(stepKey)) {
    return "done";
  }
  const firstIncomplete = steps.find((step) => !completed.has(step.key));
  return firstIncomplete?.key === stepKey ? "active" : "pending";
}

export function EpisodeProgress({ episode = null }: { episode?: Episode | null }) {
  const completed = completedSteps(episode);
  return (
    <section className="episode-progress pixel-card" aria-label="今日视频日记进度">
      <div className="episode-progress-flow" role="list">
        {steps.map((step, index) => {
          const state = stepState(step.key, completed);
          const label = state === "done" ? "已完成" : state === "active" ? "进行中" : "待处理";
          return (
          <div className={`progress-step ${state}`} key={step.label} role="listitem">
            <span className="progress-step-card">
              <span className="stage-code">STAGE {String(index + 1).padStart(2, "0")}</span>
              <span className="step-icon">
                <PixelIcon name={step.icon} />
              </span>
              <strong>{step.label}</strong>
              <span className={`step-state ${state}`}>
                {label}
              </span>
            </span>
            {index < steps.length - 1 ? (
              <span className="step-arrow" aria-hidden="true" />
            ) : null}
          </div>
          );
        })}
      </div>
    </section>
  );
}
