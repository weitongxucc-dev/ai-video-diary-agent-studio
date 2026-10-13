import type { ResourceAdmissionCheck, TaskMediaJob, TaskMediaResultMap, TaskMediaJobPage } from '../types/task-edits';
import type { AudioEditInput, CreationTaskRecording } from '../types/timeline';
import type { CreationTaskNarration } from '../types/creation-tasks';
import type { CreationTaskEditPreviewResponse } from '../types/task-edits';
import { API_BASE, apiUrl, authorizedFetch } from './transport';
import { CreationPlanApiError, creationPlanRequest } from './creation-transport';
export function previewCreationTaskAudio(taskId: string, input: AudioEditInput): Promise<CreationTaskEditPreviewResponse | TaskMediaJob> {
    return creationPlanRequest(`/api/creation-tasks/${encodeURIComponent(taskId)}/audio/preview?draft=true`, { method: "POST", body: JSON.stringify(input) });
}
export function listCreationTaskRecordings(taskId: string): Promise<{
    items: CreationTaskRecording[];
}> {
    return creationPlanRequest(`/api/creation-tasks/${encodeURIComponent(taskId)}/audio/recordings`);
}
export function adoptCreationTaskRecording(taskId: string, input: {
    script_id: string;
    source_section_id: string;
    source_ref: string;
}): Promise<CreationTaskNarration> {
    return creationPlanRequest(`/api/creation-tasks/${encodeURIComponent(taskId)}/audio/recordings/adopt`, {
        method: "POST", body: JSON.stringify(input),
    });
}
export async function saveCreationTaskRecording(taskId: string, recording: Blob): Promise<CreationTaskRecording | TaskMediaJob> {
    const mediaType = recording.type.split(";")[0].trim().toLowerCase();
    const suffix = ({ "audio/webm": "webm", "audio/mp4": "m4a", "audio/x-m4a": "m4a",
        "audio/ogg": "ogg", "audio/wav": "wav", "audio/x-wav": "wav" } as Record<string, string>)[mediaType];
    if (!suffix)
        throw new Error("当前录音格式无法保存，请换一个支持录音的浏览器重试。");
    const body = new FormData();
    body.append("audio", recording, `本机录音.${suffix}`);
    const response = await authorizedFetch(apiUrl(`/api/creation-tasks/${encodeURIComponent(taskId)}/audio/recordings`), { method: "POST", body, cache: "no-store" });
    const payload = await response.json();
    if (!response.ok)
        throw new Error(typeof payload.detail === "string" ? payload.detail : "电脑录音未保存");
    return payload as CreationTaskRecording | TaskMediaJob;
}
export function listenCreationTaskAudio(taskId: string, input: {
    base_version_id: string | null;
    start_seconds: number;
    end_seconds: number;
    solo_track_ids: string[];
}): Promise<{
    audio_url: string;
} | TaskMediaJob> {
    return creationPlanRequest(`/api/creation-tasks/${encodeURIComponent(taskId)}/audio/listen`, { method: "POST", body: JSON.stringify(input) });
}
export async function importCreationTaskAudio(taskId: string, file: File): Promise<TaskMediaResultMap["audio_source"] | TaskMediaJob> {
    const body = new FormData();
    body.append("audio", file);
    const response = await authorizedFetch(apiUrl(`/api/creation-tasks/${encodeURIComponent(taskId)}/audio/import`), { method: "POST", body });
    const payload = await response.json();
    if (!response.ok)
        throw new Error(typeof payload.detail === "string" ? payload.detail : "音频导入失败");
    return payload;
}
export function previewCreationTaskVisual(taskId: string, input: import("../types").VisualEditInput): Promise<CreationTaskEditPreviewResponse | TaskMediaJob> {
    return creationPlanRequest(`/api/creation-tasks/${encodeURIComponent(taskId)}/visual/preview?draft=true`, { method: "POST", body: JSON.stringify(input) });
}
export async function importCreationTaskGraphic(taskId: string, file: File): Promise<import("../types").GraphicSource> {
    const body = new FormData();
    body.append("image", file);
    const response = await authorizedFetch(apiUrl(`/api/creation-tasks/${encodeURIComponent(taskId)}/graphics/import`), { method: "POST", body });
    const payload = await response.json();
    if (!response.ok)
        throw new Error(typeof payload.detail === "string" ? payload.detail : "图片导入失败");
    return payload;
}
export function getCreationTaskGraphicSources(taskId: string, signal?: AbortSignal): Promise<import("../types").GraphicSource[]> {
    return creationPlanRequest(`/api/creation-tasks/${encodeURIComponent(taskId)}/graphics/sources`, { signal });
}
export async function readCreationTaskGraphicSource(taskId: string, artifactId: string, signal?: AbortSignal): Promise<Blob> {
    const response = await authorizedFetch(`${API_BASE}/api/creation-tasks/${encodeURIComponent(taskId)}/graphics/source/${encodeURIComponent(artifactId)}`, {
        signal, cache: "no-store",
    });
    if (!response.ok)
        throw new CreationPlanApiError(response.status, null, "当前任务图片来源无法读取。");
    return response.blob();
}
export function getCreationTaskMediaJob(taskId: string, jobId: string, signal?: AbortSignal): Promise<TaskMediaJob> {
    return creationPlanRequest(`/api/creation-tasks/${encodeURIComponent(taskId)}/media-jobs/${encodeURIComponent(jobId)}`, { signal });
}
export function cancelCreationTaskMediaJob(taskId: string, jobId: string): Promise<TaskMediaJob> {
    return creationPlanRequest(`/api/creation-tasks/${encodeURIComponent(taskId)}/media-jobs/${encodeURIComponent(jobId)}/cancel`, { method: "POST", body: JSON.stringify({}) });
}
export function getCreationTaskAudioWaveform(taskId: string, clipId: string, signal?: AbortSignal): Promise<TaskMediaResultMap["waveform"] | TaskMediaJob> {
    return creationPlanRequest(`/api/creation-tasks/${encodeURIComponent(taskId)}/audio/waveform/${encodeURIComponent(clipId)}`, { signal });
}
export function taskMediaJobLabel(job: TaskMediaJob): string {
    if (job.status === "queued") {
        // Reuse the ordinary device-wait wording projected by creation execution.
        const waitingText: Record<string, string> = {
            dependency: "等待前一步或受许可执行能力就绪。",
            owner_unknown: "当前成员或任务归属暂不能核实。",
            authorization_lost: "当前任务授权已变化，请核对权限。",
            input_stale: "文案或制作要求已变化，请重新确认。",
            scheduled_time: "等待指定开始时间。",
            outside_window: "当前不在选择的执行时段内。",
            activity_unknown: "暂不能确认设备是否空闲。",
            device_active: "设备有人正在播放、录音或编辑。",
            resource_unknown: "暂不能确认设备资源是否足够。",
            resource_busy: "本机资源余量不足，正在等待满足制作条件。",
            resource_pressure: "本机资源暂时不足，等待恢复。",
            retry_not_before: "等待下次可重试时间。",
            recovered: "设备恢复后等待重新核验。",
        };
        const reason = waitingText[job.waiting_reason ?? ""];
        return reason ? `等待处理：${reason}` : "已安排，等待设备空闲";
    }
    if (job.status === "running")
        return job.result_kind === "waveform" ? "正在读取波形…" : ["audio_source", "recording"].includes(job.result_kind) ? "正在处理音频…" : "正在生成预览…";
    if (job.status === "interrupted")
        return "预览已中断，等待恢复";
    if (job.status === "cancelled")
        return "预览已取消";
    if (job.status === "stale")
        return "输入已变化，请重新预览";
    if (job.status === "failed")
        return "预览未完成";
    return "预览已生成";
}
export function taskMediaResourceCheckLabel(check: ResourceAdmissionCheck): string {
    const metricLabels: Record<ResourceAdmissionCheck["metric"], string> = {
        cpu_percent: "CPU 使用率",
        available_memory_bytes: "可用内存",
        swap_used_bytes: "交换空间使用量",
        disk_free_bytes: "磁盘剩余空间",
        disk_io_bytes_per_second: "磁盘读写速度",
    };
    const value = (amount: number) => `${amount.toLocaleString("zh-CN", { maximumFractionDigits: 20 })}${check.unit === "percent" ? "%" : check.unit === "bytes" ? " 字节" : " 字节/秒"}`;
    return [
        `${metricLabels[check.metric]}：${check.actual === null ? "实测值未知" : `实测 ${value(check.actual)}`}`,
        check.minimum === null ? null : `至少 ${value(check.minimum)}`,
        check.maximum === null ? null : `不高于 ${value(check.maximum)}`,
        check.passed === null ? "检查结果未知" : check.passed ? "符合门限" : "检查未通过",
    ].filter(Boolean).join("；") + "。";
}
export class TaskMediaJobError extends Error {
    constructor(public readonly job: TaskMediaJob) {
        super(["cancelled", "stale"].includes(job.status) ? taskMediaJobLabel(job) : job.failure_reason || taskMediaJobLabel(job));
        this.name = "TaskMediaJobError";
    }
}
export async function resolveCreationTaskMediaResponse<K extends keyof TaskMediaResultMap>(taskId: string, response: TaskMediaResultMap[K] | TaskMediaJob, kind: K, options: {
    signal: AbortSignal;
    onUpdate: (job: TaskMediaJob) => void;
    baseVersionId: string | null;
}): Promise<TaskMediaResultMap[K]> {
    if (!("job_id" in response))
        return response;
    let job = response;
    while (true) {
        options.signal.throwIfAborted();
        if (job.result_kind !== kind || job.base_version_id !== options.baseVersionId)
            throw new Error("预览结果与本次输入不一致，请重新预览。");
        options.onUpdate(job);
        if (job.status === "succeeded") {
            if (!job.result)
                throw new Error("预览作业未返回可用结果，请重新读取。");
            return job.result as TaskMediaResultMap[K];
        }
        if (["failed", "cancelled", "stale"].includes(job.status))
            throw new TaskMediaJobError(job);
        await new Promise<void>((resolve, reject) => {
            const abort = () => { clearTimeout(timer); reject(options.signal.reason); };
            const timer = setTimeout(() => { options.signal.removeEventListener("abort", abort); resolve(); }, 2500);
            options.signal.addEventListener("abort", abort, { once: true });
        });
        job = await getCreationTaskMediaJob(taskId, job.job_id, options.signal);
    }
}
export function listCreationTaskMediaJobs(taskId: string, cursor: string | null = null, signal?: AbortSignal): Promise<TaskMediaJobPage> {
    const query = new URLSearchParams({ limit: "20" });
    if (cursor)
        query.set("cursor", cursor);
    return creationPlanRequest(`/api/creation-tasks/${encodeURIComponent(taskId)}/media-jobs?${query}`, { signal });
}
export function taskMediaJobMatchesContext(job: TaskMediaJob, baseVersionId: string | null, selectedRefs: string[]): boolean {
    if (job.base_version_id !== baseVersionId || !["queued", "running", "interrupted", "succeeded"].includes(job.status))
        return false;
    if (job.result_kind === "edit_preview") {
        if (!(job.operation.endsWith("_edit_preview") || job.operation === "compound_selection_preview"))
            return false;
        return job.target_refs.length === selectedRefs.length && job.target_refs.every(ref => selectedRefs.includes(ref));
    }
    if (job.result_kind === "graphic_source")
        return ["media_script_preview", "media_script_tool_preview"].includes(job.operation);
    if (job.target_refs.length !== 0)
        return false;
    return (job.operation === "audio_import" && job.result_kind === "audio_source")
        || (job.operation === "recording_save" && job.result_kind === "recording")
        || (job.operation === "audio_listen" && job.result_kind === "audio_listen")
        || (["audio_waveform", "narration_waveform"].includes(job.operation) && job.result_kind === "waveform");
}
