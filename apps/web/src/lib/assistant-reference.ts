import type { MaterialAssistantSelection } from "./types";

export type AssistantMaterialReference = { selection: MaterialAssistantSelection; label: string };
export const ASSISTANT_MATERIAL_REFERENCE_EVENT = "yaohe:assistant-material-reference";

export function referenceAssistantMaterial(reference: AssistantMaterialReference) {
  window.dispatchEvent(new CustomEvent(ASSISTANT_MATERIAL_REFERENCE_EVENT, { detail: reference }));
}
export function clearAssistantMaterialReference() {
  window.dispatchEvent(new CustomEvent(ASSISTANT_MATERIAL_REFERENCE_EVENT, { detail: null }));
}

export type AssistantProfileReference = { kind: "person" | "voice"; profile_id: string; version: string; label: string };
export const ASSISTANT_PROFILE_REFERENCE_EVENT = "yaohe:assistant-profile-reference";
export function referenceAssistantProfile(reference: AssistantProfileReference) {
  window.dispatchEvent(new CustomEvent(ASSISTANT_PROFILE_REFERENCE_EVENT, { detail: reference }));
}
export function clearAssistantProfileReference() {
  window.dispatchEvent(new CustomEvent(ASSISTANT_PROFILE_REFERENCE_EVENT, { detail: null }));
}

export type AssistantKnowledgeReference = { knowledge_item_id: string; version: string; locator?: string; label: string };
export const ASSISTANT_KNOWLEDGE_REFERENCE_EVENT = "yaohe:assistant-knowledge-reference";
export function referenceAssistantKnowledge(reference: AssistantKnowledgeReference) {
  window.dispatchEvent(new CustomEvent(ASSISTANT_KNOWLEDGE_REFERENCE_EVENT, { detail: reference }));
}
export function clearAssistantKnowledgeReference() {
  window.dispatchEvent(new CustomEvent(ASSISTANT_KNOWLEDGE_REFERENCE_EVENT, { detail: null }));
}
