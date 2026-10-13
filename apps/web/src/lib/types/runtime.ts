/**
 * 外部文字模型服务的四档状态。抄自后端 `app/config.py` 的
 * `LLM_SERVICE_MISSING / UNVERIFIED / AVAILABLE / FAILED`（dev `add94df`）。
 *
 * 注意 `SystemStatus.llm.configured` 只是「字段填了没有」，与这四档无关：
 * `configured === true` 绝不能被显示成「可用」。B03 立的线就是这两件事。
 *
 * 后端目前只通过 `/api/settings/runtime` 的 `LLM_SERVICE_STATE` 行把
 * `label`（中文）送到前端；`cause` / `detail` / `observed_at` 没有落到任何
 * 接口上，所以前端拿不到失败的具体原因——拿不到就说拿不到，不许编。
 */
export type LlmServiceState = "missing" | "unverified" | "available" | "failed";
export type SystemStatus = {
    llm: {
        provider: string;
        configured: boolean;
        model: string | null;
        base_url: string;
    };
    obsidian: {
        configured: boolean;
        exists: boolean;
        path: string | null;
        markdown_count: number | null;
    };
    product_hunt: {
        configured: boolean;
        api_url: string;
    };
    external_sources: {
        configured: boolean;
        urls: string[];
    };
    hermes_radar: {
        configured: boolean;
        exists: boolean;
        path: string | null;
        latest_report: string | null;
        total_count: number;
        product_hunt_count: number;
        watcha_count: number;
    };
    video: {
        inbox_path: string;
        output_path: string;
        inbox_exists: boolean;
        output_exists: boolean;
        pending_count: number;
        ffmpeg_available: boolean;
    };
    storage: {
        database_path: string;
        database_exists: boolean;
    };
    asr: {
        provider: string;
        configured: boolean;
        region: string;
        engine_model_type: string;
        max_local_audio_mb: number;
        local_whisper_configured: boolean;
        local_whisper_python_path: string | null;
        local_whisper_model_path: string | null;
    };
};
export type SetupStatus = {
    configured: boolean;
    missing: string[];
    next_step: string;
};
export type SetupPreflightCheckStatus = "ready" | "missing" | "invalid" | "degraded" | "not_applicable";
export type SetupPreflightOverall = "ready" | "blocked";
export type SetupPreflightCheckItem = {
    id: string;
    label: string;
    status: SetupPreflightCheckStatus;
    summary: string;
    impact: string;
    repair_action: string;
    settings_target: string | null;
    required: boolean;
};
export type SetupPreflightResponse = {
    overall: SetupPreflightOverall;
    checks: SetupPreflightCheckItem[];
    generated_at: string;
};
export type OnboardingCompletionResponse = {
    workspace_id: string;
    completed: boolean;
};
export type RuntimeHealthStatus = "ready" | "missing" | "degraded" | "invalid";
export type RuntimeHealthItem = {
    id: string;
    label: string;
    status: RuntimeHealthStatus;
    reason: string;
    impact: string;
    repair_action: string;
    last_checked_at: string;
};
export type RuntimeHealth = {
    summary: {
        total: number;
        ready: number;
        missing: number;
        degraded: number;
        invalid: number;
    };
    items: RuntimeHealthItem[];
};
export type RuntimeSettingItem = {
    name: string;
    configured: boolean;
    value: string | null;
    secret: boolean;
};
export type RuntimeSettings = {
    settings: RuntimeSettingItem[];
    status: SystemStatus;
};
export type RuntimeSettingsUpdate = {
    understanding_runtime_python_path?: string;
    native_ocr_worker_path?: string;
    asr_provider?: string;
    local_whisper_backend?: string;
    local_whisper_cli_path?: string;
    llm_provider?: string;
    llm_api_key?: string;
    llm_key_action?: "delete";
    llm_base_url?: string;
    llm_model?: string;
    deepseek_api_key?: string;
    deepseek_model?: string;
    product_hunt_token?: string;
    tencentcloud_secret_id?: string;
    tencentcloud_secret_key?: string;
    tencentcloud_asr_region?: string;
    tencentcloud_asr_engine_model_type?: string;
    local_whisper_python_path?: string;
    local_whisper_model_path?: string;
    local_whisper_language?: string;
    obsidian_vault_path?: string;
    hermes_radar_path?: string;
    video_inbox_path?: string;
    video_output_path?: string;
    draft_output_path?: string;
    jianying_draft_root_path?: string;
    database_path?: string;
    model_root_path?: string;
    temporary_root_path?: string;
    topic_source_urls?: string[];
};
export type LocalModelStatus = {
    model: {
        id: string;
        capability: string;
        display_name: string;
        repository: string;
        revision: string;
        license_name: string;
        license_url: string;
        approximate_download_bytes: number;
    };
    state: "missing_runtime" | "missing_worker" | "not_installed" | "invalid" | "ready";
    ready: boolean;
    message: string;
    model_path: string;
    runtime_python_path: string | null;
    worker_path: string | null;
};
export type LocalModelCatalog = {
    models: LocalModelStatus[];
};
export type ModelInstallJob = {
    id: string;
    model_id: string;
    display_name: string;
    status: "queued" | "running" | "ready" | "failed" | "interrupted";
    phase: string;
    message: string;
    bytes_downloaded: number;
    bytes_total: number;
    progress: number;
    license_name: string;
    license_url: string;
    license_accepted_at: string;
    created_at: string;
    updated_at: string;
    model_root_path: string;
    external_volume: string | null;
    free_bytes: number;
    required_bytes: number;
};
export type WorkflowStatus = {
    framework: string;
    node_names: string[];
    human_review_nodes: string[];
};
