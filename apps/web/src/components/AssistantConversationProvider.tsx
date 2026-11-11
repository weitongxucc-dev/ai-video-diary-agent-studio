"use client";

import { createContext, useContext, useEffect, useState, useCallback, useRef, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { ASSISTANT_LOCATE_REFERENCE_EVENT, assistantReferenceHref } from "@/lib/assistant-reference";
import { AssistantConversationContext } from "./AssistantConversationContext";
import { useScopedAssistantConversation } from "./useAssistantConversation";
import { useLocalMemberIdentity } from "@/hooks/useLocalMemberIdentity";
import { useWorkbenchTarget } from "./workbench/WorkbenchTarget";
import { ASSISTANT_SELECTION_EVENT, assistantReferenceItems, assistantReferenceKey, type SelectedAssistantReference, ASSISTANT_MATERIAL_REFERENCE_EVENT, ASSISTANT_PROFILE_REFERENCE_EVENT, ASSISTANT_KNOWLEDGE_REFERENCE_EVENT, type AssistantKnowledgeReference, type AssistantMaterialReference, type AssistantProfileReference } from "@/lib/assistant-reference";

import { getAssistantConversationReference, saveAssistantConversationReference } from "@/lib/api";
import type { AssistantSavedReference } from "@/lib/types";

const ReferenceStatusContext = createContext<{pending:boolean;reason:string|null;cancel?:()=>void}>({pending:false,reason:null});
export function useAssistantReferenceStatus() { return useContext(ReferenceStatusContext); }

const MaterialReferenceContext = createContext<{ reference: AssistantMaterialReference | null; setReference: (value: AssistantMaterialReference | null) => void } | null>(null);
export function useAssistantMaterialReference() { return useContext(MaterialReferenceContext); }

const ProfileReferenceContext = createContext<{reference: AssistantProfileReference | null; setReference: (value: AssistantProfileReference | null) => void} | null>(null);
export function useAssistantProfileReference() { return useContext(ProfileReferenceContext); }

const KnowledgeReferenceContext = createContext<{reference:AssistantKnowledgeReference|null;setReference:(value:AssistantKnowledgeReference|null)=>void}|null>(null);
export function useAssistantKnowledgeReference() { return useContext(KnowledgeReferenceContext); }

const SelectionContext = createContext<{items:SelectedAssistantReference[];version:number|undefined;select:(items:SelectedAssistantReference[],additive?:boolean)=>void;remove:(item:SelectedAssistantReference)=>void}|null>(null);
export function useAssistantSelection() { return useContext(SelectionContext); }

function MemberConversation({ children }: { children: ReactNode }) {
  const router = useRouter();
  useEffect(() => {
    const locate = (event: Event) => router.push(assistantReferenceHref((event as CustomEvent<SelectedAssistantReference>).detail));
    window.addEventListener(ASSISTANT_LOCATE_REFERENCE_EVENT, locate);
    return () => window.removeEventListener(ASSISTANT_LOCATE_REFERENCE_EVENT, locate);
  }, [router]);
  const { targetId } = useWorkbenchTarget();
  const state = useScopedAssistantConversation(targetId ? { scope_kind: "task", scope_id: targetId.slice("/tasks/".length) } : { scope_kind: "home", scope_id: "home" });
  const [savedReference, setSavedReference] = useState<AssistantSavedReference | null>(null);
  const itemsRef = useRef(assistantReferenceItems(savedReference));
  const [referenceStatus, setReferenceStatus] = useState({pending:true,reason:null as string|null});
  const activeId = useRef<string | null>(null);
  const explicitReferenceId = useRef<string|null>(null);
  if (activeId.current !== (state.conversation?.id ?? null)) explicitReferenceId.current=null;
  activeId.current = state.conversation?.id ?? null;
  const referenceRevision = useRef(0);
  const versions = useRef(new Map<string,number>());
  const queue = useRef<Promise<unknown>>(Promise.resolve());
  const pendingBeforeLoad = useRef<{value:AssistantSavedReference|null}|null>(null);
  const selectReference = useCallback((value:AssistantSavedReference|null) => {
    // Events can arrive in the same React batch (for example cross-page handoff).
    // The next selection must compose with the optimistic selection immediately.
    itemsRef.current=assistantReferenceItems(value);
    const id = activeId.current;
    if (!id) { pendingBeforeLoad.current={value}; setSavedReference(value); setReferenceStatus({pending:true,reason:null}); return; }
    explicitReferenceId.current=id;
    const revision = ++referenceRevision.current;
    setSavedReference(value);
    setReferenceStatus({pending:true,reason:null});
    const operation = queue.current.catch(() => undefined).then(async () => {
      let version = versions.current.get(id);
      if (version === undefined) { const current = await getAssistantConversationReference(id); version=current.reference_version; versions.current.set(id,version); }
      const result = await saveAssistantConversationReference(id,value,version);
      versions.current.set(id,result.reference_version);
      return result;
    });
    // After a failed CAS the next explicit action must read a fresh version;
    // do not restore the previous selection or retry the rejected write.
    queue.current=operation.catch(() => { versions.current.delete(id); });
    void operation.then(result => {
      if (activeId.current !== id || referenceRevision.current !== revision) return;
      setSavedReference(result.selected_reference);
      setReferenceStatus({pending:false,reason:result.status === "unavailable" ? result.reason : null});
    }).catch(cause => {
      if (activeId.current === id && referenceRevision.current === revision) setReferenceStatus({pending:false,reason:cause instanceof Error ? cause.message : "引用没有保存，请重新查看或取消。"});
    });
  }, []);
  useEffect(() => {
    const id = state.conversation?.id;
    // An explicit selection in this render epoch owns the state and performs its own authenticated read/save.
    if (id && explicitReferenceId.current === id) return;
    const revision = ++referenceRevision.current;
    if (!pendingBeforeLoad.current) setSavedReference(state.conversation?.selected_reference ?? null);
    setReferenceStatus({pending:Boolean(id),reason:null});
    if (!id) return;
    const operation = queue.current.catch(() => undefined).then(() => getAssistantConversationReference(id));
    queue.current = operation;
    void operation.then(value => {
      versions.current.set(id,value.reference_version);
      if (activeId.current !== id || referenceRevision.current !== revision) return;
      if (pendingBeforeLoad.current) { const pending=pendingBeforeLoad.current.value; pendingBeforeLoad.current=null; selectReference(pending); return; }
      setSavedReference(value.selected_reference);
      setReferenceStatus({pending:false,reason:value.status === "unavailable" ? value.reason ?? "所引用对象当前不可读取，请重新查看或取消引用。" : null});
    }).catch(cause => {
      if (activeId.current === id && referenceRevision.current === revision) setReferenceStatus({pending:false,reason:cause instanceof Error ? cause.message : "引用未能恢复，请重新查看或取消。"});
    });
  }, [state.conversation?.id, selectReference]);
  const items = assistantReferenceItems(savedReference);
  itemsRef.current = items;
  const selectItems = useCallback((next:SelectedAssistantReference[], additive = false) => {
    const unique = new Map((additive ? [...itemsRef.current,...next] : next).map(item=>[assistantReferenceKey(item),item]));
    const values = [...unique.values()];
    selectReference(values.length ? {kind:"selection",items:values} : null);
  },[selectReference]);
  const removeItem = useCallback((item:SelectedAssistantReference) => selectItems(itemsRef.current.filter(value=>assistantReferenceKey(value)!==assistantReferenceKey(item))),[selectItems]);
  const material = items.find(item=>item.kind === "material");
  const reference = material?.kind === "material" ? {selection:material.selection,label:material.label} : null;
  const profile = items.find(item=>item.kind === "person" || item.kind === "voice");
  const profileReference = profile && "profile_id" in profile ? profile : null;
  const knowledge = items.find(item=>item.kind === "knowledge");
  const knowledgeReference = knowledge?.kind === "knowledge" ? knowledge : null;
  const selectMaterial = useCallback((value:AssistantMaterialReference|null) => selectItems([
    ...itemsRef.current.filter(item=>item.kind!=="material"),...(value ? [{kind:"material" as const,...value}] : []),
  ]),[selectItems]);
  const selectProfile = useCallback((value:AssistantProfileReference|null) => selectItems([
    ...itemsRef.current.filter(item=>value ? item.kind!==value.kind : item.kind!=="person" && item.kind!=="voice"),...(value ? [value] : []),
  ]),[selectItems]);
  const selectKnowledge = useCallback((value:AssistantKnowledgeReference|null) => selectItems([
    ...itemsRef.current.filter(item=>item.kind!=="knowledge"),...(value ? [{kind:"knowledge" as const,...value}] : []),
  ]),[selectItems]);
  useEffect(() => {
    const receive = (event:Event) => {
      const value=(event as CustomEvent<{items:SelectedAssistantReference[];additive:boolean}>).detail;
      const taskSelection=value.items.some(item=>"task_id" in item);
      selectItems(taskSelection && !value.additive
        ? [...itemsRef.current.filter(item=>!("task_id" in item)),...value.items] : value.items,value.additive);
    };
    window.addEventListener(ASSISTANT_SELECTION_EVENT,receive);
    return () => window.removeEventListener(ASSISTANT_SELECTION_EVENT,receive);
  },[selectItems]);
  useEffect(() => {
    const receiveKnowledge = (event: Event) => selectKnowledge((event as CustomEvent<AssistantKnowledgeReference | null>).detail);
    window.addEventListener(ASSISTANT_KNOWLEDGE_REFERENCE_EVENT, receiveKnowledge);
    return () => window.removeEventListener(ASSISTANT_KNOWLEDGE_REFERENCE_EVENT, receiveKnowledge);
  }, [selectKnowledge]);
  useEffect(() => {
    const receiveProfile = (event: Event) => selectProfile((event as CustomEvent<AssistantProfileReference | null>).detail);
    window.addEventListener(ASSISTANT_PROFILE_REFERENCE_EVENT, receiveProfile);
    return () => window.removeEventListener(ASSISTANT_PROFILE_REFERENCE_EVENT, receiveProfile);
  }, [selectProfile]);
  useEffect(() => {
    const receive = (event: Event) => selectMaterial((event as CustomEvent<AssistantMaterialReference | null>).detail);
    window.addEventListener(ASSISTANT_MATERIAL_REFERENCE_EVENT, receive);
    return () => window.removeEventListener(ASSISTANT_MATERIAL_REFERENCE_EVENT, receive);
  }, [selectMaterial]);
  return <AssistantConversationContext.Provider value={state}><ReferenceStatusContext.Provider value={{...referenceStatus,cancel:()=>selectReference(null)}}>
    <SelectionContext.Provider value={{items,version:activeId.current ? versions.current.get(activeId.current) : undefined,select:selectItems,remove:removeItem}}><MaterialReferenceContext.Provider value={{reference,setReference:selectMaterial}}>
      <ProfileReferenceContext.Provider value={{reference:profileReference,setReference:selectProfile}}>
        <KnowledgeReferenceContext.Provider value={{reference:knowledgeReference,setReference:selectKnowledge}}>{children}</KnowledgeReferenceContext.Provider>
      </ProfileReferenceContext.Provider>
    </MaterialReferenceContext.Provider></SelectionContext.Provider>
  </ReferenceStatusContext.Provider></AssistantConversationContext.Provider>;
}

export function AssistantConversationProvider({ children }: { children: ReactNode }) {
  const identity = useLocalMemberIdentity();
  // The trusted member token is an identity epoch. It is not persisted with drafts
  // or sent to a model. Changing member remounts and reauthorizes the conversation.
  return <MemberConversation key={identity.token ?? "identity-pending"}>{children}</MemberConversation>;
}
