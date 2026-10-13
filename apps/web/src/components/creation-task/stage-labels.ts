import type { CreationTaskNarrationSegment, CreationTaskPersonRender } from "@/lib/types";
export function capabilityLabel(available: boolean) {
    return available ? "可用" : "不可用";
}
export function lipsyncLabel(modelId: string | null | undefined) {
    return modelId?.startsWith("latentsync-")
        ? "高精度 · LatentSync"
        : modelId?.toLowerCase().includes("musetalk") ? "低精度 · MuseTalk" : modelId ? "其他口型" : "未配置";
}
export function narrationSegmentStatus(status: CreationTaskNarrationSegment["status"]) {
    if (status === "ready")
        return "可试听";
    if (status === "generating")
        return "正在生成";
    if (status === "pending")
        return "等待生成";
    return "生成失败";
}
export function personRenderStageDetail(status: CreationTaskPersonRender["status"]) {
    if (status === "ready")
        return "口型已生成";
    if (status === "failed")
        return "口型生成失败";
    return "口型生成中";
}
