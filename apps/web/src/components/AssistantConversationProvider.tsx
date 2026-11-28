"use client";

import { createContext, useContext, useEffect, useState, useCallback, useRef, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { ASSISTANT_LOCATE_REFERENCE_EVENT, assistantReferenceHref } from "@/lib/assistant-reference";
import { workspaceNavigationHref } from "./workbench/context";
import { AssistantConversationContext } from "./AssistantConversationContext";
import { useScopedAssistantConversation } from "./useAssistantConversation";
import { useLocalMemberIdentity } from "@/hooks/useLocalMemberIdentity";
import { useWorkbenchTarget } from "./workbench/WorkbenchTarget";
import { ASSISTANT_SELECTION_EVENT, assistantReferenceItems, assistantReferenceKey, type SelectedAssistantReference, ASSISTANT_MATERIAL_REFERENCE_EVENT, ASSISTANT_PROFILE_REFERENCE_EVENT, ASSISTANT_KNOWLEDGE_REFERENCE_EVENT, type AssistantKnowledgeReference, type AssistantMaterialReference, type AssistantProfileReference } from "@/lib/assistant-reference";

import { getAssistantConversationReference, saveAssistantConversationReference } from "@/lib/api";
import { attachAssistantTurnPdf } from "@/lib/api/assistant";
import { preflightReturnUrl, validDraftId } from "@/lib/preflight-draft";
import type { AssistantConversationScope, AssistantSavedReference } from "@/lib/types";

const draftScopePrefix = "draft:";
const ReferenceStatusContext = createContext<{pending:boolean;reason:string|null;pdfUploading?:boolean;cancel?:()=>void;readAgain?:()=>void}>({pending:false,reason:null});
export function useAssistantReferenceStatus() { return useContext(ReferenceStatusContext); }

const MaterialReferenceContext = createContext<{ reference: AssistantMaterialReference | null; setReference: (value: AssistantMaterialReference | null) => void } | null>(null);
export function useAssistantMaterialReference() { return useContext(MaterialReferenceContext); }

const ProfileReferenceContext = createContext<{reference: AssistantProfileReference | null; setReference: (value: AssistantProfileReference | null) => void} | null>(null);
export function useAssistantProfileReference() { return useContext(ProfileReferenceContext); }

const KnowledgeReferenceContext = createContext<{reference:AssistantKnowledgeReference|null;setReference:(value:AssistantKnowledgeReference|null)=>void}|null>(null);
export function useAssistantKnowledgeReference() { return useContext(KnowledgeReferenceContext); }

const SelectionContext = createContext<{items:SelectedAssistantReference[];version:number|undefined;select:(items:SelectedAssistantReference[],additive?:boolean)=>void;remove:(item:SelectedAssistantReference)=>void;attachPdf?:(file:File)=>Promise<void>}|null>(null);
export function useAssistantSelection() { return useContext(SelectionContext); }

type ReferenceSelection = {
  conversationId: string | null;
  scopeKey: string;
  value: AssistantSavedReference | null;
  pending: boolean;
  reason: string | null;
};

function MemberConversation({ children }: { children: ReactNode }) {
  const router = useRouter();
  const { targetId, draftId } = useWorkbenchTarget();
  useEffect(() => {
    const locate = (event: Event) => {
      const item = (event as CustomEvent<Parameters<typeof assistantReferenceHref>[0]>).detail;
      // Give the mounted workbench a chance to handle an in-place location.
      queueMicrotask(() => {
        if (event.defaultPrevented) return;
        const href = assistantReferenceHref(item);
        if (targetId && (item.kind === "material" || item.kind === "knowledge")) {
          const [pathname, query] = href.split("?");
          router.push(`${workspaceNavigationHref(pathname, true)}&${query}`);
        } else {
          router.push(href);
        }
      });
    };
    window.addEventListener(ASSISTANT_LOCATE_REFERENCE_EVENT, locate);
    return () => window.removeEventListener(ASSISTANT_LOCATE_REFERENCE_EVENT, locate);
  }, [router, targetId]);
  const scope: AssistantConversationScope = targetId
    ? { scope_kind: "task", scope_id: targetId.slice("/tasks/".length) }
    : { scope_kind: "home", scope_id: draftId ? `${draftScopePrefix}${draftId}` : "home" };
  const state = useScopedAssistantConversation(scope);
  const scopeKey = `${scope.scope_kind}:${scope.scope_id}`;
  const { resume: resumeInScope, isCurrentConversation } = state;
  const resume = useCallback(async (id: string) => {
    const resumed = await resumeInScope(id);
    if (scopeKey === "home:home" && isCurrentConversation(resumed.id)
        && resumed.scope_kind === "home" && resumed.scope_id.startsWith(draftScopePrefix)) {
      const resumedDraftId = resumed.scope_id.slice(draftScopePrefix.length);
      if (validDraftId(resumedDraftId)) router.push(preflightReturnUrl(resumedDraftId.toLowerCase(), null));
    }
    return resumed;
  }, [resumeInScope, isCurrentConversation, scopeKey, router]);
  const conversationId = state.conversation?.id ?? null;
  const [selection, setSelection] = useState<ReferenceSelection>({
    conversationId: null, scopeKey, value: null, pending: true, reason: null,
  });
  const [referenceReadAttempt, setReferenceReadAttempt] = useState(0);
  const reference = selection.conversationId === conversationId && selection.scopeKey === scopeKey
    ? selection
    : {value: state.conversation?.selected_reference ?? null, pending: true, reason: null};
  const items = assistantReferenceItems(reference.value);
  const itemsRef = useRef(items);
  itemsRef.current = items;
  const activeId = useRef<string | null>(null);
  const explicitReferenceId = useRef<string|null>(null);
  if (activeId.current !== conversationId) explicitReferenceId.current=null;
  activeId.current = conversationId;
  const readReferenceAgain = useCallback(() => {
    explicitReferenceId.current = null;
    setReferenceReadAttempt(attempt => attempt + 1);
  }, []);
  const referenceRevision = useRef(0);
  const versions = useRef(new Map<string,number>());
  const queues = useRef(new Map<string, Promise<unknown>>());
  const pdfUploads = useRef(new Set<string>());
  const pendingBeforeLoad = useRef<{scopeKey:string;value:AssistantSavedReference|null}|null>(null);
  const showReference = useCallback((conversationId: string | null, value: AssistantSavedReference | null,
    pending: boolean, reason: string | null = null) => {
    setSelection({conversationId, scopeKey, value, pending, reason});
  }, [scopeKey]);
  const selectReference = useCallback((value:AssistantSavedReference|null) => {
    const id = activeId.current;
    if (id && pdfUploads.current.has(id)) return;
    // Events can arrive in the same React batch (for example cross-page handoff).
    // The next selection must compose with the optimistic selection immediately.
    itemsRef.current=assistantReferenceItems(value);
    if (!id) {
      pendingBeforeLoad.current={scopeKey,value};
      showReference(null,value,true);
      return;
    }
    pendingBeforeLoad.current = null;
    explicitReferenceId.current=id;
    const revision = ++referenceRevision.current;
    showReference(id,value,true);
    const operation = (queues.current.get(id) ?? Promise.resolve()).catch(() => undefined).then(async () => {
      let version = versions.current.get(id);
      if (version === undefined) { const current = await getAssistantConversationReference(id); version=current.reference_version; versions.current.set(id,version); }
      const result = await saveAssistantConversationReference(id,value,version);
      versions.current.set(id,result.reference_version);
      return result;
    });
    // After a failed CAS the next explicit action must read a fresh version;
    // do not restore the previous selection or retry the rejected write.
    queues.current.set(id,operation.catch(() => { versions.current.delete(id); }));
    void operation.then(result => {
      if (activeId.current !== id || referenceRevision.current !== revision) return;
      showReference(id,result.selected_reference,false,result.status === "unavailable" ? result.reason : null);
    }).catch(cause => {
      if (activeId.current === id && referenceRevision.current === revision) {
        showReference(id,value,false,cause instanceof Error ? cause.message : "引用没有保存，请重新查看或取消。");
      }
    });
  }, [scopeKey, showReference]);
  const attachPdf = useCallback(async (file: File) => {
    const id = activeId.current;
    const version = id ? versions.current.get(id) : undefined;
    if (!id || version === undefined || reference.pending || pdfUploads.current.has(id)) {
      throw new Error("引用正在恢复，请稍后添加附件。");
    }
    explicitReferenceId.current = id;
    const revision = ++referenceRevision.current;
    const previous = reference.value;
    pdfUploads.current.add(id);
    showReference(id, previous, true, reference.reason);
    const operation = attachAssistantTurnPdf(id, file, version);
    queues.current.set(id, operation);
    try {
      const result = await operation;
      versions.current.set(id, result.reference_version);
      if (activeId.current === id && referenceRevision.current === revision) {
        showReference(id, result.selected_reference, false, result.reason);
      }
    } catch (cause) {
      if (activeId.current === id && referenceRevision.current === revision) {
        showReference(id, previous, false, reference.reason);
        readReferenceAgain();
      }
      throw cause;
    } finally {
      pdfUploads.current.delete(id);
    }
  }, [readReferenceAgain, reference.pending, reference.reason, reference.value, showReference]);
  const serverReferenceVersion = state.conversation?.reference_version;
  const serverReference = useRef(state.conversation?.selected_reference);
  serverReference.current = state.conversation?.selected_reference;
  useEffect(() => {
    const id = conversationId;
    // An explicit selection in this render epoch owns the state and performs its own authenticated read/save.
    if (id && explicitReferenceId.current === id
        && (serverReferenceVersion ?? 0) <= (versions.current.get(id) ?? 0)) return;
    const revision = ++referenceRevision.current;
    if (pendingBeforeLoad.current?.scopeKey !== scopeKey) pendingBeforeLoad.current = null;
    const pending = pendingBeforeLoad.current;
    const value = pending ? pending.value : serverReference.current ?? null;
    showReference(id,value,Boolean(id) || Boolean(pending));
    if (!id) return;
    const operation = (queues.current.get(id) ?? Promise.resolve()).catch(() => undefined)
      .then(() => getAssistantConversationReference(id));
    queues.current.set(id,operation);
    void operation.then(result => {
      versions.current.set(id,result.reference_version);
      if (activeId.current !== id || referenceRevision.current !== revision) return;
      if (pendingBeforeLoad.current === pending && pending) {
        pendingBeforeLoad.current=null;
        selectReference(pending.value);
        return;
      }
      showReference(id,result.selected_reference,false,result.status === "unavailable" ? result.reason ?? "所引用对象当前不可读取，请重新查看或取消引用。" : null);
    }).catch(cause => {
      if (activeId.current === id && referenceRevision.current === revision) {
        showReference(id,value,false,cause instanceof Error ? cause.message : "引用未能恢复，请重新查看或取消。");
      }
    });
  }, [conversationId, referenceReadAttempt, scopeKey, selectReference, serverReferenceVersion, showReference]);
  const selectItems = useCallback((next:SelectedAssistantReference[], additive = false) => {
    const unique = new Map((additive ? [...itemsRef.current,...next] : next).map(item=>[assistantReferenceKey(item),item]));
    const values = [...unique.values()];
    selectReference(values.length ? {kind:"selection",items:values} : null);
  },[selectReference]);
  const removeItem = useCallback((item:SelectedAssistantReference) => selectItems(itemsRef.current.filter(value=>assistantReferenceKey(value)!==assistantReferenceKey(item))),[selectItems]);
  const material = items.find(item=>item.kind === "material");
  const materialReference = material?.kind === "material" ? {selection:material.selection,label:material.label} : null;
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
  return <AssistantConversationContext.Provider value={{...state,resume}}><ReferenceStatusContext.Provider value={{pending:reference.pending,reason:reference.reason,pdfUploading:!!conversationId && pdfUploads.current.has(conversationId),cancel:()=>selectReference(null),readAgain:readReferenceAgain}}>
    <SelectionContext.Provider value={{items,version:conversationId ? versions.current.get(conversationId) : undefined,select:selectItems,remove:removeItem,attachPdf:scope.scope_kind === "home" && state.conversation?.turns.length === 0 ? attachPdf : undefined}}><MaterialReferenceContext.Provider value={{reference:materialReference,setReference:selectMaterial}}>
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
