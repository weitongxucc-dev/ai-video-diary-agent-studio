import type { MaterialAssistantSelection } from "./types";
import type { AssistantReferenceItem, AssistantSavedReference, AssistantTimelineReference } from "./types/assistant";

export type SelectedAssistantReference = AssistantReferenceItem | AssistantTimelineReference;
export const ASSISTANT_SELECTION_EVENT = "yaohe:assistant-selection";
export const ASSISTANT_LOCATE_REFERENCE_EVENT = "yaohe:assistant-locate-reference";
export function assistantReferenceItems(value: AssistantSavedReference | null | undefined): SelectedAssistantReference[] {
  return !value ? [] : value.kind === "selection" ? value.items : [value];
}
export function assistantReferenceKey(value: SelectedAssistantReference): string {
  if (value.kind === "material") return `material:${value.selection.asset_id}:${value.selection.scope === "segment" ? value.selection.segment_id : ""}`;
  if ("profile_id" in value) return `${value.kind}:${value.profile_id}`;
  if (value.kind === "knowledge") return `knowledge:${value.knowledge_item_id}:${value.locator ?? ""}`;
  return `${value.kind}:${value.task_id}:${value.object_id}`;
}
export function referenceAssistantSelection(items: SelectedAssistantReference[], additive = false) {
  window.dispatchEvent(new CustomEvent(ASSISTANT_SELECTION_EVENT, { detail: { items, additive } }));
}
export function locateAssistantReference(reference: SelectedAssistantReference) {
  window.dispatchEvent(new CustomEvent(ASSISTANT_LOCATE_REFERENCE_EVENT, { detail: reference }));
}
export function assistantReferenceHref(reference: SelectedAssistantReference): string {
  const base = reference.kind === "material" ? "/library"
    : reference.kind === "knowledge" ? "/knowledge"
      : "profile_id" in reference ? `/profiles?view=${reference.kind === "voice" ? "voices" : "people"}`
        : `/tasks/${encodeURIComponent(reference.task_id)}`;
  return `${base}${base.includes("?") ? "&" : "?"}locate_ref=${encodeURIComponent(JSON.stringify(reference))}`;
}
/** A navigation hint only. Consumers must read and authorize the actual object. */
export function readAssistantLocateReference(search: {get:(key:string)=>string|null}): SelectedAssistantReference | null {
  const raw = search.get("locate_ref");
  if (!raw) return null;
  const value = JSON.parse(raw);
  if (!value || typeof value !== "object" || typeof value.label !== "string"
      || !["material","knowledge","person","voice","shot","narration","audio","subtitle","effect"].includes(value.kind)) throw new Error("引用定位格式不可用。");
  assistantReferenceKey(value);
  return value as SelectedAssistantReference;
}

export type AssistantMaterialReference = { selection: MaterialAssistantSelection; label: string };
export const ASSISTANT_MATERIAL_REFERENCE_EVENT = "yaohe:assistant-material-reference";

export function referenceAssistantMaterial(reference: AssistantMaterialReference, additive = false) {
  if (additive) { referenceAssistantSelection([{kind:"material",...reference}],true); return; }
  window.dispatchEvent(new CustomEvent(ASSISTANT_MATERIAL_REFERENCE_EVENT, { detail: reference }));
}
export function clearAssistantMaterialReference() {
  window.dispatchEvent(new CustomEvent(ASSISTANT_MATERIAL_REFERENCE_EVENT, { detail: null }));
}

export type AssistantProfileReference = { kind: "person" | "voice"; profile_id: string; version: string; label: string };
export const ASSISTANT_PROFILE_REFERENCE_EVENT = "yaohe:assistant-profile-reference";
export function referenceAssistantProfile(reference: AssistantProfileReference, additive = false) {
  if (additive) { referenceAssistantSelection([reference],true); return; }
  window.dispatchEvent(new CustomEvent(ASSISTANT_PROFILE_REFERENCE_EVENT, { detail: reference }));
}
export function clearAssistantProfileReference() {
  window.dispatchEvent(new CustomEvent(ASSISTANT_PROFILE_REFERENCE_EVENT, { detail: null }));
}

export type AssistantKnowledgeReference = { knowledge_item_id: string; version: string; locator?: string; label: string };
export const ASSISTANT_KNOWLEDGE_REFERENCE_EVENT = "yaohe:assistant-knowledge-reference";
export function referenceAssistantKnowledge(reference: AssistantKnowledgeReference, additive = false) {
  if (additive) { referenceAssistantSelection([{kind:"knowledge",...reference}],true); return; }
  window.dispatchEvent(new CustomEvent(ASSISTANT_KNOWLEDGE_REFERENCE_EVENT, { detail: reference }));
}
export function clearAssistantKnowledgeReference() {
  window.dispatchEvent(new CustomEvent(ASSISTANT_KNOWLEDGE_REFERENCE_EVENT, { detail: null }));
}
