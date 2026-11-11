import type { MaterialAsset } from "@/lib/types";
export function duration(value: number | null) {
    if (value === null)
        return "时长未知";
    const milliseconds = Math.round(value * 1000);
    const minutes = Math.floor(milliseconds / 60000);
    const seconds = Math.floor(milliseconds % 60000 / 1000).toString().padStart(2, "0");
    const fraction = (milliseconds % 1000).toString().padStart(3, "0").replace(/0+$/, "");
    return `${minutes}:${seconds}${fraction ? `.${fraction}` : ""}`;
}
// Collection scanning uses tenths; source/segment ranges retain millisecond precision via duration().
export function assetDuration(value: number | null) {
    if (value === null) return "时长未知";
    const tenths = Math.round(value * 10);
    return `${Math.floor(tenths / 600)}:${Math.floor(tenths % 600 / 10).toString().padStart(2, "0")}.${tenths % 10}`;
}
export function roleLabel(asset: MaterialAsset) {
    const suggestion = asset.usage_role === "unassigned" ? "建议：" : "";
    const role = asset.usage_role === "unassigned"
        ? asset.suggested_usage_role
        : asset.usage_role;
    if (role === "a_roll")
        return `${suggestion}人物主镜头`;
    if (role === "b_roll")
        return `${suggestion}补充画面`;
    if (asset.usage_role === "exclude")
        return "不使用";
    return "待判断";
}
export function clipRoleLabel(role: string | null) {
    if (role === "a_roll")
        return "人物主镜头";
    if (role === "b_roll")
        return "补充画面";
    return "未记录用途";
}
export function mediaLabel(mediaType: MaterialAsset["media_type"]) {
    return { video: "视频", audio: "音频", image: "图片" }[mediaType];
}
export function reviewLabel(asset: MaterialAsset) {
    if (asset.review_status === "approved")
        return "已人工确认";
    if (asset.review_status === "rejected")
        return "已标记排除";
    return "未人工确认";
}
export function modalityLabel(status: "pending" | "ready" | "blocked" | "failed" | undefined, subject: string) {
    if (status === "ready")
        return `${subject}已完成`;
    if (status === "pending")
        return `${subject}待处理`;
    if (status === "blocked")
        return `${subject}受阻`;
    if (status === "failed")
        return `${subject}失败`;
    return `${subject}状态未知`;
}
export function analysisSummary(asset: MaterialAsset) {
    const visualStatuses = asset.segments.map((segment) => segment.visual_status);
    const ocrStatuses = asset.segments.map((segment) => segment.understanding?.ocr_status);
    const summarize = (statuses: ("pending" | "ready" | "blocked" | "failed" | undefined)[], subject: string, completed: string) => {
        if (!statuses.length || statuses.every((status) => status === undefined))
            return `${subject}状态未知`;
        if (statuses.every((status) => status === "ready"))
            return completed;
        if (statuses.some((status) => status === "ready"))
            return `${subject}部分完成`;
        if (statuses.some((status) => status === undefined))
            return `${subject}状态不完整`;
        if (statuses.every((status) => status === statuses[0]))
            return modalityLabel(statuses[0], subject);
        return `${subject}部分受阻`;
    };
    const visual = summarize(visualStatuses, "画面分析", "画面已分析");
    const ocr = summarize(ocrStatuses, "文字识别", "文字识别已完成");
    return [
        visual,
        modalityLabel(asset.speech_analysis?.status, "声音转写"),
        ocr,
    ].join(" · ");
}
export function analysisMessage(asset: MaterialAsset) {
    const message = asset.analysis_message.trim();
    if (!message || /[{}\n\r]|Traceback|Error:|\/Users\/|\/Volumes\/|[A-Za-z]:\\/.test(message)) {
        return asset.analysis_status === "blocked"
            ? "素材目录暂不可用，请连接原盘后重新检查。"
            : "素材分析失败，请检查文件或本地模型后重试。";
    }
    return message;
}
export function tagLabel(value: string) {
    return value
        .replaceAll("a_roll", "人物主镜头")
        .replaceAll("b_roll", "补充画面");
}
export function fileLabel(value: string) {
    return value
        .replace(/batch_(\d+)/gi, "第 $1 组")
        .replace(/episode/gi, "创作任务");
}
