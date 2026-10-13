import type { ProductionProfile, VoiceProfile, VoiceProfiles } from '../types/profiles';
import { request } from './transport';
export function updateProductionProfile(input: Omit<ProductionProfile, "workspace_id" | "primary_platform" | "aspect_ratio" | "voice_model_status" | "voice_model_message" | "updated_at">): Promise<ProductionProfile> {
    return request<ProductionProfile>("/api/production/profile", {
        method: "PUT",
        body: JSON.stringify(input),
    });
}
export function getVoiceProfiles(): Promise<VoiceProfiles> {
    return request<VoiceProfiles>("/api/production/voices");
}
export function createVoiceProfile(input: {
    name: string;
    reference_audio_path: string;
    reference_text: string;
    consent_confirmed: boolean;
    is_default: boolean;
}): Promise<VoiceProfile> {
    return request<VoiceProfile>("/api/production/voices", {
        method: "POST",
        body: JSON.stringify(input),
    });
}
export function updateVoiceProfile(voiceProfileId: string, input: {
    name: string;
    reference_audio_path: string;
    reference_text: string;
    consent_confirmed: boolean;
    is_default: boolean;
}): Promise<VoiceProfile> {
    return request<VoiceProfile>(`/api/production/voices/${voiceProfileId}`, {
        method: "PUT",
        body: JSON.stringify(input),
    });
}
