"use client";
import { useWorkspacePage, useWorkspaceViewState } from "./workbench/context";

import { readAssistantLocateReference, referenceAssistantSelection, type SelectedAssistantReference } from "@/lib/assistant-reference";
import { useWorkspaceSearchParams as useSearchParams } from "./workbench/context";
import type { AssistantTimelineReference } from "@/lib/types/assistant";
import { canOpenStage } from "./creation-task/stage-navigation";
import { editExecutionReadinessMessage } from './creation-task/stage-labels';
import { saveTaskVideo, saveTaskCover, saveTaskSubtitles, type SaveFeedback } from "./creation-task/output-saving";
import { ReviewOutputView } from "./creation-task/ReviewOutputView";
import { NarrationPreparation } from "./creation-task/NarrationPreparation";
import { ScriptWindowStart } from "./creation-task/ScriptWindowEditor";
import { useScriptGenerationRequest } from "./creation-task/useScriptGenerationRequest";
import { TopicSelection } from "./creation-task/TopicSelection";
import { useCreationExecution } from "./creation-task/useCreationExecution";
import type { TaskMediaJob, TaskMediaResultMap, CreationTaskNarrationVoiceSelectionRequest } from "@/lib/types";
import { getCreationTaskNarrationVoiceSelection } from "@/lib/api/creation-tasks";
import { readCreationTaskTimelineRestore } from '@/lib/api';
import { useLocalMemberIdentity } from "@/hooks/useLocalMemberIdentity";
import { isLocalAuthMode } from "@/lib/auth/routing";
import { ensureLocalMemberIdentity } from "@/lib/local-member-auth";
import { createClient } from "@/lib/supabase/client";
import { CreationPlanApiError } from "@/lib/api";
import { Tabs, TabsContent } from "./ui/tabs";
import { Button } from "./ui/button";
import { TaskViewNavigation } from "./creation-task/TaskViewNavigation";

import { SubtitleWorkspace } from "./SubtitleWorkspace";
import { GraphicOverlayEditor } from "./GraphicOverlayEditor";
import { DesktopVoiceRecorder } from "./DesktopVoiceRecorder";
import { TimelineVersionHistory } from "./TimelineVersionHistory";
import Link from "next/link";
import { creationTaskStatusPresentation } from "@/lib/creation-task-status";
import { Undo2, Redo2, CheckCircle2, Clapperboard, FileText, History, LoaderCircle, Mic, Sparkles } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { HomeCardDialog } from "@/components/HomeCardDialog";
import { useAssistantSelection, useAssistantKnowledgeReference, useAssistantMaterialReference, useAssistantProfileReference } from "./AssistantConversationProvider";
import { useAssistantConversation } from "@/components/useAssistantConversation";
import { AppShell } from "@/components/AppShell";
import { HomeCommandComposer } from "@/components/HomeCommandComposer";
import { getCreationTaskMediaJob, taskMediaJobMatchesContext, getCreationTaskGraphicSources, cancelCreationTaskMediaJob, resolveCreationTaskMediaResponse, TaskMediaJobError, confirmCreationTaskEdit, confirmCreationTaskScript, transferAssistantReplyToScript, confirmCreationTaskTopic, generateCreationTaskPersonRender, generateCreationTaskTopics, getCreationTaskMontageClipCandidates, getCreationTaskTopics, getCreationPlan, getCreationTaskWorkspace, getCreationTaskScriptSections, getCreationTaskEditPreview, setCreationPlanVisibility, setCreationTaskVisibility, initializeCreationTaskTimeline, getCreationTaskAssistantHistory, startCreationExecution, resumeCreationExecution, cancelCreationExecution, getRuntimeSettings, previewCreationTaskEdit, sendCreationTaskAssistantTurn, sendMaterialAssistantTurn, previewCompoundSelection, previewCreationTaskExactEdit, applyCreationTaskExactEdit, getCreationTaskEditRequest, selectCreationTaskNarrationVoice, listCreationTaskRecordings, adoptCreationTaskRecording, undoCreationTaskTimeline, redoCreationTaskTimeline } from "@/lib/api";
import type { CreationTask, AssistantConversationTurn, CreationTaskQualityCheck, CreationTaskExactEditInput, CreationTaskClipMetadataInput, CreationTaskMetadataSaveInput, CreationTaskEditPreviewResponse, CreationTaskAssistantTurnResponse, CreationTaskRecording, CreationTaskTopicsResponse, CreationTaskWorkspace as WorkspaceState, CreationExecutionStatus, MontageClipCandidate } from "@/lib/types";
import styles from "./CreationTaskWorkspace.module.css";
import { AudioTimelineTracks, AudioTimelineInspector } from "./AudioTimelineEditor";
import { previewCreationTaskAudio, previewCreationTaskVisual, saveCreationTaskRecording } from "@/lib/api";
import type { AudioEditInput, VisualEditInput } from "@/lib/types";
import { SaveVideoTemplateForm } from "./SaveVideoTemplateForm";
import { VideoEditingStage, VideoClipInspector, VideoClipQuickEditor, createVideoClipDraft, editorTime, type VideoClipDraft } from "./VideoEditor";
import editorUi from "./VideoEditor.module.css";
import type { BusyAction, PendingLocalEdit } from './creation-task/edit-context';
import type { WorkspaceView } from './creation-task/stage-navigation';
import type { AssistantTurnRecord } from './creation-task/assistant-history';
import { queuedEditNotice } from './creation-task/edit-context';
import { productStages, stageLabels, defaultWorkspaceView, productStageState } from './creation-task/stage-navigation';
import { errorDetails } from './creation-task/request-errors';
import { executionStageLabels } from './creation-task/execution-labels';
import { assistantHistoryTurns } from './creation-task/assistant-history';
import { TaskIssue } from './creation-task/TaskIssue';
import { Inspector, SubtitleCoverInspector } from './creation-task/TaskInspector';
import { ConfirmedTopicView } from './creation-task/TopicStage';
import { ReadonlyScriptsView, ConfirmedScriptView, ScriptGenerationStatus, ScriptLibrarySave, PlanReplyDraftAction, ScriptEditor } from './creation-task/ScriptStage';
import { NarrationResultView, SegmentProductionPanel } from './creation-task/NarrationStage';
import { PersonStageView } from './creation-task/PersonStage';
import { CreationExecutionPanel } from './creation-task/CreationExecutionPanel';
import { EditPreviewCard } from './creation-task/EditPreviewCard';
import { ContextCommandBar } from './creation-task/TaskConversation';
import { PrepareMaterialsView } from './creation-task/PrepareMaterialsView';
import { PublishStageView } from './creation-task/PublishStageView';
import { FinalVideoQualityCheck } from './creation-task/FinalVideoQualityCheck';
import { JobProgressPanel, NativeOperationsPanel } from './creation-task/TaskJobProgress';

export type Props = {
  initialWorkspace: WorkspaceState;
  initialTopics: CreationTaskTopicsResponse | null;
  initialTasks: CreationTask[];
};

type MetadataEditInput = CreationTaskClipMetadataInput | Extract<CreationTaskExactEditInput, { operation_type: "UpdateSubtitleCue" }>;

function isMetadataSaveInput(input: CreationTaskExactEditInput): input is MetadataEditInput {
  return ["UpdateSubtitleCue", "ChangeCrop", "ChangeClipSpeed", "SetClipLock", "ConfirmClip", "ReplaceClip", "TrimClip", "SplitClip", "ReorderClip"].includes(input.operation_type);
}

type PendingMetadataSave = {
  memberId: string;
  taskId: string;
  workspaceId: string;
  input: CreationTaskMetadataSaveInput;
  submissionError?: ReturnType<typeof errorDetails>;
};

type PendingTimelineRestore = {
  memberId: string;
  taskId: string;
  workspaceId: string;
  action: "undo" | "redo";
  input: Parameters<typeof undoCreationTaskTimeline>[1];
  submissionError?: ReturnType<typeof errorDetails>;
};

// This identity scopes the local pending request; the API still performs authorization.
async function editRequestMemberId() {
  if (isLocalAuthMode()) return (await ensureLocalMemberIdentity()).member_id;
  const { data, error } = await createClient().auth.getSession();
  if (error || !data.session) throw new Error("请先登录原保存请求所属的账号。");
  return data.session.user.id;
}

function metadataInputKey(value: unknown): string {
  return JSON.stringify(value, (_key, item) => item && typeof item === "object" && !Array.isArray(item)
    ? Object.fromEntries(Object.entries(item).sort(([a], [b]) => a.localeCompare(b))) : item);
}

export function CreationTaskWorkspace({ initialWorkspace, initialTopics, initialTasks }: Props) {
  const [savingTemplate, setSavingTemplate] = useState(false);
  const [workspace, setWorkspace] = useState(initialWorkspace);
  const [activeView, setActiveView] = useWorkspaceViewState<WorkspaceView>("task.activeView", () => defaultWorkspaceView(initialWorkspace));
  const [reviewChoice, setReviewChoice] = useWorkspaceViewState<{ taskId: string; pane: "prepare" | "result" } | null>("task.reviewChoice", null);
  const [topics, setTopics] = useState(initialTopics);
  const [selectedTopicId, setSelectedTopicId] = useWorkspaceViewState("task.selectedTopicId", initialTopics?.selected_topic_id ?? initialTopics?.candidates[0]?.id ?? null);
  const [topicTitleDraft, setTopicTitleDraft] = useWorkspaceViewState("task.topicTitleDraft",
    () => initialTopics?.candidates.find((candidate) => candidate.id === (initialTopics?.selected_topic_id ?? initialTopics?.candidates[0]?.id))?.title ?? "",
  );
  const [tasks, setTasks] = useState(initialTasks);
  const [busy, setBusy] = useState<BusyAction | null>(null);
  const currentBusy = useRef(busy);
  currentBusy.current = busy;
  const [error, setError] = useState<ReturnType<typeof errorDetails> | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [pendingMetadataSave, setPendingMetadataSave] = useState<PendingMetadataSave | null>(null);
  const pendingMetadataSaveRef = useRef<PendingMetadataSave | null>(null);
  const [pendingTimelineRestore, setPendingTimelineRestore] = useState<PendingTimelineRestore | null>(null);
  const pendingTimelineRestoreRef = useRef<PendingTimelineRestore | null>(null);
  const editRequestScope = useRef<string | null>(null);
  const restoredIdentityScope = useRef<string | undefined>(undefined);
  const [editRequestRecoveryReady, setEditRequestRecoveryReady] = useState(false);
  const editRequestRecoveryError = useRef<ReturnType<typeof errorDetails> | null>(null);
  const editRequestOperation = useRef<{ taskId: string; memberId: string | null } | null>(null);
  const editRequestIdentity = useLocalMemberIdentity();
  const subtitleStoragePrefix = `subtitle-save:${workspace.task.workspace_id}:${workspace.task.id}:`;
  useEffect(() => {
    let active = true;
    let identityRevision = 0;
    setEditRequestRecoveryReady(false);
    const restore = (memberId: string | null) => {
      if (!active) return;
      const nextScope = memberId ? `${subtitleStoragePrefix}${memberId}` : null;
      if (editRequestScope.current === nextScope && editRequestOperation.current) return;
      setEditRequestRecoveryReady(false);
      editRequestRecoveryError.current = null;
      const previousScope = editRequestScope.current;
      editRequestScope.current = nextScope;
      const restoredScope = JSON.stringify([subtitleStoragePrefix, memberId]);
      if (restoredIdentityScope.current !== undefined && restoredIdentityScope.current !== restoredScope) {
        setError(null); setNotice(null);
      }
      restoredIdentityScope.current = restoredScope;
      const operation = editRequestOperation.current;
      if (operation && ((previousScope !== null && previousScope !== nextScope)
        || (operation.memberId !== null && (operation.memberId !== memberId || operation.taskId !== workspace.task.id)))) {
        editRequestOperation.current = null;
        setBusy(null);
      }
      pendingMetadataSaveRef.current = null;
      setPendingMetadataSave(null);
      pendingTimelineRestoreRef.current = null;
      setPendingTimelineRestore(null);
      if (!memberId) { setEditRequestRecoveryReady(true); return; }
      try {
        const stored = sessionStorage.getItem(`${subtitleStoragePrefix}${memberId}`);
        const pending: PendingMetadataSave | null = stored ? JSON.parse(stored) : null;
        if (pending && (pending.taskId !== workspace.task.id || pending.workspaceId !== workspace.task.workspace_id
          || pending.memberId !== memberId || !pending.input.request_id
          || !isMetadataSaveInput(pending.input))) throw new Error("原保存请求无法读取，请保留此页面并核对原结果。");
        pendingMetadataSaveRef.current = pending;
        setPendingMetadataSave(pending);
        if (pending?.submissionError) setError(pending.submissionError);
      } catch (cause) { editRequestRecoveryError.current = errorDetails(cause); setError(editRequestRecoveryError.current); }
      try {
        const stored = sessionStorage.getItem(`timeline-restore:${workspace.task.workspace_id}:${workspace.task.id}:${memberId}`);
        const pending: PendingTimelineRestore | null = stored ? JSON.parse(stored) : null;
        if (pending && (pending.taskId !== workspace.task.id || pending.workspaceId !== workspace.task.workspace_id
          || pending.memberId !== memberId || !pending.input.request_id || !["undo", "redo"].includes(pending.action)))
          throw new Error("原撤销重做请求无法读取，请保留现场核对。");
        pendingTimelineRestoreRef.current = pending;
        setPendingTimelineRestore(pending);
        if (pending?.submissionError) setError(pending.submissionError);
      } catch (cause) { editRequestRecoveryError.current = errorDetails(cause); setError(editRequestRecoveryError.current); }
      setEditRequestRecoveryReady(editRequestRecoveryError.current === null);
    };
    void editRequestMemberId().then(memberId => { if (identityRevision === 0) restore(memberId); })
      .catch(cause => { if (active && identityRevision === 0) setError(errorDetails(cause)); });
    // Local identity changes already notify the existing hook; formal sessions
    // notify here without starting another auth or task lifecycle.
    let unsubscribe: (() => void) | undefined;
    try {
      if (!isLocalAuthMode()) {
        const { data } = createClient().auth.onAuthStateChange((_event, session) => {
          identityRevision += 1;
          restore(session?.user.id ?? null);
        });
        unsubscribe = () => data.subscription.unsubscribe();
      }
    } catch (cause) { setError(errorDetails(cause)); }
    return () => { active = false; unsubscribe?.(); };
  }, [subtitleStoragePrefix, workspace.task.id, workspace.task.workspace_id, editRequestIdentity.token]);
  const [pendingLocalEdit, setPendingLocalEdit] = useState<PendingLocalEdit | null>(null);
  const pendingLocalEditRef = useRef(pendingLocalEdit);
  pendingLocalEditRef.current = pendingLocalEdit;
  const [pendingMediaJob, setPendingMediaJob] = useState<{ taskId: string; value: TaskMediaJob } | null>(null);
  const [recoveredMedia, setRecoveredMedia] = useState<{ taskId: string; job: TaskMediaJob; result: TaskMediaResultMap[keyof TaskMediaResultMap] } | null>(null);
  const pendingMediaJobRef = useRef(pendingMediaJob);
  pendingMediaJobRef.current = pendingMediaJob;
  const mediaController = useRef<AbortController | null>(null);
  useEffect(() => {
    currentTaskId.current = currentWorkspace.current.task.id;
    return () => {
      currentTaskId.current = "";
      previewRequestSequence.current += 1;
      assistantRequestSequence.current += 1;
      mediaController.current?.abort();
    };
  }, []);
  const currentTaskId = useRef(initialWorkspace.task.id);
  currentTaskId.current = workspace.task.id;
  const currentWorkspace = useRef(workspace);
  currentWorkspace.current = workspace;
  const workspaceReceiptRevision = useRef(0);
  function acceptWorkspace(next: WorkspaceState, readRevision: number) {
    if (readRevision !== workspaceReceiptRevision.current || next.task.id !== currentTaskId.current
      || Date.parse(next.task.updated_at) < Date.parse(currentWorkspace.current.task.updated_at)) return false;
    workspaceReceiptRevision.current += 1;
    currentWorkspace.current = next;
    setWorkspace(next);
    setTasks(items => [next.task, ...items.filter(item => item.id !== next.task.id)]);
    return true;
  }
  const navigationReadRevision = useRef(0);
  const pendingNavigationRead = useRef<{ taskId: string; inputRevision: number } | null>({ taskId: initialWorkspace.task.id, inputRevision: 0 });
  // 共享/收回只用接口回读的值：保存失败就保持原样，不显示成已保存。
  async function changeTaskVisibility(next: "private" | "shared") {
    setVisibilityBusy(true);
    setVisibilityError(null);
    try {
      const saved = await setCreationTaskVisibility(task.id, next);
      acceptWorkspace({ ...currentWorkspace.current, task: saved.task }, workspaceReceiptRevision.current);
    } catch (err) {
      if (task.id === currentTaskId.current) setVisibilityError(err instanceof Error ? err.message : "共享设置没有保存成功，请再试一次。");
    } finally {
      if (task.id === currentTaskId.current) setVisibilityBusy(false);
    }
  }
  const [selectedVoiceId, setSelectedVoiceId] = useWorkspaceViewState<string | null>("task.selectedVoiceId",
    initialWorkspace.plan.selected_voice?.profile_id
      ?? initialWorkspace.available_voices.find((voice) => voice.runnable)?.profile_id
      ?? null,
  );
  const [pendingVoiceSelection, setPendingVoiceSelection] = useWorkspaceViewState<CreationTaskNarrationVoiceSelectionRequest | null>(`task:${workspace.task.id}.voiceSelectionRequest`, null);
  const [voiceSelectionNotFound, setVoiceSelectionNotFound] = useState<string | null>(null);
  const [replacementClipId, setReplacementClipId] = useState<string | null>(null);
  const [replacementCandidates, setReplacementCandidates] = useState<MontageClipCandidate[]>([]);
  const [inserting, setInserting] = useState(false);
  const initializedDraft = useRef<{ versionId: string; clips: Record<string, VideoClipDraft>; dirty: boolean } | null>(null);
  const [editorDraft, setEditorDraft] = useWorkspaceViewState(`task:${workspace.task.current_timeline_version_id}.editorDraft`,
    () => initializedDraft.current?.versionId === workspace.task.current_timeline_version_id ? initializedDraft.current.dirty : false);
  const currentEditorDraft = useRef(editorDraft);
  currentEditorDraft.current = editorDraft;
  const [clipDrafts, setClipDrafts] = useWorkspaceViewState<Record<string, VideoClipDraft>>(`task:${workspace.task.current_timeline_version_id}.clipDrafts`,
    () => initializedDraft.current?.versionId === workspace.task.current_timeline_version_id ? initializedDraft.current.clips : {});
  const [audioPlayhead, setAudioPlayhead] = useWorkspaceViewState("task.audioPlayhead", 0);
  const restoredPlayhead = useWorkspacePage()?.viewState.get("task.audioPlayhead") as number | undefined;
  const [selectedAudioId, setSelectedAudioId] = useWorkspaceViewState<string | null>("task.selectedAudioId", null);
  const [selectedOverlayId, setSelectedOverlayId] = useWorkspaceViewState<string | null>("task.selectedOverlayId", null);
  const [selectedClipId, setSelectedClipId] = useWorkspaceViewState<string | null>("task.selectedClipId", null);
  const montageVideoRef = useRef<HTMLVideoElement>(null);
  const playheadRef = useRef<number | null>(restoredPlayhead ?? null);
  const editContextRevision = useRef(0);
  const previewRequestSequence = useRef(0);
  const assistantRequestSequence = useRef(0);
  const [selectedCueId, setSelectedCueId] = useWorkspaceViewState<string | null>("task.selectedCueId", null);
  const [selectedCover, setSelectedCover] = useWorkspaceViewState("task.selectedCover", false);
  const [activeSubtitleTrackId, setActiveSubtitleTrackId] = useWorkspaceViewState<string | null>("task.activeSubtitleTrackId",
    initialWorkspace.subtitle_tracks.find((track) => track.language === "zh-CN")?.id
      ?? initialWorkspace.subtitle_tracks[0]?.id
      ?? null,
  );
  const [selectedNarrationSegmentId, setSelectedNarrationSegmentId] = useWorkspaceViewState<string | null>("task.selectedNarrationSegmentId", null);
  const [editPreview, setEditPreviewState] = useState<CreationTaskEditPreviewResponse | null>(null);
  type DraftInput = { taskId: string; conversationId: string | undefined; text: string };
  const previewInputOwner = useRef<{ proposalId: string; input: DraftInput } | null>(null);
  function setEditPreview(preview: CreationTaskEditPreviewResponse | null, input: DraftInput | null = null) {
    previewInputOwner.current = preview && input ? { proposalId: preview.proposal.id, input } : null;
    setEditPreviewState(preview);
  }
  const currentEditPreview = useRef(editPreview);
  currentEditPreview.current = editPreview;
  const [assistantPreview, setAssistantPreview] = useState(false);
  const [recordingOpen, setRecordingOpen] = useState(false);
  const [recordings, setRecordings] = useState<CreationTaskRecording[]>([]);
  const [recordingsError, setRecordingsError] = useState<string | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [assistantTurns, setAssistantTurns] = useState<AssistantTurnRecord[]>([]);
  const [assistantConfigured, setAssistantConfigured] = useState(false);
  useEffect(() => {
    void getRuntimeSettings().then((runtime) => {
      if (runtime.status.llm.configured) {
        setAssistantConfigured(true);
      }
    }).catch(() => undefined);
  }, []);
  useEffect(() => {
    let active = true;
    if (!workspace.task.confirmed_script_id) {
      setRecordings([]);
      setRecordingsError(null);
      return () => { active = false; };
    }
    void listCreationTaskRecordings(workspace.task.id).then(({ items }) => {
      if (active) {
        setRecordings(items);
        setRecordingsError(null);
      }
    }).catch((cause) => {
      if (active) setRecordingsError(cause instanceof Error ? cause.message : "无法读取本任务已保存的录音。");
    });
    return () => { active = false; };
  }, [workspace.task.id, workspace.task.confirmed_script_id, workspace.task.stage]);
  const task = workspace.task;
  const executionController = useCreationExecution({
    taskId: task.id, confirmedScriptId: task.confirmed_script_id,
    timelineVersionId: task.current_timeline_version_id,
    voiceId: workspace.plan.selected_voice?.profile_id,
    voiceVersion: workspace.plan.selected_voice?.version,
    workspaceReadRevision: workspaceReceiptRevision.current,
    onWorkspaceRead: acceptWorkspace,
  });
  const { executionPreview, execution, executionError } = executionController;
  const storyboardInput = executionPreview?.task_id === task.id && executionPreview.script_id === task.confirmed_script_id
    && task.current_timeline_version_id && task.timeline_sequence >= 1 ? {
      script_id: executionPreview.script_id, input_digest: executionPreview.input_digest,
      expected_timeline_version_id: task.current_timeline_version_id, expected_timeline_sequence: task.timeline_sequence,
    } : null;
  const assistantConversation = useAssistantConversation({ scope_kind: "task", scope_id: task.id });
  const selectedProfileReference = useAssistantProfileReference()?.reference;
  const materialReference = useAssistantMaterialReference();
  const selectedMaterialReference = materialReference?.reference;
  const selectedKnowledgeReference = useAssistantKnowledgeReference()?.reference;
  const replyTransferRequests = useRef(new Map<string, string>());
  const editCommand = assistantConversation.draftText;
  // This identity belongs to an actual input event, including retyping the same text.
  // Exact parameter previews carry no chat-input write permission.
  const draftInput = useRef<DraftInput>({ taskId: task.id, conversationId: assistantConversation.conversation?.id, text: editCommand });
  if (draftInput.current.taskId !== task.id || draftInput.current.conversationId !== assistantConversation.conversation?.id || draftInput.current.text !== editCommand) {
    draftInput.current = { taskId: task.id, conversationId: assistantConversation.conversation?.id, text: editCommand };
  }
  function setEditCommand(text: string) {
    generationReadOwner.current = null;
    draftInput.current = { taskId: task.id, conversationId: assistantConversation.conversation?.id, text };
    assistantConversation.setDraftText(text);
  }
  function ownsDraftInput(input: DraftInput) {
    return input === draftInput.current && input.taskId === currentTaskId.current
      && Boolean(input.conversationId && assistantConversation.isCurrentConversation(input.conversationId));
  }
  const [detachedReference, setDetachedReference] = useWorkspaceViewState<string | null>("task.detachedReference", null);
  const [sharingOpen, setSharingOpen] = useState(false);
  const [visibilityBusy, setVisibilityBusy] = useState(false);
  const [visibilityError, setVisibilityError] = useState<string | null>(null);
  // 方案可见范围回读值：接口返回什么就显示什么。
  const [planVisibility, setPlanVisibility] = useState<"private" | "shared" | null>(null);

  // 方案可见范围以方案自身的读取结果为准；读不到就显示未知，不假装是私有。
  useEffect(() => {
    let cancelled = false;
    void getCreationPlan(task.plan_id)
      .then((plan) => { if (!cancelled) setPlanVisibility(plan.visibility); })
      .catch(() => { if (!cancelled) setPlanVisibility(null); });
    return () => { cancelled = true; };
  }, [task.plan_id]);

  async function changePlanVisibility(next: "private" | "shared") {
    setVisibilityBusy(true);
    setVisibilityError(null);
    try {
      const saved = await setCreationPlanVisibility(task.plan_id, next);
      setPlanVisibility(saved.visibility);
    } catch (err) {
      setVisibilityError(err instanceof Error ? err.message : "方案共享设置没有保存成功，请再试一次。");
    } finally {
      setVisibilityBusy(false);
    }
  }

  useEffect(() => {
    let active = true;
    setAssistantTurns([]);
    void getCreationTaskAssistantHistory(task.id).then((history) => {
      if (!active) return;
      const turns = assistantHistoryTurns(history.items);
      setAssistantTurns(turns);
    }).catch((cause) => { if (active) setError(errorDetails(cause)); });
    return () => { active = false; };
  }, [task.id]);
  const latestScript = workspace.scripts[0] ?? null;
  const [manualScriptOpen, setManualScriptOpen] = useWorkspaceViewState("manual-script-open", false);
  const manualForGeneration = useRef(manualScriptOpen); manualForGeneration.current = manualScriptOpen;
  const generationReadOwner = useRef<{ workspace: WorkspaceState; navigation: number; revision: number } | null>(null);
  const scriptGeneration = useScriptGenerationRequest({
    taskId: task.id, visible: activeView === "script",
    onStart: () => {
      navigationReadRevision.current += 1; pendingNavigationRead.current = null;
      generationReadOwner.current = { workspace: currentWorkspace.current, navigation: navigationReadRevision.current, revision: editContextRevision.current };
    },
    getContextKey: () => {
      const owner = generationReadOwner.current;
      return owner && owner.workspace === currentWorkspace.current
        && owner.navigation === navigationReadRevision.current && owner.revision === editContextRevision.current
        && !manualForGeneration.current && !currentEditorDraft.current ? owner : null;
    },
    onDraftReady: async (receipt, stillCurrent) => {
      await refresh(true, next => stillCurrent() && next.task.id === receipt.task_id && next.scripts.some(script =>
        script.id === receipt.script_id && script.run_id === receipt.run_id
        && script.updated_at.replace(/\+00:00$/, "Z") === receipt.script_updated_at?.replace(/\+00:00$/, "Z")));
    },
  });
  const windowScript = latestScript?.selection_status === "draft" && (!latestScript.draft_complete || latestScript.sections_total_count > 20);
  const runnableVoices = workspace.available_voices.filter((voice) => voice.runnable);
  const zhSubtitleValid = workspace.subtitle_tracks.some((track) => track.language === "zh-CN" && track.status === "valid");
  const enSubtitleValid = workspace.subtitle_tracks.some((track) => track.language === "en-US" && track.status === "valid");
  const coverValid = workspace.cover_asset?.status === "valid";
  const zhSubtitleVisible = workspace.subtitle_tracks.some(track => track.language === "zh-CN" && track.status === "valid" && track.style.enabled !== false);
  const enSubtitleVisible = workspace.subtitle_tracks.some(track => track.language === "en-US" && track.status === "valid" && track.style.enabled !== false);
  const subtitleSummary = zhSubtitleVisible ? enSubtitleVisible ? "中英双语轨已就绪" : "中文轨已就绪"
    : enSubtitleVisible ? "英文轨已就绪" : zhSubtitleValid || enSubtitleValid ? "当前成片已关闭字幕" : "当前没有有效字幕轨";
  const reviewChecks = [
    { title: "字幕", ready: zhSubtitleVisible || enSubtitleVisible, detail: subtitleSummary },
    { title: "讲解与音乐音量", ready: false, detail: "需人工试听确认，当前无自动音量校验" },
    { title: "封面", ready: coverValid, detail: coverValid ? "封面已生成" : "封面尚未生成或待确认" },
  ];
  const shellTasks = useMemo(() => [task, ...tasks.filter((item) => item.id !== task.id)], [task, tasks]);
  const selectedClipIndex = workspace.montage?.clips.findIndex((clip) => clip.id === selectedClipId) ?? -1;
  const activeSubtitleTrack = workspace.subtitle_tracks.find((track) => track.id === activeSubtitleTrackId)
    ?? workspace.subtitle_tracks.find((track) => track.language === "zh-CN")
    ?? workspace.subtitle_tracks[0]
    ?? null;
  const selectedCueIndex = activeSubtitleTrack?.cues.findIndex((cue) => cue.id === selectedCueId) ?? -1;
  const selectedSubtitleCue = selectedCueIndex >= 0 ? activeSubtitleTrack?.cues[selectedCueIndex] ?? null : null;
  const selectedNarrationSegmentIndex = workspace.narration?.segments.findIndex((segment) => segment.id === selectedNarrationSegmentId) ?? -1;
  const selectedAudioTrack = workspace.montage?.audio_timeline?.tracks.find(track => track.id === selectedAudioId || track.clips.some(clip => clip.id === selectedAudioId));
  const selectedAudioClip = selectedAudioTrack?.clips.find(clip => clip.id === selectedAudioId) ?? null;
  const selectedOverlayIndex = workspace.montage?.graphic_overlays?.findIndex(item=>item.id===selectedOverlayId) ?? -1;
  const selectedOverlay = selectedOverlayIndex>=0 ? workspace.montage!.graphic_overlays![selectedOverlayIndex] : null;
  const selectedEditContext = activeView === "montage" && selectedOverlay
    ? {kind:"overlay" as const,id:selectedOverlay.id,label:`${selectedOverlay.kind==="image"?"图片":selectedOverlay.kind==="arrow"?"箭头":"标题"}${selectedOverlayIndex+1}`,
       placeholder:selectedOverlay.kind==="image"?"例如：当前图片不透明度改为50%":selectedOverlay.kind==="arrow"?"例如：当前箭头颜色改为红":"例如：当前标题字号改为96",ref:`overlay:${selectedOverlay.id}`}
    : activeView === "montage" && selectedAudioTrack
    ? { kind: "audio" as const, id: selectedAudioId!, label: selectedAudioClip?.name ?? selectedAudioTrack.name,
        placeholder: "例如：音量小一点，或指定淡出秒数", ref: `audio:${selectedAudioId}` }
    : activeView === "narration" && selectedNarrationSegmentIndex >= 0
    ? {
        kind: "narration" as const,
        id: selectedNarrationSegmentId!,
        label: `配音 ${selectedNarrationSegmentIndex + 1}`,
        placeholder: "例如：这句读慢一点",
        ref: `narration:${selectedNarrationSegmentId}`,
      }
    : activeView === "subtitle_cover" && selectedCueIndex >= 0
      ? {
          kind: "cue" as const,
          id: selectedCueId!,
          label: `字幕 ${selectedCueIndex + 1}`,
          placeholder: "例如：把这句字幕改得更简洁",
          ref: `cue:${selectedCueId}`,
        }
      : activeView === "subtitle_cover" && selectedCover
        ? {
            kind: "cover" as const,
            id: workspace.cover_asset?.id ?? task.id,
            label: "封面",
            placeholder: "例如：用第三个镜头做封面，标题写“别只讲概念”",
            ref: `cover:${workspace.cover_asset?.id ?? task.id}`,
          }
      : (activeView === "montage" || activeView === "review") && selectedClipIndex >= 0
        ? {
            kind: "clip" as const,
            id: selectedClipId!,
            label: `镜头 ${selectedClipIndex + 1}`,
            placeholder: "例如：换成另一个镜头",
            ref: `clip:${selectedClipId}`,
          }
        : null;

  const selectionState = useAssistantSelection();
  const selectionSnapshot = JSON.stringify([selectionState?.version, selectionState?.items]);
  const latestSelectionSnapshot = useRef(selectionSnapshot);
  latestSelectionSnapshot.current = selectionSnapshot;
  const locateSearch = useSearchParams();
  const [addReferences, setAddReferences] = useWorkspaceViewState("task.addReferences", false);
  function publishSelection(kind:AssistantTimelineReference["kind"], object_id:string, label:string, range?:{start_ms:number;end_ms:number}) {
    if (!task.current_timeline_version_id) return;
    referenceAssistantSelection([{kind,object_id,task_id:task.id,version:task.current_timeline_version_id,label,...range}],addReferences);
  }
  useEffect(() => {
    const locate = () => {
      let item: SelectedAssistantReference | null;
      try { item = readAssistantLocateReference(locateSearch); } catch (error) { setNotice(error instanceof Error ? error.message : "引用定位不可用。"); return; }
      if (!item) return;
      if (!("task_id" in item) || item.task_id !== task.id) return;
      if (item.version !== task.current_timeline_version_id) { setNotice("引用来自旧版本，请重新选择；没有改绑当前对象。"); return; }
      setActiveView(item.kind === "subtitle" ? "subtitle_cover" : item.kind === "narration" ? "narration" : "montage");
      setSelectedClipId(item.kind === "shot" ? item.object_id : null);
      setSelectedCueId(item.kind === "subtitle" ? item.object_id : null);
      setSelectedNarrationSegmentId(item.kind === "narration" ? item.object_id : null);
      setSelectedOverlayId(item.kind === "effect" ? item.object_id : null);
      setSelectedAudioId(item.kind === "audio" ? item.object_id : null);
      if (item.start_ms !== undefined && montageVideoRef.current) { montageVideoRef.current.currentTime=item.start_ms/1000; playheadRef.current=item.start_ms/1000; }
    };
    locate();
  },[task.id,task.current_timeline_version_id,locateSearch]);
  const selectedTaskReference = selectedEditContext && selectedEditContext.ref !== detachedReference ? selectedEditContext : null;

  function invalidateEditPreview() {
    mediaController.current?.abort();
    mediaController.current = null;
    setPendingMediaJob(null);
    editContextRevision.current += 1;
    setEditPreview(null);
    setAssistantPreview(false);
  }

  async function resolveEditMedia(response: CreationTaskEditPreviewResponse | TaskMediaJob, revision: number, conversationId?: string) {
    const baseVersionId = task.current_timeline_version_id;
    if (currentTaskId.current !== task.id || currentWorkspace.current.task.current_timeline_version_id !== baseVersionId
      || editContextRevision.current !== revision || (conversationId && !assistantConversation.isCurrentConversation(conversationId))) {
      throw new DOMException("预览范围已变化", "AbortError");
    }
    const controller = new AbortController();
    mediaController.current?.abort(); mediaController.current = controller;
    const stillCurrent = () => !controller.signal.aborted && currentTaskId.current === task.id
      && currentWorkspace.current.task.current_timeline_version_id === baseVersionId
      && editContextRevision.current === revision
      && (!conversationId || assistantConversation.isCurrentConversation(conversationId));
    return resolveCreationTaskMediaResponse(task.id, response, "edit_preview", {
      signal: controller.signal, baseVersionId,
      onUpdate: value => { if (stillCurrent()) setPendingMediaJob({ taskId: task.id, value }); },
    });
  }

  async function cancelMediaPreview() {
    const pending = pendingMediaJob;
    if (!pending || pending.taskId !== currentTaskId.current) return;
    const controller = mediaController.current;
    try {
      const result = await cancelCreationTaskMediaJob(pending.taskId, pending.value.job_id);
      if (currentTaskId.current !== pending.taskId || pendingMediaJobRef.current?.value.job_id !== pending.value.job_id || mediaController.current !== controller) return;
      if (result.status === "cancelled") {
        mediaController.current?.abort(); setPendingMediaJob({ taskId: pending.taskId, value: result });
        setNotice("预览已取消，当前草稿保持不变。");
      }
    } catch (cause) { if (currentTaskId.current === pending.taskId) setError(errorDetails(cause)); }
  }

  async function restoreMediaPreview(candidate: TaskMediaJob) {
    const selectedRefs = selectedTaskReference ? [selectedTaskReference.ref,
      ...(activeView === "subtitle_cover" && activeSubtitleTrack ? [`subtitle-track:${activeSubtitleTrack.id}`] : [])] : [];
    if (busy !== null || !taskMediaJobMatchesContext(candidate, task.current_timeline_version_id, selectedRefs)) return;
    const revision = ++editContextRevision.current;
    const sequence = ++previewRequestSequence.current;
    const controller = new AbortController();
    mediaController.current?.abort(); mediaController.current = controller;
    const baseVersionId = task.current_timeline_version_id;
    const conversationId = assistantConversation.conversation?.id;
    const stillCurrent = () => !controller.signal.aborted && currentTaskId.current === task.id
      && currentWorkspace.current.task.current_timeline_version_id === baseVersionId
      && editContextRevision.current === revision
      && (!conversationId || assistantConversation.isCurrentConversation(conversationId));
    setBusy("edit-preview"); setError(null); setNotice(null);
    try {
      const response = await getCreationTaskMediaJob(task.id, candidate.job_id, controller.signal);
      if (!stillCurrent()) return;
      if (!taskMediaJobMatchesContext(response, baseVersionId, selectedRefs)) {
        setNotice("这次预览已不符合当前输入与选择，未恢复；请重新预览。");
        return;
      }
      let settledJob = response;
      const result = await resolveCreationTaskMediaResponse(task.id, response, response.result_kind, {
        signal: controller.signal, baseVersionId,
        onUpdate: value => { settledJob = value; if (stillCurrent()) setPendingMediaJob({ taskId: task.id, value }); },
      });
      if (!stillCurrent()) return;
      if (response.result_kind === "edit_preview") {
        setEditPreview(result as CreationTaskEditPreviewResponse);
        setAssistantPreview(true);
      } else {
        if (response.result_kind === "graphic_source") {
          const source = result as TaskMediaResultMap["graphic_source"];
          const registered = (await getCreationTaskGraphicSources(task.id, controller.signal)).find(item =>
            item.source_ref === source.source_ref && item.artifact_id === source.artifact_id
            && item.sha256 === source.sha256 && item.size_bytes === source.size_bytes);
          if (!stillCurrent()) return;
          if (!registered) throw new Error("图片来源尚未登记或修订已变化，未恢复。");
          setNotice("已读取当前任务登记的图片来源，尚未应用到草稿或生成成片。");
        }
        setRecoveredMedia({ taskId: task.id, job: settledJob, result });
        if (response.result_kind === "recording") {
          const recording = result as CreationTaskRecording;
          setRecordings(current => [recording, ...current.filter(item => item.source_ref !== recording.source_ref)]);
          setNotice("录音结果已读取；尚未采用，当前配音与草稿保持不变。");
        }
      }
    } catch (cause) {
      if (stillCurrent() && !(cause instanceof TaskMediaJobError && ["cancelled", "stale"].includes(cause.job.status))) setError(errorDetails(cause));
    } finally {
      if (currentTaskId.current === task.id && sequence === previewRequestSequence.current) setBusy(null);
    }
  }

  async function refresh(preserveActiveView = false, stillCurrent?: (next: WorkspaceState) => boolean, retainProposal = false) {
    const contextRevision = editContextRevision.current;
    const readRevision = workspaceReceiptRevision.current;
    const next = await getCreationTaskWorkspace(task.id);
    if (stillCurrent && !stillCurrent(next)) return;
    if (!acceptWorkspace(next, readRevision)) return;
    const selectionChanged = contextRevision !== editContextRevision.current;
    pendingNavigationRead.current = null;
    const proposal = currentEditPreview.current?.proposal;
    const sameProposalBase = retainProposal && proposal?.status === "proposed"
      && proposal.task_id === next.task.id && proposal.workspace_id === next.task.workspace_id
      && proposal.operation.payload.base_version_id === next.task.current_timeline_version_id
      && !selectionChanged;
    if (!sameProposalBase) invalidateEditPreview();
    setReplacementClipId(null);
    setReplacementCandidates([]);
    const retainedClip = next.montage?.clips.find(clip => clip.id === selectedClipId);
    const retainedOverlay=next.montage?.graphic_overlays?.find(item=>item.id===selectedOverlayId);
    if (!selectionChanged) {
      playheadRef.current = retainedOverlay ? (retainedOverlay.start_seconds+retainedOverlay.end_seconds)/2 : selectedAudioId ? audioPlayhead : retainedClip && next.montage?.output_url
        ? (retainedClip.timeline_start_seconds + retainedClip.timeline_end_seconds) / 2 : null;
    }
    setEditorDraft(false);
    setSelectedOverlayId(current=>next.montage?.graphic_overlays?.some(item=>item.id===current)?current:null);
    // 保留原视图只在该视图按既有门禁仍可呈现时成立。字幕确认会作废 output_url，
    // 使 subtitle_cover 不可呈现；此时保留它会让主区没有任何视图命中（空白且丢失撤销入口）。
    // 这里按既有可用/默认规则回落，不新增视图、不放开任何门禁。
    setActiveView(current => preserveActiveView && canOpenStage(current, next) ? current : defaultWorkspaceView(next));
    setSelectedClipId((current) => next.montage?.clips.some((clip) => clip.id === current) ? current : null);
    setSelectedAudioId(current => next.montage?.audio_timeline?.tracks.some(track => track.id === current || track.clips.some(clip => clip.id === current)) ? current : null);
    setActiveSubtitleTrackId((current) => next.subtitle_tracks.some((track) => track.id === current)
      ? current
      : next.subtitle_tracks.find((track) => track.language === "zh-CN")?.id ?? next.subtitle_tracks[0]?.id ?? null);
    setSelectedCueId((current) => next.subtitle_tracks.some((track) => track.cues.some((cue) => cue.id === current)) ? current : null);
    setSelectedCover((current) => current && (Boolean(next.cover_asset) || next.cover_frame_capability.available));
    setSelectedNarrationSegmentId((current) => next.narration?.segments.some((segment) => segment.id === current) ? current : null);
    if (next.task.stage === "topic") {
      const appliedRevision = editContextRevision.current;
      const nextTopics = await getCreationTaskTopics(task.id);
      if (task.id !== currentTaskId.current || appliedRevision !== editContextRevision.current) return;
      setTopics(nextTopics);
      setSelectedTopicId(nextTopics.selected_topic_id ?? nextTopics.candidates[0]?.id ?? null);
    }
  }

  useEffect(() => {
    if (pendingNavigationRead.current?.taskId !== task.id || busy !== null) return;
    let active = true;
    const entered = currentWorkspace.current;
    const readRevision = pendingNavigationRead.current.inputRevision;
    const unchanged = () => active && currentTaskId.current === task.id
      && currentWorkspace.current === entered && readRevision === navigationReadRevision.current
      && currentBusy.current === null && !currentEditorDraft.current;
    void refresh(true, next => {
      const current = unchanged() && next.task.id === task.id
        && Date.parse(next.task.updated_at) >= Date.parse(entered.task.updated_at);
      if (current && next === entered) pendingNavigationRead.current = null;
      return current && next !== entered;
    }, true).catch(cause => {
      if (unchanged()) setError(errorDetails(cause));
    });
    return () => { active = false; };
    // Selection and view changes retain their latest state while facts refresh. Unsaved
    // input still owns its old version; a busy action either refreshes facts itself or
    // lets this pending entry read resume when it settles.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [task.id, busy]);

  async function run(action: BusyAction, operation: () => Promise<unknown>, success?: string) {
    setBusy(action);
    setError(null);
    setNotice(null);
    if (action === "clip") {
      invalidateEditPreview();
    }
    try {
      await operation();
      await refresh(activeView === "montage");
      if (success) setNotice(success);
    } catch (cause) {
      setError(errorDetails(cause));
      await refresh().catch(() => undefined);
    } finally {
      setBusy(null);
    }
  }

  async function changeExecution(operation: () => Promise<CreationExecutionStatus>) {
    setBusy("execution");
    await executionController.changeExecution(operation);
    if (currentTaskId.current === task.id) setBusy(null);
  }

  async function changeStoryboard(query = false) {
    if (busy !== null || (!query && !storyboardInput)) return;
    setBusy("execution");
    try {
      if (query) await executionController.readStoryboard();
      else if (storyboardInput) await executionController.regenerateStoryboard(storyboardInput);
    } finally { if (currentTaskId.current === task.id) setBusy(null); }
  }

  async function handleNarrationVoice(mode: "select" | "query" | "replay" = "select") {
    if (busy !== null) return;
    const voice = runnableVoices.find(item => item.profile_id === selectedVoiceId);
    if (mode === "select" && (!voice || pendingVoiceSelection)) return;
    if (mode === "select" && !workspace.plan.updated_at) {
      setError({message:"方案修订尚未读回，请重新打开当前任务后选择声音。", impact:"没有提交声音采用。", repairAction:null, code:null});
      return;
    }
    const input = mode !== "select" ? pendingVoiceSelection : {
      request_id: crypto.randomUUID(), voice_profile_id: voice!.profile_id, voice_version: voice!.version,
      expected_plan_updated_at: workspace.plan.updated_at!, expected_task_updated_at: task.updated_at,
    };
    if (!input) return;
    if (mode === "replay" && voiceSelectionNotFound !== input.request_id) return;
    const targetId = task.id;
    const current = () => currentTaskId.current === targetId;
    setPendingVoiceSelection(input); setVoiceSelectionNotFound(null); setBusy("voice-select"); setError(null); setNotice(null);
    let querying = mode === "query";
    let selectionRecorded = false;
    try {
      if (mode === "query") await getCreationTaskNarrationVoiceSelection(targetId, input.request_id);
      else {
        try { await selectCreationTaskNarrationVoice(targetId, input); }
        catch (cause) {
          if (cause instanceof CreationPlanApiError && cause.status > 0 && cause.status < 500) {
            if (current()) setPendingVoiceSelection(null);
            throw cause;
          }
          querying = true;
          await getCreationTaskNarrationVoiceSelection(targetId, input.request_id);
        }
      }
      if (!current()) return;
      selectionRecorded = true;
      setPendingVoiceSelection(null);
      await refresh(true, next => current() && next.task.id === targetId);
      if (current()) setNotice("声音采用请求已查明，请确认文案和要求后开始连续制作。");
    } catch (cause) {
      if (!current()) return;
      if (querying && cause instanceof CreationPlanApiError && cause.status === 404) setVoiceSelectionNotFound(input.request_id);
      setError(selectionRecorded ? {message:"声音采用已记录，任务状态尚未回读，请重新打开当前任务。", impact:"没有重复采用声音。",
        repairAction:errorDetails(cause).message, code:null} : querying ? {message:"声音采用结果尚未查明，请查询原请求。", impact:"没有重发声音采用请求。",
        repairAction:errorDetails(cause).message, code:null} : errorDetails(cause));
    } finally { if (current()) setBusy(null); }
  }

  async function saveOutput(operation: () => Promise<SaveFeedback>) {
    setBusy("save"); setError(null); setNotice(null);
    try {
      const result = await operation();
      if (currentTaskId.current === task.id) { setError(result.error); setNotice(result.notice); }
    } finally { if (currentTaskId.current === task.id) setBusy(null); }
  }
  function saveVideo() { return saveOutput(() => saveTaskVideo(task.id, task.current_timeline_version_id)); }
  function saveCover() { const coverId = workspace.cover_asset?.id; if (coverId) return saveOutput(() => saveTaskCover(task.id, coverId)); }
  function saveSubtitle(mode: "zh" | "en" | "zh-en") { return saveOutput(() => saveTaskSubtitles(task.id, mode, task.current_timeline_version_id, workspace.subtitle_tracks, activeSubtitleTrack)); }

  // 插入用与「换素材」同一对既有端点取候选，不新开端点。InsertClip 的锚点就是
  // 当前选中镜头（clip_id），落点是「插在它之后」；ExactPreviewUseCase
  // 将 InsertClip 分派给 linked 用例，由该用例校验锚点。
  async function showInsertCandidates(clipId: string) {
    setBusy("clip");
    setError(null);
    try {
      const clip = workspace.montage?.clips.find((item) => item.id === clipId);
      const candidates = await getCreationTaskMontageClipCandidates(task.id, clipId);
      // 「添加片段」是增加，不是替换：当前镜头自己也在候选里，要排除掉。
      setReplacementCandidates(candidates.filter((candidate) => candidate.asset_id !== clip?.asset_id || candidate.segment_id !== clip?.segment_id));
      setInserting(true);
    } catch (cause) {
      setError(errorDetails(cause));
    } finally {
      setBusy(null);
    }
  }

  async function showReplacementCandidates(clipId: string) {
    setBusy("clip");
    setError(null);
    try {
      const candidates = await getCreationTaskMontageClipCandidates(task.id, clipId);
      const clip = workspace.montage?.clips.find((item) => item.id === clipId);
      setReplacementCandidates(candidates.filter((candidate) => candidate.asset_id !== clip?.asset_id || candidate.segment_id !== clip?.segment_id));
      setReplacementClipId(clipId);
    } catch (cause) {
      setError(errorDetails(cause));
    } finally {
      setBusy(null);
    }
  }

  function activateTaskSelection() {
    if (detachedReference !== null) invalidateEditPreview();
    setDetachedReference(null);
  }

  function selectOverlay(id: string|null) {
    if (id) { activateTaskSelection(); publishSelection("effect",id,workspace.montage?.graphic_overlays?.find(item=>item.id===id)?.kind ?? "效果"); }
    invalidateEditPreview(); setEditorDraft(false); setSelectedOverlayId(id); setSelectedAudioId(null);
    const item=workspace.montage?.graphic_overlays?.find(value=>value.id===id);
    if(item){const time=(item.start_seconds+item.end_seconds)/2;playheadRef.current=time;
      if(montageVideoRef.current){montageVideoRef.current.pause();montageVideoRef.current.currentTime=time;}}
  }

  function selectClip(clipId: string) {
    activateTaskSelection();
    const chosen=workspace.montage?.clips.find(item=>item.id===clipId);
    publishSelection("shot",clipId,`镜头 ${(workspace.montage?.clips.findIndex(item=>item.id===clipId) ?? 0)+1}`,chosen ? {start_ms:Math.round(chosen.timeline_start_seconds*1000),end_ms:Math.round(chosen.timeline_end_seconds*1000)} : undefined);
    if(selectedOverlayId){setEditorDraft(false);invalidateEditPreview();}
    setSelectedOverlayId(null);
    setSelectedAudioId(null);
    setInserting(false);
    if (selectedClipId !== clipId || selectedCueId || selectedNarrationSegmentId || selectedAudioId) { setEditorDraft(false); invalidateEditPreview(); }
    setSelectedClipId(clipId);
    setSelectedCueId(null);
    setSelectedNarrationSegmentId(null);
    setSelectedCover(false);
    const clip = workspace.montage?.clips.find((item) => item.id === clipId);
    if (clip && workspace.montage?.output_url && montageVideoRef.current) {
      const position = (clip.timeline_start_seconds + clip.timeline_end_seconds) / 2;
      montageVideoRef.current.currentTime = position;
      playheadRef.current = position;
      setAudioPlayhead(position);
    }
  }

  function locateQualityFinding(finding: CreationTaskQualityCheck["findings"][number]) {
    const clip = workspace.montage?.clips.find(item => finding.clip_ids.includes(item.id));
    if (clip) selectClip(clip.id);
    playheadRef.current = finding.start_seconds;
    setAudioPlayhead(finding.start_seconds);
    if (montageVideoRef.current) {
      montageVideoRef.current.pause();
      montageVideoRef.current.currentTime = finding.start_seconds;
    }
  }

  function selectPlayingClip(video: HTMLVideoElement) {
    playheadRef.current = video.currentTime; setAudioPlayhead(video.currentTime);
    if (!["montage", "review"].includes(activeView) || selectedOverlayId || editorDraft) return;
    if (selectedAudioId) { setAudioPlayhead(video.currentTime); return; }
    const clips = workspace.montage?.clips ?? [];
    const clip = clips.find((item, index) => video.currentTime >= item.timeline_start_seconds
      && (video.currentTime < item.timeline_end_seconds
        || (index === clips.length - 1 && video.currentTime === item.timeline_end_seconds)));
    if (!clip) return;
    if (selectedClipId !== clip.id) { setEditorDraft(false); invalidateEditPreview(); }
    setSelectedClipId(clip.id);
    setSelectedCueId(null);
    setSelectedNarrationSegmentId(null);
    setSelectedCover(false);
  }

  function selectCue(cueId: string) {
    const cue = activeSubtitleTrack?.cues.find(item => item.id === cueId);
    publishSelection("subtitle",cueId,`字幕 ${(activeSubtitleTrack?.cues.findIndex(item=>item.id===cueId) ?? 0)+1}`,
      cue ? {start_ms:Math.round(cue.start_seconds*1000),end_ms:Math.round(cue.end_seconds*1000)} : undefined);
    activateTaskSelection();
    setSelectedAudioId(null);
    if (selectedCueId !== cueId || selectedClipId || selectedNarrationSegmentId || selectedAudioId) invalidateEditPreview();
    setSelectedClipId(null);
    setSelectedCueId(cueId);
    setSelectedNarrationSegmentId(null);
    setSelectedCover(false);
  }

  function selectNarrationSegment(segmentId: string) {
    publishSelection("narration",segmentId,`配音 ${(workspace.narration?.segments.findIndex(item=>item.id===segmentId) ?? 0)+1}`);
    activateTaskSelection();
    if (selectedNarrationSegmentId !== segmentId || selectedClipId || selectedCueId) invalidateEditPreview();
    setSelectedClipId(null);
    setSelectedCueId(null);
    setSelectedNarrationSegmentId(segmentId);
    setSelectedCover(false);
  }

  function selectCover() {
    activateTaskSelection();
    if (!selectedCover || selectedClipId || selectedCueId || selectedNarrationSegmentId) invalidateEditPreview();
    setSelectedClipId(null);
    setSelectedCueId(null);
    setSelectedNarrationSegmentId(null);
    setSelectedCover(true);
  }

  function selectSubtitleTrack(trackId: string) {
    if (activeSubtitleTrackId !== trackId) invalidateEditPreview();
    setActiveSubtitleTrackId(trackId);
    setSelectedCueId(null);
    setSelectedCover(false);
  }

  function openView(view: WorkspaceView) {
    setSelectedOverlayId(null);
    setSavingTemplate(false);
    setActiveView(view);
    if (!(["montage", "review"].includes(activeView) && ["montage", "review"].includes(view))) {
      setSelectedClipId(null);
      playheadRef.current = null;
    }
    setSelectedCueId(null);
    setSelectedNarrationSegmentId(null);
    setSelectedCover(false);
    invalidateEditPreview();
  }

  function generatePersonRender(segmentId: string) {
    const selectedPerson = workspace.plan.selected_person;
    if (!selectedPerson) return;
    void run(
      "person-render",
      () => generateCreationTaskPersonRender(task.id, {
        narration_segment_id: segmentId,
        person_profile_id: selectedPerson.profile_id,
        person_version: selectedPerson.version,
        motion: "natural",
        expression: "neutral",
      }),
      "人物口型预览已生成；尚未写入成片时间线。",
    );
  }

  function preparePersonEdit(clipId: string, command: string) {
    selectClip(clipId);
    setEditCommand(command);
    invalidateEditPreview();
  }

  async function previewEdit(commandOverride?: string) {
    const rawCommand = (commandOverride ?? editCommand).trim();
    if (!selectedEditContext || !rawCommand) return;
    if(commandOverride === undefined && editPreview?.proposal.operation.operation_type==="EditCompoundTimeline" && editPreview.proposal.operation.payload.selection_enabled){
      await reviseCompoundSelection(rawCommand);return;
    }
    const input = commandOverride === undefined ? draftInput.current : null;
    if (montageVideoRef.current && !montageVideoRef.current.paused) montageVideoRef.current.pause();
    invalidateEditPreview();
    const revision = editContextRevision.current;
    const sequence = ++previewRequestSequence.current;
    const ownsRequest = () => sequence === previewRequestSequence.current && currentTaskId.current === task.id;
    const stillCurrent = () => ownsRequest() && revision === editContextRevision.current;
    setBusy("edit-preview");
    setError(null);
    setNotice(null);
    try {
      const response = await previewCreationTaskEdit(task.id, {
        raw_command: rawCommand,
        context_stage: activeView,
        selected_object_refs: [selectedEditContext.ref, ...(activeView === "subtitle_cover" && activeSubtitleTrack ? [`subtitle-track:${activeSubtitleTrack.id}`] : [])],
        playhead_ms: selectedEditContext.kind === "clip" && playheadRef.current !== null
          ? Math.round(playheadRef.current * 1000) : null,
      });
      const preview = await resolveEditMedia(response, revision);
      if (stillCurrent()) setEditPreview(preview, input);
    } catch (cause) {
      if (stillCurrent() && !mediaController.current?.signal.aborted && !(cause instanceof TaskMediaJobError && ["cancelled", "stale"].includes(cause.job.status))) setError(errorDetails(cause));
    } finally {
      if (ownsRequest()) setBusy(null);
    }
  }

  async function askAssistant() {
    const message = editCommand.trim();
    if (!message) return;
    const referenceSnapshot = latestSelectionSnapshot.current;
    const taskReferences = selectionState?.items.filter(item => "task_id" in item) ?? [];
    const selectedReference = taskReferences[0];
    const materialSnapshot = taskReferences.length ? null : selectedMaterialReference?.selection ?? null;
    const snapshotPlayhead = selectedReference?.kind === "shot" && playheadRef.current !== null ? Math.round(playheadRef.current * 1000) : null;
    if (montageVideoRef.current && !montageVideoRef.current.paused) montageVideoRef.current.pause();
    invalidateEditPreview();
    const revision = editContextRevision.current;
    const requestSequence = ++assistantRequestSequence.current;
    const ownsRequest = () => requestSequence === assistantRequestSequence.current && task.id === currentTaskId.current;
    const releaseBusy = () => {
      setBusy(current => ownsRequest() && current === "assistant" ? null : current);
    };
    let requestConversationId: string | null = null;
    let requestLocalTurnId: string | null = null;
    setBusy("assistant");
    setError(null);
    try {
      if (!assistantConversation.conversation) await assistantConversation.createNew();
      const turn = assistantConversation.beginTurn(message);
      requestConversationId = turn.conversationId;
      requestLocalTurnId = turn.localTurnId;
      const onStreamEvent = assistantConversation.bindTurnEvents(turn.conversationId, turn.localTurnId);
      const expectedDraftVersion = await turn.draftCheckpoint;
      if (!ownsRequest() || revision !== editContextRevision.current || referenceSnapshot !== latestSelectionSnapshot.current || !assistantConversation.isCurrentConversation(turn.conversationId)) {
        await assistantConversation.failUnpersistedTurn(turn.conversationId, turn.localTurnId,
          "编辑范围已切换；本轮没有发送，原要求已保留为草稿。");
        releaseBusy();
        return;
      }
      if (materialSnapshot) {
        await sendMaterialAssistantTurn(message, turn.conversationId, materialSnapshot, turn.clientRequestId, expectedDraftVersion, onStreamEvent, selectionState?.version);
        if (assistantConversation.isCurrentConversation(turn.conversationId)) await assistantConversation.refresh();
        return;
      }
      const result = await sendCreationTaskAssistantTurn(task.id, {
        conversation_id: turn.conversationId,
        client_request_id: turn.clientRequestId,
        expected_draft_version: expectedDraftVersion,
        expected_reference_version: selectionState?.version,
        expected_timeline_version_id: task.current_timeline_version_id,
        expected_timeline_sequence: task.timeline_sequence,
        ...(selectedKnowledgeReference ? { selected_knowledge_reference: { knowledge_item_id: selectedKnowledgeReference.knowledge_item_id, version: selectedKnowledgeReference.version, ...(selectedKnowledgeReference.locator ? { locator: selectedKnowledgeReference.locator } : {}) } } : {}),
        ...(selectedProfileReference ? {selected_profile_reference:{kind:selectedProfileReference.kind,profile_id:selectedProfileReference.profile_id,version:selectedProfileReference.version}} : {}),
        message,
        context_stage: activeView,
        // The API derives object refs from the frozen selection CAS, including multi-select.
        selected_object_refs: [],
        playhead_ms: snapshotPlayhead,
      }, onStreamEvent);
      if (assistantConversation.isCurrentConversation(turn.conversationId)) await assistantConversation.refresh();
      if (!ownsRequest() || revision !== editContextRevision.current || !assistantConversation.isCurrentConversation(turn.conversationId)) return;
      if (result.execution_status) executionController.acceptExecution(result.execution_status);
      executionController.pollExecution();
      if (result.script_draft) await refresh(true, () => ownsRequest() && revision === editContextRevision.current && assistantConversation.isCurrentConversation(turn.conversationId));
      if (result.timeline_action || result.segment_revision || result.timeline_restore) await refresh(true, () => ownsRequest() && revision === editContextRevision.current && assistantConversation.isCurrentConversation(turn.conversationId));
      if (ownsRequest() && revision === editContextRevision.current && assistantConversation.isCurrentConversation(turn.conversationId)) {
        const preview = result.media_job ? await resolveEditMedia(result.media_job, revision, turn.conversationId) : result.edit_preview;
        if (!ownsRequest() || revision !== editContextRevision.current || !assistantConversation.isCurrentConversation(turn.conversationId)) return;
        setEditPreview(preview);
        if (preview) setAssistantPreview(true);
      }
    } catch (cause) {
      if (requestConversationId && requestLocalTurnId && !(cause instanceof DOMException && cause.name === "AbortError") && !(cause instanceof TaskMediaJobError && ["cancelled", "stale"].includes(cause.job.status))) {
        const failure = errorDetails(cause);
        await assistantConversation.failUnpersistedTurn(requestConversationId, requestLocalTurnId,
          [failure.message, failure.impact, failure.repairAction].filter(Boolean).join(" "));
      }
      if (ownsRequest() && revision === editContextRevision.current && !mediaController.current?.signal.aborted && !(cause instanceof TaskMediaJobError && ["cancelled", "stale"].includes(cause.job.status)) && (!requestConversationId || assistantConversation.isCurrentConversation(requestConversationId))) {
        setError(errorDetails(cause));
        if (activeView === "script") {
          await refresh(false, () => ownsRequest() && revision === editContextRevision.current && (!requestConversationId || assistantConversation.isCurrentConversation(requestConversationId))).catch(() => undefined);
          const history = await getCreationTaskAssistantHistory(task.id).catch(() => null);
          if (history && ownsRequest() && revision === editContextRevision.current) {
            const turns = assistantHistoryTurns(history.items);
            setAssistantTurns(turns);
          }
        }
      }
    } finally {
      releaseBusy();
    }
  }

  async function openSavedEditProposal(turn: AssistantConversationTurn) {
    const saved = turn.result?.response as CreationTaskAssistantTurnResponse | undefined;
    const proposal = saved?.edit_preview?.proposal;
    if (!proposal || proposal.task_id !== task.id) {
      setError(errorDetails(new Error("这份修改属于另一个任务，请在原任务查看。")));
      return;
    }
    const conversationId = assistantConversation.conversation?.id;
    if (!conversationId) return;
    const revision = editContextRevision.current;
    setBusy("edit-preview");
    setError(null);
    try {
      const latest = await getCreationTaskEditPreview(task.id, proposal.id);
      if (currentTaskId.current !== task.id || editContextRevision.current !== revision
          || !assistantConversation.isCurrentConversation(conversationId)) return;
      setEditPreview(latest);
      setAssistantPreview(true);
    } catch (cause) {
      if (currentTaskId.current === task.id && editContextRevision.current === revision) setError(errorDetails(cause));
    } finally {
      if (currentTaskId.current === task.id) setBusy(null);
    }
  }

  async function transferReplyToDraft(turn: AssistantConversationTurn, replace: boolean) {
    const conversationId = assistantConversation.conversation?.id;
    if (!conversationId || !assistantConversation.isCurrentConversation(conversationId)
      || turn.status !== "completed" || !turn.assistant_text.trim()) return;
    if (latestScript?.selection_status === "confirmed") {
      setError(errorDetails(new Error("当前文案已确认，不能被对话回复替换。")));
      return;
    }
    if (latestScript?.selection_status === "draft" && !replace) {
      setError(errorDetails(new Error("当前已有待确认文案，请明确选择替换后再带入。")));
      return;
    }
    if (replace && latestScript?.selection_status !== "draft") {
      setError(errorDetails(new Error("没有可替换的待确认文案。")));
      return;
    }
    const requestKey = `${conversationId}:${turn.id}:${replace ? latestScript?.id : "new"}`;
    const requestId = replyTransferRequests.current.get(requestKey) ?? crypto.randomUUID();
    replyTransferRequests.current.set(requestKey, requestId);
    setBusy("reply-transfer");
    setError(null);
    setNotice(null);
    try {
      const plan = await getCreationPlan(task.plan_id);
      if (!assistantConversation.isCurrentConversation(conversationId)) return;
      await transferAssistantReplyToScript(task.id, {
        conversation_id: conversationId,
        turn_id: turn.id,
        request_id: requestId,
        expected_plan_updated_at: plan.updated_at,
        expected_task_updated_at: task.updated_at,
        ...(replace && latestScript ? {
          replace_existing_script_id: latestScript.id,
          expected_script_updated_at: latestScript.updated_at,
        } : {}),
      });
      replyTransferRequests.current.delete(requestKey);
      await refresh(true);
      setNotice("回复已保存为待确认文案草稿；请补充来源和画面建议，再确认文案。");
    } catch (cause) {
      setError(errorDetails(cause));
    } finally {
      setBusy(null);
    }
  }

  async function localEditSettled(jobId: string, status: string) {
    const pending = pendingLocalEditRef.current;
    if (!pending || pending.jobId !== jobId || pending.taskId !== currentTaskId.current) return;
    const stillCurrent = () => pendingLocalEditRef.current === pending && pending.taskId === currentTaskId.current;
    await refresh(true, stillCurrent);
    if (!stillCurrent()) return;
    setPendingLocalEdit(null);
    if (pending.operationType === "RegenerateNarrationSegment") {
      setNotice(status === "succeeded"
        ? "配音修改已完成，目标配音段和当前版本已更新；成片尚未重新生成。"
        : status === "cancelled"
          ? "配音修改已取消，当前版本和音频保持不变；请查看作业原因。"
          : "配音修改未完成，当前版本和音频保持不变；请查看作业原因。");
    } else {
      setNotice(status === "succeeded" ? "人物修改已完成，目标镜头和当前版本已更新；成片尚未重新生成。"
        : "人物修改未完成，当前版本保持不变；请查看作业原因。");
    }
  }

  async function confirmEdit() {
    if (!editPreview) return;
    const proposal = editPreview.proposal;
    const inputOwner = previewInputOwner.current;
    const preserveNextDraft = assistantPreview;
    const conversationId = preserveNextDraft ? assistantConversation.conversation?.id : undefined;
    const originalTurnId = preserveNextDraft
      ? [...(assistantConversation.conversation?.turns ?? [])].reverse().find(turn => turn.user_message.trim() !== "")?.id
      : undefined;
    const assistantOwner = assistantRequestSequence.current;
    const previewOwner = previewRequestSequence.current;
    const taskIsCurrent = () => currentTaskId.current === task.id;
    const conversationIsCurrent = () => !conversationId || assistantConversation.isCurrentConversation(conversationId);
    const ownsBusy = () => taskIsCurrent() && assistantRequestSequence.current === assistantOwner
      && previewRequestSequence.current === previewOwner;
    setBusy("edit-confirm");
    setError(null);
    setNotice(null);
    try {
      const result = await confirmCreationTaskEdit(task.id, proposal.id);
      if (!taskIsCurrent()) return;
      if (conversationIsCurrent()) {
        const consumesInput = ownsBusy() && inputOwner?.proposalId === proposal.id
          && previewInputOwner.current === inputOwner && ownsDraftInput(inputOwner.input);
        if ("status" in result) {
          setPendingLocalEdit({ taskId: task.id, jobId: result.job_id,
            operationType: proposal.operation.operation_type, targetRefs: [...proposal.target_refs] });
          setNotice(queuedEditNotice(proposal.operation.operation_type));
        } else {
          setNotice('修改已保存，可撤销。' + (editExecutionReadinessMessage(result.execution_readiness) ?? ''));
        }
        invalidateEditPreview();
        if (consumesInput) setEditCommand("");
        if (conversationId && originalTurnId) await assistantConversation.refreshAfterTaskAdoption({
          conversation_id: conversationId, original_turn_id: originalTurnId, task_id: task.id, proposal_id: proposal.id,
        });
      }
      if (taskIsCurrent()) await refresh(true, () => taskIsCurrent());
    } catch (cause) {
      if (taskIsCurrent()) {
        if (ownsBusy()) setError(errorDetails(cause));
        await refresh(true, () => taskIsCurrent()).catch(() => undefined);
      }
    } finally {
      if (ownsBusy()) setBusy(null);
    }
  }

  function selectAudio(id: string, time?: number) {
    publishSelection("audio",id,"选中音轨");
    activateTaskSelection();
    setSelectedOverlayId(null);
    invalidateEditPreview(); setEditorDraft(false); setSelectedAudioId(id);
    setAudioPlayhead(time ?? playheadRef.current ?? 0);
    if (time !== undefined) {
      playheadRef.current = time; setAudioPlayhead(time);
      if (montageVideoRef.current) { montageVideoRef.current.pause(); montageVideoRef.current.currentTime = time; }
    }
  }

  function positionAudioListen(time: number) {
    playheadRef.current=time;setAudioPlayhead(time);
    if(montageVideoRef.current){montageVideoRef.current.pause();montageVideoRef.current.currentTime=time;}
  }

  async function previewVisual(input: VisualEditInput) {
    setBusy("edit-preview"); setError(null); setNotice(null); invalidateEditPreview();
    const revision = editContextRevision.current;
    const sequence = ++previewRequestSequence.current;
    const ownsRequest = () => sequence === previewRequestSequence.current && currentTaskId.current === task.id;
    const stillCurrent = () => ownsRequest() && revision === editContextRevision.current;
    try {
      const response = await previewCreationTaskVisual(task.id,input);
      const preview = await resolveEditMedia(response, revision);
      if (stillCurrent()) {
        await confirmCreationTaskEdit(task.id, preview.proposal.id);
        if (!stillCurrent()) return;
        setEditorDraft(false);
        await refresh(true, stillCurrent);
      }
    } catch (cause) { if (stillCurrent() && !mediaController.current?.signal.aborted && !(cause instanceof TaskMediaJobError && ["cancelled", "stale"].includes(cause.job.status))) setError(errorDetails(cause)); }
    finally { if (ownsRequest()) setBusy(null); }
  }

  async function previewAudio(input: AudioEditInput) {
    setBusy("edit-preview"); setError(null); setNotice(null); invalidateEditPreview();
    const revision = editContextRevision.current;
    const sequence = ++previewRequestSequence.current;
    const ownsRequest = () => sequence === previewRequestSequence.current && currentTaskId.current === task.id;
    const stillCurrent = () => ownsRequest() && revision === editContextRevision.current;
    try {
      const response = await previewCreationTaskAudio(task.id,input);
      const preview = await resolveEditMedia(response, revision);
      if (stillCurrent()) {
        if (input.action === "delete") setEditPreview(preview);
        else {
          await confirmCreationTaskEdit(task.id, preview.proposal.id);
          if (!stillCurrent()) return;
          setEditorDraft(false);
          await refresh(true, stillCurrent);
        }
      }
    } catch (cause) { if (stillCurrent() && !mediaController.current?.signal.aborted && !(cause instanceof TaskMediaJobError && ["cancelled", "stale"].includes(cause.job.status))) setError(errorDetails(cause)); }
    finally { if (ownsRequest()) setBusy(null); }
  }

  async function submitRecordedVoice(blob: Blob): Promise<void> {
    let recording: CreationTaskRecording;
    const recordingRevision = editContextRevision.current;
    const baseVersionId = task.current_timeline_version_id;
    const controller = new AbortController();
    mediaController.current?.abort(); mediaController.current = controller;
    const stillCurrent = () => !controller.signal.aborted && currentTaskId.current === task.id
      && currentWorkspace.current.task.current_timeline_version_id === baseVersionId
      && editContextRevision.current === recordingRevision;
    try {
      const response = await saveCreationTaskRecording(task.id, blob);
      recording = await resolveCreationTaskMediaResponse(task.id, response, "recording", {
        signal: controller.signal, baseVersionId,
        onUpdate: value => { if (stillCurrent()) setPendingMediaJob({ taskId: task.id, value }); },
      });
      if (!stillCurrent()) return;
    } catch (cause) {
      const details = errorDetails(cause);
      if (stillCurrent() && !(cause instanceof TaskMediaJobError && ["cancelled", "stale"].includes(cause.job.status))) setError(details);
      throw new Error([details.message, details.repairAction].filter(Boolean).join(" "));
    }
    setRecordings((current) => [recording, ...current.filter((item) => item.source_ref !== recording.source_ref)]);
    if (task.stage === "narration" && !workspace.narration) {
      setRecordingOpen(false);
      setNotice("录音已保存；尚未采用，选择“采用这段录音”后才会进入本任务配音。");
      return;
    }
    const narrationTrackId = workspace.montage?.audio_timeline?.tracks.find((track) => track.role === "narration")?.id ?? "audio-narration";
    setError(null);
    setNotice(null);
    invalidateEditPreview();
    const revision = editContextRevision.current;
    try {
      const response = await previewCreationTaskAudio(task.id, {
        base_version_id: task.current_timeline_version_id,
        action: "replace_narration",
        target_id: narrationTrackId,
        values: { source_ref: recording.source_ref },
        raw_command: "用本机录音替换整条旁白",
      });
      const preview = await resolveEditMedia(response, revision);
      if (currentTaskId.current === task.id && revision === editContextRevision.current) {
        await confirmCreationTaskEdit(task.id, preview.proposal.id);
        setEditorDraft(false);
        setRecordingOpen(false);
        await refresh(true);
      }
    } catch (cause) {
      const details = errorDetails(cause);
      setError(details);
      throw new Error(details.message);
    }
  }

  async function adoptRecordedVoice(recording: CreationTaskRecording): Promise<void> {
    const script = workspace.scripts.find((item) => item.id === task.confirmed_script_id);
    if (!script || script.selection_status !== "confirmed" || script.sections_total_count !== 1) {
      setError(errorDetails(new Error("当前文案不是单段已确认版本，不能确定录音对应关系。")));
      return;
    }
    setBusy("recording-adopt");
    try {
      const page = await getCreationTaskScriptSections(task.id, script.id, { expected_updated_at: script.updated_at });
      if (currentTaskId.current !== task.id) return;
      if (page.total_count !== 1 || page.items.length !== 1 || page.next_after_ordinal !== null) throw new Error("当前文案已不是单段版本，请重新核对后采用录音。");
      await run("recording-adopt", () => adoptCreationTaskRecording(task.id, {
        script_id: script.id,
        source_section_id: page.items[0].section.id,
        source_ref: recording.source_ref,
      }), "本机录音已采用为当前文案的配音来源；可以按原任务继续制作。 ");
    } catch (cause) {
      if (currentTaskId.current === task.id) setError(errorDetails(cause));
    } finally {
      if (currentTaskId.current === task.id) setBusy(null);
    }
  }

  async function reviseCompoundSelection(selection: number[] | string) {
    if (!editPreview) return;
    const proposalId = editPreview.proposal.id;
    const input = typeof selection === "string" ? draftInput.current : previewInputOwner.current?.input ?? null;
    setBusy("edit-preview"); setError(null);
    const revision = ++editContextRevision.current;
    const sequence = ++previewRequestSequence.current;
    const ownsRequest = () => sequence === previewRequestSequence.current && currentTaskId.current === task.id;
    try {
      const response = await previewCompoundSelection(task.id, proposalId, selection);
      const next = await resolveEditMedia(response, revision);
      if (ownsRequest() && editContextRevision.current === revision) {
        if (input && ownsDraftInput(input)) {
          setEditCommand(next.intent.raw_command);
          setEditPreview(next, draftInput.current);
        } else {
          setEditPreview(next, input);
        }
      }
    } catch (cause) { if (ownsRequest() && editContextRevision.current === revision && !mediaController.current?.signal.aborted) setError(errorDetails(cause)); }
    finally { if (ownsRequest()) setBusy(null); }
  }

  async function readMetadataSave(pending: PendingMetadataSave, owns: () => boolean) {
    const memberId = await editRequestMemberId();
    if (!owns()) return false;
    if (memberId !== pending.memberId) throw new Error("请切回原成员账号查询这次保存，未重新提交。");
    const receipt = await getCreationTaskEditRequest(pending.taskId, pending.input.request_id).catch(cause => {
      if (pending.submissionError) throw new Error(`${pending.submissionError.message}；原请求查询：${errorDetails(cause).message}`);
      throw cause;
    });
    if (!owns()) return false;
    if (receipt.task_id !== pending.taskId || receipt.request_id !== pending.input.request_id || receipt.phase !== "apply"
      || metadataInputKey(receipt.submitted_input) !== metadataInputKey(pending.input)) {
      throw new Error("原保存回执与封存输入不一致，请保留原请求核对。");
    }
    if (receipt.status !== "recorded" || receipt.result_status_code === null) {
      setNotice("保存结果尚未核实，请查询原保存结果，不要重新提交。");
      if (pending.submissionError) setError(pending.submissionError);
      return false;
    }
    if (receipt.result_status_code < 200 || receipt.result_status_code >= 300 || !receipt.result || !("operation" in receipt.result)) {
      const target = pending.input.operation_type === "UpdateSubtitleCue" ? "字幕" : "镜头";
      setNotice(`原${target}保存返回 ${receipt.result_status_code}，可能已有写入。请保留原请求，核对版本和${target}；未自动重试。`);
      await refresh(true, () => owns());
      return false;
    }
    await refresh(true, () => owns());
    if (!owns()) return false;
    sessionStorage.removeItem(`subtitle-save:${pending.workspaceId}:${pending.taskId}:${pending.memberId}`);
    pendingMetadataSaveRef.current = null;
    setPendingMetadataSave(null);
    if (pending.input.operation_type !== "UpdateSubtitleCue") {
      const draftKey = `${pending.taskId}:${pending.input.clip_id}`;
      setClipDrafts(current => { const next = { ...current }; delete next[draftKey]; return next; });
    }
    setNotice((pending.input.operation_type === "UpdateSubtitleCue" ? "字幕修改" : "镜头修改") + "已保存，可撤销。" + (editExecutionReadinessMessage(receipt.result.execution_readiness) ?? ""));
    return true;
  }

  async function queryMetadataSave() {
    const pending = pendingMetadataSaveRef.current;
    if (!pending || editRequestOperation.current || busy !== null) return;
    const operation = { taskId: pending.taskId, memberId: pending.memberId };
    editRequestOperation.current = operation;
    const owns = () => currentTaskId.current === pending.taskId && editRequestOperation.current === operation
      && editRequestScope.current === `subtitle-save:${pending.workspaceId}:${pending.taskId}:${pending.memberId}`;
    setBusy("edit-confirm"); setError(null);
    try { await readMetadataSave(pending, owns); }
    catch (cause) { if (owns()) setError(errorDetails(cause)); }
    finally {
      // Release only this operation's busy slot; an old response cannot clear a successor's slot.
      if (editRequestOperation.current === operation) {
        editRequestOperation.current = null;
        if (currentTaskId.current === operation.taskId) setBusy(null);
      }
    }
  }

  async function saveMetadataEdit(input: MetadataEditInput) {
    if (editRequestOperation.current || pendingMetadataSaveRef.current || busy !== null) return false;
    if (editRequestRecoveryError.current) return false;
    const taskSnapshot = currentWorkspace.current.task;
    // Do not create a request locator for a command known to lack its required base.
    // Existing pending requests above still require their original receipt.
    if (taskSnapshot.current_timeline_version_id === null) {
      setError({ message: "当前故事板还没有可撤销的初始版本。", code: "timeline_version_required",
        impact: "修改没有提交，当前草稿已保留。", repairAction: "保留当前故事板并建立版本，再保存修改。" });
      return false;
    }
    const operation: { taskId: string; memberId: string | null } = { taskId: taskSnapshot.id, memberId: null };
    editRequestOperation.current = operation;
    const owns = () => currentTaskId.current === taskSnapshot.id && editRequestOperation.current === operation
      && (operation.memberId === null || editRequestScope.current === `subtitle-save:${taskSnapshot.workspace_id}:${taskSnapshot.id}:${operation.memberId}`);
    setBusy("edit-confirm"); setError(null); setNotice(null); invalidateEditPreview();
    try {
      const memberId = await editRequestMemberId();
      operation.memberId = memberId;
      if (!owns()) return false;
      const subtitleStorageKey = `subtitle-save:${taskSnapshot.workspace_id}:${taskSnapshot.id}:${memberId}`;
      const stored = sessionStorage.getItem(subtitleStorageKey);
      if (stored) {
        const original: PendingMetadataSave = JSON.parse(stored);
        if (original.taskId !== taskSnapshot.id || original.workspaceId !== taskSnapshot.workspace_id || original.memberId !== memberId)
          throw new Error("原保存请求与当前任务或成员不一致，没有提交。");
        pendingMetadataSaveRef.current = original;
        setPendingMetadataSave(original);
        setNotice("这次保存已有原请求，请先查询原保存结果。");
        return false;
      }
      const pending: PendingMetadataSave = { memberId, taskId: taskSnapshot.id, workspaceId: taskSnapshot.workspace_id,
        input: JSON.parse(JSON.stringify({ ...input, request_id: crypto.randomUUID(),
          expected_timeline_version_id: taskSnapshot.current_timeline_version_id,
          expected_timeline_sequence: taskSnapshot.timeline_sequence })) };
      const serialized = JSON.stringify(pending);
      sessionStorage.setItem(subtitleStorageKey, serialized);
      if (sessionStorage.getItem(subtitleStorageKey) !== serialized) throw new Error("无法保留原保存请求，本次没有提交。");
      pendingMetadataSaveRef.current = pending;
      setPendingMetadataSave(pending);
      // A failed POST response is not evidence that the write did not happen.
      try { await applyCreationTaskExactEdit(pending.taskId, pending.input); }
      catch (cause) {
        const stored = sessionStorage.getItem(subtitleStorageKey);
        if (!stored || (JSON.parse(stored) as PendingMetadataSave).input.request_id !== pending.input.request_id) return false;
        pending.submissionError = errorDetails(cause);
        sessionStorage.setItem(subtitleStorageKey, JSON.stringify(pending));
        if (!owns()) return false;
        setPendingMetadataSave({ ...pending }); setError(pending.submissionError);
      }
      return await readMetadataSave(pending, owns);
    } catch (cause) { if (owns()) setError(errorDetails(cause)); return false; }
    finally {
      // Release only this operation's busy slot; an old response cannot clear a successor's slot.
      if (editRequestOperation.current === operation) {
        editRequestOperation.current = null;
        if (currentTaskId.current === operation.taskId) setBusy(null);
      }
    }
  }

  async function previewExactEdit(input: CreationTaskExactEditInput) {
    if (isMetadataSaveInput(input)) return saveMetadataEdit(input);
    if (busy !== null) return false;
    setBusy("edit-confirm"); setError(null); setNotice(null); invalidateEditPreview();
    const revision = editContextRevision.current;
    const sequence = ++previewRequestSequence.current;
    let saved = false;
    try {
      if (input.operation_type.startsWith("Delete") || (input.operation_type === "EditGraphicOverlay" && input.overlay_action === "delete")) {
        const response = await previewCreationTaskExactEdit(task.id, input);
        const preview = await resolveEditMedia(response, revision);
        if (currentTaskId.current !== task.id || revision !== editContextRevision.current) return false;
        setEditPreview(preview);
        saved = true;
      } else {
        const result = await applyCreationTaskExactEdit(task.id, input);
        if ("status" in result) {
          setPendingLocalEdit({ taskId: task.id, jobId: result.job_id,
            operationType: input.operation_type, targetRefs: input.operation_type === "RegenerateNarrationSegment"
              ? [`narration:${input.narration_segment_id}`] : selectedEditContext ? [selectedEditContext.ref] : [] });
          setNotice(queuedEditNotice(input.operation_type));
          await refresh(true);
          return false;
        }
        saved = true;
        setNotice('修改已保存，可撤销。' + (editExecutionReadinessMessage(result.execution_readiness) ?? ''));
        setClipDrafts(current => {
          const next = { ...current };
          if (selectedClipId) delete next[`${task.id}:${selectedClipId}`];
          return next;
        });
        setEditorDraft(false);
        await refresh(true);
      }
    } catch (cause) {
      if (currentTaskId.current === task.id && revision === editContextRevision.current && !mediaController.current?.signal.aborted && !(cause instanceof TaskMediaJobError && ["cancelled", "stale"].includes(cause.job.status))) setError(errorDetails(cause));
      return saved;
    } finally { if (currentTaskId.current === task.id && sequence === previewRequestSequence.current) setBusy(null); }
    return saved;
  }

  function clearTimelineRestore(pending: PendingTimelineRestore, owns: () => boolean) {
    sessionStorage.removeItem(`timeline-restore:${pending.workspaceId}:${pending.taskId}:${pending.memberId}`);
    if (owns()) { pendingTimelineRestoreRef.current = null; setPendingTimelineRestore(null); }
  }

  async function readTimelineRestore(pending: PendingTimelineRestore, owns: () => boolean) {
    if (await editRequestMemberId() !== pending.memberId) throw new Error("请切回原成员账号查询撤销重做结果。");
    const receipt = await readCreationTaskTimelineRestore(pending.taskId, pending.input.request_id);
    if (!owns()) return;
    if (receipt.task_id !== pending.taskId || receipt.request_id !== pending.input.request_id || receipt.action !== pending.action
      || (pending.input.target_operation_id !== undefined && receipt.target_operation_id !== pending.input.target_operation_id))
      throw new Error("撤销重做回执与原请求不一致");
    await refresh(true, () => owns());
    if (!owns()) return;
    if (receipt.status === "applied" || receipt.status === "refused") clearTimelineRestore(pending, owns);
    if (receipt.status === "applied") {
      setClipDrafts({}); setSelectedCueId(null); setSelectedNarrationSegmentId(null); setSelectedCover(false);
      setNotice("原修改的撤销或重做已保存。" + (editExecutionReadinessMessage(receipt.execution_readiness) ?? ""));
    } else {
      setNotice(receipt.failure?.reason ?? (receipt.status === "refused" ? "原操作因冲突被拒绝。" : "原结果尚未核实，请继续查询原请求。"));
    }
  }

  async function queryTimelineRestore() {
    const pending = pendingTimelineRestoreRef.current;
    if (!pending || pending.taskId !== task.id || editRequestOperation.current || busy !== null) return;
    const operation = { taskId: pending.taskId, memberId: pending.memberId };
    editRequestOperation.current = operation;
    const owns = () => currentTaskId.current === pending.taskId && editRequestOperation.current === operation
      && editRequestScope.current === `subtitle-save:${pending.workspaceId}:${pending.taskId}:${pending.memberId}`;
    setBusy("undo"); setError(null);
    try { await readTimelineRestore(pending, owns); }
    catch (cause) { if (owns()) setError(errorDetails(cause)); }
    finally {
      if (editRequestOperation.current === operation) {
        editRequestOperation.current = null;
        if (currentTaskId.current === pending.taskId) setBusy(null);
      }
    }
  }

  async function undoTimeline(redo = false, targetOperationId?: string) {
    if (editRequestOperation.current || busy !== null) return;
    if (pendingTimelineRestoreRef.current) { setNotice("原撤销重做请求等待核对，请先查询原结果。"); return; }
    const taskSnapshot = currentWorkspace.current.task;
    const operation: { taskId: string; memberId: string | null } = { taskId: taskSnapshot.id, memberId: null };
    editRequestOperation.current = operation;
    const owns = () => currentTaskId.current === taskSnapshot.id && editRequestOperation.current === operation
      && (operation.memberId === null || editRequestScope.current === `subtitle-save:${taskSnapshot.workspace_id}:${taskSnapshot.id}:${operation.memberId}`);
    setBusy("undo"); setError(null); setNotice(null); invalidateEditPreview();
    let writeReturned = false;
    let postStarted = false;
    let pending: PendingTimelineRestore | null = null;
    try {
      const memberId = await editRequestMemberId();
      operation.memberId = memberId;
      if (!owns()) return;
      const key = `timeline-restore:${taskSnapshot.workspace_id}:${taskSnapshot.id}:${memberId}`;
      const stored = sessionStorage.getItem(key);
      if (stored) {
        const original: PendingTimelineRestore = JSON.parse(stored);
        if (original.taskId !== taskSnapshot.id || original.workspaceId !== taskSnapshot.workspace_id || original.memberId !== memberId)
          throw new Error("原撤销重做请求与当前任务或成员不一致，没有提交。");
        pendingTimelineRestoreRef.current = original; setPendingTimelineRestore(original);
        setNotice("原撤销重做请求等待核对，请先查询原结果。"); return;
      }
      pending = { taskId: taskSnapshot.id, workspaceId: taskSnapshot.workspace_id, memberId, action: redo ? "redo" : "undo",
        input: { request_id: crypto.randomUUID(), ...(targetOperationId ? { target_operation_id: targetOperationId } : {}),
          expected_timeline_version_id: taskSnapshot.current_timeline_version_id, expected_timeline_sequence: taskSnapshot.timeline_sequence } };
      const serialized = JSON.stringify(pending);
      sessionStorage.setItem(key, serialized);
      if (sessionStorage.getItem(key) !== serialized) throw new Error("无法保留撤销重做原请求，本次没有提交。");
      pendingTimelineRestoreRef.current = pending; setPendingTimelineRestore(pending);
      postStarted = true;
      await (redo ? redoCreationTaskTimeline : undoCreationTaskTimeline)(pending.taskId, pending.input);
      writeReturned = true;
      await readTimelineRestore(pending, owns);
    } catch (cause) {
      let reported = cause;
      if (pending && postStarted && !writeReturned) {
        try {
          if (cause instanceof CreationPlanApiError && ["not_submitted", "rejected"].includes(cause.submissionEffect ?? "")) {
            clearTimelineRestore(pending, owns);
          } else {
            pending.submissionError = errorDetails(cause);
            sessionStorage.setItem(`timeline-restore:${pending.workspaceId}:${pending.taskId}:${pending.memberId}`, JSON.stringify(pending));
          }
        } catch (storageCause) { reported = storageCause; }
      }
      if (owns()) {
        const details = errorDetails(reported);
        setError(!postStarted ? { ...details, message: `本次撤销重做没有提交：${details.message}` } : details);
        await refresh(true, () => owns()).catch(() => undefined);
      }
    } finally {
      if (editRequestOperation.current === operation) {
        editRequestOperation.current = null;
        if (currentTaskId.current === taskSnapshot.id) setBusy(null);
      }
    }
  }

  const workspaceStatus = task.stage_status === "failed"
    ? { label: "需要处理" }
    : task.stage_status === "waiting_for_user"
      ? { label: "等待确认" }
      : { label: task.stage_status === "running" ? "正在处理" : "本地任务" };

  const [segmentsOpen, setSegmentsOpen] = useState(false);
  const isEditor = activeView === "montage";
  const hasTimeline = Boolean(workspace.montage);
  const selectedClip = selectedClipIndex >= 0 ? workspace.montage?.clips[selectedClipIndex] : undefined;
  const clipDraftKey = selectedClip ? `${task.id}:${selectedClip.id}` : undefined;
  const savedClipDraft = selectedClip ? createVideoClipDraft(selectedClip) : undefined;
  const clipDraft = clipDraftKey && savedClipDraft ? clipDrafts[clipDraftKey] ?? savedClipDraft : undefined;
  const clipDraftChanged = Boolean(clipDraft && savedClipDraft && (
    clipDraft.start !== savedClipDraft.start || clipDraft.mode !== savedClipDraft.mode
    || clipDraft.x !== savedClipDraft.x || clipDraft.y !== savedClipDraft.y
  ));
  function updateClipDraft(next: VideoClipDraft) {
    if (!clipDraftKey || !savedClipDraft) return;
    setClipDrafts(current => ({ ...current, [clipDraftKey]: next }));
    setEditorDraft(next.start !== savedClipDraft.start || next.mode !== savedClipDraft.mode
      || next.x !== savedClipDraft.x || next.y !== savedClipDraft.y);
  }
  const quickInspector = isEditor && selectedClip && clipDraft && !selectedOverlay && !selectedAudioId
    ? (openDetails: () => void) => <VideoClipQuickEditor
      key={clipDraftKey}
      clip={selectedClip}
      index={selectedClipIndex}
      busy={busy !== null}
      draft={clipDraft}
      onDraftStateChange={updateClipDraft}
      onDraftChange={invalidateEditPreview}
      onPreview={input => void previewExactEdit(input)}
      onOpenDetails={openDetails}
    />
    : undefined;
  const inspector = isEditor
    ? selectedOverlay ? <GraphicOverlayEditor taskId={task.id} versionId={task.current_timeline_version_id} items={workspace.montage!.graphic_overlays??[]} selectedId={selectedOverlay.id} onSelect={selectOverlay} duration={workspace.montage!.duration_seconds} busy={busy!==null} onPreview={input=>void previewExactEdit(input)} onDraftChange={()=>{invalidateEditPreview();setEditorDraft(true);}} />
    : selectedAudioTrack ? <AudioTimelineInspector track={selectedAudioTrack} clip={selectedAudioClip} versionId={task.current_timeline_version_id} playhead={audioPlayhead} busy={busy !== null} onPreview={input=>void previewAudio(input)} onDraftChange={()=>{invalidateEditPreview();setEditorDraft(true);}} />
    : selectedClip ? <VideoClipInspector onSelectOverlay={selectOverlay} getPlayhead={() => playheadRef.current} onVisualPreview={input=>void previewVisual(input)} clip={selectedClip} index={selectedClipIndex} count={workspace.montage!.clips.length} taskId={task.id} versionId={task.current_timeline_version_id} subtitleTracks={workspace.subtitle_tracks} personRender={workspace.person_renders.find(item => item.id === selectedClip.person_render_id)} personRenderCapability={workspace.person_render_capability} portraitAvailable={workspace.portrait_available} graphicOverlays={workspace.montage!.graphic_overlays} durationSeconds={workspace.montage!.duration_seconds} allClipIds={workspace.montage!.clips.map(item => item.id)} hasOutput={Boolean(workspace.montage!.output_url)} busy={busy !== null} initialDraft={clipDraft} onDraftStateChange={updateClipDraft} onPreview={input => void previewExactEdit(input)} onDraftChange={changed => { invalidateEditPreview(); setEditorDraft(changed); }} onReplace={() => { setInserting(false); void showReplacementCandidates(selectedClip.id); }} candidates={replacementCandidates} replacing={replacementClipId === selectedClip.id} onAddSegment={() => { setReplacementClipId(null); void showInsertCandidates(selectedClip.id); }} inserting={inserting} onPerson={command => preparePersonEdit(selectedClip.id, command)} onAddOriginal={() => void previewEdit("提取当前镜头原声")} /> : undefined
    : activeView === "subtitle_cover"
    ? selectedSubtitleCue || selectedCover
      ? <SubtitleCoverInspector selectedCover={selectedCover} selectedCue={selectedSubtitleCue} selectedTrack={activeSubtitleTrack} cover={workspace.cover_asset} translationCapability={workspace.subtitle_translation_capability} frameCapability={workspace.cover_frame_capability} imageCapability={workspace.image_generation_capability} />
      : undefined
    : <Inspector activeView={activeView} busy={busy !== null} onPreparePersonEdit={preparePersonEdit} selectedContext={selectedEditContext} task={task} plan={workspace.plan} topic={workspace.selected_topic} scripts={workspace.scripts} narration={workspace.narration} montage={workspace.montage} personRenders={workspace.person_renders} personRenderCapability={workspace.person_render_capability} />;

  // target_refs is the actual proposal scope; external_effects is explanatory
  // copy (even a local crop includes it), not a boolean permission/fee flag.
  const operation = editPreview?.proposal.operation;
  const previewCommand = editPreview && !previewInputOwner.current ? editPreview.intent.raw_command : editCommand;
  const ordinaryObjectEdit = operation && (["ReplaceClip", "ChangeCrop", "TrimClip", "ChangeClipSpeed", "SetClipLock", "ConfirmClip", "SetClipTransition", "AdjustVisualEffects", "UpdateSubtitleCue"].includes(operation.operation_type)
    || (operation.operation_type === "EditGraphicOverlay" && operation.payload.overlay_action === "update"));
  const localConfirmation = Boolean(ordinaryObjectEdit && selectedEditContext && editPreview
    && editPreview.proposal.task_id === task.id && editPreview.proposal.workspace_id === task.workspace_id
    && editPreview.proposal.target_refs.length > 0
    && editPreview.proposal.target_refs.every(ref => ref === selectedEditContext.ref));
  const inspectorConfirmation = localConfirmation && editPreview ? <EditPreviewCard key={editPreview.proposal.id} preview={editPreview} busy={busy} compact command={previewCommand} kind="config" onCancel={invalidateEditPreview} onConfirm={() => void confirmEdit()} /> : null;
  const sharingControls = (
    <div className={styles.sharingControls}>
      <div className={styles.taskVisibility}>
        <span>{task.visibility === "shared" ? "任务：团队共享" : "任务：仅自己可见"}</span>
        {task.visibility === "shared"
          ? <button disabled={visibilityBusy || busy !== null} onClick={() => void changeTaskVisibility("private")} type="button">收回</button>
          : <button disabled={visibilityBusy || busy !== null} onClick={() => void changeTaskVisibility("shared")} type="button">共享给团队</button>}
      </div>
      <div className={styles.taskVisibility}>
        <span>方案：{planVisibility === null ? "状态未知" : planVisibility === "shared" ? "团队共享" : "仅自己可见"}</span>
        {planVisibility === "shared"
          ? <button disabled={visibilityBusy || busy !== null} onClick={() => void changePlanVisibility("private")} type="button">方案收回</button>
          : planVisibility === "private"
            ? <button disabled={visibilityBusy || busy !== null} onClick={() => void changePlanVisibility("shared")} type="button">方案共享给团队</button>
            : null}
      </div>
      {visibilityError ? <p role="alert">{visibilityError}</p> : null}
    </div>
  );

  const hasReviewOutput = task.output_ready && workspace.montage?.status === "rendered" && Boolean(workspace.montage.output_url);
  const reviewPane = !hasReviewOutput ? "prepare" : reviewChoice?.taskId === task.id ? reviewChoice.pane : "result";
  const reviewTabKey = (event: React.KeyboardEvent<HTMLButtonElement>) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    if (!hasReviewOutput) return;
    const pane = event.key === "Home" ? "prepare" : event.key === "End" ? "result" : reviewPane === "prepare" ? "result" : "prepare";
    setReviewChoice({ taskId: task.id, pane });
    document.getElementById(`review-${pane}-${task.id}`)?.focus();
  };

  const hasContextConversation = !savingTemplate && (
    (activeView === "script" && task.stage === "script" && !task.confirmed_script_id)
    || ((activeView === "montage" || activeView === "review") && workspace.montage)
    || (activeView === "narration" && workspace.narration)
    || (activeView === "subtitle_cover" && activeSubtitleTrack)
  );
  const conversation = hasContextConversation
    ? <><button type="button" aria-pressed={addReferences} onClick={()=>setAddReferences(value=>!value)}>{addReferences ? "多选引用中" : "多选引用"}</button><ContextCommandBar localConfirmation={localConfirmation} scriptMode={activeView === "script"} key={task.id} onSelectionPreview={indices => void reviseCompoundSelection(indices)} compact busy={busy} command={editCommand} previewCommand={previewCommand} onCancel={() => invalidateEditPreview()} onCommandChange={(value) => { setEditCommand(value); if (!assistantPreview && (editPreview?.proposal.operation.operation_type !== "EditCompoundTimeline" || !editPreview.proposal.operation.payload.selection_enabled)) invalidateEditPreview(); }} onConfirm={() => void confirmEdit()} onExactPreview={() => void previewEdit()} preview={editPreview} assistantState={assistantConversation} onAssistantSend={askAssistant} onOpenEditProposal={turn => void openSavedEditProposal(turn)} onTransferReply={activeView === "script" ? transferReplyToDraft : undefined} transferDraftState={latestScript?.selection_status === "confirmed" ? "confirmed" : latestScript?.selection_status === "draft" ? "draft" : "none"} onConversationChange={() => { generationReadOwner.current = null; assistantRequestSequence.current += 1; previewRequestSequence.current += 1; invalidateEditPreview(); setBusy(null); setError(null); }} assistantTurns={assistantTurns} assistantConfigured={assistantConfigured} onClearReference={() => setDetachedReference(selectedEditContext?.ref ?? null)} materialContextLabel={selectedTaskReference ? null : selectedMaterialReference?.label ?? null} selectedContextLabel={!selectedTaskReference ? null : isEditor && selectedOverlay ? `${selectedEditContext?.label} · ${editorTime(selectedOverlay.start_seconds)}–${editorTime(selectedOverlay.end_seconds)}` : isEditor && selectedClip && !selectedAudioId ? `${selectedEditContext?.label} · ${editorTime(selectedClip.timeline_start_seconds)}–${editorTime(selectedClip.timeline_end_seconds)}` : selectedEditContext?.label ?? null} selectedContextPlaceholder={selectedTaskReference ? "说说怎么改" : selectedMaterialReference ? "说说想怎样整理这条素材" : activeView === "script" ? "说说文案想怎么写" : "想问什么？"} /></>
    : <HomeCommandComposer task={task} onTaskUpdated={() => { void refresh(true); }} />;

  return (
    <AppShell closeBlockedReason={recordingOpen ? "录音窗口仍打开，请先保存或结束录音，再关闭作品页签。" : undefined} assistantContext={{ task, onTaskUpdated: () => { void refresh(true); }, taskContext: {
      stage: activeView,
      selectedObjectRefs: selectedTaskReference ? [selectedTaskReference.ref, ...(activeView === "subtitle_cover" && activeSubtitleTrack ? [`subtitle-track:${activeSubtitleTrack.id}`] : [])] : [],
      getPlayheadMs: () => playheadRef.current === null ? null : Math.round(playheadRef.current * 1000),
      onTransferReply: activeView === "script" ? transferReplyToDraft : undefined,
      transferDraftState: latestScript?.selection_status === "confirmed" ? "confirmed" : latestScript?.selection_status === "draft" ? "draft" : "none",
      onOpenEditProposal: turn => void openSavedEditProposal(turn),
    } }} inspectorConfirmation={inspectorConfirmation} inspectorConfirmationKey={localConfirmation ? editPreview?.proposal.id : undefined} conversation={conversation} creationTasks={shellTasks} inspector={inspector} quickInspector={quickInspector} quickInspectorKey={quickInspector ? clipDraftKey : undefined} quickInspectorLabel={quickInspector ? `镜头 ${String(selectedClipIndex + 1).padStart(2, "0")} 快速编辑` : undefined} editorLayout={hasTimeline} toolbar={<div className={editorUi.toolbarTitle}><h1>{task.name}</h1><span>{busy ? "处理中" : editPreview ? "待确认" : editorDraft || quickInspector && clipDraftChanged ? "未应用" : isEditor ? "已保存" : ""}</span>
      <button aria-haspopup="dialog" aria-expanded={sharingOpen} className={editorUi.button} onClick={() => setSharingOpen(true)} type="button">共享设置</button>
      <HomeCardDialog kind="config" open={sharingOpen} title="任务与方案共享" onClose={() => setSharingOpen(false)}>{sharingControls}</HomeCardDialog></div>} toolbarActions={isEditor ? <div className={editorUi.toolbarActions}><Button variant="outline" disabled={busy !== null || !task.output_ready || !workspace.montage?.output_url} onClick={() => void saveVideo()}>保存视频</Button><button className={editorUi.iconButton} aria-label="撤销" title="撤销上一次修改" disabled={busy !== null || !task.can_undo} onClick={() => void undoTimeline()} type="button"><Undo2 size={17} /></button><button className={editorUi.iconButton} aria-label="重做" title="重做刚才撤销的修改" disabled={busy !== null || !task.can_redo} onClick={() => void undoTimeline(true)} type="button"><Redo2 size={17} /></button><button className={editorUi.iconButton} aria-label="电脑录音替换旁白" title="用本机录音替换整条旁白" disabled={busy !== null} onClick={() => setRecordingOpen(true)} type="button"><Mic size={17} /></button><button className={editorUi.iconButton} aria-label="历史时间线版本" title="查看这个任务的历史时间线版本" onClick={() => setHistoryOpen((open) => !open)} type="button"><History size={17} /></button></div> : undefined} inspectorTitle={isEditor ? selectedOverlay ? selectedEditContext?.label : selectedAudioTrack ? selectedAudioClip?.name ?? selectedAudioTrack.name : `镜头 ${String(selectedClipIndex + 1).padStart(2, "0")}` : activeView === "subtitle_cover" ? "当前选择" : "任务详情"} workspaceStatus={workspaceStatus}>
      <Tabs value={activeView} onValueChange={value => { const view = value as WorkspaceView; if (canOpenStage(view, workspace)) openView(view); }} onChangeCapture={() => { navigationReadRevision.current += 1; }} data-editor-workspace="" className={[hasTimeline ? editorUi.workspace : styles.workspace, activeView === "subtitle_cover" ? styles.subtitleWorkspace : ""].filter(Boolean).join(" ")}>
        <TaskViewNavigation workspace={workspace} activeView={activeView} onOpen={openView} />
        {workspace.montage ? <div className={styles.persistentStage}><VideoEditingStage editError={error?.message} selectedOverlayId={selectedOverlayId} onSelectOverlay={selectOverlay} onDraftChange={()=>invalidateEditPreview()} busy={busy !== null} onPreview={input=>previewExactEdit(input)} audioTracks={workspace.montage.audio_timeline ? subtitleTrack => <AudioTimelineTracks sharedTimeScale subtitleTrack={subtitleTrack} onListenPosition={positionAudioListen} key={`${task.id}:${task.current_timeline_version_id}`} taskId={task.id} versionId={task.current_timeline_version_id} audioTimeline={workspace.montage!.audio_timeline!} durationSeconds={workspace.montage!.duration_seconds} selectedId={selectedAudioId} playhead={audioPlayhead} onSelect={selectAudio} onPreview={input=>void previewAudio(input)} busy={busy !== null} /> : undefined} taskId={task.id} versionId={task.current_timeline_version_id} narrationCacheKey={task.narration_cache_key} montage={workspace.montage} planAspectRatio={workspace.plan.aspect_ratio} subtitleTracks={workspace.subtitle_tracks} selectedId={selectedClipId} onSelect={selectClip} onPosition={selectPlayingClip} videoRef={montageVideoRef} initialTime={playheadRef.current ?? 0} renderAction={null} /></div> : null}
        <TabsContent value={activeView} className={hasTimeline ? [styles.auxiliaryBody, isEditor ? styles.editingAuxiliary : ""].filter(Boolean).join(" ") : styles.workspaceBody}>
        <header className={hasTimeline ? styles.stageRuntime : styles.stageHeading} data-timeline-version={task.timeline_sequence}>
          {!hasTimeline ? <div><h2>{productStages.find(stage => stage.id === activeView)?.title}</h2><span className={styles.stateBadge}>{activeView === task.stage ? creationTaskStatusPresentation(task.stage_status, task.stage).label : productStageState(activeView, workspace, workspace.subtitle_tracks.some(track => track.status === "valid") && workspace.cover_asset?.status === "valid").detail}</span></div> : null}
          {execution?.job && ["queued", "running"].includes(execution.job.status) ? <span className={styles.runtimeState} role="status">连续制作：{execution.job.status === "queued" ? "已排队" : "后台制作中"}{execution.current_stage ? ` · ${executionStageLabels[execution.current_stage]}` : ""}</span> : null}
          <JobProgressPanel graphicEditor={<GraphicOverlayEditor taskId={task.id} versionId={task.current_timeline_version_id} items={workspace.montage?.graphic_overlays ?? []}
            selectedId={null} duration={workspace.montage?.duration_seconds ?? 0} busy={busy !== null}
            onPreview={input => void previewExactEdit(input)} onDraftChange={() => {invalidateEditPreview(); setEditorDraft(true);}} />}
            selectedRefs={selectedTaskReference ? [selectedTaskReference.ref, ...(activeView === "subtitle_cover" && activeSubtitleTrack ? [`subtitle-track:${activeSubtitleTrack.id}`] : [])] : []} restoreBusy={busy !== null} onRestoreMedia={candidate => void restoreMediaPreview(candidate)} recoveredMedia={recoveredMedia?.taskId === task.id && recoveredMedia.job.base_version_id === task.current_timeline_version_id ? recoveredMedia : null} mediaJob={pendingMediaJob?.taskId === task.id ? pendingMediaJob.value : null} onCancelMedia={() => void cancelMediaPreview()} showLiveStatus={!execution?.job || !["queued", "running"].includes(execution.job.status)} key={task.id} task={task} taskId={task.id} pendingEditJobId={pendingLocalEdit?.taskId === task.id ? pendingLocalEdit.jobId : null} onEditSettled={localEditSettled} refreshToken={`${execution?.job?.id ?? "none"}:${execution?.job?.status ?? "none"}:${execution?.stages.length ?? 0}`} />
        </header>
        <HomeCardDialog kind="detail" title="历史时间线版本" open={historyOpen} onClose={() => setHistoryOpen(false)} footer={<button type="button" onClick={() => setHistoryOpen(false)}>收起历史版本</button>}>
          <TimelineVersionHistory taskId={task.id} open={historyOpen} currentVersionId={task.current_timeline_version_id} restoreBusy={busy!==null || Boolean(pendingTimelineRestore)} onRestore={(operationId,redo)=>void undoTimeline(redo,operationId)} />
        </HomeCardDialog>
        {notice ? <div className={isEditor ? editorUi.feedback : styles.notice} role="status">{!pendingMetadataSave ? <CheckCircle2 size={17} /> : null}{notice}</div> : null}
        {pendingMetadataSave?.taskId === task.id ? <div role="status">保存结果待核对。原输入已保留，不会重复提交。<Button variant="outline" disabled={busy !== null} onClick={() => void queryMetadataSave()}>查询原保存结果</Button><details><summary>保存请求详情</summary><p>原请求：{pendingMetadataSave.input.request_id}</p></details></div> : null}
        {pendingTimelineRestore?.taskId===task.id ? <p role="status">撤销或重做请求等待核对。<Button variant="outline" disabled={busy!==null} onClick={()=>void queryTimelineRestore()}>查询原撤销重做结果</Button></p> : null}
        {error ? <TaskIssue stage={activeView} reason={error.message} impact={error.impact} repair={error.repairAction} code={error.code}>{error.code === "timeline_version_required" ? <button className={editorUi.button} disabled={!editRequestRecoveryReady || busy !== null || pendingMetadataSave !== null || pendingTimelineRestore !== null} onClick={() => { if (!editRequestRecoveryReady || editRequestOperation.current || pendingMetadataSaveRef.current || pendingTimelineRestoreRef.current) return; void run("clip", async () => { const clips = clipDrafts; const dirty = editorDraft; const saved = await initializeCreationTaskTimeline(task.id); if (currentTaskId.current === task.id && currentWorkspace.current.task.current_timeline_version_id === null) initializedDraft.current = { versionId: saved.version_id, clips, dirty }; }, "已为当前故事板建立初始版本，可继续预览裁剪。"); } } type="button">保留当前故事板并建立版本</button> : null}</TaskIssue> : null}
        {executionError ? <TaskIssue stage={task.stage} reason={executionError.code === "device_budget_unavailable" ? "这台设备还没有完成制作资源标定，暂时不能开始制作。" : executionError.message} impact={executionError.impact} repair={executionError.repairAction} code={executionError.code}>{executionError.code === "device_budget_unavailable" ? <><Link href="/health">检查运行情况</Link><details className={styles.traceDetails}><summary>制作资源诊断</summary><p>{executionError.message}</p></details></> : null}{task.confirmed_script_id ? <button className={styles.secondaryButton} disabled={busy !== null} onClick={() => void changeExecution(executionController.recheckRequirements)} type="button">重新核对文案和制作要求</button> : null}</TaskIssue> : null}
        {!error && !executionError && task.stage_status === "failed" && activeView === task.stage && (task.stage_reason || task.stage_impact) ? <TaskIssue stage={task.stage} reason={task.stage_reason} impact={task.stage_impact} repair={task.stage_repair_action} label={`${stageLabels[activeView]}失败`} /> : null}
        <NativeOperationsPanel key={`native-${task.id}`} taskId={task.id}
          adoptedRefs={(workspace.montage?.graphic_overlays ?? []).flatMap(overlay=>overlay.kind==='image'?[overlay.source_ref]:[])}
          canPrepare={busy===null && Boolean(task.current_timeline_version_id && workspace.montage && workspace.montage.duration_seconds>0)}
          onPreviewImage={artifact=>void previewExactEdit({operation_type:'EditGraphicOverlay',overlay_action:'add',graphic_overlay:{kind:'image',
            id:`native-${crypto.randomUUID()}`,source_ref:artifact.source_ref,name:'工作图片',start_seconds:0,
            end_seconds:Math.min(3,workspace.montage?.duration_seconds ?? 0),x:0.5,y:0.5,width:0.4,opacity:1}})}/>
        {task.confirmed_script_id && ["narration", "montage", "review"].includes(activeView) ? <><Button variant="ghost" aria-haspopup="dialog" aria-expanded={segmentsOpen} onClick={()=>setSegmentsOpen(true)}>分段产物与修改</Button><HomeCardDialog kind="detail" title="分段产物与修改" open={segmentsOpen} onClose={()=>setSegmentsOpen(false)}><SegmentProductionPanel key={task.id} taskId={task.id}/></HomeCardDialog></> : null}
        {activeView === "review" ? <div className={styles.reviewTabs} role="tablist" aria-label="成片工作区">
          {([ ["prepare", "制作准备"], ["result", "成片检查"] ] as const).map(([pane, label]) => <button key={pane} id={`review-${pane}-${task.id}`} role="tab" disabled={pane === "result" && !hasReviewOutput} aria-selected={reviewPane === pane} aria-controls={`review-panel-${pane}-${task.id}`} tabIndex={reviewPane === pane ? 0 : -1} onKeyDown={reviewTabKey} onClick={() => setReviewChoice({ taskId: task.id, pane })} type="button">{label}</button>)}
        </div> : null}
        <div className={activeView === "review" ? styles.reviewPane : styles.flowContents} role={activeView === "review" ? "tabpanel" : undefined} id={`review-panel-prepare-${task.id}`} aria-labelledby={activeView === "review" ? `review-prepare-${task.id}` : undefined} hidden={activeView === "review" && reviewPane !== "prepare"}>
        {task.confirmed_script_id && (executionPreview || execution?.job) && activeView === "review" ? <CreationExecutionPanel storyboard={{ ...executionController.storyboard,
          canSubmit: executionController.storyboard.canSubmit && storyboardInput !== null }}
          onRegenerateStoryboard={() => void changeStoryboard()} onReadStoryboard={() => void changeStoryboard(true)} visualDecision={executionController.visualDecision} visualBusy={executionController.visualBusy}
          visualError={executionController.visualError} visualReadRequired={executionController.visualReadRequired} onReadVisualDecision={executionController.readVisualDecision}
          onAdoptVisualDecision={executionController.adoptVisualDecision} onCloseVisualDecision={executionController.closeVisualDecision} taskStage={task.stage} taskReason={task.stage_reason} voice={workspace.plan.selected_voice} availableVoices={workspace.available_voices} compact={activeView !== "review"} preview={executionPreview} execution={execution} busy={busy !== null} onRefreshPreview={() => void changeExecution(executionController.recheckRequirements)} onStart={() => executionPreview && void changeExecution(() => startCreationExecution(task.id, executionPreview))} onResume={() => void changeExecution(() => resumeCreationExecution(task.id))} onCancel={() => void changeExecution(() => cancelCreationExecution(task.id))} onOpenMontage={() => openView("montage")} /> : null}{activeView === "review" && !executionPreview && !execution?.job ? <p className={styles.notice}>还没有制作安排。可以先查看文案、配音与剪辑。</p> : null}
        </div>

        {activeView === "topic" && task.stage === "topic" ? <TopicSelection topics={topics} selectedTopicId={selectedTopicId} topicTitleDraft={topicTitleDraft} knowledgeCount={workspace.plan.knowledge_references.length} sourceCount={workspace.plan.source_references.length} busy={busy}
          onSelect={(id, title) => { setSelectedTopicId(id); setTopicTitleDraft(title); }} onTitleChange={setTopicTitleDraft}
          onGenerate={() => void run("topics", () => generateCreationTaskTopics(task.id), "选题已生成，等待选择。")}
          onConfirm={() => { if (selectedTopicId) void run("topic-confirm", async () => {
            const saved = await confirmCreationTaskTopic(task.id, selectedTopicId, { title: topicTitleDraft.trim() });
            const confirmed = saved.candidates.find(candidate => candidate.id === saved.selected_topic_id);
            setNotice(confirmed ? `选题「${confirmed.title}」已确认，下一步生成文案。` : "选题已确认，下一步生成文案。");
          }); }} /> : null}
        {activeView === "topic" && task.stage !== "topic" ? <ConfirmedTopicView topic={workspace.selected_topic} evidence={workspace.topic_evidence} /> : null}

        {activeView === "script" && scriptGeneration.input ? <section className={styles.workSection}><ScriptGenerationStatus key={scriptGeneration.input.requestId}
          requestId={scriptGeneration.input.requestId} receipt={scriptGeneration.receipt} working={scriptGeneration.working}
          stopping={scriptGeneration.stopping} error={scriptGeneration.error} onRead={() => void scriptGeneration.query()} onStop={() => void scriptGeneration.stop()}/></section> : null}
        {activeView === "script" && !scriptGeneration.input && scriptGeneration.error ? <p role="alert">{scriptGeneration.error}</p> : null}
        {activeView === "script" && task.stage === "script" && !latestScript ? manualScriptOpen ? <ScriptWindowStart taskId={task.id} onSaved={async () => { await refresh(true); setManualScriptOpen(false); }}/> : <>
          <PlanReplyDraftAction busy={busy !== null} task={task} onComplete={async () => { await refresh(true); setNotice("已保存对话回复为待确认文案草稿；请补充当前资料引用和画面建议。"); }} />
          <section className={styles.workSection}><div className={styles.emptyAction}><FileText size={28} /><h2>生成文案</h2><p>使用已确认选题与固定资料。</p><button className="modern-primary-button" disabled={busy !== null || scriptGeneration.blocked} onClick={() => void scriptGeneration.generate()} type="button">{scriptGeneration.working ? <LoaderCircle className="is-spinning" size={16} /> : <Sparkles size={16} />}生成文案</button></div></section>
          <Button variant="outline" disabled={busy !== null} onClick={() => setManualScriptOpen(true)}>手动编写文案</Button>
        </> : null}
        {activeView === "script" && (task.stage === "script" || task.can_revise_script) && latestScript ? <ScriptEditor taskId={task.id} busy={busy !== null} key={windowScript ? `${task.id}:${latestScript.id}` : `${task.id}:${latestScript.id}:${latestScript.updated_at}`} onWindowSaved={async () => { await refresh(true); }} onConfirm={(draft) => run("script-confirm", () => confirmCreationTaskScript(task.id, draft.id, { title: draft.title, sections: draft.sections, expected_updated_at: draft.updated_at, edit_mode: "replace_all" }), "文案已确认，可以生成配音。") } repairing={task.stage_status === "failed" && latestScript.selection_status === "confirmed"} script={latestScript} /> : null}
        {activeView === "script" && task.stage !== "script" && !task.can_revise_script && latestScript ? <ReadonlyScriptsView scripts={workspace.scripts} confirmedScriptId={task.confirmed_script_id} /> : null}

        {activeView === "script" && latestScript?.selection_status === "confirmed" ? <ScriptLibrarySave key={latestScript.id} script={latestScript} onReloadSource={async () => { await refresh(true); }} /> : null}

        {pendingVoiceSelection ? <p role="status">声音采用请求等待核对。<Button variant="outline" type="button" disabled={busy !== null} onClick={() => void handleNarrationVoice("query")}>查询声音采用结果</Button>{voiceSelectionNotFound === pendingVoiceSelection.request_id ? <Button variant="outline" type="button" disabled={busy !== null} onClick={() => void handleNarrationVoice("replay")}>继续原声音采用请求</Button> : null}</p> : null}
        {activeView === "narration" && task.stage === "narration" && !workspace.narration ? (
          <NarrationPreparation taskId={task.id} canReviseScript={Boolean(task.can_revise_script)} scriptConfirmed={latestScript?.selection_status === "confirmed"} scriptSectionCount={workspace.scripts.find(script => script.id === task.confirmed_script_id)?.sections_total_count ?? 0} hasSelectedVoice={Boolean(workspace.plan.selected_voice)} voices={workspace.available_voices} selectedVoiceId={selectedVoiceId} recordings={recordings} recordingsError={recordingsError} busy={busy ?? (pendingVoiceSelection ? "voice-select" : null)}
            onRecord={() => setRecordingOpen(true)} onReviseScript={() => openView("script")} onAdoptRecording={recording => void adoptRecordedVoice(recording)} onVoiceChange={setSelectedVoiceId}
            onUseVoice={() => void handleNarrationVoice()} />
        ) : null}
        {activeView === "narration" && workspace.narration ? <NarrationResultView busy={busy !== null} onGeneratePersonRender={generatePersonRender} onSelect={selectNarrationSegment} selectedSegmentId={selectedNarrationSegmentId} narration={workspace.narration} personRenders={workspace.person_renders} hasPerson={Boolean(workspace.plan.selected_person)} taskId={task.id} /> : null}

        {activeView === "montage" && task.stage === "montage" && !workspace.montage ? <section className={styles.workSection}><div className={styles.emptyAction}><Clapperboard size={30} /><h2>准备视频剪辑</h2><p>在上方核对文案与要求后继续制作。</p></div></section> : null}

        {activeView === "subtitle_cover" && activeSubtitleTrack && workspace.montage ? <SubtitleWorkspace onSeekPreview={positionAudioListen} activeTrack={activeSubtitleTrack} busy={busy !== null || Boolean(pendingMetadataSave)} onDraftChange={() => invalidateEditPreview()} onPreviewExact={(input) => void previewExactEdit(input)} onSelectCover={selectCover} onSelectCue={selectCue} onSelectTrack={selectSubtitleTrack} onSaveCover={() => void saveCover()} onSaveSubtitle={(mode) => void saveSubtitle(mode)} onUndo={() => void undoTimeline()} onRedo={() => void undoTimeline(true)} selectedCover={selectedCover} selectedCueId={selectedCueId} taskId={task.id} versionId={task.current_timeline_version_id} canUndo={task.can_undo} canRedo={task.can_redo} cover={workspace.cover_asset} montage={workspace.montage} planAspectRatio={workspace.plan.aspect_ratio} subtitleTracks={workspace.subtitle_tracks} translationCapability={workspace.subtitle_translation_capability} frameCapability={workspace.cover_frame_capability} /> : null}

        <div className={activeView === "review" ? styles.reviewPane : styles.flowContents} role={activeView === "review" ? "tabpanel" : undefined} id={`review-panel-result-${task.id}`} aria-labelledby={activeView === "review" ? `review-result-${task.id}` : undefined} hidden={activeView === "review" && reviewPane !== "result"}>
        {activeView === "review" && task.stage === "review" && workspace.montage?.output_url ? <ReviewOutputView subtitleSummary={subtitleSummary} coverValid={workspace.cover_asset?.status === "valid"} canEditSubtitle={Boolean(activeSubtitleTrack)} reviewChecks={reviewChecks} busy={busy} onEditMontage={() => openView("montage")} onRecord={() => setRecordingOpen(true)} onEditSubtitles={() => openView("subtitle_cover")} onSave={() => void saveVideo()} /> : null}
        {(activeView === "review" || activeView === "montage") && workspace.montage ? <FinalVideoQualityCheck compact={isEditor} taskId={task.id} versionId={task.current_timeline_version_id} outputUrl={workspace.montage.output_url} onLocate={locateQualityFinding} /> : null}
        {activeView === "review" && workspace.montage?.status === "rendered" && task.current_timeline_version_id ? <SaveVideoTemplateForm key={task.current_timeline_version_id} taskId={task.id} onEditingChange={setSavingTemplate} /> : null}
        </div>
        {activeView === "prepare" ? <PrepareMaterialsView matches={workspace.plan.material_matches} approvedAssetCount={workspace.plan.approved_asset_count} approvedClipCount={workspace.plan.approved_clip_count} /> : null}
        {activeView === "person" ? <PersonStageView busy={busy !== null} onGenerate={generatePersonRender} onOpenNarration={() => openView("narration")} onSelect={selectNarrationSegment} selectedSegmentId={selectedNarrationSegmentId} narration={workspace.narration} renders={workspace.person_renders} selectedPerson={workspace.plan.selected_person} lipsyncQuality={workspace.plan.lipsync_quality} presenterShareTarget={workspace.plan.presenter_share_target ?? workspace.montage?.presenter_share_target ?? null} presenterShareActual={workspace.montage?.presenter_share_actual} capability={workspace.person_render_capability} portraitAvailable={workspace.portrait_available} /> : null}
        {activeView === "publish" ? <PublishStageView ready={Boolean(workspace.montage?.output_url)} onOpenReview={() => openView("review")} /> : null}
        {task.confirmed_script_id && (executionPreview || execution?.job) && ["script", "narration", "montage"].includes(activeView) ? <CreationExecutionPanel storyboard={{ ...executionController.storyboard,
          canSubmit: executionController.storyboard.canSubmit && storyboardInput !== null }}
          onRegenerateStoryboard={() => void changeStoryboard()} onReadStoryboard={() => void changeStoryboard(true)} visualDecision={executionController.visualDecision} visualBusy={executionController.visualBusy}
          visualError={executionController.visualError} visualReadRequired={executionController.visualReadRequired} onReadVisualDecision={executionController.readVisualDecision}
          onAdoptVisualDecision={executionController.adoptVisualDecision} onCloseVisualDecision={executionController.closeVisualDecision} taskStage={task.stage} taskReason={task.stage_reason} voice={workspace.plan.selected_voice} availableVoices={workspace.available_voices} compact preview={executionPreview} execution={execution} busy={busy !== null} onRefreshPreview={() => void changeExecution(executionController.recheckRequirements)} onStart={() => executionPreview && void changeExecution(() => startCreationExecution(task.id, executionPreview))} onResume={() => void changeExecution(() => resumeCreationExecution(task.id))} onCancel={() => void changeExecution(() => cancelCreationExecution(task.id))} onOpenMontage={() => openView("montage")} /> : null}
        {recordingOpen ? <HomeCardDialog title="电脑录音" onClose={() => setRecordingOpen(false)}>{workspace.scripts.filter(script => script.id === task.confirmed_script_id).map(script => <div key={script.id}><p>录音提词：以下按页显示已确认文案，不是全文载入。</p><ConfirmedScriptView script={script}/></div>)}<DesktopVoiceRecorder onRecorded={submitRecordedVoice} onCancel={() => setRecordingOpen(false)} /></HomeCardDialog> : null}
        </TabsContent>
      </Tabs>
    </AppShell>
  );
}

export { PreviewVideo } from "./creation-task/EditPreviewCard";
export { PlanReplyDraftAction } from "./creation-task/ScriptStage";
export { FinalVideoQualityCheck } from "./creation-task/FinalVideoQualityCheck";
