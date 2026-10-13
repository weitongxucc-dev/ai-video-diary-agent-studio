export const executionStageLabels: Record<string, string> = {
    narration: "生成配音",
    person_render: "人物口型",
    montage_plan: "安排镜头与人物口型",
    render: "生成成片和字幕",
    cover: "准备封面",
};
export function formatDuration(seconds: number | null): string {
    if (seconds === null)
        return "未知";
    if (seconds < 60)
        return `${Math.round(seconds)} 秒`;
    const minutes = Math.floor(seconds / 60);
    const rest = Math.round(seconds % 60);
    return rest ? `${minutes} 分 ${rest} 秒` : `${minutes} 分`;
}
export function jobStatusLabel(status: string): string {
    if (status === "queued")
        return "已安排，等待设备";
    if (status === "running")
        return "正在处理";
    if (status === "succeeded")
        return "已完成";
    if (status === "failed")
        return "失败";
    if (status === "cancelled")
        return "已取消";
    if (status === "interrupted")
        return "已中断";
    return status;
}
/** 作业类型也用中文说明，不把内部标识直接丢给用户。 */
export function jobKindLabel(kind: string): string {
    if (kind === "creation_task_montage_render")
        return "成片输出";
    if (kind === "creation_task_media")
        return "媒体预览与处理";
    if (kind === "creation_task_execution")
        return "整条制作";
    if (kind === "person_render")
        return "人物形象生成";
    if (kind === "creation_task_person_edit")
        return "局部人物修改";
    if (kind === "creation_task_narration_edit")
        return "局部配音修改";
    if (kind === "material_analysis")
        return "素材分析";
    return "其他作业";
}
