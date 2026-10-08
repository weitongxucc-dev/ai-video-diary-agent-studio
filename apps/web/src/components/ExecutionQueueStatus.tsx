import styles from "./ExecutionQueueStatus.module.css";

export type ExecutionQueueStatusProps = {
  status: "queued" | "running" | "succeeded" | "failed" | "cancelled" | "interrupted" | null;
  currentStage: "narration" | "person_render" | "montage_plan" | "render" | "cover" | null;
  waitingReason: string | null;
  statusUpdatedAt: string | null;
};

const stageLabels: Record<NonNullable<ExecutionQueueStatusProps["currentStage"]>, string> = {
  narration: "生成配音",
  person_render: "人物口型",
  montage_plan: "安排镜头与人物口型",
  render: "生成成片和字幕",
  cover: "准备封面",
};

function updatedTime(value: string) {
  const date = new Date(value);
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date);
}

export function ExecutionQueueStatus({ status, currentStage, waitingReason, statusUpdatedAt }: ExecutionQueueStatusProps) {
  if (!status) return null;
  const stage = currentStage ? stageLabels[currentStage] : status === "queued" ? "尚未开始" : "阶段未知";
  const stageTitle = status === "queued" ? "等待执行" : status === "failed" || status === "cancelled" || status === "interrupted" ? "上次停在" : status === "succeeded" ? "最后阶段" : "当前阶段";
  const showWaitingReason = status === "queued" || status === "interrupted";
  const validUpdatedAt = statusUpdatedAt && !Number.isNaN(new Date(statusUpdatedAt).getTime()) ? statusUpdatedAt : null;

  return <dl aria-label="制作安排状态" className={styles.status}>
    <div className={styles.row}><dt>{stageTitle}</dt><dd>{stage}</dd></div>
    {showWaitingReason ? <div className={styles.row}><dt>等待原因</dt><dd>{waitingReason?.trim() || "暂未提供等待原因"}</dd></div> : null}
    <div className={styles.row}><dt>最近更新</dt><dd>{validUpdatedAt ? <time dateTime={validUpdatedAt}>{updatedTime(validUpdatedAt)}</time> : "更新时间未知"}</dd></div>
  </dl>;
}
