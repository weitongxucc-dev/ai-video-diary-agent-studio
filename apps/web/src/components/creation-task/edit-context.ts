import type { CreationTaskEditOperation } from "@/lib/types";
export type BusyAction = "topics" | "topic-confirm" | "script" | "script-confirm" | "reply-transfer" | "execution" | "voice-select" | "recording-adopt" | "person-render" | "clip" | "save" | "assistant" | "edit-preview" | "edit-confirm" | "undo";
export type PendingLocalEdit = {
    taskId: string;
    jobId: string;
    operationType: CreationTaskEditOperation["operation_type"];
    targetRefs: string[];
};
export function queuedEditNotice(operation: PendingLocalEdit["operationType"]): string {
    return operation === "RegenerateNarrationSegment"
        ? "配音修改已排队，取得设备许可并完成后才更新目标配音段；当前版本和音频保持不变。"
        : "人物修改已排队，取得设备许可并完成后才更新目标镜头；当前版本保持不变。";
}
export type SelectedEditContext = {
    kind: "narration" | "cue" | "clip" | "cover" | "audio" | "overlay";
    id: string;
    label: string;
    placeholder: string;
    ref: string;
};
