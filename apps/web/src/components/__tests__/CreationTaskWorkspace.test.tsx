import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CreationTaskWorkspace, PreviewVideo } from "../CreationTaskWorkspace";
import type { CreationTask, CreationTaskWorkspace as WorkspaceState } from "@/lib/types";

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
    openQuickDetails: vi.fn(),
    confirmEdit: vi.fn(),
    confirmScript: vi.fn(),
    exactEdit: vi.fn(),
    exactPreview: vi.fn(),
    generateNarration: vi.fn(),
    getCandidates: vi.fn(),
    getWorkspace: vi.fn(),
    initializeTimeline: vi.fn(),
    getAssistantHistory: vi.fn().mockResolvedValue({ items: [] }),
    getExecution: vi.fn().mockResolvedValue({ task_id: "task-1", job: null, stages: [], video_ready: false, cover_status: "pending", cover_message: null }),
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
    previewAudioEdit: vi.fn(),
  };
});

vi.mock("@/components/AppShell", () => ({
  AppShell: ({ children, conversation, inspector, quickInspector, quickInspectorLabel, toolbar, toolbarActions, inspectorTitle = "任务详情" }: { children: React.ReactNode; conversation?: React.ReactNode; toolbar?: React.ReactNode; toolbarActions?: React.ReactNode; inspector?: React.ReactNode; quickInspector?: (openDetails: () => void) => React.ReactNode; quickInspectorLabel?: string; inspectorTitle?: string }) => (
    <><header>{toolbar}{toolbarActions}</header><section>{conversation}</section><main>{children}</main>{mocks.showQuickInspector && quickInspector ? <section aria-label={quickInspectorLabel}>{quickInspector(mocks.openQuickDetails)}</section> : null}{inspector ? <aside aria-label={inspectorTitle}>{inspector}</aside> : null}</>
  ),
}));

vi.mock("../DesktopVoiceRecorder", () => ({
  DesktopVoiceRecorder: ({ onRecorded, onCancel }: { onRecorded: (blob: Blob) => void | Promise<void>; onCancel: () => void }) => (
    <div data-testid="voice-recorder">
      <button onClick={() => void Promise.resolve(onRecorded(new Blob(["audio"], { type: "audio/webm" }))).catch(() => undefined)} type="button">提交录音</button>
      <button onClick={onCancel} type="button">取消录音</button>
    </div>
  ),
}));

vi.mock("@/lib/api", () => ({
  apiUrl: (path: string) => path,
  getCreationTaskGraphicSources: vi.fn().mockResolvedValue([]),
  applyCreationTaskExactEdit: mocks.exactEdit,
  CreationPlanApiError: mocks.MockCreationPlanApiError,
  confirmCreationTaskEdit: mocks.confirmEdit,
  confirmCreationTaskScript: mocks.confirmScript,
  confirmCreationTaskTopic: vi.fn(),
  createCreationTaskMontagePlan: vi.fn(),
  generateCreationTaskNarration: mocks.generateNarration,
  generateCreationTaskScript: vi.fn(),
  generateCreationTaskTopics: vi.fn(),
  getCreationTaskMontageClipCandidates: mocks.getCandidates,
  getCreationTaskTopics: vi.fn(),
  getCreationTaskWorkspace: mocks.getWorkspace,
  initializeCreationTaskTimeline: mocks.initializeTimeline,
  getCreationTaskAssistantHistory: mocks.getAssistantHistory,
  getCreationExecution: mocks.getExecution,
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
  previewCreationTaskAudio: mocks.previewAudioEdit,
}));

const baseTask: CreationTask = {
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

function exactPreview(task: CreationTask, operationType: "EditAudioTimeline" | "EditCompoundTimeline" | "EditGraphicOverlay" | "SetClipTransition" | "ChangeCanvas" | "SplitSubtitleCue" | "MergeSubtitleCue" | "DeleteSubtitleCue" | "ReplaceSubtitleText" | "UpdateSubtitleCue" | "ConfigureSubtitleTrack" | "GenerateCoverFromFrame" | "ReplaceClip" | "ChangeCrop" | "TrimClip" | "ReorderClip" | "SetClipLock" | "ConfirmClip") {
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
  mocks.showQuickInspector = false;
  vi.clearAllMocks();
  mocks.previewExecution.mockResolvedValue(null);
  mocks.getExecution.mockResolvedValue({ task_id: "task-1", job: null, stages: [], video_ready: false, cover_status: "pending", cover_message: null });
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
      requirements: { aspect_ratio: "9:16", duration_target_seconds: 30,
        presenter_share_target: 20, lipsync_quality: "high", selected_voice: "voice-1",
        template_settings: {} },
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

  it("keeps start unavailable until an authorized voice is selected", async () => {
    const initial = workspace({ ...baseTask, stage: "narration", stage_status: "not_started", status: "running" });
    mocks.getWorkspace.mockResolvedValue(initial);
    mocks.previewExecution.mockResolvedValue({
      task_id: "task-1", script_id: "script-1", script_title: "已确认文案",
      script_sections: [{ title: "正文", content: "实际内容" }],
      plan_id: "plan-1", input_digest: "b".repeat(64),
      requirements: { aspect_ratio: "9:16", duration_target_seconds: 30,
        presenter_share_target: 0, lipsync_quality: "fast", selected_voice: null,
        template_settings: {} },
    });
    render(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    expect(await screen.findByText("请先在下方选择已授权声音，再开始制作。")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "请先选择授权声音" })).toBeDisabled();
    expect(mocks.startExecution).not.toHaveBeenCalled();
  });

  it("keeps the assistant proposal under explicit confirmation and the selected clip", async () => {
    const initial = subtitleCoverWorkspace();
    initial.montage!.clips = [{
      id: "clip-1", cue_id: "cue-1", asset_id: "asset-1", segment_id: "segment-1",
      source_reference: "workspace://clip.mp4", source_start_seconds: 0, source_end_seconds: 3,
      timeline_start_seconds: 0, timeline_end_seconds: 3, match_score: .9,
      match_reasons: [], needs_confirmation: false, role: "b_roll", locked: false, manually_selected: false,
    }];
    const preview = exactPreview(initial.task, "ReplaceClip");
    preview.intent.raw_command = "换成产品特写";
    mocks.askAssistant.mockResolvedValue({ reply: "已生成镜头替换预览，请确认。", provider: "minimax", model: "MiniMax-M3", edit_preview: preview });
    render(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    fireEvent.click(screen.getByRole("button", { name: /^检查与输出：/ }));
    fireEvent.click(screen.getByRole("button", { name: "修改镜头" }));
    fireEvent.click(screen.getByRole("button", { name: "选择镜头 1" }));
    fireEvent.change(screen.getByLabelText("局部修改要求"), { target: { value: "换成产品特写" } });
    await waitFor(() => expect(screen.getByRole("button", { name: "发送" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    await waitFor(() => expect(mocks.askAssistant).toHaveBeenCalledWith("task-1", expect.objectContaining({
      message: "换成产品特写", selected_object_refs: ["clip:clip-1"],
    })));
    expect(await screen.findByLabelText("变更预览")).toBeInTheDocument();
    expect(mocks.confirmEdit).not.toHaveBeenCalled();
    expect(screen.getByText("已生成镜头替换预览，请确认。")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "确认修改" }));
    await waitFor(() => expect(mocks.confirmEdit).toHaveBeenCalledWith("task-1", "proposal-ReplaceClip"));
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

  it("requires a fresh preview after changing an already previewed subtitle draft", async () => {
    const initial = subtitleCoverWorkspace();
    mocks.exactPreview.mockResolvedValue(exactPreview(initial.task, "UpdateSubtitleCue"));
    render(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    fireEvent.click(screen.getByRole("button", { name: "字幕与封面：待补封面" }));
    fireEvent.change(screen.getByLabelText("字幕 1 文本"), { target: { value: "第一版修改" } });
    fireEvent.click(screen.getByRole("button", { name: "预览字幕修改" }));
    expect(await screen.findByRole("button", { name: "确认修改" })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("字幕 1 文本"), { target: { value: "第二版修改" } });
    expect(screen.queryByRole("button", { name: "确认修改" })).not.toBeInTheDocument();
    expect(mocks.confirmEdit).not.toHaveBeenCalled();
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
    expect(screen.getByText(/不需要重做选题或文案/)).toBeInTheDocument();
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
      requirements: { aspect_ratio: "9:16", duration_target_seconds: 30,
        presenter_share_target: 0, lipsync_quality: "fast", selected_voice: null as string | null,
        template_settings: {} },
    };
    mocks.previewExecution.mockResolvedValueOnce(preview).mockResolvedValueOnce({
      ...preview, requirements: { ...preview.requirements, selected_voice: "voice-1" },
    });

    render(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    expect(await screen.findByRole("button", { name: "请先选择授权声音" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "使用此声音" }));

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
    expect(screen.queryByText("人物与口型")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "字幕与封面：待补封面" })).toBeDisabled();
    expect(screen.queryByText("数据监控")).not.toBeInTheDocument();
    expect(screen.getByTitle("字幕与封面：待补封面")).toHaveAttribute("aria-current", "step");
    expect(screen.getByRole("button", { name: "检查与输出：待上一步" })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "保存到本地" }));

    await waitFor(() => expect(saveRenderedVideo).toHaveBeenCalledWith("task-1"));
    expect(saveRenderedVideo).toHaveBeenCalledTimes(1);
    expect(await screen.findByRole("status")).toHaveTextContent("成片.mp4");
    fireEvent.click(screen.getByRole("button", { name: "修改镜头" }));
    expect(screen.getByLabelText("镜头故事板")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "生成成片" }));
    await waitFor(() => expect(mocks.renderMontage).toHaveBeenCalledWith("task-1"));
  });

  it("opens the persisted subtitle and cover workspace and previews cue edits before writing", async () => {
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
    fireEvent.click(screen.getByRole("button", { name: "预览字幕修改" }));

    await waitFor(() => expect(mocks.exactPreview).toHaveBeenCalledWith("task-1", {
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
    expect(mocks.exactEdit).not.toHaveBeenCalled();
    expect(await screen.findByLabelText("变更预览")).toHaveTextContent("未确认前不写入时间线");

    fireEvent.click(screen.getByRole("button", { name: "确认修改" }));
    await waitFor(() => expect(mocks.confirmEdit).toHaveBeenCalledWith("task-1", "proposal-UpdateSubtitleCue"));
    await waitFor(() => expect(mocks.getWorkspace).toHaveBeenCalledWith("task-1"));
    await waitFor(() => expect(screen.getByLabelText("字幕 1 文本")).toHaveValue("真实运行\n不是口号"));
    expect(mocks.exactEdit).not.toHaveBeenCalled();

    mocks.previewEdit.mockResolvedValue({ ...exactPreview(refreshed.task, "UpdateSubtitleCue"), proposal: { ...exactPreview(refreshed.task, "UpdateSubtitleCue").proposal, id: "proposal-natural" } });
    fireEvent.change(screen.getByLabelText("局部修改要求"), { target: { value: "这句再简洁一点" } });
    fireEvent.click(screen.getByRole("button", { name: "预览修改" }));
    await waitFor(() => expect(mocks.previewEdit).toHaveBeenCalledWith("task-1", {
      raw_command: "这句再简洁一点",
      context_stage: "subtitle_cover",
      selected_object_refs: ["cue:subtitle-cue-1", "subtitle-track:subtitle-track-zh"],
      playhead_ms: null,
    }));
  });

  it("previews the selected cut dissolve without immediately applying it", async () => {
    const initial=subtitleCoverWorkspace();
    const clip={id:"clip-1",cue_id:"cue-1",asset_id:"asset-1",segment_id:"segment-1",source_reference:"workspace://clip.mp4",
      source_start_seconds:0,source_end_seconds:3,timeline_start_seconds:0,timeline_end_seconds:3,match_score:.9,
      match_reasons:[],needs_confirmation:false,role:"b_roll" as const,locked:false,manually_selected:false};
    initial.montage!.clips=[clip,{...clip,id:"clip-2",timeline_start_seconds:3,timeline_end_seconds:6}];
    mocks.exactPreview.mockResolvedValue(exactPreview(initial.task,"SetClipTransition"));
    render(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    fireEvent.click(screen.getByRole("button",{name:/^视频剪辑：/}));
    fireEvent.click(screen.getByRole("button",{name:"选择镜头 2"}));
    fireEvent.click(screen.getByText("镜头过渡"));
    fireEvent.change(screen.getByLabelText("叠化时长（秒）"),{target:{value:"0.4"}});
    fireEvent.click(screen.getByRole("button",{name:"预览转场"}));
    await waitFor(()=>expect(mocks.exactPreview).toHaveBeenCalledWith("task-1",{operation_type:"SetClipTransition",clip_id:"clip-2",transition_seconds:.4}));
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
    expect(screen.getByRole("button",{name:"确认修改"})).toBeDisabled();
    expect(screen.queryByLabelText("修改后画面")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button",{name:"重看所选"}));
    await waitFor(()=>expect(mocks.previewSelection).toHaveBeenCalledWith("task-1",original.proposal.id,[0]));
    await waitFor(()=>expect(screen.getByLabelText("修改后画面")).toHaveAttribute("src","/selected.mp4"));
    expect(screen.getByRole("button",{name:"确认修改"})).toBeEnabled();
    expect(screen.getByLabelText("保留修改2：配音小一点")).not.toBeChecked();
    mocks.previewSelection.mockResolvedValue({...original,proposal:{...original.proposal,id:"restored-proposal"}});
    fireEvent.change(screen.getByLabelText("局部修改要求"),{target:{value:"恢复第2项"}});
    expect(screen.getByLabelText("保留修改2：配音小一点")).toBeDisabled();
    expect(screen.getByRole("button",{name:"确认修改"})).toBeDisabled();
    expect(screen.queryByLabelText("修改后画面")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button",{name:"预览修改"}));
    await waitFor(()=>expect(mocks.previewSelection).toHaveBeenLastCalledWith("task-1","selected-proposal","恢复第2项"));
    await waitFor(()=>expect(screen.getByLabelText("保留修改2：配音小一点")).toBeChecked());
    expect(screen.getByRole("button",{name:"确认修改"})).toBeEnabled();
    expect(mocks.confirmEdit).not.toHaveBeenCalled();
  });

  it("previews whole-video canvas changes and invalidates the proposal when the draft changes", async () => {
    const initial=subtitleCoverWorkspace();
    mocks.exactPreview.mockResolvedValue(exactPreview(initial.task,"ChangeCanvas"));
    render(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    fireEvent.click(screen.getByRole("button", {name:/^视频剪辑：/}));
    fireEvent.click(screen.getByText("画幅设置"));
    fireEvent.change(screen.getByLabelText("成片画幅"),{target:{value:"16:9"}});
    fireEvent.change(screen.getByLabelText("整片画面适配"),{target:{value:"fit"}});
    fireEvent.click(screen.getByRole("button",{name:"预览画幅"}));
    await waitFor(()=>expect(mocks.exactPreview).toHaveBeenCalledWith("task-1",{operation_type:"ChangeCanvas",aspect_ratio:"16:9",fit_mode:"fit"}));
    expect(await screen.findByLabelText("变更预览")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("成片画幅"),{target:{value:"1:1"}});
    expect(screen.queryByLabelText("变更预览")).not.toBeInTheDocument();
    expect(mocks.confirmEdit).not.toHaveBeenCalled();
  });

  it("previews real subtitle style images before confirmation in English-only tasks", async () => {
    const initial = subtitleCoverWorkspace();
    initial.subtitle_tracks = [{...initial.subtitle_tracks[0], language:"en-US", source_language:null}];
    const preview = exactPreview(initial.task, "ConfigureSubtitleTrack");
    mocks.exactPreview.mockResolvedValue({...preview, proposal:{...preview.proposal, preview:{...preview.proposal.preview,
      after:{...preview.proposal.preview.after, image_url:"/api/style-preview.png"}}}});
    render(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    fireEvent.click(screen.getByRole("button", { name: /^字幕与封面：/ }));
    fireEvent.click(screen.getByText("全部字幕样式"));
    fireEvent.change(screen.getByLabelText("字幕字号"), {target:{value:"64"}});
    fireEvent.change(screen.getByLabelText("字幕背景不透明度"), {target:{value:"0"}});
    fireEvent.click(screen.getByRole("button", {name:"预览字幕样式"}));
    await waitFor(() => expect(mocks.exactPreview).toHaveBeenCalledWith("task-1", {operation_type:"ConfigureSubtitleTrack",style:{font_size:64,background_opacity:0}}));
    expect(await screen.findByAltText("修改后字幕样式")).toHaveAttribute("src", expect.stringContaining("style-preview.png"));
    expect(mocks.confirmEdit).not.toHaveBeenCalled();
  });

  it("previews subtitle structure changes without confirming automatically", async () => {
    const initial = subtitleCoverWorkspace();
    mocks.exactPreview.mockResolvedValue(exactPreview(initial.task, "SplitSubtitleCue"));
    render(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    fireEvent.click(screen.getByRole("button", { name: /^字幕与封面：/ }));
    fireEvent.click(screen.getByText("拆分、合并与删除"));
    fireEvent.change(screen.getByLabelText("字幕拆分文字位置"), { target: { value: "3" } });
    fireEvent.change(screen.getByLabelText("字幕拆分时间"), { target: { value: "1" } });
    fireEvent.click(screen.getByRole("button", { name: "预览拆分" }));
    await waitFor(() => expect(mocks.exactPreview).toHaveBeenCalledWith("task-1", {
      operation_type: "SplitSubtitleCue", track_id: "subtitle-track-zh", cue_id: "subtitle-cue-1", split_offset: 3, split_time_seconds: 1,
    }));
    expect(mocks.confirmEdit).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText("字幕 1 文本"), { target: { value: "未保存草稿" } });
    expect(screen.getByRole("button", { name: "预览删除字幕" })).toBeDisabled();
  });

  it("previews full-track literal replacement with each changed caption", async () => {
    const initial = subtitleCoverWorkspace();
    const proposal = exactPreview(initial.task, "ReplaceSubtitleText");
    mocks.exactPreview.mockResolvedValue({ ...proposal, proposal: { ...proposal.proposal,
      preview: { ...proposal.proposal.preview, text_changes: [{ cue_id: "c", before: "旧名字", after: "新名字", occurrences: 1 }] } } });
    render(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    fireEvent.click(screen.getByRole("button", { name: /^字幕与封面：/ }));
    fireEvent.click(screen.getByText("全文查找替换"));
    fireEvent.change(screen.getByLabelText("字幕查找文字"), { target: { value: "旧名字" } });
    fireEvent.change(screen.getByLabelText("字幕替换文字"), { target: { value: "新名字" } });
    fireEvent.click(screen.getByRole("button", { name: "预览全文替换" }));
    await waitFor(() => expect(mocks.exactPreview).toHaveBeenCalledWith("task-1", {
      operation_type: "ReplaceSubtitleText", track_id: "subtitle-track-zh", find_text: "旧名字", replacement_text: "新名字",
    }));
    expect(await screen.findByText("字幕变化 · 1")).toBeInTheDocument();
    expect(screen.getByText("修改前：旧名字")).toBeInTheDocument();
    expect(screen.getByText("修改后：新名字")).toBeInTheDocument();
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

  it("previews hiding subtitles without generating a translation", async () => {
    const initial = subtitleCoverWorkspace();
    mocks.exactPreview.mockResolvedValue(exactPreview(initial.task, "ConfigureSubtitleTrack"));
    render(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    fireEvent.click(screen.getByRole("button", { name: /^字幕与封面：/ }));
    fireEvent.click(screen.getByRole("button", { name: "无字幕" }));
    await waitFor(() => expect(mocks.exactPreview).toHaveBeenCalledWith("task-1", {
      operation_type: "ConfigureSubtitleTrack", subtitle_display: "none",
    }));
    expect(mocks.confirmEdit).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "英文" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "中文" })).toHaveAttribute("aria-pressed", "true");
  });

  it("previews bilingual configuration and real-frame cover generation without exposing AI image success", async () => {
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
    mocks.exactPreview
      .mockResolvedValueOnce(exactPreview(initial.task, "ConfigureSubtitleTrack"))
      .mockResolvedValueOnce(exactPreview(initial.task, "GenerateCoverFromFrame"));

    render(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    fireEvent.click(screen.getByRole("button", { name: "字幕与封面：已完成" }));
    fireEvent.click(screen.getByRole("button", { name: "生成英文字幕" }));
    await waitFor(() => expect(mocks.exactPreview).toHaveBeenNthCalledWith(1, "task-1", {
      operation_type: "ConfigureSubtitleTrack",
      track_id: "subtitle-track-zh",
      target_language: "en-US",
    }));
    expect(mocks.exactEdit).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "中文" })).toBeDisabled();

    fireEvent.change(screen.getByLabelText("封面画面时间"), { target: { value: "4.2" } });
    fireEvent.change(screen.getByLabelText("封面标题"), { target: { value: "真实运行" } });
    fireEvent.click(screen.getByRole("button", { name: "从画面生成封面" }));
    await waitFor(() => expect(mocks.exactPreview).toHaveBeenNthCalledWith(2, "task-1", {
      operation_type: "GenerateCoverFromFrame",
      frame_time_seconds: 4.2,
      title: "真实运行",
    }));
    const inspector = screen.getByLabelText("当前选择");
    expect(inspector).toHaveTextContent("图片产物artifact:cover:v6");
    expect(inspector).toHaveTextContent("选帧能力可用 · ffmpeg+Pillow");
    expect(inspector).toHaveTextContent("AI 生图不可用 · 未配置模型");
    expect(screen.queryByRole("button", { name: /AI 生成封面/ })).not.toBeInTheDocument();
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

  it("routes clip trimming through the exact edit endpoint and refreshes version facts", async () => {
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
    fireEvent.change(screen.getByLabelText("镜头 1 裁剪起点"), { target: { value: "1.5" } });
    const trimProposal = exactPreview(task, "TrimClip");
    mocks.exactPreview.mockResolvedValue({ ...trimProposal, proposal: { ...trimProposal.proposal, preview: {
      before: { ...trimProposal.proposal.preview.before, video_url: "/api/production/materials/asset-1/media#t=1.000,4.000" },
      after: { ...trimProposal.proposal.preview.after, video_url: "/api/production/materials/asset-1/media#t=1.500,4.500" },
    } } });
    mocks.confirmEdit.mockResolvedValue({});
    fireEvent.click(screen.getByRole("button", { name: "预览裁剪" }));

    await waitFor(() => expect(mocks.exactPreview).toHaveBeenCalledWith("task-1", {
      operation_type: "TrimClip",
      clip_id: "clip-1",
      source_start_seconds: 1.5,
    }));
    expect(mocks.exactEdit).not.toHaveBeenCalled();
    const afterVideo = await screen.findByLabelText("修改后画面") as HTMLVideoElement;
    fireEvent.loadedMetadata(afterVideo);
    expect(afterVideo.currentTime).toBe(1.5);
    const pauseOtherMedia = vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => {});
    const pause = vi.spyOn(afterVideo, "pause").mockImplementation(() => {});
    afterVideo.currentTime = 4.7;
    fireEvent.timeUpdate(afterVideo);
    expect(pause).toHaveBeenCalledOnce();
    expect(afterVideo.currentTime).toBe(4.5);
    const frames: FrameRequestCallback[] = [];
    const animationFrame = vi.spyOn(window, "requestAnimationFrame").mockImplementation(callback => {
      frames.push(callback);
      return frames.length;
    });
    fireEvent.play(afterVideo);
    expect(afterVideo.currentTime).toBe(1.5);
    const pausesBeforeFrame = pause.mock.calls.length;
    afterVideo.currentTime = 4.7;
    frames[0](0);
    expect(afterVideo.currentTime).toBe(4.5);
    expect(pause.mock.calls.length).toBeGreaterThan(pausesBeforeFrame);
    expect(afterVideo.muted).toBe(false);
    animationFrame.mockRestore();
    pause.mockRestore();
    pauseOtherMedia.mockRestore();
    fireEvent.click(await screen.findByRole("button", { name: "确认修改" }));
    expect(await screen.findByText(/版本 3$/)).toBeInTheDocument();
    expect(screen.getByLabelText("源素材预览")).toBeInTheDocument();
    expect(screen.queryByText("不直接采用编辑响应")).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("镜头分割位置"), { target: { value: "0" } });
    expect(screen.getByRole("button", { name: "预览切开" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText("镜头分割位置"), { target: { value: "1.2" } });
    fireEvent.click(screen.getByRole("button", { name: "预览切开" }));
    await waitFor(() => expect(mocks.exactPreview).toHaveBeenLastCalledWith("task-1", {
      operation_type: "SplitClip", clip_id: "clip-1", split_time_seconds: 1.2,
    }));
    expect(mocks.exactEdit).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "预览删除" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "预览复制" }));
    await waitFor(() => expect(mocks.exactPreview).toHaveBeenLastCalledWith("task-1", {
      operation_type: "DuplicateClip", clip_id: "clip-1",
    }));
    expect(mocks.exactEdit).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText("画面倍速"), { target: { value: "1.5" } });
    fireEvent.click(screen.getByRole("button", { name: "预览速度" }));
    await waitFor(() => expect(mocks.exactPreview).toHaveBeenLastCalledWith("task-1", {
      operation_type: "ChangeClipSpeed", clip_id: "clip-1", playback_rate: 1.5,
    }));
    expect(screen.getByRole("button", { name: "预览变速" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText("关联倍速"), { target: { value: "2" } });
    fireEvent.click(screen.getByRole("button", { name: "预览变速" }));
    await waitFor(() => expect(mocks.exactPreview).toHaveBeenLastCalledWith("task-1", {
      operation_type: "ChangeLinkedSpeed", clip_id: "clip-1", playback_rate: 2,
    }));
  });

  it("wires a desktop voice recording into a narration replacement proposal without applying it", async () => {
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
    mocks.saveRecording.mockResolvedValue({ source_ref: "recording:rec-1", artifact_id: "artifact-rec-1", audio_url: "/api/recordings/rec-1.wav", duration_ms: 3000, applied: false });
    mocks.previewAudioEdit.mockResolvedValue({
      intent: { id: "intent-audio", workspace_id: "local", task_id: task.id, raw_command: "用本机录音替换整条旁白", context_stage: "montage", selected_object_refs: [], playhead_ms: null, intent_type: "audio_edit", scope: {}, constraints: {}, confidence: 1, status: "compiled", created_at: task.created_at },
      proposal: { id: "proposal-replace-narration", workspace_id: "local", task_id: task.id, intent_id: "intent-audio", summary: "用本机录音替换整条旁白", target_refs: ["timeline:timeline-version-2"], operation: { operation_type: "EditAudioTimeline", payload: {} }, invalidated_refs: ["artifact:audio:v2"], preview: { before: { ref: "timeline:timeline-version-2", label: "当前版本", detail: "已保存" }, after: { ref: "timeline:proposal", label: "待确认版本", detail: "替换旁白" } }, external_effects: ["未确认前不写入时间线。"], status: "proposed" as const, created_at: task.created_at, updated_at: task.updated_at },
    });

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
    expect(mocks.confirmEdit).not.toHaveBeenCalled();
    expect(await screen.findByRole("button", { name: "确认修改" })).toBeInTheDocument();
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

  it("keeps each clip's quick draft through selection and a failed preview", async () => {
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
    mocks.exactPreview.mockRejectedValue(new Error("预览暂不可用"));

    render(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    fireEvent.click(screen.getByRole("button", { name: "选择镜头 1" }));
    const quick = screen.getByRole("region", { name: "镜头 01 快速编辑" });
    fireEvent.change(within(quick).getByLabelText("镜头 1 快速裁剪起点"), { target: { value: "1.5" } });
    expect(screen.getByText(/未应用 · 版本 2/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "选择镜头 2" }));
    expect(screen.getByLabelText("镜头 2 快速裁剪起点")).toHaveValue(1);
    fireEvent.click(screen.getByRole("button", { name: "选择镜头 1" }));
    expect(screen.getByLabelText("镜头 1 快速裁剪起点")).toHaveValue(1.5);
    expect(screen.getByLabelText("镜头 1 裁剪起点")).toHaveValue(1.5);
    fireEvent.click(within(quick).getByRole("button", { name: "预览修改" }));
    await waitFor(() => expect(mocks.exactPreview).toHaveBeenCalledWith(task.id, {
      operation_type: "TrimClip", clip_id: "clip-1", source_start_seconds: 1.5,
    }));
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
    await waitFor(() => expect(screen.getByLabelText("镜头 1 快速裁剪起点")).toHaveValue(2));
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
    mocks.exactPreview.mockRejectedValue(new mocks.MockCreationPlanApiError(
      409, "timeline_version_required", "当前故事板还没有可撤销的初始版本。",
    ));
    mocks.initializeTimeline.mockResolvedValue({ version_id: "timeline-version-1" });
    render(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    fireEvent.click(screen.getByRole("button", { name: "选择镜头 1" }));
    fireEvent.change(screen.getByLabelText("镜头 1 裁剪起点"), { target: { value: "1.5" } });
    fireEvent.click(screen.getByRole("button", { name: "预览裁剪" }));
    const initialize = await screen.findByRole("button", { name: "保留当前故事板并建立版本" });
    expect(mocks.initializeTimeline).not.toHaveBeenCalled();
    mocks.getWorkspace.mockResolvedValue(workspace(
      { ...task, current_timeline_version_id: "timeline-version-1", timeline_sequence: 1 }, montage,
    ));
    fireEvent.click(initialize);
    await waitFor(() => expect(mocks.initializeTimeline).toHaveBeenCalledWith(task.id));
    expect(await screen.findByText(/版本 1$/)).toBeInTheDocument();
    expect(screen.getByLabelText("镜头 1 裁剪起点")).toHaveValue(1);
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
      mocks.exactPreview.mockResolvedValue(exactPreview(task, operation.operation_type));
      fireEvent.click(screen.getByRole("button", { name: label }));
      await waitFor(() => expect(mocks.exactPreview).toHaveBeenCalledWith("task-1", operation));
      expect(await screen.findByLabelText("变更预览")).toBeInTheDocument();
      expect(mocks.exactEdit).not.toHaveBeenCalled();
      fireEvent.click(screen.getByRole("button", { name: "取消" }));
    }

    mocks.exactPreview.mockResolvedValue(exactPreview(task, "ReplaceClip"));
    fireEvent.click(screen.getAllByRole("button", { name: "换素材" })[0]);
    await waitFor(() => expect(mocks.getCandidates).toHaveBeenCalledWith("task-1", "clip-1"));
    fireEvent.change(await screen.findByLabelText("替换为已审核镜头"), { target: { value: "asset-3:segment-3" } });
    await waitFor(() => expect(mocks.exactPreview).toHaveBeenCalledWith("task-1", {
      operation_type: "ReplaceClip", clip_id: "clip-1", asset_id: "asset-3", segment_id: "segment-3", source_start_seconds: 5,
    }));
    expect(mocks.exactEdit).not.toHaveBeenCalled();
    expect(await screen.findByLabelText("变更预览")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "撤销" })).toBeEnabled());

    fireEvent.click(screen.getByRole("button", { name: "撤销" }));
    await waitFor(() => expect(mocks.undoTimeline).toHaveBeenCalledWith("task-1"));
    await waitFor(() => expect(mocks.getWorkspace).toHaveBeenCalledWith("task-1"));
    expect(screen.queryByText("不直接采用 exact 响应")).not.toBeInTheDocument();
    expect(screen.queryByText("不直接采用 undo 响应")).not.toBeInTheDocument();
    mocks.exactPreview.mockResolvedValue(exactPreview(task, "ChangeCrop"));
    fireEvent.click(screen.getByRole("button", { name: "选择镜头 1" }));
    fireEvent.change(screen.getAllByLabelText("画面显示方式")[0], { target: { value: "fit" } });
    fireEvent.click(screen.getAllByRole("button", { name: "预览画面" })[0]);
    await waitFor(() => expect(mocks.exactPreview).toHaveBeenCalledWith("task-1", {
      operation_type: "ChangeCrop", clip_id: "clip-1", fit_mode: "fit", crop_x: .5, crop_y: .5,
    }));
    expect(await screen.findByLabelText("变更预览")).toBeInTheDocument();
    fireEvent.change(screen.getAllByLabelText("画面显示方式")[0], { target: { value: "fill" } });
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
      language: "zh" as const,
      pronunciation_version: "v1",
      segments: [
        {
          id: "narration-segment-1", source_section_id: "section-1", text: "第一段真实配音",
          audio_reference: "narration://artifact/segment-1", audio_url: "/api/creation-tasks/task-1/narration/segments/narration-segment-1/audio",
          duration_seconds: 3, sample_rate: 24000, model_id: "qwen3-tts-12hz-0.6b-base-8bit",
          voice_profile_id: "voice-1", voice_version: "v1", language: "zh" as const, pronunciation_version: "p1", status: "ready" as const,
        },
        {
          id: "narration-segment-2", source_section_id: "section-2", text: "第二段真实配音",
          audio_reference: "narration://artifact/segment-2", audio_url: "/api/creation-tasks/task-1/narration/segments/narration-segment-2/audio",
          duration_seconds: 3, sample_rate: 24000, model_id: "qwen3-tts-12hz-0.6b-base-8bit",
          voice_profile_id: "voice-1", voice_version: "v1", language: "zh" as const, pronunciation_version: "p1", status: "ready" as const,
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

    fireEvent.click(screen.getByRole("button", { name: "确认修改" }));
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

    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(screen.queryByLabelText("变更预览")).not.toBeInTheDocument();
    expect(mocks.confirmEdit).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "预览修改" }));
    await screen.findByLabelText("变更预览");
    fireEvent.click(screen.getByRole("button", { name: "确认修改" }));

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
      language: "zh",
      pronunciation_version: "v1",
      segments: [{
        id: "narration-segment-2", source_section_id: "section-2", text: "第二段旁白",
        audio_reference: "narration://artifact/segment-2", audio_url: "/api/audio-2",
        duration_seconds: 3, sample_rate: 24000, model_id: "qwen3-tts",
        voice_profile_id: "voice-cc", voice_version: "voice-v3", language: "zh",
        pronunciation_version: "v1", status: "ready",
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

});
