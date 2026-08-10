"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { PixelIcon, type PixelIconName } from "@/components/PixelIcon";
import { getTodayEpisode } from "@/lib/api";
import type { Episode } from "@/lib/types";

type WorkflowStepKey = "topics" | "script" | "record" | "edit" | "publish";

type WorkflowStep = {
  key: WorkflowStepKey;
  label: string;
  href: string;
  icon: PixelIconName;
};

const WORKFLOW_STEPS: WorkflowStep[] = [
  { key: "topics", label: "选题", href: "/topics", icon: "note" },
  { key: "script", label: "脚本", href: "/script", icon: "script" },
  { key: "record", label: "录制", href: "/video", icon: "video" },
  { key: "edit", label: "剪辑", href: "/edit-plan", icon: "clip" },
  { key: "publish", label: "导出", href: "/history", icon: "publish" },
];

type StepStatus = "done" | "active" | "pending";

function deriveWorkflowState(episode: Episode | null): WorkflowStepKey | null {
  if (!episode) {
    return null;
  }
  if (episode.status === "published") {
    return null;
  }
  if (!episode.selected_topic_id) {
    return "topics";
  }
  if (!episode.script) {
    return "script";
  }
  if (episode.status === "topic_approved") {
    return "script";
  }
  if (episode.status === "script_approved") {
    return "record";
  }
  if (
    episode.status === "waiting_video" ||
    episode.status === "video_processing"
  ) {
    return "record";
  }
  if (episode.status === "draft_ready") {
    return "edit";
  }
  if (episode.status === "script_ready") {
    return "script";
  }
  if (
    episode.status === "created" ||
    episode.status === "sources_ingesting" ||
    episode.status === "topics_ready"
  ) {
    return "topics";
  }
  return "topics";
}

function completedSteps(episode: Episode | null): Set<WorkflowStepKey> {
  const completed = new Set<WorkflowStepKey>();
  if (!episode) {
    return completed;
  }
  if (episode.selected_topic_id) {
    completed.add("topics");
  }
  if (episode.script) {
    completed.add("script");
  }
  if (episode.video_processed_file_name) {
    completed.add("record");
  }
  if (episode.draft_package_name) {
    completed.add("edit");
  }
  if (episode.status === "published") {
    completed.add("publish");
  }
  return completed;
}

function stepState(
  stepKey: WorkflowStepKey,
  completed: Set<WorkflowStepKey>,
  activeKey: WorkflowStepKey | null,
): StepStatus {
  if (completed.has(stepKey)) {
    return "done";
  }
  if (stepKey === activeKey) {
    return "active";
  }
  return "pending";
}

function statusLabel(status: StepStatus): string {
  if (status === "done") return "完成";
  if (status === "active") return "进行中";
  return "待处理";
}

export function WorkflowProgress() {
  const pathname = usePathname();
  const [episode, setEpisode] = useState<Episode | null>(null);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let cancelled = false;
    async function refresh() {
      try {
        const result = await getTodayEpisode();
        if (!cancelled) {
          setEpisode(result);
          setLoaded(true);
        }
      } catch {
        if (!cancelled) {
          setEpisode(null);
          setLoaded(true);
        }
      }
    }
    void refresh();
    return () => {
      cancelled = true;
    };
  }, [pathname]);

  const completed = completedSteps(episode);
  const activeKey = deriveWorkflowState(episode);
  const completedCount = completed.size;

  return (
    <nav aria-label="生产工作流" className="workflow-progress">
      <header className="workflow-progress-heading">
        <strong>生产工作流</strong>
        <span>
          {loaded
            ? episode
              ? `已完成 ${completedCount}/${WORKFLOW_STEPS.length}`
              : "今日 Episode 待创建"
            : "正在加载..."}
        </span>
      </header>
      <ol className="workflow-progress-list">
        {WORKFLOW_STEPS.map((step, index) => {
          const status = stepState(step.key, completed, activeKey);
          const isCurrent = status === "active";
          return (
            <li
              className={`workflow-progress-row ${status}`}
              key={step.key}
            >
              <Link
                aria-current={isCurrent ? "step" : undefined}
                className="workflow-progress-link"
                href={step.href}
                title={`${step.label} · ${statusLabel(status)}`}
              >
                <span
                  aria-hidden="true"
                  className={`workflow-progress-node ${status}`}
                >
                  <span className="workflow-progress-node-frame">
                    {status === "done" ? (
                      <PixelIcon name="check" />
                    ) : (
                      <PixelIcon name={step.icon} />
                    )}
                  </span>
                </span>
                <span className="workflow-progress-body">
                  <strong>{step.label}</strong>
                  <span className={`workflow-progress-state ${status}`}>
                    {statusLabel(status)}
                  </span>
                </span>
                {index < WORKFLOW_STEPS.length - 1 ? (
                  <span
                    aria-hidden="true"
                    className={`workflow-progress-arrow ${status}`}
                  />
                ) : null}
              </Link>
            </li>
          );
        })}
      </ol>
    </nav>
  );
}