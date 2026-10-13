import type { CreationPlanPreview } from './creation-plans';
import type { MaterialAssistantSelection } from './materials';
import type { CreationTaskTopicsResponse, CreationTaskScript, CreationExecutionStatus } from './creation-tasks';
import type { CreationTaskEditPreviewResponse, TaskMediaJob } from './task-edits';
import type { CreationRequest } from './creation-requests';
export type AssistantEditRequest = {
    task_id: string;
    request_id: string;
    phase: "preview" | "apply" | "script";
    status: "recorded" | "unknown" | "rejected" | "not_submitted";
    query_url: string;
    input_digest?: string | null;
    result?: CreationTaskEditPreviewResponse | TaskMediaJob | Record<string, unknown> | null;
    submitted_input?: Record<string, unknown> | null;
};
export type AssistantBusinessContinuation = {
    type: "business_result";
    event_id: string;
    status: string;
    actionable: boolean;
    phase?: string;
    task_id?: string;
    job_id?: string | null;
    proposal_id?: string | null;
    operation_id?: string | null;
    base_version_id?: string | null;
    result_version_id?: string | null;
    waiting_reason?: string | null;
};
export type AssistantScriptToolReceipt = {
    action: "save" | "query" | "update" | "revoke";
    status: "recorded" | "unknown" | "rejected" | "not_submitted";
    request_id?: string | null;
    tool_id?: string | null;
    tool_version?: number | null;
    expected_version?: number | null;
    query_url: string;
    submitted_input?: Record<string, unknown> | null;
    tool?: Record<string, unknown> | null;
};
export type CreationTaskAssistantTurnResponse = {
    reply: string;
    provider: string;
    model: string;
    edit_preview: CreationTaskEditPreviewResponse | null;
    media_job?: TaskMediaJob | null;
    edit_request?: AssistantEditRequest | null;
    script_tool?: AssistantScriptToolReceipt | null;
    script_draft: CreationTaskScript | null;
    execution_action: "start" | "resume" | "cancel" | null;
    execution_status: CreationExecutionStatus | null;
    topic_candidates: CreationTaskTopicsResponse | null;
    timeline_action?: "undo" | "redo" | "reject" | null;
    turn_id?: string | null;
    turn_status?: "running" | "completed" | "stopped";
    draft_version?: number | null;
};
export type AssistantTaskTurnResult = {
    kind: "task_turn";
    response?: CreationTaskAssistantTurnResponse;
    continuation?: AssistantBusinessContinuation;
    edit_preview?: boolean;
};
/** Host checkpoints for one original user instruction; never a model progress estimate. */
export type AssistantGoalContinuation = {
    original_turn_id: string;
    current_turn_id: string;
    conversation_id: string;
    workspace_id: string;
    task_id: string;
    status: string;
    waiting_reason?: string;
    unfinished_steps?: string[];
    completed_output_checkpoint?: {
        status: "completed";
        scope: "production_and_output_observation";
        job_id: string;
        observation_job_id: string;
        timeline_version_id: string;
        sha256: string;
        measurement_scope: string;
        unavailable: {
            scope: string;
            reason: string;
        }[];
    };
};
export type AssistantToolFailure = {
    reason: string;
    repair_action?: string;
};
export type AssistantStreamEvent = {
    type: "turn_started";
    turn_id: string;
    turn_state: "running" | "completed" | "failed" | "stopped";
    draft_version?: number;
} | {
    type: "turn_status";
    turn_id: string;
    turn_state: "running" | "completed" | "failed" | "stopped";
} | {
    type: "text_delta";
    delta: string;
} | {
    type: "tool_start";
    tool_id: number;
    label: string;
} | {
    type: "tool_end";
    tool_id: number;
    label: string;
    ok: boolean;
    summary: string;
    failure?: AssistantToolFailure;
};
export type AssistantToolActivity = {
    tool_id: number;
    label: string;
    state: "running" | "succeeded" | "failed";
    summary?: string;
    failure?: AssistantToolFailure;
};
export type AssistantBusinessResultEvent = AssistantBusinessContinuation & {
    video_ready?: boolean;
};
export type AssistantTurnActivity = {
    text: string;
    tools: AssistantToolActivity[];
    events: (Extract<AssistantStreamEvent, {
        type: "text_delta" | "tool_start" | "tool_end";
    }> | AssistantBusinessResultEvent | {
        type: "goal_continuation_started";
        source_kind: "business_event";
        original_turn_id: string;
        source_turn_id: string;
        event_id: string;
    })[];
};
export type AssistantConversationTurn = {
    id: string;
    sequence: number;
    user_message: string;
    assistant_text: string;
    status: "running" | "completed" | "failed" | "stopped";
    events: AssistantTurnActivity["events"];
    result: Record<string, unknown> | null;
    created_at: string;
    updated_at: string;
};
export type AssistantSavedReference = {
    kind: "material";
    selection: MaterialAssistantSelection;
    label: string;
} | {
    kind: "person" | "voice";
    profile_id: string;
    version: string;
    label: string;
} | {
    kind: "knowledge";
    knowledge_item_id: string;
    version: string;
    locator?: string;
    label: string;
};
export type AssistantConversationReferenceState = {
    selected_reference: AssistantSavedReference | null;
    reference_version: number;
    status: "ready" | "unavailable";
    reason: string | null;
};
export type AssistantConversation = {
    selected_reference?: AssistantSavedReference | null;
    reference_version?: number;
    id: string;
    workspace_id: string;
    scope_kind: "home" | "material" | "task";
    scope_id: string;
    title: string;
    draft_text: string;
    draft_version: number;
    created_at: string;
    updated_at: string;
    turns: AssistantConversationTurn[];
};
export type AssistantConversationScope = Pick<AssistantConversation, "scope_kind" | "scope_id">;
export type AssistantCapabilities = {
    application_commands: string[];
    pi_tui_commands: {
        available: boolean;
        inventory_source: string;
        inventory_version: string;
        runtime_version: string | null;
        runtime_status: string;
        runtime_reason: string;
        runtime_check: string;
        product_mode: string;
        reason: string;
        commands: string[];
    };
    extension_commands: {
        available: boolean;
        reason: string;
    };
    skills: {
        available: boolean;
        reason: string;
    };
    prompt_templates: {
        available: boolean;
        reason: string;
    };
    business_tools: Record<string, string[]>;
};
export type CreationTaskAssistantHistory = {
    items: {
        role: "user" | "assistant";
        message: string;
        stage: string;
        script_id: string | null;
        at: string;
    }[];
};
export type WorkspaceAssistantPlanTurnResponse = {
    reply: string;
    provider: string;
    model: string;
    plan_preview: CreationPlanPreview | null;
    creation_request?: CreationRequest | null;
    turn_id?: string | null;
    turn_status?: "running" | "completed" | "stopped";
    draft_version?: number | null;
};
