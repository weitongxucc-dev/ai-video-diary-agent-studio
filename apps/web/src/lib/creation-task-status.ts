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

export function creationTaskShortStatus(status: CreationTask["stage_status"] | "needs_action", stage: string): string {
  const name = Object.hasOwn(stageNames, stage) ? stageNames[stage as CreationTask["stage"]] : "任务";
  const shortName = stage === "montage" ? "剪辑" : stage === "review" ? "成片检查" : name;
  if (status === "running") return `正在${shortName}`;
  if (status === "waiting_for_user") return stage === "review" ? "成片待检查" : `${shortName}待确认`;
  if (status === "completed") return `${shortName}已完成`;
  if (status === "not_started") return `${shortName}待开始`;
  if (status === "needs_action") return `${shortName}待处理`;
  return stage === "topic" ? "选题未生成" : `${shortName}未完成`;
}

export function creationTaskFailureReason(reason: string | null, stage: string): string | null {
  if (!reason) return null;
  const technical = /\b(?:JSON|schema|payload|Traceback|TypeError|ValueError|ECONNREFUSED|ENOTFOUND)\b|\bHTTP\s*\d+|\b[a-z]+(?:_[a-z0-9]+)+\b|\/(?:Users|Volumes|private)\//i.test(reason);
  if (!technical) return reason;
  const names: Record<string, string> = {
    topic: "选题没有生成成功",
    script: "文案没有生成成功",
    narration: "配音没有完成",
    montage: "视频剪辑没有完成",
    review: "成片检查没有完成",
  };
  return names[stage] ?? "这一步没有完成";
}

export function creationTaskRepairAction(action: string | null): string | null {
  if (!action) return null;
  if (/JSON|schema|输出能力|兼容模型/i.test(action)) return "可以重新尝试；仍未完成时，到设置检查对话服务。";
  if (/文案预计时长.*超过|文案.*创作方案上限/.test(action)) return "缩短文案后重新确认。";
  return action;
}

export function creationTaskStatusPresentation(stageStatus: CreationTask["stage_status"], stage: CreationTask["stage"] = "topic"): CreationTaskStatusPresentation {
  const stageName = stageNames[stage];
  if (stageStatus === "not_started") return { label: "等待开始", stageLabel: `${stageName}：未开始`, tone: "idle" };
  if (stageStatus === "running") return { label: `${stageName}处理中`, stageLabel: `${stageName}：处理中`, tone: "running" };
  if (stageStatus === "waiting_for_user") return { label: "等待确认", stageLabel: `${stageName}：等待确认`, tone: "warning" };
  if (stageStatus === "completed") return { label: "已完成", stageLabel: `${stageName}：已完成`, tone: "success" };
  return { label: "需要处理", stageLabel: `${stageName}：需要处理`, tone: "danger" };
}
