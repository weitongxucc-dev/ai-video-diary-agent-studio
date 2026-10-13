import type { CreationPlan, CreationPlanInput, CreationPlanPreview } from '../types/creation-plans';
import type { ContentVisibility } from '../types/common';
import { request } from './transport';
import { creationPlanRequest } from './creation-transport';
export function setCreationPlanVisibility(planId: string, visibility: ContentVisibility): Promise<CreationPlan> {
    return request<CreationPlan>(`/api/creation-plans/${encodeURIComponent(planId)}/visibility`, { method: "PUT", body: JSON.stringify({ visibility }) });
}
export function previewCreationPlan(input: CreationPlanInput): Promise<CreationPlanPreview> {
    return creationPlanRequest<CreationPlanPreview>("/api/creation-plans/preview", {
        method: "POST",
        body: JSON.stringify(input),
    });
}
export function updateAssistantPlanPreview(conversationId: string, turnId: string, input: CreationPlanInput): Promise<CreationPlanPreview> {
    return creationPlanRequest<CreationPlanPreview>(`/api/assistant/conversations/${encodeURIComponent(conversationId)}/turns/${encodeURIComponent(turnId)}/plan-preview`, { method: "POST", body: JSON.stringify(input) });
}
export function confirmCreationPlan(input: CreationPlanInput & {
    preflight_fingerprint: string;
    reply_origin?: {
        conversation_id: string;
        turn_id: string;
        request_id: string;
    };
}): Promise<CreationPlan> {
    return creationPlanRequest<CreationPlan>("/api/creation-plans/confirm", {
        method: "POST",
        body: JSON.stringify(input),
    });
}
export function getCreationPlan(planId: string): Promise<CreationPlan> {
    return creationPlanRequest<CreationPlan>(`/api/creation-plans/${encodeURIComponent(planId)}`);
}
