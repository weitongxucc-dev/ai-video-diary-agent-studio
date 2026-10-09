import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CreationTaskWorkspace, FinalVideoQualityCheck, PreviewVideo } from "../CreationTaskWorkspace";
import type { CreationExecutionPreview, CreationExecutionStatus, CreationTask, CreationTaskTopicsResponse, CreationTaskWorkspace as WorkspaceState } from "@/lib/types";
import { confirmCreationTaskTopic, setCreationTaskVisibility } from "@/lib/api";

const mocks = vi.hoisted(() => {
  class MockCreationPlanApiError extends Error {
    constructor(
      public readonly status: number,
      public readonly code: string | null,
      message: string,
      public readonly impact: string | null = null,
      public readonly repairAction: string | null = null,
    ) {
      super(message);
    }
  }
  return {
    MockCreationPlanApiError,
    showQuickInspector: false,
    realShell: false,
    openQuickDetails: vi.fn(),
    confirmEdit: vi.fn(),
    confirmScript: vi.fn(),
    exactEdit: vi.fn(),
    exactPreview: vi.fn(),
    createMontage: vi.fn(),
    generateNarration: vi.fn(),
    generatePersonRender: vi.fn(),
    getCandidates: vi.fn(),
    getWorkspace: vi.fn(),
    getQualityCheck: vi.fn().mockResolvedValue({ check_state: "none", result_status: null,
      stale: false, timeline_version_id: null, findings: [], unavailable: [] }),
    checkQuality: vi.fn(),
    initializeTimeline: vi.fn(),
    getAssistantHistory: vi.fn().mockResolvedValue({ items: [] }),
    getExecution: vi.fn().mockResolvedValue({ task_id: "task-1", job: null, stages: [], video_ready: false, cover_status: "pending", cover_message: null }),
  getJobProgress: vi.fn().mockResolvedValue({ task_id: "task-1", current: null, items: [], total_count: 0 }),
    previewExecution: vi.fn().mockResolvedValue(null),
    startExecution: vi.fn(),
    resumeExecution: vi.fn(),
    cancelExecution: vi.fn(),
    previewEdit: vi.fn(),
    previewSelection: vi.fn(),
    askAssistant: vi.fn(),
    renderMontage: vi.fn(),
    selectVoice: vi.fn(),
    undoTimeline: vi.fn(),
    redoTimeline: vi.fn(),
    saveRecording: vi.fn(),
    listRecordings: vi.fn().mockResolvedValue({ items: [] }),
    adoptRecording: vi.fn(),
    previewAudioEdit: vi.fn(),
    getProductionMaterials: vi.fn().mockResolvedValue({ assets: [], total_count: 0, limit: 200, offset: 0, has_more: false }),
    listTimelineVersions: vi.fn().mockResolvedValue({ task_id: "task-1", current_version_id: null, items: [] }),
    setCreationTaskVisibility: vi.fn(),
    setCreationPlanVisibility: vi.fn(),
    getCreationPlan: vi.fn().mockRejectedValue(new Error("no plan loaded in this test")),
    getTimelineVersion: vi.fn(),
  };
});

vi.mock("@/components/AppShell", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/components/AppShell")>();
  return { AppShell: (props: import("@/components/AppShell").AppShellProps) => {
    if (mocks.realShell) return <actual.AppShell {...props} />;
    const { children, conversation, inspector, inspectorConfirmation, quickInspector, quickInspectorLabel, toolbar, toolbarActions, inspectorTitle = "任务详情" } = props;
    return <><header>{toolbar}{toolbarActions}</header><section>{conversation}</section><main>{children}</main>{mocks.showQuickInspector && quickInspector ? <section aria-label={quickInspectorLabel}>{quickInspector(mocks.openQuickDetails)}</section> : null}{inspector ? <aside aria-label={inspectorTitle}>{inspector}</aside> : null}{inspectorConfirmation}</>;
  } };
});
vi.mock("next/navigation", () => ({ usePathname: () => "/tasks/task-1", useRouter: () => ({ push: vi.fn(), back: vi.fn() }), useSearchParams: () => new URLSearchParams() }));
vi.mock("@/hooks/useLocalMemberIdentity", () => ({ useLocalMemberIdentity: () => ({ role: "owner" }) }));
vi.mock("@/components/HelpPanel", () => ({ HelpPanel: () => null }));
vi.mock("@/components/KeyboardShortcuts", () => ({ KeyboardShortcuts: () => null }));

vi.mock("../DesktopVoiceRecorder", () => ({
  DesktopVoiceRecorder: ({ onRecorded, onCancel }: { onRecorded: (blob: Blob) => void | Promise<void>; onCancel: () => void }) => (
    <div data-testid="voice-recorder">
      <button onClick={() => void Promise.resolve(onRecorded(new Blob(["audio"], { type: "audio/webm" }))).catch(() => undefined)} type="button">提交录音</button>
      <button onClick={onCancel} type="button">取消录音</button>
    </div>
  ),
}));

vi.mock("@/lib/api", () => ({
  listCreationTasks: vi.fn().mockResolvedValue({ items: [], total_count: 0 }),
  apiUrl: (path: string) => path,
  getCreationTaskGraphicSources: vi.fn().mockResolvedValue([]),
  applyCreationTaskExactEdit: mocks.exactEdit,
  CreationPlanApiError: mocks.MockCreationPlanApiError,
  confirmCreationTaskEdit: mocks.confirmEdit,
  confirmCreationTaskScript: mocks.confirmScript,
  confirmCreationTaskTopic: vi.fn(),
  createCreationTaskMontagePlan: mocks.createMontage,
  generateCreationTaskNarration: mocks.generateNarration,
  generateCreationTaskPersonRender: mocks.generatePersonRender,
  generateCreationTaskScript: vi.fn(),
  generateCreationTaskTopics: vi.fn(),
  getCreationTaskMontageClipCandidates: mocks.getCandidates,
  getCreationTaskTopics: vi.fn(),
  getCreationTaskWorkspace: mocks.getWorkspace,
  getCreationTaskQualityCheck: mocks.getQualityCheck,
  checkCreationTaskQuality: mocks.checkQuality,
  getCreationPlan: mocks.getCreationPlan,
  setCreationTaskVisibility: mocks.setCreationTaskVisibility,
  setCreationPlanVisibility: mocks.setCreationPlanVisibility,
  initializeCreationTaskTimeline: mocks.initializeTimeline,
  getCreationTaskAssistantHistory: mocks.getAssistantHistory,
  getCreationExecution: mocks.getExecution,
  getTaskJobProgress: mocks.getJobProgress,
  previewCreationExecution: mocks.previewExecution,
  startCreationExecution: mocks.startExecution,
  resumeCreationExecution: mocks.resumeExecution,
  cancelCreationExecution: mocks.cancelExecution,
  getRuntimeSettings: vi.fn().mockResolvedValue({ status: { llm: { configured: true, provider: "minimax", model: "MiniMax-M3" } } }),
  sendCreationTaskAssistantTurn: mocks.askAssistant,
  previewCreationTaskEdit: mocks.previewEdit,
  previewCompoundSelection: mocks.previewSelection,
  previewCreationTaskExactEdit: mocks.exactPreview,
  renderCreationTaskMontage: mocks.renderMontage,
  selectCreationTaskNarrationVoice: mocks.selectVoice,
  undoCreationTaskTimeline: mocks.undoTimeline,
  redoCreationTaskTimeline: mocks.redoTimeline,
  saveCreationTaskRecording: mocks.saveRecording,
  listCreationTaskRecordings: mocks.listRecordings,
  adoptCreationTaskRecording: mocks.adoptRecording,
  previewCreationTaskAudio: mocks.previewAudioEdit,
  getProductionMaterials: mocks.getProductionMaterials,
  listCreationTaskTimelineVersions: mocks.listTimelineVersions,
  getCreationTaskTimelineVersion: mocks.getTimelineVersion,
}));

const baseTask: CreationTask = {
  visibility: "private",
  id: "task-1",
  workspace_id: "local",
  plan_id: "plan-1",
  name: "本地真成片",
  scenario: "制作一条中文口播视频",
  stage: "narration",
  stage_status: "failed",
  status: "failed",
  progress: 40,
  recovery_point: "script_confirmed",
  selected_topic_id: "topic-1",
  confirmed_script_id: "script-1",
  narration_cache_key: null,
  montage_plan_id: null,
  current_timeline_version_id: null,
  timeline_sequence: 0,
  can_undo: false,
  output_ready: false,
  stage_reason: "voice_identity_missing",
  stage_impact: "当前创作任务未生成配音，未调用声音模型。",
  stage_repair_action: "重新预检并选择已授权的中文声音版本。",
  stage_retryable: true,
  created_at: "2026-08-30T00:00:00Z",
  updated_at: "2026-08-30T00:00:00Z",
};

describe("persisted final video quality checks", () => {
  const completed = { check_state: "done", result_status: "issues_found", stale: false,
    timeline_version_id: "timeline-version-6", check_id: "check-1", render_ref: "hash",
    checked_at: null, error: null, validation_scope: "deterministic_media_errors_only",
    findings: [{ kind: "black_frame", start_seconds: 3.25, end_seconds: 4.1,
      duration_seconds: .85, detail: "真实黑场", clip_ids: ["clip-2"] }],
    unavailable: [{ kind: "clipping", reason: "音频无法测量", status: "unavailable" }] };

  it("shows identity read failure without claiming unchecked or triggering inspection", async () => {
    mocks.getQualityCheck.mockRejectedValueOnce(new Error("身份服务不可用"));
    render(<FinalVideoQualityCheck taskId="task-1" versionId="timeline-version-6"
      outputUrl={null} onLocate={vi.fn()} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("身份服务不可用");
    expect(screen.getByRole("status", { name: "成片质量检查状态" })).toHaveTextContent("检查结果读取失败");
    expect(screen.queryByText("当前成片尚未检查")).not.toBeInTheDocument();
    expect(screen.queryByText("未发现确定性媒体问题")).not.toBeInTheDocument();
    expect(mocks.checkQuality).not.toHaveBeenCalled();
  });

  it.each([
    [{ check_state: "checking", result_status: null }, "成片检查中"],
    [{ check_state: "failed", result_status: null, error: "解码失败" }, "成片检查失败"],
    [{ result_status: "unavailable", findings: [] }, "部分检测不可用"],
    [{ result_status: "clean", findings: [], unavailable: [] }, "未发现确定性媒体问题"],
    [{ stale: true }, "旧检查已失效，请检查当前成片"],
  ])("separates persisted states %s", async (overrides, label) => {
    mocks.getQualityCheck.mockResolvedValue({ ...completed, ...overrides });
    render(<FinalVideoQualityCheck taskId="task-1" versionId="timeline-version-6"
      outputUrl="/video" onLocate={vi.fn()} />);
    expect(await screen.findByText(label)).toBeInTheDocument();
    if (label !== "未发现确定性媒体问题") expect(screen.queryByText("未发现确定性媒体问题")).not.toBeInTheDocument();
    if (label.includes("失效")) expect(screen.queryByText("真实黑场")).not.toBeInTheDocument();
  });

  it("shows issues and unavailable together, seeks exact interval on current player and selects its clip", async () => {
    const initial = subtitleCoverWorkspace();
    const clip = { id: "clip-1", cue_id: "cue", asset_id: "asset", segment_id: "segment",
      source_reference: "workspace://clip.mp4", source_start_seconds: 0, source_end_seconds: 3,
      timeline_start_seconds: 0, timeline_end_seconds: 3, match_score: 1, match_reasons: [],
      needs_confirmation: false, role: "b_roll" as const, locked: false, manually_selected: false };
    initial.montage!.clips = [clip, { ...clip, id: "clip-2", timeline_start_seconds: 3, timeline_end_seconds: 6 }];
    mocks.getQualityCheck.mockResolvedValue(completed);
    render(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    fireEvent.click(screen.getByRole("button", { name: /^检查与输出：/ }));
    expect(await screen.findByText("成片发现问题")).toBeInTheDocument();
    expect(screen.getByText("检测不可用：音频无法测量")).toBeInTheDocument();
    const player = screen.getByLabelText("成片预览") as HTMLVideoElement;
    const pause = vi.spyOn(player, "pause").mockImplementation(() => undefined);
    fireEvent.click(screen.getByRole("button", { name: "定位 3.25–4.10 秒" }));
    expect(pause).toHaveBeenCalled();
    expect(player.currentTime).toBe(3.25);
    expect(screen.getByLabelText("局部修改要求")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "修改镜头" }));
    const editor = screen.getByLabelText("剪辑成片预览") as HTMLVideoElement;
    fireEvent.loadedMetadata(editor);
    expect(editor.currentTime).toBe(3.25);
    pause.mockRestore();
  });

  it("posts only task identity, polls checking to done and reloads persisted result after remount", async () => {
    mocks.getQualityCheck.mockResolvedValueOnce({ ...completed, check_state: "none", result_status: null,
      findings: [], unavailable: [] }).mockResolvedValue(completed);
    mocks.checkQuality.mockResolvedValue({ ...completed, check_state: "checking", result_status: null,
      findings: [], unavailable: [] });
    const props = { taskId: "task-1", versionId: "timeline-version-6", outputUrl: "/video", onLocate: vi.fn() };
    const view = render(<FinalVideoQualityCheck {...props} />);
    expect(await screen.findByText("当前成片尚未检查")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "重新检查成片" }));
    expect(await screen.findByText("成片检查中")).toBeInTheDocument();
    expect(mocks.checkQuality).toHaveBeenCalledWith("task-1");
    expect(await screen.findByText("成片发现问题", {}, { timeout: 2000 })).toBeInTheDocument();
    view.unmount();
    render(<FinalVideoQualityCheck {...props} />);
    expect(await screen.findByText("真实黑场")).toBeInTheDocument();
  });

  it("discards a late response after version change and never shows an old clean result", async () => {
    let resolve!: (value: unknown) => void;
    mocks.getQualityCheck.mockReturnValueOnce(new Promise(done => { resolve = done; }))
      .mockResolvedValue({ ...completed, stale: true, timeline_version_id: "v7", findings: [], unavailable: [] });
    const view = render(<FinalVideoQualityCheck taskId="task-1" versionId="timeline-version-6" outputUrl="/video" onLocate={vi.fn()} />);
    view.rerender(<FinalVideoQualityCheck taskId="task-1" versionId="v7" outputUrl={null} onLocate={vi.fn()} />);
    expect(await screen.findByText("旧检查已失效，请检查当前成片")).toBeInTheDocument();
    await act(async () => resolve({ ...completed, result_status: "clean", findings: [], unavailable: [] }));
    expect(screen.queryByText("未发现确定性媒体问题")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^定位/ })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重新检查成片" })).toBeDisabled();
  });

  it("shows clipping totals and separate evidence limits only when a current summary exists", async () => {
    const clipping_summary = {
      event_count: 2001, total_clipped_seconds: 20.01,
      first_start_seconds: 0, last_end_seconds: 40, full_scale_duty: .25,
      event_list_limit: 50, events: [{ start_seconds: 0, end_seconds: .01, duration_seconds: .01 }],
      events_truncated: true, truncated_event_count: 1951,
      findings_event_limit: 2000, findings_truncated: true, findings_truncated_count: 1,
    };
    mocks.getQualityCheck.mockResolvedValue({ ...completed, clipping_summary });
    const props = { taskId: "task-1", versionId: "timeline-version-6", outputUrl: "/video", onLocate: vi.fn() };
    const view = render(<FinalVideoQualityCheck {...props} />);
    const summary = await screen.findByLabelText("音频削平摘要");
    expect(summary).toHaveTextContent("音频削平共 2001 段，累计 20.01 秒。");
    expect(summary).toHaveTextContent("削平问题明细已限流至 2000 条，另有 1 段未逐条列出；总量包含这些事件。");
    expect(summary).toHaveTextContent("削平区间摘要仅保留 50 条，另有 1951 段未在区间摘要中列举；总量仍完整。");
    mocks.getQualityCheck.mockResolvedValue({ ...completed, clipping_summary: null });
    view.rerender(<FinalVideoQualityCheck {...props} outputUrl="/new-video" />);
    await waitFor(() => expect(screen.queryByLabelText("音频削平摘要")).not.toBeInTheDocument());
    expect(await screen.findByText("成片发现问题")).toBeInTheDocument();
    expect(screen.queryByText(/音频削平共/)).not.toBeInTheDocument();
    mocks.getQualityCheck.mockResolvedValue({ ...completed, clipping_summary, stale: true });
    view.rerender(<FinalVideoQualityCheck {...props} outputUrl="/stale-video" />);
    expect(await screen.findByText("旧检查已失效，请检查当前成片")).toBeInTheDocument();
    expect(screen.queryByLabelText("音频削平摘要")).not.toBeInTheDocument();
  });
});

function queuePreview(): CreationExecutionPreview {
  return {
    task_id: "task-1", script_id: "script-1", script_title: "真实文案",
    script_sections: [{ title: "开场", content: "这段就是实际要读的内容" }],
    plan_id: "plan-1", input_digest: "a".repeat(64),
    narration_source: null,
    requirements: { aspect_ratio: "9:16", duration_target_seconds: 30,
      presenter_share_target: 20, lipsync_quality: "high", selected_voice: "voice-1",
      can_continue: true, continuation_blocker: null, template_settings: {} },
  };
}

function queueStatus(status: NonNullable<CreationExecutionStatus["job"]>["status"] | null): CreationExecutionStatus {
  return { task_id: "task-1", job: status ? {
    id: "job-1", kind: "creation_task_execution", status, progress: 0,
    failure_reason: null, impact: null, repair_action: null,
  } : null, stages: [], current_stage: status === "running" ? "montage_plan" : null,
    waiting_reason: status === "queued" ? "设备资源正在处理其他工作。" : null,
    status_updated_at: "2026-10-07T00:00:00Z", video_ready: false, cover_status: "pending", cover_message: null };
}

function workspace(task: CreationTask, montage: WorkspaceState["montage"] = null): WorkspaceState {
  return {
    task,
    plan: {
      goal: task.scenario,
      name: task.name,
      target_count: 1,
      language: "zh-CN",
      aspect_ratio: "9:16",
      duration_target_seconds: 30,
      knowledge_references: [],
      source_references: [],
      selected_voice: null,
      pronunciation_references: [],
      approved_asset_count: 1,
      approved_clip_count: 1,
      material_rule_summary: "只用已审核素材",
      material_matches: [{ asset_ref: "asset:asset-1", segment_ref: "segment:segment-1", score: 0.9, reasons: ["已审核"] }],
      model_snapshot: [],
      confirmation_points: [],
      blockers: [],
      confirmable: true,
      preflight_fingerprint: "f".repeat(64),
    },
    selected_topic: null,
    topic_evidence: [],
    scripts: [],
    available_voices: [],
    narration: null,
    person_renders: [],
    person_render_capability: {
      available: true,
      model_id: "musetalk-local",
      message: "本地人物口型模型可用。",
      repair_action: null,
    },
    montage,
    subtitle_tracks: [],
    cover_asset: null,
    subtitle_translation_capability: {
      available: false,
      model_id: null,
      message: "当前未配置英文翻译模型。",
      repair_action: "在设置中连接文字模型。",
    },
    cover_frame_capability: {
      available: true,
      model_id: "ffmpeg+Pillow",
      message: "可从当前成片选帧生成封面。",
      repair_action: null,
    },
    image_generation_capability: {
      available: false,
      model_id: null,
      message: "尚未配置图片模型。",
      repair_action: "配置图片模型后再使用 AI 生图。",
    },
  };
}

function subtitleCoverWorkspace() {
  const task = {
    ...baseTask,
    stage: "review" as const,
    stage_status: "waiting_for_user" as const,
    status: "waiting_for_user" as const,
    progress: 90,
    current_timeline_version_id: "timeline-version-6",
    timeline_sequence: 6,
    can_undo: true,
    output_ready: true,
    stage_reason: null,
    stage_impact: null,
    stage_repair_action: null,
  };
  const result = workspace(task, {
    id: "montage-1", task_id: task.id, script_id: "script-1", script_title: "成片",
    status: "rendered", cues: [], clips: [], duration_seconds: 12,
    low_confidence_clip_count: 0, narration_reference: "narration://artifact/audio-1",
    output_url: `/api/creation-tasks/${task.id}/output`, subtitle_url: null,
    message: "成片已就绪", created_at: task.created_at, updated_at: task.updated_at,
  });
  result.subtitle_tracks = [{
    id: "subtitle-track-zh", workspace_id: "local", task_id: task.id, timeline_version_id: "timeline-version-6",
    language: "zh-CN", source_language: null,
    style: {
      name: "default", font_family: "PingFang SC", font_size: 40,
      primary_color: "#FFFFFF", emphasis_color: "#F0B35F", position: "bottom",
      bilingual_order: "source_then_translation",
    },
    cues: [{
      id: "subtitle-cue-1", source_cue_id: null, text: "真实运行不是口号",
      start_seconds: 0, end_seconds: 3.2, speaker: "主讲人", line_breaks: [], emphasis: [],
    }],
    generation_method: "timeline", model_id: null, status: "valid",
    artifact_ref: "artifact:subtitle:zh:v6", transform_id: "transform:subtitle:v6",
    failure_reason: null, impact: null, repair_action: null,
    created_at: task.created_at, updated_at: task.updated_at,
  }];
  return result;
}

function exactPreview(task: CreationTask, operationType: "EditAudioTimeline" | "EditCompoundTimeline" | "EditGraphicOverlay" | "SetClipTransition" | "ChangeCanvas" | "SplitSubtitleCue" | "MergeSubtitleCue" | "DeleteSubtitleCue" | "ReplaceSubtitleText" | "UpdateSubtitleCue" | "ConfigureSubtitleTrack" | "GenerateCoverFromFrame" | "ReplaceClip" | "InsertClip" | "ChangeCrop" | "TrimClip" | "ReorderClip" | "SetClipLock" | "ConfirmClip") {
  return {
    intent: {
      id: "intent-exact", workspace_id: "local", task_id: task.id, raw_command: "精确控件修改",
      context_stage: "subtitle_cover", selected_object_refs: [], playhead_ms: null,
      intent_type: "subtitle_cover_edit", scope: {}, constraints: {}, confidence: 1,
      status: "compiled", created_at: task.created_at,
    },
    proposal: {
      id: `proposal-${operationType}`, workspace_id: "local", task_id: task.id, intent_id: "intent-exact",
      summary: "已生成待确认的字幕与封面修改。", target_refs: ["timeline:timeline-version-6"],
      operation: { operation_type: operationType, payload: {} },
      invalidated_refs: ["artifact:subtitle:v6", "artifact:preview:v6"],
      preview: {
        before: { ref: "timeline:timeline-version-6", label: "当前版本", detail: "已保存" },
        after: { ref: "timeline:proposal", label: "待确认版本", detail: "只更新所选对象" },
      },
      external_effects: ["未确认前不写入时间线。"], status: "proposed" as const,
      created_at: task.created_at, updated_at: task.updated_at,
    },
  };
}

function workspaceWithCover() {
  const initial = subtitleCoverWorkspace();
  initial.cover_asset = {
    id: "cover-1", workspace_id: "local", task_id: initial.task.id, timeline_version_id: "timeline-version-6",
    source_video_ref: "artifact:video:v6", frame_time_seconds: 2.4, title: "别只讲概念",
    safe_area: { top: 144, right: 72, bottom: 180, left: 72 },
    layout: { title_position: "bottom", text_align: "center" }, generation_method: "video_frame",
    model_or_engine: "ffmpeg", status: "valid", artifact_ref: "artifact:cover:v6",
    transform_id: "transform:cover:v6", failure_reason: null, impact: null, repair_action: null,
    created_at: initial.task.created_at, updated_at: initial.task.updated_at,
  };
  return initial;
}

afterEach(() => {
  mocks.realShell = false;
  mocks.showQuickInspector = false;
  vi.clearAllMocks();
  mocks.getQualityCheck.mockReset().mockResolvedValue({ check_state: "none", result_status: null,
    stale: false, timeline_version_id: null, findings: [], unavailable: [] });
  mocks.checkQuality.mockReset();
  mocks.previewExecution.mockResolvedValue(null);
  mocks.getExecution.mockResolvedValue({ task_id: "task-1", job: null, stages: [], video_ready: false, cover_status: "pending", cover_message: null });
  mocks.getJobProgress.mockResolvedValue({ task_id: "task-1", current: null, items: [], total_count: 0 });
  mocks.getAssistantHistory.mockResolvedValue({ items: [] });
  Reflect.deleteProperty(window, "electronAPI");
});

it("stops the old trim interval when a playing preview receives a new source URL", () => {
  const frames: FrameRequestCallback[] = [];
  const animationFrame = vi.spyOn(window, "requestAnimationFrame").mockImplementation(callback => {
    frames.push(callback);
    return frames.length;
  });
  const cancelFrame = vi.spyOn(window, "cancelAnimationFrame").mockImplementation(() => {});
  const pause = vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => {});
  const props = { label: "裁剪画面", muted: false, pauseOthers: vi.fn() };
  const { rerender } = render(<PreviewVideo {...props} url="/source.mp4#t=0.500,2.500" />);
  const video = screen.getByLabelText("裁剪画面") as HTMLVideoElement;
  fireEvent.play(video);
  expect(frames).toHaveLength(1);
  rerender(<PreviewVideo {...props} url="/source.mp4#t=3.000,4.000" />);
  expect(screen.getByLabelText("裁剪画面")).toBe(video);
  expect(cancelFrame).toHaveBeenCalledWith(1);
  expect(pause).toHaveBeenCalled();
  expect(video.currentTime).toBe(3);
  fireEvent.loadedMetadata(video);
  expect(video.currentTime).toBe(3);
  pause.mockRestore();
  cancelFrame.mockRestore();
  animationFrame.mockRestore();
});

describe("CreationTaskWorkspace", () => {
  it("shows task script dialogue and refreshes the saved draft after the assistant reply", async () => {
    const task = { ...baseTask, stage: "script" as const, stage_status: "not_started" as const,
      status: "running" as const, confirmed_script_id: null };
    const initial = workspace(task);
    const draft = {
      id: "draft-1", task_id: task.id, run_id: "run-1", topic_id: "topic-1",
      title: "待确认文案", language: "zh-CN" as const, selection_status: "draft" as const,
      created_at: task.created_at, updated_at: task.updated_at, confirmed_at: null,
      sections: [{ id: "section-1", title: "开头", content: "真实草稿正文。",
        visual_prompt: "现场画面", evidence_reference_ids: ["knowledge:one:v1"] }],
    };
    mocks.getWorkspace.mockResolvedValue({ ...initial, scripts: [draft], task: {
      ...task, stage_status: "waiting_for_user", status: "waiting_for_user",
    } });
    mocks.askAssistant.mockResolvedValue({ reply: "草稿已保存，请检查。", provider: "minimax",
      model: "test", edit_preview: null, script_draft: draft });
    render(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    expect(screen.getByRole("heading", { name: "这段文案怎么写？" })).toBeInTheDocument();
    fireEvent.change(screen.getByRole("textbox", { name: "文案要求" }), { target: { value: "写一个简短开头" } });
    await waitFor(() => expect(screen.getByRole("button", { name: "发送" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    await waitFor(() => expect(mocks.askAssistant).toHaveBeenCalledWith("task-1", expect.objectContaining({
      message: "写一个简短开头", context_stage: "script",
    })));
    expect(await screen.findByText("真实草稿正文。", { exact: false })).toBeInTheDocument();
    expect(screen.getByText("草稿已保存，请检查。")).toBeInTheDocument();
  });

  it("restores task dialogue from persisted history after reopening", async () => {
    const initial = workspace({ ...baseTask, stage: "script", confirmed_script_id: null });
    mocks.getAssistantHistory.mockResolvedValue({ items: [
      { role: "user", message: "把开头缩短", stage: "script", script_id: null, at: baseTask.created_at },
      { role: "assistant", message: "已改好，请检查。", stage: "script", script_id: "draft-1", at: baseTask.updated_at },
    ] });
    render(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    expect(await screen.findByText("已改好，请检查。")).toBeInTheDocument();
    expect(mocks.getAssistantHistory).toHaveBeenCalledWith("task-1");
  });

  it("shows a saved question whose model reply was interrupted", async () => {
    const initial = workspace({ ...baseTask, stage: "script", confirmed_script_id: null });
    mocks.getAssistantHistory.mockResolvedValue({ items: [
      { role: "user", message: "把开头缩短", stage: "script", script_id: null, at: baseTask.created_at },
    ] });
    render(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    expect(await screen.findByText("上次没有得到完整回复。")).toBeInTheDocument();
    expect(screen.getByText("我：把开头缩短")).toBeInTheDocument();
  });

  it("starts only after showing the confirmed script and making requirements", async () => {
    const initial = workspace({ ...baseTask, stage: "narration", stage_status: "not_started", status: "running" });
    const preview = {
      task_id: "task-1", script_id: "script-1", script_title: "真实文案",
      script_sections: [{ title: "开场", content: "这段就是实际要读的内容" }],
      plan_id: "plan-1", input_digest: "a".repeat(64),
      narration_source: null,
      requirements: { aspect_ratio: "9:16", duration_target_seconds: 30,
        presenter_share_target: 20, lipsync_quality: "high", selected_voice: "voice-1",
        can_continue: true, continuation_blocker: null, template_settings: {} },
    };
    mocks.previewExecution.mockResolvedValue(preview);
    mocks.getWorkspace.mockResolvedValue(initial);
    const queuedExecution = { task_id: "task-1", job: {
      id: "job-1", kind: "creation_task_execution", status: "queued", progress: 0,
      failure_reason: null, impact: null, repair_action: null,
    }, stages: [], current_stage: null, waiting_reason: "设备资源正在处理其他工作。",
    status_updated_at: "2026-09-27T11:00:00+00:00",
    video_ready: false, cover_status: "pending", cover_message: null };
    mocks.startExecution.mockResolvedValue(queuedExecution);
    mocks.getExecution.mockResolvedValueOnce({ task_id: "task-1", job: null, stages: [],
      video_ready: false, cover_status: "pending", cover_message: null }).mockResolvedValue(queuedExecution);
    render(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    await waitFor(() => expect(screen.getByText(/这段就是实际要读的内容/)).toBeInTheDocument());
    expect(screen.getByText(/露脸目标 20%/)).toBeInTheDocument();
    expect(mocks.startExecution).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "确认以上文案和要求，开始制作" }));
    await waitFor(() => expect(mocks.startExecution).toHaveBeenCalledWith("task-1", preview));
    expect(await screen.findByLabelText("制作安排状态")).toHaveTextContent("设备资源正在处理其他工作。");
    expect(mocks.generateNarration).not.toHaveBeenCalled();
  });

  it("keeps the editor player and draft mounted while reviewing production details without starting", async () => {
    const initial = subtitleCoverWorkspace();
    initial.task.stage = "montage";
    initial.montage!.clips = [{ id: "clip-1", cue_id: "cue-1", asset_id: "asset-1", segment_id: "segment-1",
      source_reference: "workspace://clip.mp4", source_start_seconds: 0, source_end_seconds: 3,
      timeline_start_seconds: 0, timeline_end_seconds: 3, match_score: 1, match_reasons: [],
      needs_confirmation: false, role: "b_roll", locked: false, manually_selected: false }];
    mocks.getWorkspace.mockResolvedValue(initial);
    mocks.getExecution.mockResolvedValue(queueStatus(null));
    mocks.previewExecution.mockResolvedValue(queuePreview());
    render(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    const entry = await screen.findByRole("button", { name: "查看制作详情" });
    expect(screen.queryByRole("region", { name: "连续制作" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "确认以上文案和要求，开始制作" })).not.toBeInTheDocument();
    const player = screen.getByLabelText("剪辑成片预览") as HTMLVideoElement;
    fireEvent.click(screen.getByRole("button", { name: "选择镜头 1" }));
    player.currentTime = 1.25;
    fireEvent.change(screen.getByLabelText("局部修改要求"), { target: { value: "未发送的剪辑草稿" } });
    fireEvent.click(entry);
    const panel = await screen.findByRole("region", { name: "连续制作" });
    expect(panel).toHaveTextContent("这段就是实际要读的内容");
    expect(panel).toHaveTextContent("画幅 9:16 · 目标时长 30 秒 · 露脸目标 20%");
    expect(within(panel).getByRole("button", { name: "确认以上文案和要求，开始制作" })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "收起制作详情" }));
    expect(screen.getByLabelText("剪辑成片预览")).toBe(player);
    expect(player.currentTime).toBe(1.25);
    expect(screen.getByRole("button", { name: "选择镜头 1" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByLabelText("局部修改要求")).toHaveValue("未发送的剪辑草稿");
    expect(screen.getByText(/版本 6/)).toBeInTheDocument();
    fireEvent.click(entry);
    expect(screen.getByRole("region", { name: "连续制作" })).toBe(panel);
    expect(mocks.startExecution).not.toHaveBeenCalled();
    expect(mocks.resumeExecution).not.toHaveBeenCalled();
    expect(mocks.renderMontage).not.toHaveBeenCalled();
  });

  it.each([
    ["narration", "queued"], ["storyboard", "running"], ["video", "queued"],
  ] as const)("uses the same confirmed queue from %s and shows %s without claiming an artifact", async (phase, status) => {
    const task = { ...baseTask, stage: phase === "narration" ? "narration" as const : "montage" as const,
      stage_status: "not_started" as const, status: "running" as const, stage_reason: null, stage_impact: null };
    const initial = phase === "video" ? subtitleCoverWorkspace() : workspace(task);
    initial.task = task;
    if (initial.montage) initial.montage = { ...initial.montage, status: "ready", output_url: null };
    const preview = queuePreview();
    const execution = queueStatus(status);
    mocks.previewExecution.mockResolvedValue(preview);
    mocks.getWorkspace.mockResolvedValue(initial);
    mocks.getExecution.mockResolvedValueOnce(queueStatus(null)).mockResolvedValue(execution);
    mocks.startExecution.mockResolvedValue(execution);
    mocks.cancelExecution.mockResolvedValue(queueStatus("cancelled"));
    render(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    if (phase === "video") fireEvent.click(await screen.findByRole("button", { name: "查看制作详情" }));
    const panel = await screen.findByRole("region", { name: "连续制作" });
    expect(panel).toHaveTextContent("这段就是实际要读的内容");
    expect(panel).toHaveTextContent("这次制作要求");
    expect(panel).toHaveTextContent("露脸目标 20%");
    expect(mocks.startExecution).not.toHaveBeenCalled();
    expect(screen.queryByRole("button", { name: /^(单步)?生成(配音|故事板|成片)$/ })).not.toBeInTheDocument();
    fireEvent.click(within(panel).getByRole("button", { name: "确认以上文案和要求，开始制作" }));
    await waitFor(() => expect(mocks.startExecution).toHaveBeenCalledWith("task-1", preview));
    expect(await within(panel).findByRole("heading", { name: status === "queued" ? "已排队" : "后台制作中" })).toBeInTheDocument();
    expect(screen.getByLabelText("制作安排状态")).toHaveTextContent(status === "queued" ? "设备资源正在处理其他工作。" : "安排镜头与人物口型");
    expect(panel).not.toHaveTextContent("成片已生成");
    expect(panel).not.toHaveTextContent("真实配音已生成");
    if (phase === "video") {
      expect(screen.getByLabelText("镜头故事板")).toBeInTheDocument();
      fireEvent.click(within(panel).getByRole("button", { name: "停止制作" }));
      await waitFor(() => expect(mocks.cancelExecution).toHaveBeenCalledWith("task-1"));
    }
    expect(mocks.generateNarration).not.toHaveBeenCalled();
    expect(mocks.createMontage).not.toHaveBeenCalled();
    expect(mocks.renderMontage).not.toHaveBeenCalled();
  });

  it("confirms the script and requirements again before resuming a failed queue in montage", async () => {
    const initial = subtitleCoverWorkspace();
    initial.task = { ...initial.task, stage: "montage", status: "failed", stage_status: "failed" };
    const failed = queueStatus("failed");
    failed.job!.failure_reason = "输出磁盘写入失败";
    failed.job!.repair_action = "检查输出目录的可用空间后继续制作。";
    failed.current_stage = "person_render";
    failed.stages = [{ stage: "narration", status: "completed", at: "2026-10-07T00:00:00Z" },
      { stage: "montage_plan", status: "reused", at: "2026-10-07T00:00:01Z" },
      { stage: "person_render", status: "completed", at: "2026-10-07T00:00:02Z" }];
    const recordingPreview: CreationExecutionPreview = {
      ...queuePreview(),
      narration_source: { source_kind: "recorded", source_artifact_ref: "artifact:task-1/recording-1", duration_seconds: 4.2 },
      requirements: { ...queuePreview().requirements, presenter_share_target: 0, selected_voice: null,
        can_continue: true, continuation_blocker: null },
    };
    mocks.previewExecution.mockResolvedValue(recordingPreview);
    mocks.getExecution.mockResolvedValue(failed);
    mocks.getWorkspace.mockResolvedValue(initial);
    mocks.resumeExecution.mockResolvedValue(queueStatus("queued"));
    render(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    expect(await screen.findByRole("region", { name: "制作状态" })).toHaveTextContent("输出磁盘写入失败");
    expect(screen.getByRole("region", { name: "制作状态" })).toHaveTextContent("人物口型");
    fireEvent.click(screen.getByRole("button", { name: "查看制作详情" }));
    const panel = await screen.findByRole("region", { name: "连续制作" });
    expect(panel).toHaveTextContent("这段就是实际要读的内容");
    expect(panel).toHaveTextContent("输出磁盘写入失败");
    expect(panel).toHaveTextContent("检查输出目录的可用空间后继续制作。");
    expect(panel).toHaveTextContent("沿用有效结果");
    expect(panel).toHaveTextContent("人物口型：已完成");
    expect(panel).not.toHaveTextContent("person_render");
    expect(mocks.resumeExecution).not.toHaveBeenCalled();
    fireEvent.click(within(panel).getByRole("button", { name: "确认以上文案和要求，继续制作" }));
    await waitFor(() => expect(mocks.resumeExecution).toHaveBeenCalledWith("task-1"));
    expect(mocks.startExecution).not.toHaveBeenCalled();
    expect(mocks.generateNarration).not.toHaveBeenCalled();
    expect(mocks.createMontage).not.toHaveBeenCalled();
    expect(mocks.renderMontage).not.toHaveBeenCalled();
  });

  it("keeps the first preflight cause and repair visible without hiding the montage or offering a legacy fallback", async () => {
    const initial = subtitleCoverWorkspace();
    initial.task = { ...initial.task, stage: "montage", status: "failed", stage_status: "failed" };
    mocks.getWorkspace.mockResolvedValue(initial);
    mocks.getExecution.mockResolvedValue(queueStatus("failed"));
    mocks.previewExecution.mockRejectedValue(new mocks.MockCreationPlanApiError(503,
      "device_budget_unavailable", "配音预算未标定", null, "先标定本机配音资源预算，再重试。"));
    render(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("配音预算未标定 先标定本机配音资源预算，再重试。");
    expect(screen.getByLabelText("镜头故事板")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "查看制作详情" }));
    expect(screen.getByRole("button", { name: "确认以上文案和要求，继续制作" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: "生成成片" })).not.toBeInTheDocument();
    expect(mocks.startExecution).not.toHaveBeenCalled();
    expect(mocks.resumeExecution).not.toHaveBeenCalled();
    expect(mocks.renderMontage).not.toHaveBeenCalled();
    mocks.previewExecution.mockResolvedValue(queuePreview());
    fireEvent.click(screen.getByRole("button", { name: "重新核对文案和制作要求" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "确认以上文案和要求，继续制作" })).toBeEnabled());
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByLabelText("镜头故事板")).toBeInTheDocument();
    expect(mocks.resumeExecution).not.toHaveBeenCalled();
  });

  it("keeps start unavailable until an authorized voice is selected", async () => {
    const initial = workspace({ ...baseTask, stage: "narration", stage_status: "not_started", status: "running" });
    mocks.getWorkspace.mockResolvedValue(initial);
    mocks.previewExecution.mockResolvedValue({
      task_id: "task-1", script_id: "script-1", script_title: "已确认文案",
      script_sections: [{ title: "正文", content: "实际内容" }],
      plan_id: "plan-1", input_digest: "b".repeat(64),
      narration_source: null,
      requirements: { aspect_ratio: "9:16", duration_target_seconds: 30,
        presenter_share_target: 0, lipsync_quality: "fast", selected_voice: null,
        can_continue: false, continuation_blocker: "请先选择已授权声音，或保存并明确采用本人录音；本人录音仅支持纯素材成片。",
        template_settings: {} },
    });
    render(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    expect(await screen.findByText("请先选择已授权声音，或保存并明确采用本人录音；本人录音仅支持纯素材成片。")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "暂不能开始制作" })).toBeDisabled();
    expect(mocks.startExecution).not.toHaveBeenCalled();
  });

  it("enables pure-material continuation for a currently adopted recording and shows its true source", async () => {
    const initial = workspace({ ...baseTask, stage: "narration", stage_status: "not_started", status: "running",
      narration_cache_key: "recorded-narration" });
    initial.narration = {
      task: initial.task, script_id: "script-1", audio_reference: "narration://artifact/recorded-narration",
      duration_seconds: 4.2, sample_rate: 16000, model_id: null, voice_profile_id: null, source_kind: "recorded",
      source_artifact_ref: "artifact:task-1/recording-1", source_sha256: "a".repeat(64),
      source_script_digest: "b".repeat(64), language: "zh", pronunciation_version: "",
      segments: [{ id: "segment-1", source_section_id: "section-1", text: "本人录制的段落", audio_reference: "narration://artifact/recorded-narration",
        audio_url: null, duration_seconds: 4.2, sample_rate: 16000, model_id: null, voice_profile_id: null,
        voice_version: null, language: "zh", pronunciation_version: "", status: "ready", source_kind: "recorded",
        source_artifact_ref: "artifact:task-1/recording-1", source_sha256: "a".repeat(64), source_script_digest: "b".repeat(64) }],
    };
    mocks.getWorkspace.mockResolvedValue(initial);
    mocks.previewExecution.mockResolvedValue({
      ...queuePreview(), narration_source: { source_kind: "recorded", source_artifact_ref: "artifact:task-1/recording-1", duration_seconds: 4.2 },
      requirements: { ...queuePreview().requirements, presenter_share_target: 0, selected_voice: null,
        can_continue: true, continuation_blocker: null },
    });
    render(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    expect(await screen.findByRole("button", { name: "确认以上文案和要求，开始制作" })).toBeEnabled();
    expect(screen.getByText(/配音来源：本人录音 · 已采用 · 4\.2 秒/)).toBeInTheDocument();
    expect(screen.getByText("本人录音 · 已采用")).toBeInTheDocument();
    expect(screen.getByText("artifact:task-1/recording-1")).toBeInTheDocument();
    expect(screen.queryByText("未就绪")).not.toBeInTheDocument();
  });

  it.each(["local", "delete", "cross-object"])("keeps the real shell single-clip scope %s on its actual confirmation surface", async (scope) => {
    mocks.realShell = true;
    Object.defineProperty(window, "matchMedia", { configurable: true, value: () => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() }) });
    const initial = subtitleCoverWorkspace();
    initial.montage!.clips = [{
      id: "clip-1", cue_id: "cue-1", asset_id: "asset-1", segment_id: "segment-1",
      source_reference: "workspace://clip.mp4", source_start_seconds: 0, source_end_seconds: 3,
      timeline_start_seconds: 0, timeline_end_seconds: 3, match_score: .9,
      match_reasons: [], needs_confirmation: false, role: "b_roll", locked: false, manually_selected: false,
    }];
    const preview = exactPreview(initial.task, "ReplaceClip");
    preview.proposal.target_refs = ["clip:clip-1"];
    preview.proposal.external_effects = ["确认后只更新任务版本，不会立即调用模型或渲染。"];
    if (scope === "delete") Object.assign(preview.proposal.operation, { operation_type: "DeleteClip", payload: { clip_id: "clip-1" } });
    if (scope === "cross-object") preview.proposal.target_refs.push("clip:clip-2");
    preview.intent.raw_command = "换成产品特写";
    mocks.askAssistant.mockResolvedValue({ reply: "已生成镜头替换预览，请确认。", provider: "minimax", model: "MiniMax-M3", edit_preview: preview });
    render(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    fireEvent.click(screen.getByRole("button", { name: /^检查与输出：/ }));
    fireEvent.click(screen.getByRole("button", { name: "修改镜头" }));
    fireEvent.click(screen.getByRole("button", { name: "选择镜头 1" }));
    expect(screen.queryByRole("dialog", { name: "镜头 01" })).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("局部修改要求"), { target: { value: "换成产品特写" } });
    await waitFor(() => expect(screen.getByRole("button", { name: "发送" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    await waitFor(() => expect(mocks.askAssistant).toHaveBeenCalledWith("task-1", expect.objectContaining({
      message: "换成产品特写", selected_object_refs: ["clip:clip-1"],
    })));
    expect(await screen.findByLabelText("变更预览")).toBeInTheDocument();
    const confirmation = screen.getByRole("dialog", { name: "修改提案" });
    const detail = screen.queryByRole("dialog", { name: "镜头 01" });
    if (scope === "local") {
      expect(detail).toHaveAttribute("data-card-kind", "detail");
      expect(detail?.inert).not.toBe(true);
      expect(confirmation).toHaveAttribute("data-card-kind", "config");
      expect(confirmation).not.toHaveAttribute("aria-modal");
      expect(screen.getByRole("button", { name: "返回详情" })).toHaveFocus();
    } else {
      expect(detail).toBeNull();
      expect(confirmation).toHaveAttribute("data-card-kind", "modal");
      expect(confirmation).toHaveAttribute("aria-modal", "true");
      expect(screen.getByRole("button", { name: "取消" })).toHaveFocus();
    }
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: "修改提案" })).not.toBeInTheDocument();
    if (scope === "local") {
      expect(screen.getByRole("dialog", { name: "镜头 01" })).toBe(detail);
      expect(detail?.inert).not.toBe(true);
    } else {
      expect(mocks.confirmEdit).not.toHaveBeenCalled();
    }
    expect(mocks.confirmEdit).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    await screen.findByRole("dialog", { name: "修改提案" });
    expect(mocks.confirmEdit).not.toHaveBeenCalled();
    expect(screen.getAllByText("已生成镜头替换预览，请确认。").length).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole("button", { name: scope === "delete" ? "确认删除" : "应用到草稿" }));
    await waitFor(() => expect(mocks.confirmEdit).toHaveBeenCalledWith("task-1", "proposal-ReplaceClip"));
  });
  it.each(["succeeded", "failed", "cancelled"])("keeps a queued person edit unapplied until persisted job %s is read", async (status) => {
    const initial = subtitleCoverWorkspace();
    initial.montage!.clips = [{
      id: "clip-1", cue_id: "cue-1", asset_id: "asset-1", segment_id: "segment-1",
      source_reference: "workspace://clip.mp4", source_start_seconds: 0, source_end_seconds: 3,
      timeline_start_seconds: 0, timeline_end_seconds: 3, match_score: .9,
      match_reasons: [], needs_confirmation: false, role: "b_roll", locked: false, manually_selected: false,
    }];
    const preview = exactPreview(initial.task, "ReplaceClip");
    Object.assign(preview.proposal.operation, { operation_type: "ChangePersonSegment", payload: { clip_id: "clip-1" } });
    preview.proposal.target_refs = ["clip:clip-1"];
    mocks.askAssistant.mockResolvedValue({ reply: "人物修改待确认。", provider: "minimax", model: "MiniMax-M3", edit_preview: preview });
    mocks.getWorkspace.mockResolvedValue(initial);
    let submitted = false;
    let readTerminal: (value: unknown) => void = () => {};
    const terminal = new Promise(resolve => { readTerminal = resolve; });
    mocks.getJobProgress.mockImplementation(() => submitted ? terminal : Promise.resolve({ task_id: "task-1", current: null, items: [], total_count: 0 }));
    mocks.confirmEdit.mockImplementation(async () => {
      submitted = true;
      return { status: "queued", job_id: "person-edit-job", request_id: "device-request" };
    });
    render(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    fireEvent.click(screen.getByRole("button", { name: /^检查与输出：/ }));
    fireEvent.click(screen.getByRole("button", { name: "修改镜头" }));
    fireEvent.click(screen.getByRole("button", { name: "选择镜头 1" }));
    fireEvent.change(screen.getByLabelText("局部修改要求"), { target: { value: "这一段换成当前人物出镜" } });
    await waitFor(() => expect(screen.getByRole("button", { name: "发送" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    await screen.findByRole("dialog", { name: "修改提案" });
    fireEvent.click(screen.getByRole("button", { name: "应用到草稿" }));
    expect(await screen.findByText("人物修改已排队，取得设备许可并完成后才更新目标镜头；当前版本保持不变。")).toBeInTheDocument();
    expect(screen.queryByText(/人物修改已完成/)).not.toBeInTheDocument();
    expect(screen.getByText(/版本 6$/)).toBeInTheDocument();
    const refreshCount = mocks.getWorkspace.mock.calls.length;
    const finalState = status === "succeeded" ? { ...initial, task: { ...initial.task, current_timeline_version_id: "timeline-version-7", timeline_sequence: 7 } } : initial;
    mocks.getWorkspace.mockResolvedValue(finalState);
    const job = { id: "person-edit-job", kind: "creation_task_person_edit", status,
      progress: status === "succeeded" ? 100 : 0, attempt: 1, started_at: initial.task.created_at,
      finished_at: initial.task.updated_at, updated_at: initial.task.updated_at, elapsed_seconds: 1,
      failure_reason: status === "succeeded" ? null : "人物授权已失效", impact: "旧版本保持不变",
      repair_action: "修复授权后重新预览", wait_estimate_seconds: null, processing_estimate_seconds: null, estimate_basis: null };
    await act(async () => { readTerminal({ task_id: "task-1", current: job, items: [job], total_count: 1 }); });
    await waitFor(() => expect(mocks.getWorkspace.mock.calls.length).toBeGreaterThan(refreshCount));
    expect(screen.getByText(status === "succeeded" ? /版本 7$/ : /版本 6$/)).toBeInTheDocument();
    if (status === "succeeded") expect(await screen.findByText("人物修改已完成，目标镜头和当前版本已更新；成片尚未重新生成。")).toBeInTheDocument();
    else {
      expect(await screen.findByText("人物修改未完成，当前版本保持不变；请查看作业原因。")).toBeInTheDocument();
      expect(screen.getByLabelText("作业进度")).toHaveTextContent("人物授权已失效");
      expect(screen.queryByText(/人物修改已完成/)).not.toBeInTheDocument();
    }
    expect(mocks.generatePersonRender).not.toHaveBeenCalled();
    expect(mocks.renderMontage).not.toHaveBeenCalled();
  });
  it("selects the playhead clip, keeps its position and discards a late preview for another clip", async () => {
    const initial = subtitleCoverWorkspace();
    const clip = {
      id: "clip-1", cue_id: "cue-1", asset_id: "asset-1", segment_id: "segment-1",
      source_reference: "workspace://clip.mp4", source_start_seconds: 0, source_end_seconds: 3,
      timeline_start_seconds: 0, timeline_end_seconds: 3, match_score: .9,
      match_reasons: [], needs_confirmation: false, role: "b_roll" as const,
      locked: false, manually_selected: false,
    };
    initial.montage!.clips = [clip, { ...clip, id: "clip-2", timeline_start_seconds: 3, timeline_end_seconds: 6 }];
    render(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    fireEvent.click(screen.getByRole("button", { name: /^检查与输出：/ }));
    const player = screen.getByLabelText("成片预览") as HTMLVideoElement;
    player.currentTime = 3;
    fireEvent.seeked(player);
    expect(screen.getByLabelText("局部修改要求")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "修改镜头" }));
    const editor = screen.getByLabelText("剪辑成片预览") as HTMLVideoElement;
    fireEvent.loadedMetadata(editor);
    expect(editor.currentTime).toBe(3);
    expect(screen.getByRole("button", { name: "选择镜头 2" })).toHaveAttribute("aria-pressed", "true");
    let resolvePreview!: (value: ReturnType<typeof exactPreview>) => void;
    mocks.previewEdit.mockReturnValue(new Promise((resolve) => { resolvePreview = resolve; }));
    fireEvent.change(screen.getByLabelText("局部修改要求"), { target: { value: "换成另一个镜头" } });
    fireEvent.click(screen.getByRole("button", { name: "预览修改" }));
    fireEvent.click(screen.getByRole("button", { name: "选择镜头 1" }));
    await act(async () => resolvePreview(exactPreview(initial.task, "ReplaceClip")));
    expect(screen.queryByLabelText("变更预览")).not.toBeInTheDocument();
    expect(editor.currentTime).toBe(1.5);
    expect(screen.getByRole("button", { name: "选择镜头 1" })).toHaveAttribute("aria-pressed", "true");
    editor.currentTime = 6;
    fireEvent.pause(editor);
    expect(screen.getByRole("button", { name: "选择镜头 2" })).toHaveAttribute("aria-pressed", "true");
    expect(mocks.confirmEdit).not.toHaveBeenCalled();
  });

  it("restores server line breaks, equal-length emphasis and cover fields after undo", async () => {
    const initial = workspaceWithCover();
    initial.subtitle_tracks[0].cues[0].emphasis = ["真实运行"];
    const restored = structuredClone(initial);
    restored.subtitle_tracks[0].cues[0].line_breaks = [4];
    restored.subtitle_tracks[0].cues[0].emphasis = ["不是口号"];
    restored.cover_asset!.title = "恢复后的标题";
    restored.cover_asset!.frame_time_seconds = 1.2;
    restored.cover_asset!.layout.title_position = "center";
    mocks.undoTimeline.mockResolvedValue({});
    mocks.getWorkspace.mockResolvedValue(restored);
    render(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    fireEvent.click(screen.getByRole("button", { name: "字幕与封面：已完成" }));
    fireEvent.change(screen.getByLabelText("字幕 1 文本"), { target: { value: "未保存草稿" } });
    fireEvent.change(screen.getByLabelText("封面标题"), { target: { value: "未保存封面" } });
    fireEvent.click(screen.getByRole("button", { name: "撤销" }));
    await waitFor(() => expect(screen.getByLabelText("字幕 1 文本")).toHaveValue("真实运行\n不是口号"));
    expect(screen.getByLabelText("字幕 1 强调词")).toHaveValue("不是口号");
    await waitFor(() => expect(screen.getByLabelText("封面标题")).toHaveValue("恢复后的标题"));
    expect(screen.getByLabelText("封面画面时间")).toHaveValue(1.2);
    expect(within(screen.getByRole("group", { name: "封面标题位置" })).getByRole("button", { name: "中部" })).toHaveAttribute("aria-pressed", "true");
  });

  it("uses persisted cues when switching tracks and selecting another cue", () => {
    const initial = subtitleCoverWorkspace();
    const chinese = initial.subtitle_tracks[0];
    chinese.cues.push({ ...chinese.cues[0], id: "subtitle-cue-2", text: "第二条字幕", line_breaks: [3] });
    initial.subtitle_tracks.push({ ...structuredClone(chinese), id: "subtitle-track-en", language: "en-US", cues: [{ ...chinese.cues[0], id: "subtitle-en-1", text: "Real work" }] });
    render(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    fireEvent.click(screen.getByRole("button", { name: "字幕与封面：待补封面" }));
    fireEvent.change(screen.getByLabelText("字幕 1 文本"), { target: { value: "未保存" } });
    fireEvent.click(screen.getByRole("tab", { name: /英文轨/ }));
    expect(screen.getByLabelText("字幕 1 文本")).toHaveValue("Real work");
    expect(screen.getByLabelText("字幕字号")).toBeEnabled();
    expect(screen.getByText("全部字幕样式")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: /中文轨/ }));
    expect(screen.getByLabelText("字幕 1 文本")).toHaveValue("真实运行不是口号");
    fireEvent.change(screen.getByLabelText("选择字幕"), { target: { value: "subtitle-cue-2" } });
    expect(screen.getByLabelText("字幕 2 文本")).toHaveValue("第二条\n字幕");
  });

  it("saves the persisted cover through ids and reports cancellation and failure", async () => {
    const initial = workspaceWithCover();
    const saveCoverImage = vi.fn()
      .mockResolvedValueOnce({ saved: true, fileName: "封面.png" })
      .mockResolvedValueOnce({ saved: false, canceled: true })
      .mockResolvedValueOnce({ saved: false, error: "目标目录不可写" });
    Object.defineProperty(window, "electronAPI", { configurable: true, value: { saveCoverImage } });
    render(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    fireEvent.click(screen.getByRole("button", { name: "字幕与封面：已完成" }));
    fireEvent.click(screen.getByRole("button", { name: "保存封面" }));
    await waitFor(() => expect(saveCoverImage).toHaveBeenCalledWith("task-1", "cover-1"));
    expect(await screen.findByText("封面已保存：封面.png")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "保存封面" }));
    expect(await screen.findByText("已取消保存封面。")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "保存封面" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("目标目录不可写");
  });

  it("explains the desktop requirement when cover saving is unavailable", async () => {
    render(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={workspaceWithCover()} />);
    fireEvent.click(screen.getByRole("button", { name: "字幕与封面：已完成" }));
    fireEvent.click(screen.getByRole("button", { name: "保存封面" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("请在桌面客户端中保存封面");
    expect(screen.queryByText(/封面已保存/)).not.toBeInTheDocument();
  });

  it("saves only the current valid subtitle draft directly and leaves a later edit unsaved", async () => {
    const initial = subtitleCoverWorkspace();
    mocks.exactEdit.mockResolvedValue({ operation: { id: "operation-subtitle" } });
    render(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    fireEvent.click(screen.getByRole("button", { name: "字幕与封面：待补封面" }));
    fireEvent.change(screen.getByLabelText("字幕 1 文本"), { target: { value: "第一版修改" } });
    fireEvent.click(screen.getByRole("button", { name: "保存字幕草稿" }));
    await waitFor(() => expect(mocks.exactEdit).toHaveBeenCalledWith("task-1", expect.objectContaining({ operation_type: "UpdateSubtitleCue", text: "第一版修改" })));
    fireEvent.change(screen.getByLabelText("字幕 1 文本"), { target: { value: "第二版修改" } });
    expect(mocks.exactEdit).toHaveBeenCalledOnce();
    expect(mocks.confirmEdit).not.toHaveBeenCalled();
  });
  it("blocks the doomed subtitle preview past the 96-character limit and recovers by shortening", async () => {
    const initial = subtitleCoverWorkspace();
    mocks.exactEdit.mockResolvedValue({ operation: { id: "operation-subtitle" } });
    render(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    fireEvent.click(screen.getByRole("button", { name: "字幕与封面：待补封面" }));

    // 超限：97 个字符，按 UpdateSubtitleCue 的 96 字上限必须拦下，且不发那条已知会 422 的请求。
    fireEvent.change(screen.getByLabelText("字幕 1 文本"), { target: { value: "字".repeat(97) } });
    expect(screen.getByLabelText("字幕 1 字数")).toHaveTextContent("97 / 96 字");
    expect(screen.getByRole("alert")).toHaveTextContent("超出 96 字上限");
    expect(screen.getByRole("button", { name: "保存字幕草稿" })).toBeDisabled();
    expect(mocks.exactEdit).not.toHaveBeenCalled();

    // 恢复：缩短回正好 96 个 Unicode 字符（含 6 个星芒平面字符，UTF-16 要 102 个码元），
    // 提示消失、按钮恢复可用，直接草稿提交按Unicode字符计数放行。
    const recoveredText = `${"字".repeat(90)}${"🎬".repeat(6)}`;
    expect(Array.from(recoveredText).length).toBe(96);
    expect(recoveredText.length).toBe(102);
    fireEvent.change(screen.getByLabelText("字幕 1 文本"), { target: { value: recoveredText } });
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByLabelText("字幕 1 字数")).toHaveTextContent("96 / 96 字");
    fireEvent.click(screen.getByRole("button", { name: "保存字幕草稿" }));
    await waitFor(() => expect(mocks.exactEdit).toHaveBeenCalledWith("task-1", expect.objectContaining({ operation_type: "UpdateSubtitleCue", text: recoveredText })));
    expect(mocks.exactPreview).not.toHaveBeenCalled();
  });

  it("lets the user review persisted topic and script results without rerunning them", () => {
    const initial = workspace(baseTask);
    initial.selected_topic = {
      id: "topic-1", run_id: "run-1", title: "真实选题", angle: "从一次真实失败说起",
      audience: "内容运营人员", outline: ["问题", "判断", "行动"], score: 92,
      selection_status: "confirmed", created_at: baseTask.created_at,
    };
    initial.topic_evidence = [{
      id: "evidence-1", candidate_id: "topic-1", kind: "knowledge", reference_id: "knowledge-1",
      version_or_identity: "v1", title: "已授权复盘笔记", authorization_or_license: "工作区已授权",
      source_url: null, created_at: baseTask.created_at,
    }];
    initial.scripts = [{
      id: "script-1", task_id: baseTask.id, run_id: "run-2", topic_id: "topic-1", title: "真实文案",
      language: "zh-CN", selection_status: "confirmed", created_at: baseTask.created_at,
      updated_at: baseTask.updated_at, confirmed_at: baseTask.updated_at,
      sections: [{ id: "section-1", title: "开头", content: "这是已经确认的口播正文。", visual_prompt: "人物主镜头", evidence_reference_ids: ["evidence-1"] }],
    }];

    render(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    fireEvent.click(screen.getByRole("button", { name: "选题：已完成" }));
    expect(screen.getByRole("heading", { name: "真实选题" })).toBeInTheDocument();
    expect(screen.getByText("已授权复盘笔记")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "文案：已完成" }));
    expect(screen.getByRole("heading", { name: "真实文案" })).toBeInTheDocument();
    expect(screen.getByText("这是已经确认的口播正文。")).toBeInTheDocument();
  });

  it("does not offer a doomed generation action when no authorized voice exists", () => {
    const initial = workspace(baseTask);

    render(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    expect(screen.queryByRole("button", { name: "生成配音" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "添加声音" })).toHaveAttribute("href", "/profiles");
    expect(screen.getByText(/你仍可采用这次本人录音/)).toBeInTheDocument();
  });

  it.each(["script", "narration"] as const)("lets the user revise and reconfirm after %s fails", async (stage) => {
    const failedTask = {
      ...baseTask,
      stage,
      confirmed_script_id: "script-1",
      can_revise_script: true,
      stage_reason: "文字模型服务拒绝了文案请求。",
      stage_impact: "未生成压缩后的文案。",
      stage_repair_action: "手动修订并重新确认文案。",
    };
    const initial = workspace(failedTask);
    initial.scripts = [{
      id: "script-1", task_id: failedTask.id, run_id: "run-2", topic_id: "topic-1", title: "原文案",
      language: "zh-CN", selection_status: "confirmed", created_at: failedTask.created_at,
      updated_at: failedTask.updated_at, confirmed_at: failedTask.updated_at,
      sections: [{ id: "section-1", title: "正文", content: "原来的正文过长。", visual_prompt: "人物主镜头", evidence_reference_ids: ["evidence-1"] }],
    }];
    const repaired = workspace({
      ...failedTask,
      stage: "narration" as const,
      stage_status: "not_started" as const,
      status: "running" as const,
      stage_reason: null,
      stage_impact: null,
      stage_repair_action: null,
    });
    repaired.scripts = initial.scripts;
    mocks.confirmScript.mockResolvedValue(repaired);
    mocks.getWorkspace.mockResolvedValue(repaired);

    render(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    if (stage === "narration") fireEvent.click(screen.getByRole("button", { name: "修改文案" }));
    expect(screen.getByRole("heading", { name: "压缩并重新确认文案" })).toBeInTheDocument();
    fireEvent.change(screen.getByDisplayValue("原来的正文过长。"), { target: { value: "压缩后的三十秒正文。" } });
    fireEvent.click(screen.getByRole("button", { name: "确认修订文案" }));

    await waitFor(() => expect(mocks.confirmScript).toHaveBeenCalledWith("task-1", "script-1", expect.objectContaining({
      sections: [expect.objectContaining({ content: "压缩后的三十秒正文。" })],
    })));
    expect(mocks.generateNarration).not.toHaveBeenCalled();
  });

  it("explicitly binds a later authorized voice before enabling generation", async () => {
    const initial = workspace(baseTask);
    initial.available_voices = [{
      profile_id: "voice-1", version: "v1", person_display_name: "主讲人",
      language: "zh", model_id: "qwen3-tts-12hz-0.6b-base-8bit",
      runnable: true, runnable_reason: null,
    }];
    const selected = workspace({ ...baseTask, stage_status: "not_started", status: "running", stage_reason: null, stage_impact: null, stage_repair_action: null });
    selected.plan.selected_voice = {
      profile_id: "voice-1", version: "v1", person_profile_id: "person-1", person_version: "v1",
      person_authorization_id: "person-auth", person_authorization_version: "v1",
      authorization_id: "voice-auth", authorization_version: "v1", language: "zh",
      model_id: "qwen3-tts-12hz-0.6b-base-8bit",
    };
    selected.available_voices = initial.available_voices;
    mocks.selectVoice.mockResolvedValue(selected);
    mocks.getWorkspace.mockResolvedValue(selected);
    const preview = {
      task_id: "task-1", script_id: "script-1", script_title: "已确认文案",
      script_sections: [{ title: "正文", content: "实际内容" }],
      plan_id: "plan-1", input_digest: "c".repeat(64),
      narration_source: null,
      requirements: { aspect_ratio: "9:16", duration_target_seconds: 30,
        presenter_share_target: 0, lipsync_quality: "fast", selected_voice: null as string | null,
        can_continue: false, continuation_blocker: "请先选择授权声音。", template_settings: {} },
    };
    mocks.previewExecution.mockResolvedValueOnce(preview).mockResolvedValueOnce({
      ...preview, requirements: { ...preview.requirements, selected_voice: "voice-1", can_continue: true, continuation_blocker: null },
    });

    render(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    expect(await screen.findByRole("button", { name: "暂不能开始制作" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "使用授权声音生成" }));

    await waitFor(() => expect(mocks.selectVoice).toHaveBeenCalledWith("task-1", { voice_profile_id: "voice-1", voice_version: "v1" }));
    expect(await screen.findByRole("button", { name: "确认以上文案和要求，开始制作" })).toBeEnabled();
  });

  it("sends only the task id to the native save boundary", async () => {
    const task = { ...baseTask, stage: "review" as const, stage_status: "waiting_for_user" as const, status: "waiting_for_user" as const, progress: 90, output_ready: true };
    const initial = workspace(task, {
      id: "montage-1", task_id: task.id, script_id: "script-1", script_title: "成片",
      status: "rendered", cues: [], clips: [], duration_seconds: 12,
      low_confidence_clip_count: 0, narration_reference: "narration://artifact/audio-1",
      output_url: `/api/creation-tasks/${task.id}/output`,
      subtitle_url: `/api/creation-tasks/${task.id}/output/subtitles`,
      message: "等待检查", created_at: task.created_at, updated_at: task.updated_at,
    });
    const saveRenderedVideo = vi.fn().mockResolvedValue({ saved: true, fileName: "成片.mp4" });
    Object.defineProperty(window, "electronAPI", { configurable: true, value: { saveRenderedVideo } });
    mocks.renderMontage.mockResolvedValue(initial.montage);
    mocks.getWorkspace.mockResolvedValue(initial);

    render(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    expect(screen.getByRole("button", { name: "人物与口型：待配音" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "字幕与封面：待补封面" })).toBeDisabled();
    expect(screen.queryByText("数据监控")).not.toBeInTheDocument();
    expect(screen.getByTitle("字幕与封面：待补封面")).toHaveAttribute("aria-current", "step");
    expect(screen.getByRole("button", { name: "检查与输出：待上一步" })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "保存到本地" }));

    await waitFor(() => expect(saveRenderedVideo).toHaveBeenCalledWith("task-1"));
    expect(saveRenderedVideo).toHaveBeenCalledTimes(1);
    expect(await screen.findByRole("status", { name: "" })).toHaveTextContent("成片.mp4");
    fireEvent.click(screen.getByRole("button", { name: "修改镜头" }));
    expect(screen.getByLabelText("镜头故事板")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "生成成片" })).not.toBeInTheDocument();
    expect(mocks.renderMontage).not.toHaveBeenCalled();
  });

  it("enters the person stage from the rail and reports a pure-material plan as skipped rather than done", () => {
    const task = { ...baseTask, stage: "montage" as const, stage_status: "waiting_for_user" as const, status: "waiting_for_user" as const, progress: 70 };
    const initial = workspace(task);
    initial.plan.presenter_share_target = 0;
    initial.narration = {
      task, script_id: "script-1", audio_reference: "narration://artifact/full-audio", duration_seconds: 3,
      sample_rate: 24000, model_id: "qwen3-tts-12hz-0.6b-base-8bit", voice_profile_id: "voice-1",
      source_kind: "generated", source_artifact_ref: null, source_sha256: null, source_script_digest: null,
      language: "zh" as const, pronunciation_version: "v1",
      segments: [{
        id: "narration-segment-1", source_section_id: "section-1", text: "第一段真实配音",
        audio_reference: "narration://artifact/segment-1",
        audio_url: "/api/creation-tasks/task-1/narration/segments/narration-segment-1/audio",
        duration_seconds: 3, sample_rate: 24000, model_id: "qwen3-tts-12hz-0.6b-base-8bit",
        voice_profile_id: "voice-1", voice_version: "v1", language: "zh" as const,
        pronunciation_version: "p1", status: "ready" as const,
        source_kind: "generated", source_artifact_ref: null, source_sha256: null, source_script_digest: null,
      }],
    };

    render(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);

    const railItem = screen.getByTitle("人物与口型：明确跳过 · 全素材画面");
    expect(within(railItem).getByRole("button")).toBeEnabled();
    fireEvent.click(within(railItem).getByRole("button"));

    expect(screen.getByRole("heading", { name: "本阶段明确跳过" })).toBeInTheDocument();
    expect(screen.getByText("0% · 实际 0%")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /生成人物口型预览/ })).not.toBeInTheDocument();
    expect(screen.getByTitle("人物与口型：明确跳过 · 全素材画面")).not.toHaveAttribute("aria-current");
  });

  it("generates lipsync for a chosen narration segment when the plan asks for on-camera presence", async () => {
    const task = { ...baseTask, stage: "montage" as const, stage_status: "waiting_for_user" as const, status: "waiting_for_user" as const, progress: 70 };
    const initial = workspace(task);
    initial.plan.presenter_share_target = 40;
    initial.plan.selected_voice = {
      profile_id: "voice-1", version: "v1", person_profile_id: "person-1", person_version: "person-v1",
      person_authorization_id: "person-auth-1", person_authorization_version: "v1",
      authorization_id: "voice-auth-1", authorization_version: "v1", language: "zh", model_id: "qwen3-tts-12hz-0.6b-base-8bit",
    };
    initial.narration = {
      task, script_id: "script-1", audio_reference: "narration://artifact/full-audio", duration_seconds: 3,
      sample_rate: 24000, model_id: "qwen3-tts-12hz-0.6b-base-8bit", voice_profile_id: "voice-1",
      source_kind: "generated", source_artifact_ref: null, source_sha256: null, source_script_digest: null,
      language: "zh" as const, pronunciation_version: "v1",
      segments: [{
        id: "narration-segment-1", source_section_id: "section-1", text: "第一段真实配音",
        audio_reference: "narration://artifact/segment-1",
        audio_url: "/api/creation-tasks/task-1/narration/segments/narration-segment-1/audio",
        duration_seconds: 3, sample_rate: 24000, model_id: "qwen3-tts-12hz-0.6b-base-8bit",
        voice_profile_id: "voice-1", voice_version: "v1", language: "zh" as const,
        pronunciation_version: "p1", status: "ready" as const,
        source_kind: "generated", source_artifact_ref: null, source_sha256: null, source_script_digest: null,
      }],
    };
    mocks.generatePersonRender.mockResolvedValue({ id: "person-render-1" });
    mocks.getWorkspace.mockResolvedValue(initial);

    render(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    fireEvent.click(screen.getByRole("button", { name: "人物与口型：待生成口型" }));

    expect(screen.getByText("还没有生成任何口型；系统只为选中的露脸段生成口型。")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "选择配音 1" }));
    fireEvent.click(screen.getByRole("button", { name: "生成人物口型预览" }));

    await waitFor(() => expect(mocks.generatePersonRender).toHaveBeenCalledWith("task-1", {
      narration_segment_id: "narration-segment-1",
      person_profile_id: "person-1",
      person_version: "person-v1",
      motion: "natural",
      expression: "neutral",
    }));
  });

  it("does not report the person stage complete while a required segment is still failed", () => {
    const task = { ...baseTask, stage: "montage" as const, stage_status: "waiting_for_user" as const, status: "waiting_for_user" as const, progress: 70 };
    const segment = (id: string) => ({
      id, source_section_id: "section-1", text: "一段真实配音", audio_reference: `narration://artifact/${id}`,
      audio_url: `/api/creation-tasks/task-1/narration/segments/${id}/audio`,
      duration_seconds: 3, sample_rate: 24000, model_id: "qwen3-tts",
      voice_profile_id: "voice-1", voice_version: "v1", language: "zh" as const,
      pronunciation_version: "p1", status: "ready" as const,
      source_kind: "generated" as const, source_artifact_ref: null, source_sha256: null, source_script_digest: null,
    });
    const initial = workspace(task);
    initial.plan.presenter_share_target = 40;
    initial.plan.selected_voice = {
      profile_id: "voice-1", version: "v1", person_profile_id: "person-1", person_version: "person-v1",
      person_authorization_id: "person-auth-1", person_authorization_version: "v1",
      authorization_id: "voice-auth-1", authorization_version: "v1", language: "zh", model_id: "qwen3-tts",
    };
    initial.narration = {
      task, script_id: "script-1", audio_reference: "narration://artifact/full-audio", duration_seconds: 6,
      sample_rate: 24000, model_id: "qwen3-tts", voice_profile_id: "voice-1",
      source_kind: "generated", source_artifact_ref: null, source_sha256: null, source_script_digest: null,
      language: "zh" as const, pronunciation_version: "p1",
      segments: [segment("narration-segment-1"), segment("narration-segment-2")],
    };
    const personRender = (id: string, narrationSegmentId: string, status: "ready" | "failed") => ({
      id, task_id: task.id,
      person_profile_id: "person-1", person_version: "person-v1",
      voice_profile_id: "voice-1", voice_version: "v1",
      narration_segment_id: narrationSegmentId, visual_reference: "workspace://person.mp4",
      model_id: "musetalk-local", motion: "natural" as const, expression: "neutral" as const,
      status, duration_seconds: 3, alignment_evidence: "aligned",
      media_url: status === "ready" ? `/api/${id}` : null,
      failure_reason: status === "failed" ? "口型未完成" : null,
      impact: status === "failed" ? "这一段没有成片。" : null,
      repair_action: status === "failed" ? "重新生成这一段。" : null,
      created_at: task.created_at, updated_at: task.updated_at,
    });
    // 第一段已生成，第二段失败；成片两个镜头都引用了人物口型。
    initial.person_renders = [personRender("person-render-1", "narration-segment-1", "ready"), personRender("person-render-2", "narration-segment-2", "failed")];
    initial.montage = {
      id: "montage-1", task_id: task.id, script_id: "script-1", script_title: "成片",
      status: "waiting_for_user", cues: [], duration_seconds: 6, low_confidence_clip_count: 0,
      narration_reference: "narration://artifact/full-audio",
      output_url: null, subtitle_url: null, message: "等待确认",
      clips: ["person-render-1", "person-render-2"].map((personRenderId, index) => ({
        id: `clip-${index + 1}`, cue_id: `cue-${index + 1}`, asset_id: "asset-1", segment_id: `segment-${index + 1}`,
        source_reference: "asset://asset-1", source_start_seconds: 0, source_end_seconds: 3,
        timeline_start_seconds: index * 3, timeline_end_seconds: index * 3 + 3, match_score: 0.9,
        needs_confirmation: false, role: "a_roll" as const, locked: false, manually_selected: false,
        person_render_id: personRenderId,
      })),
      created_at: task.created_at, updated_at: task.updated_at,
    };

    render(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);

    expect(screen.getByRole("button", { name: "人物与口型：已生成 1/2 段 · 仍有口型未完成" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "人物与口型：已完成" })).not.toBeInTheDocument();
  });

  it("does not report narration as complete while segments are still running or have failed", () => {
    const task = { ...baseTask, stage: "narration" as const, stage_status: "running" as const, status: "running" as const };
    const segment = {
      id: "narration-segment-1", source_section_id: "section-1", text: "第一段真实配音",
      audio_reference: "narration://artifact/segment-1",
      audio_url: "/api/creation-tasks/task-1/narration/segments/narration-segment-1/audio",
      duration_seconds: 3, sample_rate: 24000, model_id: "qwen3-tts-12hz-0.6b-base-8bit",
      voice_profile_id: "voice-1", voice_version: "v1", language: "zh" as const,
      pronunciation_version: "p1", status: "ready" as const,
      source_kind: "generated" as const, source_artifact_ref: null, source_sha256: null, source_script_digest: null,
    };
    const initial = workspace(task);
    initial.narration = {
      task, script_id: "script-1", audio_reference: "narration://artifact/full-audio", duration_seconds: 3,
      sample_rate: 24000, model_id: "qwen3-tts-12hz-0.6b-base-8bit", voice_profile_id: "voice-1",
      source_kind: "generated", source_artifact_ref: null, source_sha256: null, source_script_digest: null,
      language: "zh" as const, pronunciation_version: "v1", segments: [segment],
    };

    const { unmount } = render(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={{
      ...initial,
      narration: { ...initial.narration!, segments: [{ ...segment, status: "generating" as const }] },
    }} />);
    expect(screen.getByRole("button", { name: "配音：配音生成中" })).toBeEnabled();
    expect(screen.queryByRole("button", { name: "配音：已完成" })).not.toBeInTheDocument();
    unmount();

    render(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={{
      ...initial,
      narration: { ...initial.narration!, segments: [{ ...segment, status: "failed" as const }] },
    }} />);
    expect(screen.getByRole("button", { name: "配音：有配音段未完成" })).toBeEnabled();
    expect(screen.getByTitle("配音：有配音段未完成")).toHaveAttribute("aria-current", "step");
  });

  it("opens the persisted subtitle and cover workspace and saves completed cue edits directly", async () => {
    const initial = subtitleCoverWorkspace();
    const refreshed = subtitleCoverWorkspace();
    refreshed.task = { ...refreshed.task, current_timeline_version_id: "timeline-version-7", timeline_sequence: 7 };
    refreshed.subtitle_tracks = [{
      ...refreshed.subtitle_tracks[0],
      timeline_version_id: "timeline-version-7",
      cues: [{
        ...refreshed.subtitle_tracks[0].cues[0],
        text: "真实运行不是口号",
        line_breaks: [4],
        emphasis: ["真实运行"],
      }],
    }];
    mocks.exactPreview.mockResolvedValue(exactPreview(initial.task, "UpdateSubtitleCue"));
    mocks.confirmEdit.mockResolvedValue({ operation: { id: "operation-subtitle" }, montage: initial.montage });
    mocks.getWorkspace.mockResolvedValue(refreshed);

    render(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    fireEvent.click(screen.getByRole("button", { name: "字幕与封面：待补封面" }));

    expect(screen.getByLabelText("字幕与封面工作区")).toBeInTheDocument();
    expect(screen.getByLabelText("字幕成片预览")).toHaveAttribute("src", "/api/creation-tasks/task-1/output");
    expect(screen.queryByLabelText("当前选择")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /AI 生成封面/ })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "选择中文字幕 1" }));
    const inspector = screen.getByLabelText("当前选择");
    expect(inspector).toHaveTextContent("时间0.0–3.2 秒");
    expect(inspector).toHaveTextContent("字幕产物artifact:subtitle:zh:v6");
    expect(inspector).toHaveTextContent("运行证据transform:subtitle:v6");

    fireEvent.change(screen.getByLabelText("字幕 1 文本"), { target: { value: "真实运行\n不是口号" } });
    fireEvent.change(screen.getByLabelText("字幕 1 出现时间"), { target: { value: "0.4" } });
    fireEvent.change(screen.getByLabelText("字幕 1 结束时间"), { target: { value: "2.8" } });
    fireEvent.change(screen.getByLabelText("字幕 1 强调词"), { target: { value: "真实运行" } });
    fireEvent.blur(screen.getByLabelText("字幕 1 强调词"));

    await waitFor(() => expect(mocks.exactEdit).toHaveBeenCalledWith("task-1", {
      operation_type: "UpdateSubtitleCue",
      track_id: "subtitle-track-zh",
      cue_id: "subtitle-cue-1",
      text: "真实运行\n不是口号",
      start_seconds: 0.4,
      end_seconds: 2.8,
      line_breaks: [4],
      emphasis: ["真实运行"],
      style: {
        name: "default", font_family: "PingFang SC", font_size: 40,
        primary_color: "#FFFFFF", emphasis_color: "#F0B35F", position: "bottom",
        bilingual_order: "source_then_translation",
      },
    }));
    expect(mocks.exactPreview).not.toHaveBeenCalled();
    expect(mocks.confirmEdit).not.toHaveBeenCalled();
    expect(screen.queryByLabelText("变更预览")).not.toBeInTheDocument();
    await waitFor(() => expect(mocks.getWorkspace).toHaveBeenCalledWith("task-1"));
    await waitFor(() => expect(screen.getByLabelText("字幕 1 文本")).toHaveValue("真实运行\n不是口号"));
    expect(mocks.exactEdit).toHaveBeenCalledOnce();

    mocks.previewEdit.mockResolvedValue({ ...exactPreview(refreshed.task, "UpdateSubtitleCue"), proposal: { ...exactPreview(refreshed.task, "UpdateSubtitleCue").proposal, id: "proposal-natural" } });
    fireEvent.change(screen.getByLabelText("局部修改要求"), { target: { value: "这句再简洁一点" } });
    fireEvent.click(screen.getByRole("button", { name: "预览修改" }));
    await waitFor(() => expect(mocks.previewEdit).toHaveBeenCalledWith("task-1", {
      raw_command: "这句再简洁一点",
      context_stage: "subtitle_cover",
      selected_object_refs: ["cue:subtitle-cue-1", "subtitle-track:subtitle-track-zh"],
      playhead_ms: null,
    }));
    expect(mocks.confirmEdit).not.toHaveBeenCalled();
    fireEvent.click(await screen.findByRole("button", {name:"应用到草稿"}));
    await waitFor(()=>expect(mocks.confirmEdit).toHaveBeenCalledOnce());
    expect(mocks.confirmEdit).toHaveBeenCalledWith("task-1","proposal-natural");
  });

  // WIL-180 专项：字幕确认后服务端把任务退回 montage 并作废旧成片
  // （stage:"montage"、output_ready:false、output_url:null，见 WIL-152/evidence/02-replaced.json），
  // 字幕轨仍是 1 条 valid、can_undo:true。此时字幕页按既有门禁已不可呈现
  // （canOpenStage("subtitle_cover") 要求 output_url），但 confirmEdit 的 refresh(true) 仍保留它，
  // 结果主区没有任何视图命中 → 空白、撤销入口一起消失。
  // 正确表现是按既有可用/默认规则回到 montage 剪辑页，撤销入口可见。
  it("keeps the editable subtitle view after a versioned save invalidates the old output", async () => {
    const initial = subtitleCoverWorkspace();
    const confirmed = subtitleCoverWorkspace();
    confirmed.task = {
      ...confirmed.task,
      stage: "montage" as const,
      progress: 75,
      current_timeline_version_id: "timeline-version-7",
      can_undo: true,
      can_redo: false,
      output_ready: false,
    };
    confirmed.montage = {
      ...confirmed.montage!,
      output_url: null,
      subtitle_url: null,
      message: "字幕修改已保存，配音未改；请重新渲染。",
    };
    confirmed.subtitle_tracks = [{
      ...confirmed.subtitle_tracks[0],
      timeline_version_id: "timeline-version-7",
      cues: [{ ...confirmed.subtitle_tracks[0].cues[0], text: "真实运行\n不是口号" }],
    }];
    mocks.exactEdit.mockResolvedValue({ operation: { id: "operation-subtitle" }, montage: confirmed.montage });
    mocks.getWorkspace.mockResolvedValue(confirmed);

    render(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    fireEvent.click(screen.getByRole("button", { name: "字幕与封面：待补封面" }));
    expect(screen.getByLabelText("字幕与封面工作区")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "选择中文字幕 1" }));
    fireEvent.change(screen.getByLabelText("字幕 1 文本"), { target: { value: "真实运行\n不是口号" } });
    fireEvent.change(screen.getByLabelText("字幕 1 出现时间"), { target: { value: "0.4" } });
    fireEvent.change(screen.getByLabelText("字幕 1 结束时间"), { target: { value: "2.8" } });
    fireEvent.change(screen.getByLabelText("字幕 1 强调词"), { target: { value: "真实运行" } });
    fireEvent.click(screen.getByRole("button", { name: "保存字幕草稿" }));

    await waitFor(() => expect(mocks.exactEdit).toHaveBeenCalledWith("task-1", expect.objectContaining({ operation_type: "UpdateSubtitleCue", text: "真实运行\n不是口号" })));
    await waitFor(() => expect(mocks.getWorkspace).toHaveBeenCalledWith("task-1"));

    // 字幕草稿仍可编辑，但失效的成片不能冒充新预览；回到剪辑阶段仍能撤销。
    expect(screen.getByLabelText("字幕与封面工作区")).toBeInTheDocument();
    expect(screen.queryByLabelText("字幕成片预览")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /^视频剪辑：/ }));
    expect(screen.getByText("未出片")).toBeInTheDocument();
    expect(screen.queryByLabelText("剪辑成片预览")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "撤销" })).toBeEnabled();
  });

  // 对照用例：成片仍有效（output_url 保留）时，refresh(true) 仍保留字幕页，不得乱跳。
  // 字段改动与既有用例一致，确保单跑也能产出合法精确预览。
  it("does not jump away from a still-valid view when output_url is present", async () => {
    const initial = subtitleCoverWorkspace();
    const confirmed = subtitleCoverWorkspace();
    confirmed.task = { ...confirmed.task, current_timeline_version_id: "timeline-version-7", timeline_sequence: 7 };
    confirmed.subtitle_tracks = [{
      ...confirmed.subtitle_tracks[0],
      timeline_version_id: "timeline-version-7",
      cues: [{ ...confirmed.subtitle_tracks[0].cues[0], text: "真实运行\n不是口号" }],
    }];
    mocks.exactEdit.mockResolvedValue({ operation: { id: "operation-subtitle" }, montage: confirmed.montage });
    mocks.getWorkspace.mockResolvedValue(confirmed);

    render(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    fireEvent.click(screen.getByRole("button", { name: "字幕与封面：待补封面" }));
    fireEvent.click(screen.getByRole("button", { name: "选择中文字幕 1" }));
    fireEvent.change(screen.getByLabelText("字幕 1 文本"), { target: { value: "真实运行\n不是口号" } });
    fireEvent.change(screen.getByLabelText("字幕 1 出现时间"), { target: { value: "0.4" } });
    fireEvent.change(screen.getByLabelText("字幕 1 结束时间"), { target: { value: "2.8" } });
    fireEvent.change(screen.getByLabelText("字幕 1 强调词"), { target: { value: "真实运行" } });
    fireEvent.click(screen.getByRole("button", { name: "保存字幕草稿" }));
    await waitFor(() => expect(mocks.exactEdit).toHaveBeenCalledWith("task-1", expect.objectContaining({ operation_type: "UpdateSubtitleCue", text: "真实运行\n不是口号" })));
    await waitFor(() => expect(mocks.getWorkspace).toHaveBeenCalledWith("task-1"));

    // 成片仍有效时不得乱跳：仍停在字幕与封面页，预览器照旧
    await waitFor(() => expect(screen.getByLabelText("字幕与封面工作区")).toBeInTheDocument());
    expect(screen.getByLabelText("字幕成片预览")).toHaveAttribute("src", "/api/creation-tasks/task-1/output");
  });

  it("applies a valid transition edit when the field is committed", async () => {
    const initial=subtitleCoverWorkspace();
    const clip={id:"clip-1",cue_id:"cue-1",asset_id:"asset-1",segment_id:"segment-1",source_reference:"workspace://clip.mp4",
      source_start_seconds:0,source_end_seconds:3,timeline_start_seconds:0,timeline_end_seconds:3,match_score:.9,
      match_reasons:[],needs_confirmation:false,role:"b_roll" as const,locked:false,manually_selected:false};
    initial.montage!.clips=[clip,{...clip,id:"clip-2",timeline_start_seconds:3,timeline_end_seconds:6}];
    mocks.exactEdit.mockResolvedValue({ operation: { id: "transition-edit" } });
    render(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    fireEvent.click(screen.getByRole("button",{name:/^视频剪辑：/}));
    fireEvent.click(screen.getByRole("button",{name:"选择镜头 2"}));
    fireEvent.click(screen.getByText("镜头过渡"));
    fireEvent.change(screen.getByLabelText("叠化时长（秒）"),{target:{value:"0.4"}});
    fireEvent.blur(screen.getByLabelText("叠化时长（秒）"));
    await waitFor(()=>expect(mocks.exactEdit).toHaveBeenCalledWith("task-1",{operation_type:"SetClipTransition",clip_id:"clip-2",transition_seconds:.4}));
    expect(mocks.exactPreview).not.toHaveBeenCalled();
    expect(mocks.confirmEdit).not.toHaveBeenCalled();
  });

  it("keeps selected image, inspector, playhead and natural language scope synchronized",async()=>{
    const initial=subtitleCoverWorkspace();
    initial.montage!.clips=[{id:"clip-1",cue_id:"cue-1",asset_id:"asset-1",segment_id:"segment-1",source_reference:"workspace://clip.mp4",source_start_seconds:0,source_end_seconds:3,timeline_start_seconds:0,timeline_end_seconds:3,match_score:.9,match_reasons:[],needs_confirmation:false,role:"b_roll",locked:false,manually_selected:false}];
    vi.spyOn(HTMLMediaElement.prototype,"pause").mockImplementation(()=>{});
    initial.montage!.graphic_overlays=[{kind:"image",id:"overlay-logo",source_ref:"artifacts/logo.png",name:"logo.png",start_seconds:1,end_seconds:3,x:.5,y:.5,width:.3,opacity:1}];
    mocks.previewEdit.mockResolvedValue(exactPreview(initial.task,"EditGraphicOverlay"));
    render(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial}/>);
    fireEvent.click(screen.getByRole("button",{name:/^视频剪辑：/}));
    fireEvent.click(screen.getByRole("button",{name:"图片1 · logo.png"}));
    expect(screen.getByRole("complementary",{name:"图片1"})).toBeInTheDocument();
    expect(screen.getByLabelText("图片不透明度")).toHaveValue(1);
    const player=screen.getByLabelText("剪辑成片预览") as HTMLVideoElement;
    expect(player.currentTime).toBe(2);
    player.currentTime=4;fireEvent.timeUpdate(player);
    expect(screen.getByRole("complementary",{name:"图片1"})).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("局部修改要求"),{target:{value:"当前图片不透明度改为50%"}});
    fireEvent.click(screen.getByRole("button",{name:"预览修改"}));
    await waitFor(()=>expect(mocks.previewEdit).toHaveBeenCalledWith("task-1",expect.objectContaining({selected_object_refs:["overlay:overlay-logo"],raw_command:"当前图片不透明度改为50%"})));
    expect(await screen.findByLabelText("变更预览")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button",{name:"选择镜头 1"}));
    expect(screen.queryByLabelText("变更预览")).not.toBeInTheDocument();
    expect(screen.queryByRole("complementary",{name:"图片1"})).not.toBeInTheDocument();
  });

  it("requires a new preview when compound steps are excluded",async()=>{
    const initial=subtitleCoverWorkspace();
    initial.montage!.clips=[{id:"clip-1",cue_id:"cue-1",asset_id:"asset-1",segment_id:"segment-1",source_reference:"workspace://clip.mp4",source_start_seconds:0,source_end_seconds:3,timeline_start_seconds:0,timeline_end_seconds:3,match_score:.9,match_reasons:[],needs_confirmation:false,role:"b_roll",locked:false,manually_selected:false}];
    const base=exactPreview(initial.task,"EditCompoundTimeline");
    const steps=[{index:0,command:"当前镜头亮一点",enabled:true},{index:1,command:"配音小一点",enabled:true}];
    const original={...base,intent:{...base.intent,raw_command:"当前镜头亮一点，同时配音小一点"},proposal:{...base.proposal,operation:{operation_type:"EditCompoundTimeline",payload:{selection_enabled:true,steps}},preview:{before:{ref:"before",label:"修改前",detail:"",video_url:"/before.mp4"},after:{ref:"after",label:"修改后",detail:"",video_url:"/after.mp4"}}}};
    mocks.previewEdit.mockResolvedValue(original);
    mocks.previewSelection.mockResolvedValue({...original,proposal:{...original.proposal,id:"selected-proposal",operation:{operation_type:"EditCompoundTimeline",payload:{selection_enabled:true,steps:[steps[0],{...steps[1],enabled:false}]}},preview:{...original.proposal.preview,after:{...original.proposal.preview.after,video_url:"/selected.mp4"}}}});
    render(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial}/>);
    fireEvent.click(screen.getByRole("button",{name:/^视频剪辑：/}));
    fireEvent.click(screen.getByRole("button",{name:"选择镜头 1"}));
    fireEvent.change(screen.getByLabelText("局部修改要求"),{target:{value:"当前镜头亮一点，同时配音小一点"}});
    fireEvent.click(screen.getByRole("button",{name:"预览修改"}));
    const checkbox=await screen.findByLabelText("保留修改2：配音小一点");
    fireEvent.click(checkbox);
    expect(screen.getByLabelText("局部修改要求")).toBeDisabled();
    expect(screen.getByRole("button",{name:"应用到草稿"})).toBeDisabled();
    expect(screen.queryByLabelText("修改后画面")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button",{name:"重看所选"}));
    await waitFor(()=>expect(mocks.previewSelection).toHaveBeenCalledWith("task-1",original.proposal.id,[0]));
    await waitFor(()=>expect(screen.getByLabelText("修改后画面")).toHaveAttribute("src","/selected.mp4"));
    expect(screen.getByRole("button",{name:"应用到草稿"})).toBeEnabled();
    expect(screen.getByLabelText("保留修改2：配音小一点")).not.toBeChecked();
    mocks.previewSelection.mockResolvedValue({...original,proposal:{...original.proposal,id:"restored-proposal"}});
    fireEvent.change(screen.getByLabelText("局部修改要求"),{target:{value:"恢复第2项"}});
    expect(screen.getByLabelText("保留修改2：配音小一点")).toBeDisabled();
    expect(screen.getByRole("button",{name:"应用到草稿"})).toBeDisabled();
    expect(screen.queryByLabelText("修改后画面")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button",{name:"预览修改"}));
    await waitFor(()=>expect(mocks.previewSelection).toHaveBeenLastCalledWith("task-1","selected-proposal","恢复第2项"));
    await waitFor(()=>expect(screen.getByLabelText("保留修改2：配音小一点")).toBeChecked());
    expect(screen.getByRole("button",{name:"应用到草稿"})).toBeEnabled();
    expect(mocks.confirmEdit).not.toHaveBeenCalled();
  });

  it("submits canvas ratio and fit changes directly from their selectors", async () => {
    const initial=subtitleCoverWorkspace();
    mocks.exactEdit.mockResolvedValue({ operation: { id: "canvas-edit" } });
    render(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    fireEvent.click(screen.getByRole("button", {name:/^视频剪辑：/}));
    fireEvent.click(screen.getByText("画幅设置"));
    fireEvent.click(screen.getByRole("button", {name:"成片画幅"}));
    fireEvent.click(screen.getByRole("option", {name:"16:9 横屏"}));
    await waitFor(()=>expect(mocks.exactEdit).toHaveBeenLastCalledWith("task-1",{operation_type:"ChangeCanvas",aspect_ratio:"16:9"}));
    fireEvent.click(screen.getByRole("button", {name:"整片画面适配"}));
    fireEvent.click(screen.getByRole("option", {name:"完整画面"}));
    await waitFor(()=>expect(mocks.exactEdit).toHaveBeenCalledWith("task-1",{operation_type:"ChangeCanvas",aspect_ratio:"16:9",fit_mode:"fit"}));
    expect(screen.queryByRole("button",{name:"预览画幅"})).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", {name:"成片画幅"}));
    fireEvent.click(screen.getByRole("option", {name:"1:1 方形"}));
    await waitFor(()=>expect(mocks.exactEdit).toHaveBeenLastCalledWith("task-1",{operation_type:"ChangeCanvas",aspect_ratio:"1:1",fit_mode:"fit"}));
    expect(mocks.exactPreview).not.toHaveBeenCalled();
    expect(mocks.confirmEdit).not.toHaveBeenCalled();
  });

  it("restores the saved canvas value and shows the real API failure when a direct edit is rejected", async () => {
    const initial = subtitleCoverWorkspace();
    initial.montage!.aspect_ratio = "16:9";
    mocks.exactEdit.mockRejectedValue(new Error("valid 字幕轨必须有条目、产物和变换证据"));
    render(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    fireEvent.click(screen.getByRole("button", {name:/^视频剪辑：/}));
    fireEvent.click(screen.getByText("画幅设置"));
    fireEvent.click(screen.getByRole("button", {name:"成片画幅"}));
    fireEvent.click(screen.getByRole("option", {name:"1:1 方形"}));
    expect(await screen.findByRole("alert")).toHaveTextContent("valid 字幕轨必须有条目、产物和变换证据");
    fireEvent.click(screen.getByRole("button", {name:/^画幅设置/}));
    expect(screen.getByRole("button", {name:"成片画幅"})).toHaveTextContent("16:9 横屏");
    expect(screen.getByLabelText("剪辑成片预览").parentElement).toHaveAttribute("data-editor-aspect-ratio", "16:9");
    expect(mocks.exactEdit).toHaveBeenCalledWith("task-1", {operation_type:"ChangeCanvas",aspect_ratio:"1:1"});
    expect(mocks.exactPreview).not.toHaveBeenCalled();
    expect(mocks.confirmEdit).not.toHaveBeenCalled();
  });

  it("sizes the editor preview and timeline clips from actual montage intervals", () => {
    const initial = subtitleCoverWorkspace();
    initial.montage!.aspect_ratio = "16:9";
    initial.montage!.duration_seconds = 10;
    const clip = { id:"clip-1", cue_id:"cue-1", asset_id:"asset-1", segment_id:"segment-1", source_reference:"workspace://clip.mp4", source_start_seconds:0, source_end_seconds:2, timeline_start_seconds:2, timeline_end_seconds:4, match_score:.9, match_reasons:[], needs_confirmation:false, role:"b_roll" as const, locked:false, manually_selected:false };
    initial.montage!.clips = [clip, { ...clip, id:"clip-2", timeline_start_seconds:5, timeline_end_seconds:10 }];
    render(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    fireEvent.click(screen.getByRole("button", { name:/^视频剪辑：/ }));
    const player = screen.getByLabelText("剪辑成片预览").parentElement;
    expect(player).toHaveAttribute("data-editor-aspect-ratio", "16:9");
    const timeline = screen.getByLabelText("配音与字幕时间范围");
    const clips = within(timeline).getAllByRole("button", { name:/^定位镜头/ });
    expect(clips[0]).toHaveStyle({ left:"20%", width:"20%" });
    expect(clips[1]).toHaveStyle({ left:"50%", width:"50%" });
  });

  it("applies subtitle style settings directly without relying on generated preview imagery", async () => {
    const initial = subtitleCoverWorkspace();
    initial.subtitle_tracks = [{...initial.subtitle_tracks[0], language:"en-US", source_language:null}];
    mocks.exactEdit.mockResolvedValue({ operation: { id: "subtitle-style-edit" } });
    render(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    fireEvent.click(screen.getByRole("button", { name: /^字幕与封面：/ }));
    fireEvent.click(screen.getByText("全部字幕样式"));
    fireEvent.change(screen.getByLabelText("字幕字号"), {target:{value:"64"}});
    fireEvent.change(screen.getByLabelText("字幕背景不透明度"), {target:{value:"0"}});
    fireEvent.click(screen.getByRole("button", {name:"保存字幕样式"}));
    await waitFor(() => expect(mocks.exactEdit.mock.calls.some(([, input]) => input.operation_type === "ConfigureSubtitleTrack" && input.style?.font_size === 64)).toBe(true));
    expect(mocks.exactEdit.mock.calls.some(([, input]) => input.operation_type === "ConfigureSubtitleTrack" && input.style?.background_opacity === 0)).toBe(true);
    expect(screen.queryByAltText("修改后字幕样式")).not.toBeInTheDocument();
    expect(mocks.exactPreview).not.toHaveBeenCalled();
    expect(mocks.confirmEdit).not.toHaveBeenCalled();
  });

  it("applies subtitle structure changes directly and disables stale delete after a draft edit", async () => {
    const initial = subtitleCoverWorkspace();
    mocks.exactEdit.mockResolvedValue({ operation: { id: "split-subtitle" } });
    render(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    fireEvent.click(screen.getByRole("button", { name: /^字幕与封面：/ }));
    fireEvent.click(screen.getByText("拆分、合并与删除"));
    fireEvent.change(screen.getByLabelText("字幕拆分文字位置"), { target: { value: "3" } });
    fireEvent.change(screen.getByLabelText("字幕拆分时间"), { target: { value: "1" } });
    fireEvent.click(screen.getByRole("button", { name: "拆分字幕" }));
    await waitFor(() => expect(mocks.exactEdit).toHaveBeenCalledWith("task-1", {
      operation_type: "SplitSubtitleCue", track_id: "subtitle-track-zh", cue_id: "subtitle-cue-1", split_offset: 3, split_time_seconds: 1,
    }));
    expect(mocks.confirmEdit).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.getByRole("button", { name: "保存字幕草稿" })).toBeEnabled());
    fireEvent.change(screen.getByLabelText("字幕 1 文本"), { target: { value: "未保存草稿" } });
    await waitFor(() => expect(screen.getByRole("button", { name: "删除字幕" })).toBeDisabled());
  });

  it("applies full-track literal replacement directly with each changed caption", async () => {
    const initial = subtitleCoverWorkspace();
    mocks.exactEdit.mockResolvedValue({ operation: { id: "replace-subtitle-text" } });
    render(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    fireEvent.click(screen.getByRole("button", { name: /^字幕与封面：/ }));
    fireEvent.click(screen.getByText("全文查找替换"));
    fireEvent.change(screen.getByLabelText("字幕查找文字"), { target: { value: "旧名字" } });
    fireEvent.change(screen.getByLabelText("字幕替换文字"), { target: { value: "新名字" } });
    fireEvent.click(screen.getByRole("button", { name: "应用全文替换" }));
    await waitFor(() => expect(mocks.exactEdit).toHaveBeenCalledWith("task-1", {
      operation_type: "ReplaceSubtitleText", track_id: "subtitle-track-zh", find_text: "旧名字", replacement_text: "新名字",
    }));
    expect(mocks.exactPreview).not.toHaveBeenCalled();
    expect(mocks.confirmEdit).not.toHaveBeenCalled();
  });

  it("does not offer missing Chinese captions in an English-only task", () => {
    const initial = subtitleCoverWorkspace();
    initial.subtitle_tracks = [{ ...initial.subtitle_tracks[0], language: "en-US", source_language: null }];
    render(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    fireEvent.click(screen.getByRole("button", { name: /^字幕与封面：/ }));
    expect(screen.getByRole("button", { name: "中文" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "中英双语" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "英文" })).toHaveAttribute("aria-pressed", "true");
  });

  it("applies hiding subtitles directly without generating a translation", async () => {
    const initial = subtitleCoverWorkspace();
    mocks.exactEdit.mockResolvedValue({ operation: { id: "subtitle-display-edit" } });
    render(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    fireEvent.click(screen.getByRole("button", { name: /^字幕与封面：/ }));
    fireEvent.click(screen.getByRole("button", { name: "无字幕" }));
    await waitFor(() => expect(mocks.exactEdit).toHaveBeenCalledWith("task-1", {
      operation_type: "ConfigureSubtitleTrack", subtitle_display: "none",
    }));
    expect(mocks.exactPreview).not.toHaveBeenCalled();
    expect(mocks.confirmEdit).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "英文" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "中文" })).toHaveAttribute("aria-pressed", "true");
  });

  it("applies bilingual configuration and keeps real-frame cover generation distinct from AI image success", async () => {
    const initial = subtitleCoverWorkspace();
    initial.subtitle_translation_capability = {
      available: true, model_id: "qwen-text",
      message: "英文翻译模型可用。", repair_action: null,
    };
    initial.cover_asset = {
      id: "cover-1", workspace_id: "local", task_id: initial.task.id, timeline_version_id: "timeline-version-6",
      source_video_ref: "artifact:video:v6", frame_time_seconds: 2.4, title: "别只讲概念",
      safe_area: { top: 144, right: 72, bottom: 180, left: 72 },
      layout: { title_position: "bottom", text_align: "center" }, generation_method: "video_frame",
      model_or_engine: "ffmpeg", status: "valid", artifact_ref: "artifact:cover:v6",
      transform_id: "transform:cover:v6", failure_reason: null, impact: null, repair_action: null,
      created_at: initial.task.created_at, updated_at: initial.task.updated_at,
    };
    mocks.exactEdit
      .mockResolvedValueOnce({ operation: { id: "subtitle-language-edit" } })
      .mockResolvedValueOnce({ operation: { id: "cover-generation" } });

    render(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    fireEvent.click(screen.getByRole("button", { name: "字幕与封面：已完成" }));
    fireEvent.click(screen.getByRole("button", { name: "生成英文字幕" }));
    await waitFor(() => expect(mocks.exactEdit).toHaveBeenNthCalledWith(1, "task-1", {
      operation_type: "ConfigureSubtitleTrack",
      track_id: "subtitle-track-zh",
      target_language: "en-US",
    }));
    await waitFor(() => expect(screen.getByRole("button", { name: "从画面生成封面" })).toBeEnabled());
    expect(screen.getByRole("button", { name: "中文" })).toBeDisabled();

    fireEvent.change(screen.getByLabelText("封面画面时间"), { target: { value: "4.2" } });
    fireEvent.change(screen.getByLabelText("封面标题"), { target: { value: "真实运行" } });
    const positions = within(screen.getByRole("group", { name: "封面标题位置" }));
    expect(positions.getByRole("button", { name: "底部" })).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(positions.getByRole("button", { name: "中部" }));
    fireEvent.click(screen.getByRole("button", { name: "从画面生成封面" }));
    await waitFor(() => expect(mocks.exactEdit).toHaveBeenNthCalledWith(2, "task-1", {
      operation_type: "GenerateCoverFromFrame",
      frame_time_seconds: 4.2,
      title: "真实运行",
      title_position: "center",
    }));
    const inspector = screen.getByLabelText("当前选择");
    expect(inspector).toHaveTextContent("选帧能力可用 · ffmpeg+Pillow");
    expect(inspector).toHaveTextContent("AI 生图不可用 · 未配置模型");
    expect(inspector).not.toHaveTextContent("AI 生图已生成");
    expect(screen.queryByRole("button", { name: /AI 生成封面/ })).not.toBeInTheDocument();
  });

  it("applies a cover title position change and refreshes persisted layout", async () => {
    const initial = workspaceWithCover();
    const confirmed = structuredClone(initial);
    confirmed.cover_asset!.id = "cover-center";
    confirmed.cover_asset!.layout.title_position = "center";
    mocks.exactEdit.mockResolvedValue({ operation: { id: "cover-position-edit" } });
    mocks.getWorkspace.mockResolvedValue(confirmed);
    render(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    fireEvent.click(screen.getByRole("button", { name: "字幕与封面：已完成" }));
    const positions = within(screen.getByRole("group", { name: "封面标题位置" }));
    fireEvent.click(positions.getByRole("button", { name: "中部" }));
    expect(screen.getByText(/选帧、标题或构图待生成/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "从画面生成封面" }));
    await waitFor(() => expect(mocks.exactEdit).toHaveBeenCalledWith(initial.task.id, {
      operation_type: "GenerateCoverFromFrame", frame_time_seconds: initial.cover_asset!.frame_time_seconds,
      title: initial.cover_asset!.title, title_position: "center",
    }));
    await waitFor(() => expect(mocks.getWorkspace).toHaveBeenCalledWith(initial.task.id));
    await waitFor(() => expect(screen.queryByText(/选帧、标题或构图待生成/)).not.toBeInTheDocument());
    expect(positions.getByRole("button", { name: "中部" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByLabelText("当前选择")).toHaveTextContent("画面中部");
    expect(mocks.confirmEdit).not.toHaveBeenCalled();
  });

  it("keeps bilingual unavailable with one visible repair instruction", () => {
    const initial = subtitleCoverWorkspace();
    render(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    fireEvent.click(screen.getByRole("button", { name: "字幕与封面：待补封面" }));

    expect(screen.getByRole("button", { name: "中英双语" })).toBeDisabled();
    expect(screen.getByRole("status")).toHaveTextContent("在设置中连接文字模型");
    fireEvent.change(screen.getByLabelText("封面标题"), { target: { value: "真实运行" } });
    expect(screen.getByRole("button", { name: "从画面生成封面" })).toBeEnabled();
  });

  it("applies clip trimming through the exact edit endpoint and refreshes version facts", async () => {
    const task = {
      ...baseTask,
      stage: "montage" as const,
      stage_status: "waiting_for_user" as const,
      status: "waiting_for_user" as const,
      progress: 70,
      current_timeline_version_id: "timeline-version-2",
      timeline_sequence: 2,
      can_undo: true,
    };
    const montage = {
      id: "montage-1", task_id: task.id, script_id: "script-1", script_title: "故事板",
      status: "ready" as const,
      cues: [{ id: "cue-1", source_section_id: "section-1", narration_segment_id: "narration-segment-1", text: "第一段旁白", duration_seconds: 3 }],
      clips: [{ id: "clip-1", cue_id: "cue-1", asset_id: "asset-1", segment_id: "segment-1", source_reference: "workspace://clip.mp4", source_start_seconds: 1, source_end_seconds: 4, timeline_start_seconds: 0, timeline_end_seconds: 3, match_score: .9, match_reasons: ["已审核"], needs_confirmation: false, role: "b_roll" as const, locked: false, manually_selected: false }],
      duration_seconds: 3, low_confidence_clip_count: 0,
      narration_reference: "narration://artifact/audio-1", output_url: null, subtitle_url: null,
      message: "可以渲染", created_at: task.created_at, updated_at: task.updated_at,
    };
    const initial = workspace(task, montage);
    const refreshed = workspace(
      { ...task, current_timeline_version_id: "timeline-version-3", timeline_sequence: 3 },
      { ...montage, clips: [{ ...montage.clips[0], source_start_seconds: 1.5 }], message: "镜头已更新，需要重新渲染。" },
    );
    mocks.exactEdit.mockResolvedValue({ montage: { ...montage, message: "不直接采用编辑响应" }, operation: { id: "operation-1" } });
    mocks.getWorkspace.mockResolvedValue(refreshed);

    render(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    expect(screen.getByText(/版本 2$/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "选择镜头 1" }));
    const trimField = screen.getByLabelText("镜头 1 裁剪起点");
    trimField.focus(); fireEvent.change(trimField, { target: { value: "1.5" } });
    mocks.exactEdit.mockResolvedValue({ operation: { id: "trim-edit" } });
    fireEvent.keyDown(trimField, { key: "Enter" });

    await waitFor(() => expect(mocks.exactEdit).toHaveBeenCalledWith("task-1", {
      operation_type: "TrimClip",
      clip_id: "clip-1",
      source_start_seconds: 1.5,
    }));
    expect(mocks.exactPreview).not.toHaveBeenCalled();
    expect(await screen.findByText(/版本 3$/)).toBeInTheDocument();
    expect(screen.getByLabelText("源素材预览")).toBeInTheDocument();
    expect(screen.queryByText("不直接采用编辑响应")).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("镜头分割位置"), { target: { value: "0" } });
    expect(screen.getByRole("button", { name: "切开镜头" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText("镜头分割位置"), { target: { value: "1.2" } });
    fireEvent.click(screen.getByRole("button", { name: "切开镜头" }));
    await waitFor(() => expect(mocks.exactEdit).toHaveBeenLastCalledWith("task-1", {
      operation_type: "SplitClip", clip_id: "clip-1", split_time_seconds: 1.2,
    }));
    expect(mocks.exactPreview).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "删除镜头…" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "复制镜头" }));
    await waitFor(() => expect(mocks.exactEdit).toHaveBeenLastCalledWith("task-1", {
      operation_type: "DuplicateClip", clip_id: "clip-1",
    }));
    fireEvent.change(screen.getByLabelText("画面倍速"), { target: { value: "1.5" } });
    fireEvent.blur(screen.getByLabelText("画面倍速"));
    await waitFor(() => expect(mocks.exactEdit).toHaveBeenLastCalledWith("task-1", {
      operation_type: "ChangeClipSpeed", clip_id: "clip-1", playback_rate: 1.5,
    }));
    fireEvent.change(screen.getByLabelText("关联倍速"), { target: { value: "2" } });
    fireEvent.blur(screen.getByLabelText("关联倍速"));
    await waitFor(() => expect(mocks.exactEdit).toHaveBeenLastCalledWith("task-1", {
      operation_type: "ChangeLinkedSpeed", clip_id: "clip-1", playback_rate: 2,
    }));
  });

  it("applies one recorded narration replacement and refreshes its version", async () => {
    const task = {
      ...baseTask,
      stage: "montage" as const,
      stage_status: "waiting_for_user" as const,
      status: "waiting_for_user" as const,
      progress: 70,
      current_timeline_version_id: "timeline-version-2",
      timeline_sequence: 2,
      can_undo: true,
    };
    const montage = {
      id: "montage-1", task_id: task.id, script_id: "script-1", script_title: "故事板",
      status: "ready" as const,
      cues: [{ id: "cue-1", source_section_id: "section-1", narration_segment_id: "narration-segment-1", text: "第一段旁白", duration_seconds: 3 }],
      clips: [{ id: "clip-1", cue_id: "cue-1", asset_id: "asset-1", segment_id: "segment-1", source_reference: "workspace://clip.mp4", source_start_seconds: 1, source_end_seconds: 4, timeline_start_seconds: 0, timeline_end_seconds: 3, match_score: .9, match_reasons: ["已审核"], needs_confirmation: false, role: "b_roll" as const, locked: false, manually_selected: false }],
      duration_seconds: 3, low_confidence_clip_count: 0,
      narration_reference: "narration://artifact/audio-1", output_url: null, subtitle_url: null,
      audio_timeline: { format_version: 1 as const, tracks: [{ id: "audio-narration", name: "旁白", role: "narration" as const, gain_db: 0, muted: false, locked: false, duck_under_voice: false, clips: [] }] },
      message: "可以渲染", created_at: task.created_at, updated_at: task.updated_at,
    };
    const initial = workspace(task, montage);
    const refreshed = workspace({ ...task, current_timeline_version_id: "timeline-version-3", timeline_sequence: 3 }, {
      ...montage, narration_reference: "recording:rec-1", message: "旁白已替换，需要重新渲染。",
    });
    mocks.saveRecording.mockResolvedValue({ source_ref: "recording:rec-1", artifact_id: "artifact-rec-1", audio_url: "/api/recordings/rec-1.wav", duration_ms: 3000, applied: false });
    mocks.previewAudioEdit.mockResolvedValue({
      intent: { id: "intent-audio", workspace_id: "local", task_id: task.id, raw_command: "用本机录音替换整条旁白", context_stage: "montage", selected_object_refs: [], playhead_ms: null, intent_type: "audio_edit", scope: {}, constraints: {}, confidence: 1, status: "compiled", created_at: task.created_at },
      proposal: { id: "proposal-replace-narration", workspace_id: "local", task_id: task.id, intent_id: "intent-audio", summary: "用本机录音替换整条旁白", target_refs: ["timeline:timeline-version-2"], operation: { operation_type: "EditAudioTimeline", payload: {} }, invalidated_refs: ["artifact:audio:v2"], preview: { before: { ref: "timeline:timeline-version-2", label: "当前版本", detail: "已保存" }, after: { ref: "timeline:proposal", label: "待确认版本", detail: "替换旁白" } }, external_effects: ["未确认前不写入时间线。"], status: "proposed" as const, created_at: task.created_at, updated_at: task.updated_at },
    });
    mocks.confirmEdit.mockResolvedValue({ operation: { id: "recording-replacement" } });
    mocks.getWorkspace.mockResolvedValue(refreshed);

    render(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    fireEvent.click(screen.getByRole("button", { name: "电脑录音替换旁白" }));
    fireEvent.click(await screen.findByRole("button", { name: "提交录音" }));

    await waitFor(() => expect(mocks.saveRecording).toHaveBeenCalledWith("task-1", expect.any(Blob)));
    await waitFor(() => expect(mocks.previewAudioEdit).toHaveBeenCalledWith("task-1", {
      base_version_id: "timeline-version-2",
      action: "replace_narration",
      target_id: "audio-narration",
      values: { source_ref: "recording:rec-1" },
      raw_command: "用本机录音替换整条旁白",
    }));
    await waitFor(() => expect(mocks.confirmEdit).toHaveBeenCalledExactlyOnceWith("task-1", "proposal-replace-narration"));
    await waitFor(() => expect(mocks.getWorkspace).toHaveBeenCalledWith("task-1"));
    expect(await screen.findByText(/版本 3$/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "应用到草稿" })).not.toBeInTheDocument();
  });

  it("keeps a first task recording unapplied until the user explicitly adopts it", async () => {
    const task = { ...baseTask, stage_status: "not_started" as const, status: "running" as const };
    const initial = workspace(task);
    initial.scripts = [{
      id: "script-1", task_id: task.id, run_id: "run-1", topic_id: "topic-1", title: "已确认文案",
      language: "zh-CN", selection_status: "confirmed", created_at: task.created_at,
      updated_at: task.updated_at, confirmed_at: task.updated_at,
      sections: [{ id: "section-1", title: "开场", content: "当前文案",
        visual_prompt: "现场", evidence_reference_ids: [] }],
    }];
    const recording = {
      source_ref: "recording:first", artifact_id: "artifact-first",
      audio_url: "/api/recordings/first.wav", duration_ms: 2400, applied: false,
    };
    mocks.listRecordings.mockResolvedValue({ items: [] });
    mocks.saveRecording.mockResolvedValue(recording);
    mocks.adoptRecording.mockResolvedValue({ source_kind: "recorded" });
    mocks.getWorkspace.mockResolvedValue(initial);

    render(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    fireEvent.click(screen.getByRole("button", { name: "录一段旁白" }));
    fireEvent.click(await screen.findByRole("button", { name: "提交录音" }));

    expect(await screen.findByText("已保存 · 尚未采用")).toBeInTheDocument();
    expect(mocks.adoptRecording).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "采用这段录音" }));
    await waitFor(() => expect(mocks.adoptRecording).toHaveBeenCalledExactlyOnceWith("task-1", {
      script_id: "script-1", source_section_id: "section-1", source_ref: "recording:first",
    }));
    expect(await screen.findByText(/本机录音已采用为当前文案的配音来源/)).toBeInTheDocument();
  });

  it("surfaces an honest recording error and never previews when the upload fails", async () => {
    const task = {
      ...baseTask,
      stage: "montage" as const,
      stage_status: "waiting_for_user" as const,
      status: "waiting_for_user" as const,
      progress: 70,
      current_timeline_version_id: "timeline-version-2",
      timeline_sequence: 2,
      can_undo: true,
    };
    const montage = {
      id: "montage-1", task_id: task.id, script_id: "script-1", script_title: "故事板",
      status: "ready" as const, cues: [], clips: [], duration_seconds: 3, low_confidence_clip_count: 0,
      narration_reference: "narration://artifact/audio-1", output_url: null, subtitle_url: null,
      message: "可以渲染", created_at: task.created_at, updated_at: task.updated_at,
    };
    const initial = workspace(task, montage);
    mocks.saveRecording.mockRejectedValue(new mocks.MockCreationPlanApiError(409, "stage_invalid", "当前任务阶段无法保存录音", null, "请回到剪辑阶段再重试。"));

    render(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    fireEvent.click(screen.getByRole("button", { name: "电脑录音替换旁白" }));
    fireEvent.click(await screen.findByRole("button", { name: "提交录音" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("当前任务阶段无法保存录音");
    expect(alert).toHaveTextContent("请回到剪辑阶段再重试");
    expect(mocks.previewAudioEdit).not.toHaveBeenCalled();
    expect(mocks.confirmEdit).not.toHaveBeenCalled();
  });

  it("keeps each clip's quick draft through selection and a failed direct apply", async () => {
    mocks.showQuickInspector = true;
    const task = {
      ...baseTask, stage: "montage" as const, stage_status: "waiting_for_user" as const,
      status: "waiting_for_user" as const, current_timeline_version_id: "timeline-version-2", timeline_sequence: 2,
      can_undo: true,
    };
    const clip = {
      id: "clip-1", cue_id: "cue-1", asset_id: "asset-1", segment_id: "segment-1",
      source_reference: "workspace://clip.mp4", source_start_seconds: 1, source_end_seconds: 4,
      timeline_start_seconds: 0, timeline_end_seconds: 3, match_score: .9,
      match_reasons: ["已审核"], needs_confirmation: false, role: "b_roll" as const,
      locked: false, manually_selected: false,
    };
    const montage = {
      id: "montage-1", task_id: task.id, script_id: "script-1", script_title: "故事板",
      status: "ready" as const, cues: [],
      clips: [clip, { ...clip, id: "clip-2", timeline_start_seconds: 3, timeline_end_seconds: 6 }],
      duration_seconds: 6, low_confidence_clip_count: 0,
      narration_reference: "narration://artifact/audio-1", output_url: null, subtitle_url: null,
      message: "可以渲染", created_at: task.created_at, updated_at: task.updated_at,
    };
    const initial = workspace(task, montage);
    mocks.getWorkspace.mockResolvedValue(initial);
    mocks.exactEdit.mockRejectedValue(new Error("草稿暂不可用"));

    render(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    fireEvent.click(screen.getByRole("button", { name: "选择镜头 1" }));
    const quick = screen.getByRole("region", { name: "镜头 01 快速编辑" });
    const quickStart = within(quick).getByLabelText("镜头 1 快速裁剪起点");
    fireEvent.change(quickStart, { target: { value: "1.5" } });
    expect(screen.getByText(/未应用 · 版本 2/)).toBeInTheDocument();
    fireEvent.blur(quickStart);
    await waitFor(() => expect(mocks.exactEdit).toHaveBeenCalledWith(task.id, {
      operation_type: "TrimClip", clip_id: "clip-1", source_start_seconds: 1.5,
    }));
    expect(mocks.exactPreview).not.toHaveBeenCalled();
    expect(await screen.findByRole("alert")).toHaveTextContent("草稿暂不可用");
    fireEvent.click(screen.getByRole("button", { name: "选择镜头 2" }));
    expect(screen.getByLabelText("镜头 2 快速裁剪起点")).toHaveValue(1);
    fireEvent.click(screen.getByRole("button", { name: "选择镜头 1" }));
    expect(screen.getByLabelText("镜头 1 快速裁剪起点")).toHaveValue(1.5);
    expect(screen.getByLabelText("镜头 1 裁剪起点")).toHaveValue(1.5);
    await waitFor(() => expect(screen.getByLabelText("镜头 1 快速裁剪起点")).toHaveValue(1.5));
    fireEvent.click(within(quick).getByRole("button", { name: "换素材" }));
    fireEvent.click(within(quick).getByRole("button", { name: "查看候选" }));
    expect(mocks.openQuickDetails).toHaveBeenCalledOnce();
    mocks.undoTimeline.mockResolvedValue({});
    mocks.getWorkspace.mockResolvedValue(workspace(
      { ...task, current_timeline_version_id: "timeline-version-1", timeline_sequence: 1 },
      { ...montage, clips: [{ ...clip, source_start_seconds: 2 }, montage.clips[1]] },
    ));
    fireEvent.click(screen.getByRole("button", { name: "撤销" }));
    await waitFor(() => expect(screen.getByLabelText("镜头 1 裁剪起点")).toHaveValue(2));
  });

  it("offers an explicit version snapshot for a legacy storyboard without replacing its clips", async () => {
    const task = {
      ...baseTask, stage: "montage" as const, stage_status: "waiting_for_user" as const,
      status: "waiting_for_user" as const, current_timeline_version_id: null,
    };
    const montage = {
      id: "montage-1", task_id: task.id, script_id: "script-1", script_title: "原故事板",
      status: "ready" as const, cues: [], clips: [{
        id: "clip-1", cue_id: "cue-1", asset_id: "asset-1", segment_id: "segment-1",
        source_reference: "workspace://clip.mp4", source_start_seconds: 1,
        source_end_seconds: 4, timeline_start_seconds: 0, timeline_end_seconds: 3,
        match_score: .9, match_reasons: ["已审核"], needs_confirmation: false,
        role: "b_roll" as const, locked: false, manually_selected: false,
      }],
      duration_seconds: 3, low_confidence_clip_count: 0,
      narration_reference: "narration://artifact/audio-1", output_url: null, subtitle_url: null,
      message: "保留原故事板", created_at: task.created_at, updated_at: task.updated_at,
    };
    const initial = workspace(task, montage);
    mocks.getWorkspace.mockResolvedValue(initial);
    mocks.exactEdit.mockRejectedValueOnce(new mocks.MockCreationPlanApiError(
      409, "timeline_version_required", "当前故事板还没有可撤销的初始版本。",
    )).mockResolvedValue({ operation: { id: "trim-after-init" } });
    mocks.initializeTimeline.mockResolvedValue({ version_id: "timeline-version-1" });
    render(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    fireEvent.click(screen.getByRole("button", { name: "选择镜头 1" }));
    const trimField = screen.getByLabelText("镜头 1 裁剪起点");
    trimField.focus(); fireEvent.change(trimField, { target: { value: "1.5" } });
    fireEvent.keyDown(trimField, { key: "Enter" });
    const initialize = await screen.findByRole("button", { name: "保留当前故事板并建立版本" });
    expect(mocks.initializeTimeline).not.toHaveBeenCalled();
    mocks.getWorkspace.mockResolvedValue(workspace(
      { ...task, current_timeline_version_id: "timeline-version-1", timeline_sequence: 1 }, montage,
    ));
    fireEvent.click(initialize);
    await waitFor(() => expect(mocks.initializeTimeline).toHaveBeenCalledWith(task.id));
    expect(await screen.findByText(/版本 1$/)).toBeInTheDocument();
    expect(screen.getByLabelText("镜头 1 裁剪起点")).toHaveValue(1.5);
    fireEvent.keyDown(screen.getByLabelText("镜头 1 裁剪起点"), { key: "Enter" });
    await waitFor(() => expect(mocks.exactEdit).toHaveBeenLastCalledWith(task.id, {
      operation_type: "TrimClip", clip_id: "clip-1", source_start_seconds: 1.5,
    }));
  });

  it("uses typed operations for replace, reorder, lock, confirmation, and undo", async () => {
    const task = {
      ...baseTask,
      stage: "montage" as const,
      stage_status: "waiting_for_user" as const,
      status: "waiting_for_user" as const,
      progress: 70,
      current_timeline_version_id: "timeline-version-4",
      timeline_sequence: 4,
      can_undo: true,
    };
    const clip = {
      id: "clip-1", cue_id: "cue-1", asset_id: "asset-1", segment_id: "segment-1",
      source_reference: "workspace://clip-1.mp4", source_start_seconds: 1, source_end_seconds: 4,
      timeline_start_seconds: 0, timeline_end_seconds: 3, match_score: .9,
      match_reasons: ["用途匹配", "时长满足"], needs_confirmation: true,
      role: "b_roll" as const, locked: false, manually_selected: false,
    };
    const montage = {
      id: "montage-1", task_id: task.id, script_id: "script-1", script_title: "故事板",
      status: "ready" as const,
      cues: [
        { id: "cue-1", source_section_id: "section-1", narration_segment_id: "narration-segment-1", text: "第一段旁白", duration_seconds: 3 },
        { id: "cue-2", source_section_id: "section-2", narration_segment_id: "narration-segment-2", text: "第二段旁白", duration_seconds: 3 },
      ],
      clips: [
        clip,
        { ...clip, id: "clip-2", cue_id: "cue-2", asset_id: "asset-2", segment_id: "segment-2", source_reference: "workspace://clip-2.mp4", timeline_start_seconds: 3, timeline_end_seconds: 6, needs_confirmation: false },
      ],
      duration_seconds: 6, low_confidence_clip_count: 1,
      narration_reference: "narration://artifact/audio-1", output_url: "/api/old-preview", subtitle_url: "/api/old-subtitles",
      message: "等待镜头确认", created_at: task.created_at, updated_at: task.updated_at,
    };
    const initial = workspace(task, montage);
    mocks.exactEdit.mockResolvedValue({ montage: { ...montage, message: "不直接采用 exact 响应" }, operation: { id: "operation-exact" } });
    mocks.undoTimeline.mockResolvedValue({ montage: { ...montage, message: "不直接采用 undo 响应" }, operation: { id: "operation-undo" } });
    mocks.getCandidates.mockResolvedValue([{
      asset_id: "asset-3", segment_id: "segment-3", file_name: "团队协作.mp4",
      start_seconds: 5, end_seconds: 8, description: "团队协作画面", score: .96, reasons: ["标签命中"],
    }]);
    mocks.getWorkspace.mockResolvedValue(initial);

    render(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);

    fireEvent.click(screen.getByRole("button", { name: "选择镜头 1" }));
    for (const [label, operation] of [
      ["向后移动", { operation_type: "ReorderClip", clip_id: "clip-1", position: 1 }],
      ["锁定镜头", { operation_type: "SetClipLock", clip_id: "clip-1", locked: true }],
      ["确认镜头", { operation_type: "ConfirmClip", clip_id: "clip-1" }],
    ] as const) {
      fireEvent.click(screen.getByRole("button", { name: label }));
      await waitFor(() => expect(mocks.exactEdit).toHaveBeenCalledWith("task-1", operation));
      expect(screen.queryByLabelText("变更预览")).not.toBeInTheDocument();
    }

    fireEvent.click(screen.getAllByRole("button", { name: "换素材" })[0]);
    await waitFor(() => expect(mocks.getCandidates).toHaveBeenCalledWith("task-1", "clip-1"));
    fireEvent.change(await screen.findByLabelText("替换为已审核镜头"), { target: { value: "asset-3:segment-3" } });
    await waitFor(() => expect(mocks.exactEdit).toHaveBeenCalledWith("task-1", {
      operation_type: "ReplaceClip", clip_id: "clip-1", asset_id: "asset-3", segment_id: "segment-3", source_start_seconds: 5,
    }));
    expect(screen.queryByLabelText("变更预览")).not.toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("button", { name: "撤销" })).toBeEnabled());

    // WIL-90：主轨 Insert —— clip_id 是锚点镜头，插在它之后。后端对每个操作都要求
    // clip_id，缺了直接 409，所以 payload 必须带且等于当前选中镜头。
    // 仍不带 duration（时长继承源片段 span）。
    fireEvent.click(screen.getAllByRole("button", { name: "添加片段" })[0]);
    await waitFor(() => expect(mocks.getCandidates).toHaveBeenCalledWith("task-1", "clip-1"));
    fireEvent.change(await screen.findByLabelText("插在当前镜头之后"), { target: { value: "asset-3:segment-3" } });
    await waitFor(() => expect(mocks.exactEdit).toHaveBeenCalledWith("task-1", {
      operation_type: "InsertClip", clip_id: "clip-1", asset_id: "asset-3", segment_id: "segment-3", source_start_seconds: 5,
    }));
    const insertCall = mocks.exactEdit.mock.calls.at(-1)?.[1];
    // 锚点必填：等于当前选中镜头 id，否则后端 409
    expect(insertCall?.clip_id).toBe("clip-1");
    expect(insertCall).not.toHaveProperty("duration_seconds");
    expect(screen.queryByLabelText("变更预览")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "撤销" }));
    await waitFor(() => expect(mocks.undoTimeline).toHaveBeenCalledWith("task-1"));
    await waitFor(() => expect(mocks.getWorkspace).toHaveBeenCalledWith("task-1"));
    await waitFor(() => expect(screen.getByRole("button", { name: "撤销" })).toBeEnabled());
    expect(screen.queryByText("不直接采用 exact 响应")).not.toBeInTheDocument();
    expect(screen.queryByText("不直接采用 undo 响应")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "选择镜头 1" }));
    fireEvent.change(screen.getAllByLabelText("画面显示方式")[0], { target: { value: "fit" } });
    await waitFor(() => expect(mocks.exactEdit).toHaveBeenCalledWith("task-1", {
      operation_type: "ChangeCrop", clip_id: "clip-1", fit_mode: "fit", crop_x: .5, crop_y: .5,
    }));
    expect(screen.queryByLabelText("变更预览")).not.toBeInTheDocument();
  });

  it("selects and previews persisted narration segments with API-owned audio URLs", async () => {
    const task = {
      ...baseTask,
      stage: "montage" as const,
      stage_status: "waiting_for_user" as const,
      status: "waiting_for_user" as const,
      progress: 70,
    };
    const narration = {
      task,
      script_id: "script-1",
      audio_reference: "narration://artifact/full-audio",
      duration_seconds: 6,
      sample_rate: 24000,
      model_id: "qwen3-tts-12hz-0.6b-base-8bit",
      voice_profile_id: "voice-1",
      source_kind: "generated" as const, source_artifact_ref: null, source_sha256: null, source_script_digest: null,
      language: "zh" as const,
      pronunciation_version: "v1",
      segments: [
        {
          id: "narration-segment-1", source_section_id: "section-1", text: "第一段真实配音",
          audio_reference: "narration://artifact/segment-1", audio_url: "/api/creation-tasks/task-1/narration/segments/narration-segment-1/audio",
          duration_seconds: 3, sample_rate: 24000, model_id: "qwen3-tts-12hz-0.6b-base-8bit",
          voice_profile_id: "voice-1", voice_version: "v1", language: "zh" as const, pronunciation_version: "p1", status: "ready" as const,
          source_kind: "generated" as const, source_artifact_ref: null, source_sha256: null, source_script_digest: null,
        },
        {
          id: "narration-segment-2", source_section_id: "section-2", text: "第二段真实配音",
          audio_reference: "narration://artifact/segment-2", audio_url: "/api/creation-tasks/task-1/narration/segments/narration-segment-2/audio",
          duration_seconds: 3, sample_rate: 24000, model_id: "qwen3-tts-12hz-0.6b-base-8bit",
          voice_profile_id: "voice-1", voice_version: "v1", language: "zh" as const, pronunciation_version: "p1", status: "ready" as const,
          source_kind: "generated" as const, source_artifact_ref: null, source_sha256: null, source_script_digest: null,
        },
      ],
    };
    const initial = workspace(task);
    initial.narration = narration;
    const refreshed = workspace(task);
    refreshed.narration = {
      ...narration,
      segments: [{ ...narration.segments[0], status: "generating" as const }, narration.segments[1]],
    };
    mocks.previewEdit.mockResolvedValue({
      intent: {
        id: "intent-narration", workspace_id: "local", task_id: task.id,
        raw_command: "这句读慢一点", context_stage: "narration",
        selected_object_refs: ["narration:narration-segment-1"], playhead_ms: null,
        intent_type: "regenerate_narration_segment", scope: { narration_segment_id: "narration-segment-1" }, constraints: {},
        confidence: .97, status: "compiled", created_at: task.created_at,
      },
      proposal: {
        id: "proposal-narration", workspace_id: "local", task_id: task.id, intent_id: "intent-narration",
        summary: "只重做第一段配音。", target_refs: ["narration:narration-segment-1"],
        operation: { operation_type: "RegenerateNarrationSegment", payload: { narration_segment_id: "narration-segment-1", delivery_instruction: "读慢一点" } },
        invalidated_refs: ["narration:narration-segment-1", "artifact:preview:v4"],
        preview: {
          before: { ref: "narration:narration-segment-1", label: "当前配音", detail: "正常语速" },
          after: { ref: "narration:narration-segment-1", label: "重做配音", detail: "读慢一点" },
        },
        external_effects: ["其他配音段保持不变。"], status: "proposed",
        created_at: task.created_at, updated_at: task.updated_at,
      },
    });
    mocks.confirmEdit.mockResolvedValue({
      operation: { id: "operation-narration" },
      montage: null,
      narration: { ...narration, segments: [{ ...narration.segments[0], text: "不应直接显示" }, narration.segments[1]] },
    });
    mocks.getWorkspace.mockResolvedValue(refreshed);

    render(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    fireEvent.click(screen.getByRole("button", { name: "配音：已完成" }));
    expect(screen.getByText("第一段真实配音")).toBeInTheDocument();
    expect(screen.getByLabelText("试听配音 1")).toHaveAttribute("src", "/api/creation-tasks/task-1/narration/segments/narration-segment-1/audio");

    fireEvent.click(screen.getByRole("button", { name: "选择配音 1" }));
    expect(within(screen.getByLabelText("修改视频")).getByText("配音 1")).toBeInTheDocument();
    expect(screen.getByLabelText("已选配音详情")).toHaveTextContent("第一段真实配音");
    expect(screen.getByLabelText("已选配音详情")).toHaveTextContent("片段时长");
    fireEvent.change(screen.getByLabelText("局部修改要求"), { target: { value: "这句读慢一点" } });
    fireEvent.click(screen.getByRole("button", { name: "预览修改" }));
    await waitFor(() => expect(mocks.previewEdit).toHaveBeenCalledWith("task-1", {
      raw_command: "这句读慢一点",
      context_stage: "narration",
      selected_object_refs: ["narration:narration-segment-1"],
      playhead_ms: null,
    }));
    expect(await screen.findByLabelText("变更预览")).toHaveTextContent("artifact:preview:v4");
    expect(screen.getByLabelText("变更预览")).toHaveTextContent("其他配音段保持不变");

    fireEvent.click(screen.getByRole("button", { name: "应用到草稿" }));
    await waitFor(() => expect(mocks.confirmEdit).toHaveBeenCalledWith("task-1", "proposal-narration"));
    await waitFor(() => expect(screen.getByLabelText("已选配音详情")).toHaveTextContent("正在生成"));
    expect(screen.queryByText("不应直接显示")).not.toBeInTheDocument();
  });

  it("previews and confirms a selected clip edit using only API facts", async () => {
    const task = { ...baseTask, stage: "montage" as const, stage_status: "waiting_for_user" as const, status: "waiting_for_user" as const, progress: 70 };
    const montage = {
      id: "montage-1", task_id: task.id, script_id: "script-1", script_title: "故事板",
      status: "ready" as const,
      cues: [{ id: "cue-1", source_section_id: "section-1", narration_segment_id: "narration-segment-1", text: "第一段旁白", duration_seconds: 3 }],
      clips: [{ id: "clip-1", cue_id: "cue-1", asset_id: "asset-1", segment_id: "segment-1", source_reference: "workspace://clip.mp4", source_start_seconds: 1, source_end_seconds: 4, timeline_start_seconds: 0, timeline_end_seconds: 3, match_score: .9, match_reasons: ["已审核"], needs_confirmation: false, role: "b_roll" as const, locked: false, manually_selected: false }],
      duration_seconds: 3, low_confidence_clip_count: 0,
      narration_reference: "narration://artifact/audio-1", output_url: "/api/old-preview", subtitle_url: "/api/old-subtitles",
      message: "可以修改", created_at: task.created_at, updated_at: task.updated_at,
    };
    const preview = {
      intent: {
        id: "intent-1", workspace_id: "local", task_id: task.id,
        raw_command: "换成另一个镜头", context_stage: "montage",
        selected_object_refs: ["clip:clip-1"], playhead_ms: null,
        intent_type: "replace_clip", scope: { clip_id: "clip-1" }, constraints: {},
        confidence: .98, status: "compiled", created_at: task.created_at,
      },
      proposal: {
        id: "proposal-1", workspace_id: "local", task_id: task.id, intent_id: "intent-1",
        summary: "把镜头 1 替换为已审核的团队协作画面。",
        target_refs: ["clip:clip-1"],
        operation: { operation_type: "ReplaceClip" as const, payload: { clip_id: "clip-1", asset_id: "asset-2", segment_id: "segment-2", source_start_seconds: 5 } },
        invalidated_refs: ["artifact:preview:v1", "artifact:final:v1"],
        preview: {
          before: { ref: "segment:segment-1", label: "人物口播画面", detail: "asset-1 · 1.0 秒" },
          after: { ref: "segment:segment-2", label: "团队协作画面", detail: "asset-2 · 5.0 秒" },
        },
        external_effects: ["确认后只更新任务版本，不会立即调用模型或渲染。"],
        status: "proposed" as const, created_at: task.created_at, updated_at: task.updated_at,
      },
    };
    const refreshed = workspace(task, {
      ...montage,
      clips: [{ ...montage.clips[0], asset_id: "asset-2", segment_id: "segment-2", source_reference: "workspace://replacement.mp4", source_start_seconds: 5, source_end_seconds: 8 }],
      output_url: null,
      subtitle_url: null,
      message: "镜头已替换，需要重新渲染。",
    });
    mocks.previewEdit.mockResolvedValue(preview);
    mocks.confirmEdit.mockResolvedValue({
      operation: { id: "operation-1", status: "applied" },
      montage: { ...montage, message: "这个 confirm 响应不应直接显示" },
    });
    mocks.getWorkspace.mockResolvedValue(refreshed);

    render(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={workspace(task, montage)} />);
    fireEvent.click(screen.getByRole("button", { name: "选择镜头 1" }));
    const player = screen.getByLabelText("剪辑成片预览") as HTMLVideoElement;
    expect(player.currentTime).toBe(1.5);
    player.currentTime = 1.25;
    fireEvent.seeked(player);
    fireEvent.change(screen.getByLabelText("局部修改要求"), { target: { value: "换成另一个镜头" } });
    fireEvent.click(screen.getByRole("button", { name: "预览修改" }));

    await waitFor(() => expect(mocks.previewEdit).toHaveBeenCalledWith("task-1", {
      raw_command: "换成另一个镜头",
      context_stage: "montage",
      selected_object_refs: ["clip:clip-1"],
      playhead_ms: 1250,
    }));
    expect(await screen.findByLabelText("变更预览")).toHaveTextContent("人物口播画面");
    expect(screen.getByLabelText("变更预览")).toHaveTextContent("团队协作画面");
    expect(screen.getByLabelText("变更预览")).toHaveTextContent("artifact:preview:v1");
    expect(screen.getByLabelText("变更预览")).toHaveTextContent("不会立即调用模型或渲染");
    expect(mocks.confirmEdit).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "返回详情" }));
    expect(screen.queryByLabelText("变更预览")).not.toBeInTheDocument();
    expect(mocks.confirmEdit).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "预览修改" }));
    await screen.findByLabelText("变更预览");
    fireEvent.click(screen.getByRole("button", { name: "应用到草稿" }));

    await waitFor(() => expect(mocks.confirmEdit).toHaveBeenCalledWith("task-1", "proposal-1"));
    await waitFor(() => expect(mocks.getWorkspace).toHaveBeenCalledWith("task-1"));
    expect(await screen.findByLabelText("源素材预览")).toBeInTheDocument();
    expect(screen.queryByText("这个 confirm 响应不应直接显示")).not.toBeInTheDocument();
  });

  it("prepares person commands and requires preview before confirmation", async () => {
    const task = {
      ...baseTask,
      stage: "montage" as const,
      stage_status: "waiting_for_user" as const,
      status: "waiting_for_user" as const,
      progress: 70,
    };
    const clip = {
      id: "clip-1", cue_id: "cue-1", asset_id: "asset-1", segment_id: "segment-1",
      source_reference: "workspace://clip-1.mp4", source_start_seconds: 1, source_end_seconds: 4,
      timeline_start_seconds: 0, timeline_end_seconds: 3, match_score: .9,
      match_reasons: ["已审核"], needs_confirmation: false,
      role: "b_roll" as const, locked: false, manually_selected: false,
    };
    const montage = {
      id: "montage-1", task_id: task.id, script_id: "script-1", script_title: "故事板",
      status: "ready" as const,
      cues: [
        { id: "cue-1", source_section_id: "section-1", narration_segment_id: "narration-segment-1", text: "第一段旁白", duration_seconds: 3 },
        { id: "cue-2", source_section_id: "section-2", narration_segment_id: "narration-segment-2", text: "第二段旁白", duration_seconds: 3 },
      ],
      clips: [
        clip,
        { ...clip, id: "clip-2", cue_id: "cue-2", person_render_id: "person-render-1", timeline_start_seconds: 3, timeline_end_seconds: 6 },
      ],
      duration_seconds: 6, low_confidence_clip_count: 0,
      narration_reference: "narration://artifact/audio-1", output_url: null, subtitle_url: null,
      message: "可以修改", created_at: task.created_at, updated_at: task.updated_at,
    };
    const initial = workspace(task, montage);
    initial.narration = {
      task,
      script_id: "script-1",
      audio_reference: "narration://artifact/audio-1",
      duration_seconds: 6,
      sample_rate: 24000,
      model_id: "qwen3-tts",
      voice_profile_id: "voice-cc",
      source_kind: "generated", source_artifact_ref: null, source_sha256: null, source_script_digest: null,
      language: "zh",
      pronunciation_version: "v1",
      segments: [{
        id: "narration-segment-2", source_section_id: "section-2", text: "第二段旁白",
        audio_reference: "narration://artifact/segment-2", audio_url: "/api/audio-2",
        duration_seconds: 3, sample_rate: 24000, model_id: "qwen3-tts",
        voice_profile_id: "voice-cc", voice_version: "voice-v3", language: "zh",
        pronunciation_version: "v1", status: "ready",
        source_kind: "generated", source_artifact_ref: null, source_sha256: null, source_script_digest: null,
      }],
    };
    initial.person_renders = [{
      id: "person-render-1", task_id: task.id,
      person_profile_id: "person-cc", person_version: "person-v2",
      voice_profile_id: "voice-cc", voice_version: "voice-v3",
      narration_segment_id: "narration-segment-2", visual_reference: "workspace://cc.mp4",
      model_id: "musetalk-local", motion: "natural", expression: "neutral",
      status: "ready", duration_seconds: 3, alignment_evidence: "aligned",
      media_url: "/api/person-render-1", failure_reason: null, impact: null, repair_action: null,
      created_at: task.created_at, updated_at: task.updated_at,
    }];
    mocks.previewEdit
      .mockResolvedValueOnce({
        intent: {
          id: "intent-person-add", workspace_id: "local", task_id: task.id,
          raw_command: "这一段换成当前人物出镜", context_stage: "montage",
          selected_object_refs: ["clip:clip-1"], playhead_ms: null,
          intent_type: "change_person_segment", scope: { clip_id: "clip-1" }, constraints: {},
          confidence: .99, status: "compiled", created_at: task.created_at,
        },
        proposal: {
          id: "proposal-person-add", workspace_id: "local", task_id: task.id, intent_id: "intent-person-add",
          summary: "把镜头 1 换成当前人物出镜。", target_refs: ["clip:clip-1"],
          operation: { operation_type: "ChangePersonSegment", payload: { clip_id: "clip-1" } },
          invalidated_refs: ["clip:clip-1", "artifact:preview:v1"],
          preview: {
            before: { ref: "clip:clip-1", label: "当前镜头", detail: "补充画面" },
            after: { ref: "clip:clip-1", label: "人物口型", detail: "当前人物版本" },
          },
          external_effects: ["其他镜头与配音保持不变。"], status: "proposed",
          created_at: task.created_at, updated_at: task.updated_at,
        },
      })
      .mockResolvedValueOnce({
        intent: {
          id: "intent-person-regenerate", workspace_id: "local", task_id: task.id,
          raw_command: "这里只重做口型，其他都不要动", context_stage: "montage",
          selected_object_refs: ["clip:clip-2"], playhead_ms: null,
          intent_type: "regenerate_person_render", scope: { clip_id: "clip-2" }, constraints: {},
          confidence: .99, status: "compiled", created_at: task.created_at,
        },
        proposal: {
          id: "proposal-person-regenerate", workspace_id: "local", task_id: task.id, intent_id: "intent-person-regenerate",
          summary: "只重做镜头 2 的口型。", target_refs: ["clip:clip-2"],
          operation: { operation_type: "RegeneratePersonRender", payload: { clip_id: "clip-2" } },
          invalidated_refs: ["person-render:person-render-1", "artifact:preview:v1"],
          preview: {
            before: { ref: "person-render:person-render-1", label: "当前口型", detail: "person-v2" },
            after: { ref: "clip:clip-2", label: "重做口型", detail: "匹配当前配音" },
          },
          external_effects: ["其他镜头、文案和配音保持不变。"], status: "proposed",
          created_at: task.created_at, updated_at: task.updated_at,
        },
      });

    const availableView = render(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);

    fireEvent.click(screen.getByRole("button", { name: "选择镜头 1" }));
    fireEvent.click(screen.getByText("人物口型", { selector: "summary" }));
    fireEvent.click(screen.getByRole("button", { name: "换成当前人物出镜" }));
    expect(screen.getByLabelText("局部修改要求")).toHaveValue("这一段换成当前人物出镜");
    expect(mocks.exactEdit).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "预览修改" }));
    await waitFor(() => expect(mocks.previewEdit).toHaveBeenNthCalledWith(1, "task-1", {
      raw_command: "这一段换成当前人物出镜",
      context_stage: "montage",
      selected_object_refs: ["clip:clip-1"],
      playhead_ms: null,
    }));
    expect(await screen.findByLabelText("变更预览")).toHaveTextContent("把镜头 1 换成当前人物出镜");

    fireEvent.click(screen.getByRole("button", { name: "选择镜头 2" }));
    fireEvent.click(screen.getByText("人物口型", { selector: "summary" }));
    const details = screen.getByLabelText("已选镜头详情");
    expect(details).toHaveTextContent("人物版本person-cc · person-v2");
    expect(details).toHaveTextContent("声音版本voice-cc · voice-v3");
    expect(details).toHaveTextContent("当前模型musetalk-local");
    expect(details).not.toHaveTextContent("能力状态");
    fireEvent.click(screen.getByRole("button", { name: "重新对口型" }));
    expect(screen.getByLabelText("局部修改要求")).toHaveValue("这里只重做口型，其他都不要动");
    expect(mocks.exactEdit).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "预览修改" }));
    await waitFor(() => expect(mocks.previewEdit).toHaveBeenNthCalledWith(2, "task-1", {
      raw_command: "这里只重做口型，其他都不要动",
      context_stage: "montage",
      selected_object_refs: ["clip:clip-2"],
      playhead_ms: null,
    }));
    expect(await screen.findByLabelText("变更预览")).toHaveTextContent("只重做镜头 2 的口型");

    availableView.unmount();
    mocks.previewEdit.mockClear();
    const unavailable = {
      ...initial,
      person_render_capability: {
        available: false,
        model_id: null,
        message: "本地 MuseTalk 运行环境尚未配置。",
        repair_action: "配置本地人物口型运行环境后重试。",
      },
    };
    render(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={unavailable} />);

    fireEvent.click(screen.getByRole("button", { name: "选择镜头 1" }));
    fireEvent.click(screen.getByText("人物口型", { selector: "summary" }));
    expect(screen.getByRole("button", { name: "换成当前人物出镜" })).toBeDisabled();
    const unavailableReason = screen.getByLabelText("人物口型不可用原因");
    expect(unavailableReason).toHaveTextContent("本地 MuseTalk 运行环境尚未配置。");
    expect(unavailableReason).toHaveTextContent("配置本地人物口型运行环境后重试。");

    fireEvent.click(screen.getByRole("button", { name: "选择镜头 2" }));
    fireEvent.click(screen.getByText("人物口型", { selector: "summary" }));
    expect(screen.getByRole("button", { name: "重新对口型" })).toBeDisabled();
    expect(mocks.previewEdit).not.toHaveBeenCalled();
  });
  it("previews original audio from the selected saved clip without immediately confirming", async () => {
    const initial=subtitleCoverWorkspace();
    initial.montage!.clips=[{id:"clip-original",cue_id:"cue-1",asset_id:"asset-1",segment_id:"segment-1",
      source_reference:"workspace://video.mp4",source_start_seconds:2,source_end_seconds:8,
      timeline_start_seconds:0,timeline_end_seconds:3,match_score:1,match_reasons:[],needs_confirmation:false,
      role:"b_roll",locked:false,manually_selected:false}];
    const preview=exactPreview(initial.task,"EditAudioTimeline");
    preview.proposal.summary="添加原声，与现有音轨混合";
    mocks.previewEdit.mockResolvedValue(preview);
    render(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial}/>);
    fireEvent.click(screen.getByRole("button",{name:/^视频剪辑：/}));
    fireEvent.click(screen.getByRole("button",{name:"选择镜头 1"}));
    fireEvent.change(screen.getByLabelText("镜头 1 裁剪起点"),{target:{value:"3"}});
    expect(screen.getByRole("button",{name:"试听原声"})).toBeDisabled();
    fireEvent.change(screen.getByLabelText("镜头 1 裁剪起点"),{target:{value:"2"}});
    fireEvent.click(screen.getByRole("button",{name:"试听原声"}));
    await waitFor(()=>expect(mocks.previewEdit).toHaveBeenCalledWith("task-1",{
      raw_command:"提取当前镜头原声",context_stage:"montage",selected_object_refs:["clip:clip-original"],playhead_ms:1500}));
    expect(screen.getByLabelText("局部修改要求")).toHaveValue("提取当前镜头原声");
    expect(await screen.findByLabelText("变更预览")).toHaveTextContent("与现有音轨混合");
    expect(mocks.confirmEdit).not.toHaveBeenCalled();
    expect(mocks.exactEdit).not.toHaveBeenCalled();
  });

  it("shows a read-only materials list when the prepare stage is opened", async () => {
    mocks.getProductionMaterials.mockResolvedValue({
      assets: [
        {
          id: "asset-1",
          workspace_id: "local",
          file_name: "外景口播.mp4",
          path: "/materials/外景口播.mp4",
          size_bytes: 1024,
          modified_at: "2026-08-30T00:00:00Z",
          duration_seconds: 12.5,
          usage_role: "b_roll",
          suggested_usage_role: "b_roll",
          usage_role_confidence: 1,
          usage_role_reason: "已审核",
          review_status: "approved",
          use_state: "available",
          analysis_status: "ready",
          analysis_message: "",
          user_tags: ["外景", "口播"],
          segments: [],
          created_at: "2026-08-30T00:00:00Z",
          updated_at: "2026-08-30T00:00:00Z",
        },
      ],
      total_count: 1,
      limit: 200,
      offset: 0,
      has_more: false,
    });
    render(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={workspace(baseTask)} />);
    fireEvent.click(screen.getByRole("button", { name: "资料准备：已完成" }));
    expect(await screen.findByRole("heading", { name: "本次任务素材" })).toBeInTheDocument();
    expect(screen.getByText("外景口播.mp4")).toBeInTheDocument();
    expect(screen.getByText(/已解析/)).toBeInTheDocument();
    expect(screen.getByText(/已通过/)).toBeInTheDocument();
    expect(mocks.getProductionMaterials).toHaveBeenCalledWith({ limit: 200 });
  });

  it("shows an honest publish placeholder and does not claim the video is published", async () => {
    const initial = subtitleCoverWorkspace();
    render(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    fireEvent.click(screen.getByRole("button", { name: "发布与数据：未接入" }));
    expect(screen.getByRole("heading", { name: "平台发布能力未接入" })).toBeInTheDocument();
    expect(screen.getByText(/本页不会把本地成片计为已发布/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "回到检查与输出" })).toBeInTheDocument();
    expect(screen.getAllByText("未接入")).toHaveLength(3);
  });

  it("edits the topic title, requires a non-empty title, and confirms the selected topic", async () => {
    const topicTask: CreationTask = {
      ...baseTask,
      stage: "topic",
      stage_status: "waiting_for_user",
      status: "waiting_for_user",
      progress: 5,
      recovery_point: "",
      stage_reason: null,
      stage_impact: null,
      stage_repair_action: null,
    };
    const topics: CreationTaskTopicsResponse = {
      task: topicTask,
      candidates: [
        { id: "topic-1", run_id: "run-1", title: "用三件小事讲清断舍离", angle: "从生活场景切入", audience: "想整理生活的人", outline: ["开场", "三件事", "收尾"], score: 92, selection_status: "unselected", created_at: topicTask.created_at },
        { id: "topic-2", run_id: "run-1", title: "一分钟复盘我的周计划", angle: "效率视角", audience: "职场人", outline: ["开场", "复盘", "收尾"], score: 88, selection_status: "unselected", created_at: topicTask.created_at },
      ],
      evidence_references: [],
      selected_topic_id: "topic-1",
      latest_run: null,
      route_preflight: { provider_kind: "minimax", execution_location: "cloud", model_id: "MiniMax-M3", endpoint_identity: "ep", connection_version: "1", availability: "succeeded", checked_at: topicTask.created_at },
      egress_preflight: { cloud_egress: true, source_count: 1, text_excerpt_count: 1, reason: null, impact: null, repair_action: null },
    };
    const initial = workspace(topicTask);
    mocks.getWorkspace.mockResolvedValue({ ...initial, task: { ...topicTask, stage: "script" as const } });
    render(<CreationTaskWorkspace initialTasks={[]} initialTopics={topics} initialWorkspace={initial} />);
    const input = screen.getByRole("textbox", { name: "编辑标题" });
    expect(input).toHaveValue("用三件小事讲清断舍离");
    fireEvent.change(input, { target: { value: "我的新标题" } });
    expect(input).toHaveValue("我的新标题");
    fireEvent.change(input, { target: { value: "   " } });
    expect(screen.getByRole("button", { name: "确认选题" })).toBeDisabled();
    fireEvent.change(input, { target: { value: "我的新标题" } });
    fireEvent.click(screen.getByRole("button", { name: "确认选题" }));
    await waitFor(() => expect(confirmCreationTaskTopic).toHaveBeenCalledWith("task-1", "topic-1"));
    expect(await screen.findByText(/选题「我的新标题」已确认/)).toBeInTheDocument();
  });

  it("shows an honest read-only review checklist and drops the blanket pass claim", () => {
    const initial = workspaceWithCover();
    render(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    expect(screen.getByText("检查清单 · 2/3 项已就绪")).toBeInTheDocument();
    expect(screen.getByText("中文轨已就绪")).toBeInTheDocument();
    expect(screen.getByText("讲解与音乐音量")).toBeInTheDocument();
    expect(screen.getByText("需人工试听确认，当前无自动音量校验")).toBeInTheDocument();
    expect(screen.getAllByText("已就绪")).toHaveLength(2);
    expect(screen.queryByText("画面、声音和尺寸检查已通过。")).not.toBeInTheDocument();
    expect(screen.getByText("成片可预览；请按上方清单逐项确认。")).toBeInTheDocument();
  });

  // WIL-56 遗留：箭头叠加层选中后，编辑上下文芯片不能叫「标题」。
  it("labels a selected arrow overlay as 箭头, not 标题", async () => {
    const initial = subtitleCoverWorkspace();
    initial.montage!.clips = [{
      id: "clip-1", cue_id: "cue-1", asset_id: "asset-1", segment_id: "segment-1",
      source_reference: "workspace://clip.mp4", source_start_seconds: 0, source_end_seconds: 3,
      timeline_start_seconds: 0, timeline_end_seconds: 3, match_score: .9,
      match_reasons: [], needs_confirmation: false, role: "b_roll", locked: false, manually_selected: false,
    }];
    vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => {});
    initial.montage!.graphic_overlays = [{
      kind: "arrow", id: "overlay-arrow", x: 0.3, y: 0.4, name: "指向数据",
      purpose: "指出关键数字", start_seconds: 1, end_seconds: 3,
      position: "right-top", color: "#FFDD00",
    }];
    mocks.previewEdit.mockResolvedValue(exactPreview(initial.task, "EditGraphicOverlay"));
    render(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    fireEvent.click(screen.getByRole("button", { name: /^视频剪辑：/ }));
    fireEvent.click(screen.getByRole("button", { name: "箭头1 · 指向数据" }));
    // 右侧面板标题用的是同一个编辑上下文标签
    expect(screen.getByRole("complementary", { name: "箭头1" })).toBeInTheDocument();
    expect(screen.queryByRole("complementary", { name: "标题1" })).not.toBeInTheDocument();
    // 指令栏里选中的芯片也要叫「箭头1」
    expect(screen.getByText(/已选中箭头1 ·/)).toBeInTheDocument();
  });

  // WIL-160：连续制作已成功，但剪辑改动让成片不再对应当前时间线（video_ready=false），
  // 面板此前只剩一行状态文字、零按钮，且 onStart 会命中同一幂等键返回旧作业。
  // 当前纯素材编辑版本经原 resume 通道申请 render Grant，不从旧单步渲染绕过。
  it.each(["queued", "blocked"])("confirms current timeline re-output through resume and preserves %s status", async (outcome) => {
    const task = {
      ...baseTask, stage: "narration" as const, stage_status: "waiting_for_user" as const,
      status: "waiting_for_user" as const, stage_reason: null, stage_impact: null,
      stage_repair_action: null, stage_retryable: null, progress: 75,
      current_timeline_version_id: "timeline-version-2", timeline_sequence: 2,
      montage_plan_id: "montage-1", output_ready: false,
    };
    const montage = {
      id: "montage-1", task_id: task.id, script_id: "script-1", script_title: "故事板",
      status: "ready" as const,
      cues: [{ id: "cue-1", source_section_id: "section-1", narration_segment_id: "narration-segment-1", text: "第一段旁白", duration_seconds: 3 }],
      clips: [{ id: "clip-1", cue_id: "cue-1", asset_id: "asset-1", segment_id: "segment-1", source_reference: "workspace://clip.mp4", source_start_seconds: 1, source_end_seconds: 4, timeline_start_seconds: 0, timeline_end_seconds: 3, match_score: .9, match_reasons: ["已审核"], needs_confirmation: false, role: "b_roll" as const, locked: false, manually_selected: false }],
      duration_seconds: 3, low_confidence_clip_count: 0,
      narration_reference: "narration://artifact/audio-1", output_url: null, subtitle_url: null,
      message: "镜头审核已完成，可以重新渲染。", created_at: task.created_at, updated_at: task.updated_at,
    };
    const initial = workspace(task, montage);
    const preview = queuePreview();
    preview.requirements.presenter_share_target = 0;
    mocks.previewExecution.mockResolvedValue(preview);
    mocks.getWorkspace.mockResolvedValue(initial);
    mocks.getExecution.mockResolvedValue({
      task_id: "task-1",
      job: { id: "job-1", kind: "creation_task_execution", status: "succeeded", progress: 0, failure_reason: null, impact: null, repair_action: null },
      stages: [], current_stage: null, waiting_reason: null, status_updated_at: "2026-10-01T00:00:00+00:00",
      video_ready: false, cover_status: "pending", cover_message: null,
    });
    if (outcome === "queued") {
      mocks.resumeExecution.mockImplementation(async () => {
        const queued = queueStatus("queued");
        mocks.getExecution.mockResolvedValue(queued);
        return queued;
      });
    } else {
      mocks.resumeExecution.mockRejectedValue(new mocks.MockCreationPlanApiError(409, null,
        "当前有效音轨组合尚未接入 CPU 成片预算"));
    }

    render(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);

    // 文案/配音侧的连续制作面板：状态仍是「成片产物需要检查」，但现在有出口
    await waitFor(() => expect(screen.getByRole("region", { name: "连续制作" })).toHaveTextContent("成片产物需要检查"));

    let panel = screen.getByRole("region", { name: "连续制作" });
    expect(panel).toHaveTextContent("仅重新输出当前时间线");
    expect(panel).toHaveTextContent("这段就是实际要读的内容");
    expect(panel).not.toHaveTextContent("当前暂不支持已编辑时间线重新输出");
    expect(mocks.resumeExecution).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "查看剪辑与已有产物" }));
    expect(screen.getByLabelText("镜头故事板")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "查看制作详情" }));
    panel = screen.getByRole("region", { name: "连续制作" });
    expect(screen.getByRole("region", { name: "连续制作" })).toHaveTextContent("成片产物需要检查");
    expect(screen.queryByRole("button", { name: "生成成片" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "确认以上文案和要求，开始制作" })).not.toBeInTheDocument();
    fireEvent.click(within(panel).getByRole("button", { name: "确认以上文案和要求，仅重新输出当前剪辑" }));
    await waitFor(() => expect(mocks.resumeExecution).toHaveBeenCalledExactlyOnceWith("task-1"));
    if (outcome === "queued") {
      expect(await within(panel).findByRole("heading", { name: "已排队" })).toBeInTheDocument();
      expect(panel).not.toHaveTextContent("成片已生成");
    } else {
      expect(await screen.findByRole("alert")).toHaveTextContent("当前有效音轨组合尚未接入 CPU 成片预算");
      expect(panel).not.toHaveTextContent("成片已生成");
    }
    expect(mocks.startExecution).not.toHaveBeenCalled();
    expect(mocks.generateNarration).not.toHaveBeenCalled();
    expect(mocks.createMontage).not.toHaveBeenCalled();
    expect(mocks.renderMontage).not.toHaveBeenCalled();
  });

  it("rechecks the changed timeline before confirming re-output and preserves the new preflight blocker", async () => {
    const initial = subtitleCoverWorkspace();
    const changed = structuredClone(initial);
    changed.task.current_timeline_version_id = "timeline-version-7";
    changed.task.output_ready = false;
    changed.montage!.output_url = null;
    const done = queueStatus("succeeded");
    done.video_ready = true;
    mocks.getWorkspace.mockResolvedValue(initial);
    mocks.getExecution.mockResolvedValue(done);
    let rejectPreview!: (cause: Error) => void;
    mocks.previewExecution.mockResolvedValueOnce(queuePreview())
      .mockImplementationOnce(() => new Promise((_, reject) => { rejectPreview = reject; }));
    mocks.undoTimeline.mockImplementation(async () => {
      mocks.getWorkspace.mockResolvedValue(changed);
      mocks.getExecution.mockResolvedValue({ ...done, video_ready: false });
      return {};
    });
    render(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    await waitFor(() => expect(mocks.previewExecution).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole("button", { name: "撤销" }));
    await waitFor(() => expect(mocks.previewExecution).toHaveBeenCalledTimes(2));
    fireEvent.click(screen.getByRole("button", { name: /^视频剪辑：/ }));
    fireEvent.click(screen.getByRole("button", { name: "查看制作详情" }));
    expect(screen.getByRole("button", { name: "确认以上文案和要求，仅重新输出当前剪辑" })).toBeDisabled();
    expect(screen.queryByText("这段就是实际要读的内容")).not.toBeInTheDocument();
    await act(async () => rejectPreview(new mocks.MockCreationPlanApiError(503,
      "device_budget_unavailable", "配音预算未标定", null, "先标定本机配音资源预算，再重试。")));
    expect(await screen.findByRole("alert")).toHaveTextContent("配音预算未标定 先标定本机配音资源预算，再重试。");
    expect(screen.getByRole("button", { name: "确认以上文案和要求，仅重新输出当前剪辑" })).toBeDisabled();
    expect(mocks.resumeExecution).not.toHaveBeenCalled();
    expect(mocks.startExecution).not.toHaveBeenCalled();
    expect(mocks.renderMontage).not.toHaveBeenCalled();
  });

  it("lists historical timeline versions and shows an old version's own snapshot read-only", async () => {
    const task = { ...baseTask, stage: "montage" as const, stage_status: "waiting_for_user" as const,
      status: "waiting_for_user" as const, progress: 70, timeline_sequence: 2 };
    const initial = workspace(task, {
      ...subtitleCoverWorkspace().montage!,
      clips: [{ id: "clip-1", cue_id: "cue-1", asset_id: "asset-1", segment_id: "segment-1",
        source_reference: "workspace://clip.mp4", source_start_seconds: 0, source_end_seconds: 3,
        timeline_start_seconds: 0, timeline_end_seconds: 3, match_score: 1, match_reasons: [],
        needs_confirmation: false, role: "b_roll", locked: false, manually_selected: false }],
    });
    const versions = {
      task_id: "task-1",
      current_version_id: "tv-2",
      items: [
        { id: "tv-1", sequence: 1, parent_version_id: null, created_at: "2026-10-01T02:00:00+00:00",
          is_current: false, shot_count: 2, subtitle_track_count: 1, narration_segment_count: 1 },
        { id: "tv-2", sequence: 2, parent_version_id: "tv-1", created_at: "2026-10-02T02:00:00+00:00",
          is_current: true, shot_count: 2, subtitle_track_count: 0, narration_segment_count: 1 },
      ],
    };
    mocks.listTimelineVersions.mockResolvedValue(versions);
    mocks.getTimelineVersion.mockResolvedValue({
      task_id: "task-1",
      version: { id: "tv-1", sequence: 1, parent_version_id: null, created_at: "2026-10-01T02:00:00+00:00",
        is_current: false, shot_count: 2, subtitle_track_count: 1, narration_segment_count: 1 },
      shots: [{ index: 0, id: "clip-1", narration_text: "旧版本第一句", start_seconds: 0, end_seconds: 1.5, description: "旧镜头描述" }],
      narration_segments: [],
      subtitle_tracks: [],
      cover_asset: null,
      artifacts: [
        { kind: "video", status: "available",
          url: "/api/creation-tasks/task-1/timeline/versions/tv-1/artifacts/video", reason: null },
        { kind: "cover", status: "not_generated", url: null, reason: "该版本没有生成封面。" },
      ],
    });

    render(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);

    expect(mocks.listTimelineVersions).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "选择镜头 1" }));
    const player = screen.getByLabelText("剪辑成片预览") as HTMLVideoElement;
    const playerSource = player.getAttribute("src");
    player.currentTime = 1.25;
    fireEvent.seeked(player);
    fireEvent.change(screen.getByLabelText("局部修改要求"), { target: { value: "保留这份未应用草稿" } });
    fireEvent.click(screen.getByRole("button", { name: "历史时间线版本" }));
    const panel = await screen.findByRole("dialog", { name: "历史时间线版本" });
    expect(panel).toHaveAttribute("data-card-kind", "detail");
    expect(screen.getByRole("main")).not.toContainElement(panel);
    expect(within(panel).getByRole("heading", { name: "历史时间线版本" })).toBeInTheDocument();

    // 版本序号、时间与「当前版本」标识都来自接口当前返回的版本列表
    expect(mocks.listTimelineVersions).toHaveBeenCalledWith("task-1");
    await waitFor(() => expect(within(panel).getByText(/第 2 版/)).toBeInTheDocument());
    expect(within(panel).getByText("当前版本")).toBeInTheDocument();
    expect(within(panel).getAllByText(/2 个镜头/)).toHaveLength(2);
    expect(within(panel).getByText(/1 条字幕轨/)).toBeInTheDocument();

    fireEvent.click(within(panel).getByRole("button", { name: /第 1 版/ }));
    await waitFor(() => expect(mocks.getTimelineVersion).toHaveBeenCalledWith("task-1", "tv-1"));

    // 旧版本按自己的快照显示，缺失产物说明原因而不是留空
    expect(await within(panel).findByText("旧版本第一句")).toBeInTheDocument();
    expect(within(panel).getByText("旧镜头描述")).toBeInTheDocument();
    expect(within(panel).getByRole("link", { name: "查看这一版的成片" })).toHaveAttribute(
      "href", "/api/creation-tasks/task-1/timeline/versions/tv-1/artifacts/video");
    expect(within(panel).getByText("该版本没有生成封面。")).toBeInTheDocument();
    expect(within(panel).queryByRole("button", { name: /回滚|恢复|应用/ })).not.toBeInTheDocument();

    // 收起/恢复只隐藏同一浮卡，保留历史选择和滚动，也不重建当前播放器、选择或草稿。
    const body = within(panel).getByRole("region", { name: "历史时间线版本" }).parentElement!;
    body.scrollTop = 120;
    fireEvent.click(within(panel).getByRole("button", { name: "收起历史版本" }));
    expect(screen.queryByRole("dialog", { name: "历史时间线版本" })).not.toBeInTheDocument();
    expect(screen.getByLabelText("剪辑成片预览")).toBe(player);
    expect(player.currentTime).toBe(1.25);
    expect(player).toHaveAttribute("src", playerSource);
    expect(screen.getByRole("button", { name: "选择镜头 1" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByLabelText("局部修改要求")).toHaveValue("保留这份未应用草稿");
    // 隐藏期间真实列表有新版本：重开必须重读，不能沿用挂载时的当前标记。
    mocks.listTimelineVersions.mockResolvedValue({
      ...versions, current_version_id: "tv-3",
      items: [...versions.items.map(version => ({ ...version, is_current: false })),
        { ...versions.items[1], id: "tv-3", sequence: 3, parent_version_id: "tv-2", is_current: true }],
    });
    fireEvent.click(screen.getByRole("button", { name: "历史时间线版本" }));
    expect(screen.getByRole("dialog", { name: "历史时间线版本" })).toBe(panel);
    expect(body.scrollTop).toBe(120);
    expect(within(panel).getByRole("button", { name: /第 1 版/ })).toHaveAttribute("aria-current", "true");
    expect(within(panel).getByText("旧版本第一句")).toBeInTheDocument();
    expect(await within(panel).findByRole("button", { name: /第 3 版/ })).toHaveTextContent("当前版本");
    expect(within(panel).getByRole("button", { name: /第 2 版/ })).not.toHaveTextContent("当前版本");
    expect(within(panel).getByRole("button", { name: /第 1 版/ })).toHaveAttribute("aria-current", "true");
    expect(mocks.listTimelineVersions).toHaveBeenCalledTimes(2);
    expect(mocks.getTimelineVersion).toHaveBeenCalledTimes(1);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: "历史时间线版本" })).not.toBeInTheDocument();

    // 查看、收起与重开都不触发写时间线或替换当前工作区。
    expect(mocks.confirmEdit).not.toHaveBeenCalled();
    expect(mocks.undoTimeline).not.toHaveBeenCalled();
    expect(mocks.getWorkspace).not.toHaveBeenCalled();
  });

  it("keeps the detail bound to the still-selected version when an older response arrives late", async () => {
    const task = { ...baseTask, stage: "montage" as const, stage_status: "waiting_for_user" as const,
      status: "waiting_for_user" as const, progress: 70, timeline_sequence: 2 };
    const initial = workspace(task);
    mocks.listTimelineVersions.mockResolvedValue({
      task_id: "task-1",
      current_version_id: "tv-2",
      items: [
        { id: "tv-1", sequence: 1, parent_version_id: null, created_at: "2026-10-01T02:00:00+00:00",
          is_current: false, shot_count: 2, subtitle_track_count: 1, narration_segment_count: 1 },
        { id: "tv-2", sequence: 2, parent_version_id: "tv-1", created_at: "2026-10-02T02:00:00+00:00",
          is_current: true, shot_count: 2, subtitle_track_count: 0, narration_segment_count: 1 },
      ],
    });
    const detailFor = (versionId: "tv-1" | "tv-2", sequence: number, narrationText: string) => ({
      task_id: "task-1",
      version: { id: versionId, sequence, parent_version_id: null, created_at: "2026-10-01T02:00:00+00:00",
        is_current: versionId === "tv-2", shot_count: 2, subtitle_track_count: 0, narration_segment_count: 1 },
      shots: [{ index: 0, id: `clip-${sequence}`, narration_text: narrationText, start_seconds: 0, end_seconds: 1.5, description: null }],
      narration_segments: [],
      subtitle_tracks: [],
      cover_asset: null,
      artifacts: [],
    });
    const oldDetail = detailFor("tv-1", 1, "旧版本第一句");
    const currentDetail = detailFor("tv-2", 2, "当前版本第一句");
    // 每次详情请求都挂起，由测试自己决定哪一版先返回（A 慢 / B 快）
    const pending = new Map<string, Array<(value: unknown) => void>>();
    mocks.getTimelineVersion.mockImplementation((_taskId: string, versionId: string) =>
      new Promise((resolve) => {
        const queue = pending.get(versionId) ?? [];
        queue.push(resolve);
        pending.set(versionId, queue);
      }));
    const settle = async (versionId: string, value: unknown) => {
      const resolve = pending.get(versionId)?.shift();
      expect(resolve).toBeTruthy();
      await act(async () => { resolve?.(value); });
    };

    render(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);

    fireEvent.click(screen.getByRole("button", { name: "历史时间线版本" }));
    const panel = await screen.findByRole("dialog", { name: "历史时间线版本" });
    await waitFor(() => expect(within(panel).getByRole("button", { name: /第 2 版/ })).toBeInTheDocument());

    const firstButton = within(panel).getByRole("button", { name: /第 1 版/ });
    const secondButton = within(panel).getByRole("button", { name: /第 2 版/ });

    fireEvent.click(secondButton);
    await settle("tv-2", currentDetail);
    expect(await within(panel).findByText("当前版本第一句")).toBeInTheDocument();

    // 切到第 1 版时，上一版详情必须立刻消失，不能继续显示着等新响应
    fireEvent.click(firstButton);
    expect(within(panel).queryByText("当前版本第一句")).not.toBeInTheDocument();
    expect(within(panel).getByText("正在读取这一版…")).toBeInTheDocument();
    expect(firstButton).toHaveAttribute("aria-current", "true");

    // 第 1 版这次请求被挂起：快速改选第 2 版，让后发先至
    fireEvent.click(secondButton);
    expect(within(panel).queryByText("当前版本第一句")).not.toBeInTheDocument();
    await settle("tv-2", currentDetail);
    expect(await within(panel).findByText("当前版本第一句")).toBeInTheDocument();
    expect(secondButton).toHaveAttribute("aria-current", "true");

    // 迟到的第 1 版响应不能覆盖当前选中的第 2 版
    await settle("tv-1", oldDetail);
    expect(within(panel).queryByText("旧版本第一句")).not.toBeInTheDocument();
    expect(within(panel).getByText("当前版本第一句")).toBeInTheDocument();
    expect(secondButton).toHaveAttribute("aria-current", "true");

    // 切换查看旧版本始终只是读，没有任何写任务的调用
    expect(mocks.confirmEdit).not.toHaveBeenCalled();
    expect(mocks.undoTimeline).not.toHaveBeenCalled();
    expect(mocks.getWorkspace).not.toHaveBeenCalled();
  });

});

describe("作业进度读数", () => {
  it("shows real job status, progress, elapsed and unknown estimates from persisted jobs", async () => {
    mocks.getJobProgress.mockResolvedValue({
      task_id: "task-1", total_count: 2,
      current: { id: "job-1", kind: "creation_task_montage_render", status: "running", progress: 40, attempt: 1,
        started_at: "2026-10-04T08:00:00Z", finished_at: null, updated_at: "2026-10-04T08:05:00Z",
        elapsed_seconds: 300, failure_reason: null, impact: null, repair_action: null,
        wait_estimate_seconds: null, processing_estimate_seconds: null, estimate_basis: null },
      items: [
        { id: "job-1", kind: "creation_task_montage_render", status: "running", progress: 40, attempt: 1,
          started_at: "2026-10-04T08:00:00Z", finished_at: null, updated_at: "2026-10-04T08:05:00Z",
          elapsed_seconds: 300, failure_reason: null, impact: null, repair_action: null,
          wait_estimate_seconds: null, processing_estimate_seconds: null, estimate_basis: null },
        { id: "job-0", kind: "creation_task_montage_render", status: "failed", progress: 10, attempt: 1,
          started_at: "2026-10-04T07:00:00Z", finished_at: "2026-10-04T07:01:00Z", updated_at: "2026-10-04T07:01:00Z",
          elapsed_seconds: 60, failure_reason: "输出磁盘写入失败", impact: "本次没有成片", repair_action: "检查磁盘空间后重新生成",
          wait_estimate_seconds: null, processing_estimate_seconds: null, estimate_basis: null },
      ],
    });
    render(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={subtitleCoverWorkspace()} />);
    const panel = await screen.findByRole("region", { name: "作业进度" });
    // 进度是作业自己写下的 40%，不是阶段序号；已耗时与更新时间来自记录。
    expect(within(panel).getAllByText(/成片输出 · 正在处理 · 进度 40% · 已耗时 5 分/).length).toBeGreaterThan(0);
    // 没有可信样本：等待与处理估时都写未知，不编造倒计时。
    expect(within(panel).getByText(/预计等待：未知 · 预计处理：未知（尚无可信样本，无法预计）/)).toBeInTheDocument();
    // 历史失败原因和修复动作仍在。
    expect(within(panel).getAllByText(/失败原因：输出磁盘写入失败/).length).toBeGreaterThan(0);
  });


  it("shows a concrete read failure and retries instead of silently showing nothing", async () => {
    mocks.getJobProgress.mockRejectedValue(new Error("本地服务未接通"));
    render(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={subtitleCoverWorkspace()} />);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("作业进度暂时读不到：本地服务未接通");
    // 重试会真的再读一次。
    mocks.getJobProgress.mockResolvedValue({
      task_id: "task-1", total_count: 1,
      current: { id: "job-2", kind: "creation_task_execution", status: "queued", progress: 0, attempt: 1,
        started_at: null, finished_at: null, updated_at: "2026-10-04T08:00:00Z",
        elapsed_seconds: null, failure_reason: null, impact: null, repair_action: null,
        wait_estimate_seconds: null, processing_estimate_seconds: null, estimate_basis: null },
      items: [],
    });
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(await screen.findByRole("region", { name: "作业进度" })).toHaveTextContent("整条制作 · 已安排，等待设备");
  });

  it("re-reads after the execution poll reports a new job, even though the first read had no jobs", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      mocks.getJobProgress.mockResolvedValue({ task_id: "task-1", current: null, items: [], total_count: 0 });
      mocks.getExecution.mockResolvedValue({ task_id: "task-1", job: null, stages: [], video_ready: false, cover_status: "pending", cover_message: null });
      render(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={subtitleCoverWorkspace()} />);
      const panel = await screen.findByRole("region", { name: "作业进度" });
      expect(within(panel).getByText("这个任务还没有后台作业记录。")).toBeInTheDocument();

      // 开始制作后，执行轮询读到新作业：面板随之重新读取，不停在空态。
      mocks.getJobProgress.mockResolvedValue({
        task_id: "task-1", total_count: 1,
        current: { id: "job-new", kind: "creation_task_execution", status: "running", progress: 5, attempt: 1,
          started_at: "2026-10-04T08:00:00Z", finished_at: null, updated_at: "2026-10-04T08:00:05Z",
          elapsed_seconds: 5, failure_reason: null, impact: null, repair_action: null,
          wait_estimate_seconds: null, processing_estimate_seconds: null, estimate_basis: null },
        items: [],
      });
      mocks.getWorkspace.mockResolvedValue(subtitleCoverWorkspace());
      mocks.getExecution.mockResolvedValue({ task_id: "task-1",
        job: { id: "job-new", kind: "creation_task_execution", status: "running", progress: 5, failure_reason: null, impact: null, repair_action: null },
        stages: [{ stage: "render", status: "running", at: "2026-10-04T08:00:00Z" }], current_stage: "render",
        waiting_reason: null, status_updated_at: "2026-10-04T08:00:05Z", video_ready: false, cover_status: "pending", cover_message: null });
      await act(async () => { await vi.advanceTimersByTimeAsync(16000); });
      await act(async () => { await Promise.resolve(); });
      expect(mocks.getJobProgress.mock.calls.length).toBeGreaterThan(1);
      await within(panel).findByText(/整条制作 · 正在处理 · 进度 5%/);
      return;
      await within(panel).findByText(/整条制作 · 正在处理 · 进度 5%/);
    } finally { vi.useRealTimers(); }
  });

  it("keeps the last reading and offers the same retry when a later refresh fails", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      mocks.getJobProgress.mockResolvedValueOnce({
        task_id: "task-1", total_count: 1,
        current: { id: "job-1", kind: "creation_task_montage_render", status: "running", progress: 10, attempt: 1,
          started_at: "2026-10-04T08:00:00Z", finished_at: null, updated_at: "2026-10-04T08:00:05Z",
          elapsed_seconds: 5, failure_reason: null, impact: null, repair_action: null,
          wait_estimate_seconds: null, processing_estimate_seconds: null, estimate_basis: null },
        items: [],
      });
      mocks.getExecution.mockResolvedValue({ task_id: "task-1", job: null, stages: [], video_ready: false, cover_status: "pending", cover_message: null });
      render(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={subtitleCoverWorkspace()} />);
      const panel = await screen.findByRole("region", { name: "作业进度" });
      expect(within(panel).getByText(/成片输出 · 正在处理/)).toBeInTheDocument();

      // 作业仍在执行，这次面板的重新读取失败：保留上次读数并给出同一个重试动作。
      mocks.getJobProgress.mockRejectedValueOnce(new Error("本地服务未接通"));
      await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
      const alert = await screen.findByRole("alert");
      expect(alert).toHaveTextContent("最新一次刷新失败：本地服务未接通");
      expect(screen.getByRole("button", { name: "重试" })).toBeInTheDocument();

      mocks.getJobProgress.mockResolvedValue({
        task_id: "task-1", total_count: 1,
        current: { id: "job-2", kind: "creation_task_montage_render", status: "running", progress: 20, attempt: 1,
          started_at: "2026-10-04T08:10:00Z", finished_at: null, updated_at: "2026-10-04T08:10:05Z",
          elapsed_seconds: 5, failure_reason: null, impact: null, repair_action: null,
          wait_estimate_seconds: null, processing_estimate_seconds: null, estimate_basis: null },
        items: [],
      });
      fireEvent.click(screen.getByRole("button", { name: "重试" }));
      await screen.findByText(/成片输出 · 正在处理 · 进度 20%/);
    } finally { vi.useRealTimers(); }
  });
  it("shares the task and shows the visibility the API returned", async () => {
    const shared = { ...baseTask, visibility: "shared" as const };
    mocks.setCreationTaskVisibility.mockResolvedValue({ ...workspace(baseTask), task: shared });
    render(<CreationTaskWorkspace initialTopics={null} initialTasks={[baseTask]} initialWorkspace={workspace(baseTask)} />);

    const trigger = screen.getByRole("button", { name: "共享设置" });
    trigger.focus(); fireEvent.click(trigger);
    const dialog = await screen.findByRole("dialog", { name: "任务与方案共享" });
    expect(within(dialog).getByText("任务：仅自己可见")).toBeInTheDocument();
    expect(mocks.setCreationTaskVisibility).not.toHaveBeenCalled();
    fireEvent.click(within(dialog).getByRole("button", { name: "共享给团队" }));

    await within(dialog).findByText("任务：团队共享");
    expect(mocks.setCreationTaskVisibility).toHaveBeenCalledWith("task-1", "shared");
  });

  it("keeps plan sharing independent inside the compact toolbar menu", async () => {
    mocks.getCreationPlan.mockResolvedValue({ visibility: "private" });
    mocks.setCreationPlanVisibility.mockResolvedValue({ visibility: "shared" });
    render(<CreationTaskWorkspace initialTopics={null} initialTasks={[baseTask]} initialWorkspace={workspace(baseTask)} />);

    const trigger = screen.getByRole("button", { name: "共享设置" });
    trigger.focus();
    fireEvent.click(trigger);
    const dialog = await screen.findByRole("dialog", { name: "任务与方案共享" });
    await within(dialog).findByText("方案：仅自己可见");
    expect(mocks.setCreationPlanVisibility).not.toHaveBeenCalled();
    fireEvent.click(within(dialog).getByRole("button", { name: "方案共享给团队" }));

    await within(dialog).findByText("方案：团队共享");
    expect(within(dialog).getByText("任务：仅自己可见")).toBeInTheDocument();
    expect(mocks.setCreationPlanVisibility).toHaveBeenCalledWith(baseTask.plan_id, "shared");
    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => expect(trigger).toHaveFocus());
  });

  it("keeps the current value and reports the reason when sharing fails", async () => {
    mocks.setCreationTaskVisibility.mockRejectedValue(new Error("本地服务拒绝了这次保存"));
    render(<CreationTaskWorkspace initialTopics={null} initialTasks={[baseTask]} initialWorkspace={workspace(baseTask)} />);

    const trigger = screen.getByRole("button", { name: "共享设置" });
    trigger.focus(); fireEvent.click(trigger);
    const dialog = await screen.findByRole("dialog", { name: "任务与方案共享" });
    fireEvent.click(within(dialog).getByRole("button", { name: "共享给团队" }));

    await within(dialog).findByRole("alert");
    expect(within(dialog).getByText("本地服务拒绝了这次保存")).toBeInTheDocument();
    // 没有保存成功就不能显示成已共享。
    expect(within(dialog).getByText("任务：仅自己可见")).toBeInTheDocument();
  });
});
