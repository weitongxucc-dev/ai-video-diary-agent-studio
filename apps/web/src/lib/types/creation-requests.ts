import type { CreationPlanInput, CreationPlanPreview } from './creation-plans';
import type { CreationTask } from './creation-tasks';
export type CreationRequestOutputRelation = "single" | "different_topics" | "variants" | "series";
export type CreationRequestItem = {
    ordinal: number;
    window_id?: string | null;
    plan_id: string;
    task_id: string;
    plan: CreationPlanPreview | null;
    task: CreationTask | null;
};
export type CreationRequest = {
    id: string;
    goal: string;
    target_count: number;
    output_relation: CreationRequestOutputRelation | null;
    requirements: CreationPlanInput;
    material_scope: {
        kind: "workspace_library" | "today_received";
        [key: string]: unknown;
    };
    clarification_questions: string[];
    revision: number;
    status: "awaiting_clarification" | "awaiting_plan" | "awaiting_confirmation" | "active";
    expanded_count: number;
    confirmed_count: number;
    task_status_counts: Record<string, number>;
    current_window: {
        id: string;
        request_id: string;
        ordinal_start: number;
        status: "proposed" | "confirmed";
        input_fingerprint: string;
        entries: CreationRequestItem[];
        created_at: string;
        confirmed_at: string | null;
    } | null;
};
