import type { CreationTask } from "@/lib/types";

export type CreationTaskStatusTone = "idle" | "running" | "warning" | "success" | "danger";

type CreationTaskStatusPresentation = {
  label: string;
  stageLabel: string;
  tone: CreationTaskStatusTone;
};

const stageNames: Record<CreationTask["stage"], string> = {
  topic: "选题",
  script: "文案",
  narration: "配音",
  montage: "视频剪辑",
  review: "检查成片",
};

export function creationTaskStatusPresentation(stageStatus: CreationTask["stage_status"], stage: CreationTask["stage"] = "topic"): CreationTaskStatusPresentation {
  const stageName = stageNames[stage];
  if (stageStatus === "not_started") return { label: "等待开始", stageLabel: `${stageName}：未开始`, tone: "idle" };
  if (stageStatus === "running") return { label: `${stageName}处理中`, stageLabel: `${stageName}：处理中`, tone: "running" };
  if (stageStatus === "waiting_for_user") return { label: "等待确认", stageLabel: `${stageName}：等待确认`, tone: "warning" };
  if (stageStatus === "completed") return { label: "已完成", stageLabel: `${stageName}：已完成`, tone: "success" };
  return { label: "需要处理", stageLabel: `${stageName}：需要处理`, tone: "danger" };
}
