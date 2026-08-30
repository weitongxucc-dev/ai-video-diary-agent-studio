import type { CreationTask } from "@/lib/types";

export type CreationTaskStatusTone = "idle" | "running" | "warning" | "success" | "danger";

type CreationTaskStatusPresentation = {
  label: string;
  stageLabel: string;
  tone: CreationTaskStatusTone;
};

const statusPresentation: Record<CreationTask["stage_status"], CreationTaskStatusPresentation> = {
  not_started: { label: "等待开始", stageLabel: "选题：未开始", tone: "idle" },
  running: { label: "选题生成中", stageLabel: "选题：生成中", tone: "running" },
  waiting_for_user: { label: "等待确认", stageLabel: "选题：等待确认", tone: "warning" },
  completed: { label: "选题已完成", stageLabel: "选题：已完成", tone: "success" },
  failed: { label: "需要处理", stageLabel: "选题：需要处理", tone: "danger" },
};

export function creationTaskStatusPresentation(stageStatus: CreationTask["stage_status"]): CreationTaskStatusPresentation {
  return statusPresentation[stageStatus];
}
