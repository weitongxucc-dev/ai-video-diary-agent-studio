import { ensureLocalMemberIdentity, readLocalMemberToken } from "../local-member-auth";
import { isLocalAuthMode } from "../auth/routing";
import type { AssistantStreamEvent } from '../types/assistant';
import { API_BASE, authorizedFetch } from './transport';
export class CreationPlanApiError extends Error {
    constructor(public readonly status: number, public readonly code: string | null, message: string, public readonly impact: string | null = null, public readonly repairAction: string | null = null, public readonly reason: string | null = null,
        public readonly submissionEffect: "not_submitted" | "rejected" | "unknown" | null = null) {
        super(message);
        this.name = "CreationPlanApiError";
    }
}
const creationPlanErrorTitles: Record<string, string> = {
    creation_plan_preflight_stale: "预检结果已变化",
    creation_plan_confirmation_rejected: "无法确认创建创作任务",
    content_sources_missing: "缺少可用于选题的资料",
    capability_unavailable: "当前文字模型不可用",
    creation_task_topic_stage_conflict: "当前创作任务不能重复开始选题",
    creation_task_topic_confirmation_conflict: "当前选题不能被确认",
    confirmed_topic_missing: "请先确认选题",
    creation_task_script_stage_conflict: "当前任务不能生成文案",
    creation_task_script_confirmation_conflict: "当前文案不能被确认",
    creation_task_narration_conflict: "当前任务不能生成配音",
    voice_identity_missing: "缺少已授权的声音版本",
    voice_reference_wav_unavailable: "授权声音的 WAV 不可用",
    narration_generation_failed: "中文配音生成失败",
    creation_task_montage_conflict: "当前任务不能生成故事板",
    montage_plan_failed: "故事板生成失败",
    provider_output_invalid: "文字模型返回的选题无法使用",
};
export function parseJson(value: string): unknown {
    try {
        return JSON.parse(value);
    }
    catch {
        return value;
    }
}
export async function creationPlanRequest<T>(path: string, init?: RequestInit): Promise<T> {
    let response: Response;
    try {
        response = await authorizedFetch(`${API_BASE}${path}`, {
            ...init,
            headers: {
                "Content-Type": "application/json",
                ...(init?.headers ?? {}),
            },
            cache: "no-store",
        });
    }
    catch {
        throw new CreationPlanApiError(0, null, "本地服务未接通。");
    }
    const text = await response.text();
    const payload = text ? parseJson(text) : null;
    if (!response.ok) {
        const detail = payload && typeof payload === "object" && "detail" in payload
            ? (payload as {
                detail: unknown;
            }).detail
            : payload;
        const details = detail && typeof detail === "object" ? detail as Record<string, unknown> : {};
        const code = typeof details.error === "string" ? details.error : null;
        const reason = typeof details.reason === "string" ? details.reason : null;
        throw new CreationPlanApiError(response.status, code, reason ?? (code && creationPlanErrorTitles[code]
            ? creationPlanErrorTitles[code]
            : typeof detail === "string"
                ? detail
                : typeof details.detail === "string"
                    ? details.detail
                    : code === "creation_task_timeline_version_conflict" && typeof details.message === "string"
                        ? details.message
                        : typeof payload === "string"
                            ? payload
                            : `请求失败：${response.status}`), typeof details.impact === "string" ? details.impact : null, typeof details.repair_action === "string" ? details.repair_action : null, reason,
            details.submission_effect === 'not_submitted' || details.submission_effect === 'rejected' || details.submission_effect === 'unknown' ? details.submission_effect : null);
    }
    return payload as T;
}
export async function assistantStreamRequest<T>(path: string, body: unknown, onEvent: (event: AssistantStreamEvent) => void): Promise<T> {
    if (typeof window !== "undefined" && isLocalAuthMode() && !readLocalMemberToken()) {
        await ensureLocalMemberIdentity();
    }
    let response: Response;
    try {
        response = await authorizedFetch(`${API_BASE}${path}`, {
            method: "POST",
            body: JSON.stringify(body),
            headers: {
                "Content-Type": "application/json",
                Accept: "text/event-stream",
            },
            cache: "no-store",
        });
    }
    catch {
        throw new CreationPlanApiError(0, null, "本地服务未接通。");
    }
    if (!response.ok) {
        const text = await response.text();
        const payload = text ? parseJson(text) : null;
        const detail = payload && typeof payload === "object" && "detail" in payload
            ? (payload as {
                detail: unknown;
            }).detail
            : payload;
        const details = detail && typeof detail === "object" ? detail as Record<string, unknown> : {};
        throw new CreationPlanApiError(response.status, typeof details.error === "string" ? details.error : null, typeof details.reason === "string" ? details.reason : typeof detail === "string" ? detail
            : typeof details.detail === "string" ? details.detail
                : details.error === "creation_task_timeline_version_conflict" && typeof details.message === "string" ? details.message
                : `请求失败：${response.status}`, typeof details.impact === "string" ? details.impact : null, typeof details.repair_action === "string" ? details.repair_action : null, typeof details.reason === "string" ? details.reason : null);
    }
    const reader = response.body?.getReader();
    if (!reader)
        throw new CreationPlanApiError(0, null, "对话连接中断，未收到完整结果。");
    const decoder = new TextDecoder();
    let buffer = "";
    let result: T | undefined;
    const processFrame = (frame: string) => {
        const data = frame.split(/\r?\n/).filter(line => line.startsWith("data:"))
            .map(line => line.slice(5).replace(/^ /, "")).join("\n");
        if (!data)
            return;
        const event = parseJson(data);
        if (!event || typeof event !== "object")
            return;
        const value = event as Record<string, unknown>;
        if (value.type === "result") {
            result = value.result as T;
            return;
        }
        if (value.type === "error") {
            const failure = value.failure && typeof value.failure === "object" ? value.failure as Record<string, unknown> : null;
            const code = failure?.code === "creation_task_timeline_version_conflict" ? failure.code : null;
            throw new CreationPlanApiError(typeof value.status === "number" ? value.status : 0, code, typeof value.message === "string" ? value.message : "对话没有完成，请检查任务状态。");
        }
        if (value.type === "turn_started" && typeof value.turn_id === "string") {
            const turnState = value.turn_state === "completed" || value.turn_state === "failed" || value.turn_state === "stopped"
                ? value.turn_state : "running";
            const draftVersion = typeof value.draft_version === "number" && Number.isInteger(value.draft_version)
                && value.draft_version >= 0 ? value.draft_version : undefined;
            onEvent({ type: "turn_started", turn_id: value.turn_id, turn_state: turnState,
                ...(draftVersion === undefined ? {} : { draft_version: draftVersion }) });
        }
        else if (value.type === "turn_status" && typeof value.turn_id === "string"
            && (value.turn_state === "completed" || value.turn_state === "failed" || value.turn_state === "stopped")) {
            onEvent({ type: "turn_status", turn_id: value.turn_id, turn_state: value.turn_state });
        }
        else if (value.type === "text_delta" && typeof value.delta === "string") {
            onEvent({ type: "text_delta", delta: value.delta });
        }
        else if (value.type === "tool_start" && typeof value.tool_id === "number" && typeof value.label === "string") {
            onEvent({ type: "tool_start", tool_id: value.tool_id, label: value.label });
        }
        else if (value.type === "tool_end" && typeof value.tool_id === "number"
            && typeof value.label === "string" && typeof value.ok === "boolean" && typeof value.summary === "string") {
            const failure = value.failure && typeof value.failure === "object"
                ? value.failure as Record<string, unknown> : null;
            onEvent({ type: "tool_end", tool_id: value.tool_id, label: value.label, ok: value.ok, summary: value.summary,
                ...(value.ok === false && typeof failure?.reason === "string" ? { failure: { reason: failure.reason,
                        ...(typeof failure.impact === "string" ? { impact: failure.impact } : {}),
                        ...(typeof failure.repair_action === "string" ? { repair_action: failure.repair_action } : {}) } } : {}) });
        }
    };
    try {
        while (true) {
            const { done, value } = await reader.read();
            buffer += decoder.decode(value, { stream: !done });
            let boundary = buffer.search(/\r?\n\r?\n/);
            while (boundary >= 0) {
                const frame = buffer.slice(0, boundary);
                const delimiter = buffer.slice(boundary).match(/^\r?\n\r?\n/)?.[0] ?? "\n\n";
                buffer = buffer.slice(boundary + delimiter.length);
                processFrame(frame);
                boundary = buffer.search(/\r?\n\r?\n/);
            }
            if (done)
                break;
        }
        if (buffer.trim())
            processFrame(buffer);
    }
    catch (error) {
        if (error instanceof CreationPlanApiError)
            throw error;
        throw new CreationPlanApiError(0, null, "对话连接中断，未收到完整结果。");
    }
    finally {
        reader.releaseLock();
    }
    if (result === undefined)
        throw new CreationPlanApiError(0, null, "连接已结束，但没有收到完整的对话结果。");
    return result;
}
