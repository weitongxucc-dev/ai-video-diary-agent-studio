import type { CreationTaskNarrationSegment, CreationTaskPersonRender } from "@/lib/types";
import type { CreationTaskSegmentRevision } from "@/lib/types/creation-tasks";
export function editExecutionReadinessMessage(readiness: unknown) {
    if (!readiness || typeof readiness !== 'object' || !('status' in readiness)) return null;
    if (readiness.status === 'not_ready') {
        const reasons: Record<string,string> = {task_authorization_unavailable:'原任务尚无后台执行授权',
            task_authorization_unverified:'原任务授权尚未核验',task_authorization_expired:'原授权已过期',
            task_authorization_revoked:'原授权已撤销',task_authorization_outside_scope:'修改超出原授权范围',
            original_execution_identity_differs:'原执行身份已变化',original_execution_identity_unverified:'原执行身份尚未核验'};
        const reason = 'reason_code' in readiness && typeof readiness.reason_code === 'string' ? reasons[readiness.reason_code] : null;
        return '本次保存时后台执行授权未就绪'+(reason ? `（${reason}）` : '')+'；修改已保留。';
    }
    if (readiness.status === 'authorization_ready') return '本次保存时后台执行授权已核验；设备准入尚未检查。';
    return null;
}
export function segmentRevisionStatus(receipt: CreationTaskSegmentRevision) {
    const adoption = receipt.adoption?.status;
    if (adoption === 'current') return '本段结果已被当前作品采用；整片输出仍须核对';
    if (adoption === 'candidate') return '本段产物已登记，尚未采用';
    if (adoption === 'superseded' || adoption === 'invalidated') return '原段产物仅保留为历史，当前作品未采用';
    return ({queued:'本段修改已排队',running:'正在处理本段修改',interrupted:'本段修改已中断',
        failed:'本段修改未完成',cancelled:'本段修改已取消',succeeded:'本段执行已完成，采用关系待核对'} as Record<string,string>)[receipt.status] ?? '本段修改状态待核对';
}
export function segmentAdoptionReason(reason: string | null | undefined) {
    if (!reason) return null;
    return ({section_render_inputs_changed:'当前片段的画面、声音或字幕已变化，原视频仅供历史查看。',
        current_section_adopts_other_publication:'当前片段已采用另一个版本，原视频仅供历史查看。',
        candidate_not_adopted:'本段候选已保留，尚未被当前作品采用。',
        original_request_has_no_publication:'原请求尚未登记可采用的段产物。'} as Record<string,string>)[reason]
        ?? '当前片段的采用关系已变化，请查询原修改回执后处理。';
}
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
