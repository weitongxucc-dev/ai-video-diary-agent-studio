import type { LocalModelCatalog, ModelInstallJob, OnboardingCompletionResponse, RuntimeHealth, RuntimeSettings, RuntimeSettingsUpdate, SetupPreflightResponse, SetupStatus, SystemStatus, WorkflowStatus } from '../types/runtime';
import { request } from './transport';
export function getSystemStatus(): Promise<SystemStatus> {
    return request<SystemStatus>("/api/system/status");
}
export function getWorkflowStatus(): Promise<WorkflowStatus> {
    return request<WorkflowStatus>("/api/workflow/status");
}
export function getRuntimeSettings(): Promise<RuntimeSettings> {
    return request<RuntimeSettings>("/api/settings/runtime");
}
export function getRuntimeHealth(): Promise<RuntimeHealth> {
    return request<RuntimeHealth>("/api/runtime/health");
}
export function getSetupStatus(): Promise<SetupStatus> {
    return request<SetupStatus>("/api/is_setup");
}
export function getSetupPreflight(): Promise<SetupPreflightResponse> {
    return request<SetupPreflightResponse>("/api/setup/preflight");
}
export function completeOnboarding(): Promise<OnboardingCompletionResponse> {
    return request<OnboardingCompletionResponse>("/api/onboarding/complete", {
        method: "POST",
    });
}
export function updateRuntimeSettings(payload: RuntimeSettingsUpdate): Promise<RuntimeSettings> {
    return request<RuntimeSettings>("/api/settings/runtime", {
        method: "POST",
        body: JSON.stringify(payload),
    });
}
export function getLocalModels(): Promise<LocalModelCatalog> {
    return request<LocalModelCatalog>("/api/models");
}
export function installLocalModel(modelId: string): Promise<ModelInstallJob> {
    return request<ModelInstallJob>(`/api/models/${encodeURIComponent(modelId)}/install`, {
        method: "POST",
        body: JSON.stringify({ license_accepted: true }),
    });
}
export function getModelInstallJob(jobId: string): Promise<ModelInstallJob> {
    return request<ModelInstallJob>(`/api/model-install-jobs/${encodeURIComponent(jobId)}`);
}
