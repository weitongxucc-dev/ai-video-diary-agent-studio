import type { VideoTemplateSettings } from './templates';
import type { ContentVisibility } from './common';
import type { MaterialSegmentMatch } from './materials';
import type { CreationTask } from './creation-tasks';
export type CreationPlanInput = {
    template_id?: string | null;
    template_version?: number | null;
    goal: string;
    name?: string;
    target_count: number;
    language: "zh-CN" | "en-US" | "zh-CN/en-US";
    aspect_ratio: "9:16" | "16:9" | "1:1";
    duration_target_seconds: number;
    presenter_share_target?: 0 | 20 | 40 | 60 | 80 | 100;
    lipsync_quality?: "high" | "fast";
    selected_voice?: {
        profile_id: string;
        version: string;
    } | null;
    selected_person?: {
        profile_id: string;
        version: string;
    } | null;
    selected_knowledge?: {
        knowledge_item_id: string;
        version: string;
    } | null;
};
export type CreationPlanKnowledgeReference = {
    id: string;
    version: string;
    title: string;
    authorization: string;
    ownership: string;
};
export type CreationPlanSourceReference = {
    id: string;
    connector_id: string;
    source_label: string;
    title: string;
    license: string;
};
export type CreationPlanVoiceReference = {
    profile_id: string;
    version: string;
    person_profile_id: string;
    person_version: string;
    person_authorization_id: string;
    person_authorization_version: string;
    authorization_id: string;
    authorization_version: string;
    language: string;
    model_id: string;
};
export type CreationPlanPronunciationReference = {
    id: string;
    version: string;
    scope: "workspace" | "person" | "task";
    scope_reference: string | null;
    language: "zh" | "en" | "zh-en";
};
export type CreationPlanModelStatus = {
    id: string;
    capability: string;
    state: string;
    ready: boolean;
    message: string;
};
export type CreationPlanBlocker = {
    reason: string;
    impact: string;
    repair_action: string;
    stage: string;
    blocks_confirmation: boolean;
};
export type CreationPlanPreview = Omit<CreationPlanInput, "selected_voice"> & {
    template_settings?: VideoTemplateSettings;
    name: string;
    knowledge_references: CreationPlanKnowledgeReference[];
    source_references: CreationPlanSourceReference[];
    selected_voice: CreationPlanVoiceReference | null;
    pronunciation_references: CreationPlanPronunciationReference[];
    approved_asset_count: number;
    approved_clip_count: number;
    material_rule_summary: string;
    material_matches: MaterialSegmentMatch[];
    model_snapshot: CreationPlanModelStatus[];
    confirmation_points: string[];
    blockers: CreationPlanBlocker[];
    confirmable: boolean;
    preflight_fingerprint: string;
    selected_person?: {
        profile_id: string;
        version: string;
    } | null;
};
export type CreationPlan = CreationPlanPreview & {
    id: string;
    workspace_id: string;
    visibility: ContentVisibility;
    status: "confirmed";
    creation_task: CreationTask;
    created_at: string;
    confirmed_at: string;
    updated_at: string;
};
