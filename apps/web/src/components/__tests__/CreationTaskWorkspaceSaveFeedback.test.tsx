import { useState } from "react";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CreationTaskWorkspace } from "../CreationTaskWorkspace";
import type { CreationTask, CreationTaskWorkspace as WorkspaceState } from "@/lib/types";

// WIL-175 专项：输出页（activeView === "review"）原生保存的成功/取消/失败三种结果，
// 失败必须同时给出「没有保存成」「为什么」「接下来怎么做」，取消必须安静，
// 失败之后必须还能再次保存。剪辑（montage）与字幕封面（subtitle_cover）的既有错误行为
// 由 CreationTaskWorkspace.test.tsx 覆盖，本文件不重复。

const mocks = vi.hoisted(() => {
  // 与真实 lib/api 保持同名同形：errorDetails() 无条件执行 `cause instanceof CreationPlanApiError`，
  // 该符号若是 undefined 会直接抛 TypeError，因此即使没有用例构造它也必须存在。
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
    getWorkspace: vi.fn(),
  getRuntimeSettings: vi.fn().mockResolvedValue({ status: { llm: { configured: true, provider: "minimax", model: "MiniMax-M3" } } }),
  getAssistantHistory: vi.fn().mockResolvedValue({ items: [] }),
  getExecution: vi.fn().mockResolvedValue({ task_id: "task-1", job: null, stages: [], video_ready: false, cover_status: "pending", cover_message: null }),
  previewExecution: vi.fn().mockResolvedValue(null),
  };
});

// 忠实复刻真实 AppShell：inspector 放在 detailsOpen 门后（AppShell.tsx L222/L555），
// 默认不渲染，必须由用户点「查看详情」才出现。不得无条件渲染 inspector——
// 否则会凭空造出一个真实产品里看不见的 alert，把「输出页无提示」这个缺陷掩盖掉。
vi.mock("@/components/AppShell", () => ({
  AppShell: ({ children, conversation, inspector, quickInspector, quickInspectorLabel, toolbar, toolbarActions, inspectorTitle = "任务详情" }: { children: React.ReactNode; conversation?: React.ReactNode; toolbar?: React.ReactNode; toolbarActions?: React.ReactNode; inspector?: React.ReactNode; quickInspector?: (openDetails: () => void) => React.ReactNode; quickInspectorLabel?: string; inspectorTitle?: string }) => {
    const [detailsOpen, setDetailsOpen] = useState(false);
    return (
      <><header>{toolbar}{toolbarActions}</header><section>{conversation}</section><main>{children}</main>{quickInspector ? <section aria-label={quickInspectorLabel}>{quickInspector(() => setDetailsOpen(true))}</section> : null}{inspector && !detailsOpen ? <button onClick={() => setDetailsOpen(true)} type="button">查看详情</button> : null}{detailsOpen && inspector ? <aside aria-label={inspectorTitle}>{inspector}</aside> : null}</>
    );
  },
}));

vi.mock("../DesktopVoiceRecorder", () => ({
  DesktopVoiceRecorder: ({ onCancel }: { onCancel: () => void }) => (
    <button onClick={onCancel} type="button">关闭录音</button>
  ),
}));

vi.mock("@/lib/api", () => ({
  apiUrl: (path: string) => path,
  CreationPlanApiError: mocks.MockCreationPlanApiError,
  getCreationTaskGraphicSources: vi.fn().mockResolvedValue([]),
  applyCreationTaskExactEdit: vi.fn(),
  confirmCreationTaskEdit: vi.fn(),
  confirmCreationTaskScript: vi.fn(),
  confirmCreationTaskTopic: vi.fn(),
  createCreationTaskMontagePlan: vi.fn(),
  generateCreationTaskNarration: vi.fn(),
  generateCreationTaskPersonRender: vi.fn(),
  generateCreationTaskScript: vi.fn(),
  generateCreationTaskTopics: vi.fn(),
  getCreationTaskMontageClipCandidates: vi.fn(),
  getCreationTaskTopics: vi.fn(),
  getCreationTaskWorkspace: mocks.getWorkspace,
  initializeCreationTaskTimeline: vi.fn(),
  getCreationTaskAssistantHistory: mocks.getAssistantHistory,
  getCreationExecution: mocks.getExecution,
  previewCreationExecution: mocks.previewExecution,
  startCreationExecution: vi.fn(),
  resumeCreationExecution: vi.fn(),
  cancelCreationExecution: vi.fn(),
  getRuntimeSettings: mocks.getRuntimeSettings,
  sendCreationTaskAssistantTurn: vi.fn(),
  previewCreationTaskEdit: vi.fn(),
  previewCompoundSelection: vi.fn(),
  previewCreationTaskExactEdit: vi.fn(),
  previewCreationTaskAudio: vi.fn(),
  previewCreationTaskVisual: vi.fn(),
  renderCreationTaskMontage: vi.fn(),
  saveCreationTaskRecording: vi.fn(),
  selectCreationTaskNarrationVoice: vi.fn(),
  undoCreationTaskTimeline: vi.fn(),
  redoCreationTaskTimeline: vi.fn(),
  getProductionMaterials: vi.fn().mockResolvedValue({ assets: [], total_count: 0, limit: 200, offset: 0, has_more: false }),
}));

// 输出页所需的最小工作区：stage=review、有 output_url、无有效字幕轨
// （defaultWorkspaceView 因此返回 "review" 而不是 "subtitle_cover"）。
function reviewWorkspace(): WorkspaceState {
  const task: CreationTask = {
    id: "task-1",
    workspace_id: "local",
    plan_id: "plan-1",
    name: "本地真成片",
    scenario: "制作一条中文口播视频",
    stage: "review",
    stage_status: "waiting_for_user",
    status: "waiting_for_user",
    progress: 90,
    recovery_point: "montage_rendered",
    selected_topic_id: "topic-1",
    confirmed_script_id: "script-1",
    narration_cache_key: null,
    montage_plan_id: "montage-plan-1",
    current_timeline_version_id: "timeline-version-1",
    timeline_sequence: 1,
    can_undo: true,
    output_ready: true,
    stage_reason: null,
    stage_impact: null,
    stage_repair_action: null,
    stage_retryable: false,
    created_at: "2026-08-30T00:00:00Z",
    updated_at: "2026-08-30T00:00:00Z",
  };
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
      material_matches: [],
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
    person_render_capability: { available: false, model_id: null, message: "本地人物口型模型未配置。", repair_action: null },
    montage: {
      id: "montage-1",
      task_id: task.id,
      script_id: "script-1",
      script_title: "成片",
      status: "rendered",
      cues: [],
      clips: [],
      duration_seconds: 12,
      low_confidence_clip_count: 0,
      narration_reference: "narration://artifact/audio-1",
      output_url: `/api/creation-tasks/${task.id}/output`,
      subtitle_url: `/api/creation-tasks/${task.id}/output/subtitles`,
      message: "等待检查",
      created_at: task.created_at,
      updated_at: task.updated_at,
    },
    subtitle_tracks: [],
    cover_asset: null,
    subtitle_translation_capability: { available: false, model_id: null, message: "当前未配置英文翻译模型。", repair_action: null },
    cover_frame_capability: { available: true, model_id: "ffmpeg+Pillow", message: "可从当前成片选帧生成封面。", repair_action: null },
    image_generation_capability: { available: false, model_id: null, message: "尚未配置图片模型。", repair_action: null },
  };
}

function useElectronSave(saveRenderedVideo: ReturnType<typeof vi.fn>) {
  Object.defineProperty(window, "electronAPI", { configurable: true, value: { saveRenderedVideo } });
}

beforeEach(() => {
  mocks.getWorkspace.mockResolvedValue(reviewWorkspace());
});

afterEach(() => {
  vi.clearAllMocks();
  mocks.previewExecution.mockResolvedValue(null);
  Reflect.deleteProperty(window, "electronAPI");
});

describe("CreationTaskWorkspace 输出页原生保存反馈", () => {
  it("保存失败时在输出页显示未保存状态、原因和可执行下一步", async () => {
    const saveRenderedVideo = vi.fn().mockResolvedValue({
      saved: false,
      error: "ENOENT: no such file or directory, open '/Volumes/素材盘/成片.mp4'",
    });
    useElectronSave(saveRenderedVideo);

    render(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={reviewWorkspace()} />);
    fireEvent.click(screen.getByRole("button", { name: "保存到本地" }));

    await waitFor(() => expect(saveRenderedVideo).toHaveBeenCalledWith("task-1"));
    const alert = await screen.findByRole("alert");
    // 1) 未保存状态
    expect(alert).toHaveTextContent(/没有保存到本地/);
    // 2) 原因：桌面端返回的原始报错必须保留，不得被通用文案吞掉
    expect(alert).toHaveTextContent("ENOENT: no such file or directory");
    // 3) 可执行下一步
    expect(alert).toHaveTextContent(/再次点击「保存到本地」/);
    // 失败不得留下成功提示
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("本地服务没返回成片时，提示不会只让用户去查磁盘", async () => {
    // 真实 IPC 返回形状：main.js 的 save-rendered-video 是 resolve {saved, error}，
    // 不会 reject、也不会把 API 的 body/code 结构化传回，error 就是一句人话字符串。
    // 若下一步只写「检查保存位置」，用户会对着完好的外接盘反复重试，永远查不到真正原因。
    const saveRenderedVideo = vi.fn().mockResolvedValue({
      saved: false,
      error: "本地服务没有返回可保存的 MP4（HTTP 404）。",
    });
    useElectronSave(saveRenderedVideo);

    render(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={reviewWorkspace()} />);
    fireEvent.click(screen.getByRole("button", { name: "保存到本地" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/没有保存到本地/);
    // 桌面端返回的原始原因必须原样保留
    expect(alert).toHaveTextContent("本地服务没有返回可保存的 MP4");
    // 下一步必须同时覆盖「当前成片/本地服务」和「保存位置」，不能只归因磁盘
    expect(alert).toHaveTextContent(/当前成片和本地服务/);
    expect(alert).toHaveTextContent(/保存位置是否可写/);
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("用户取消保存时保持安静，既不报成功也不报失败", async () => {
    const saveRenderedVideo = vi.fn().mockResolvedValue({ saved: false, canceled: true });
    useElectronSave(saveRenderedVideo);

    render(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={reviewWorkspace()} />);
    fireEvent.click(screen.getByRole("button", { name: "保存到本地" }));

    await waitFor(() => expect(saveRenderedVideo).toHaveBeenCalledWith("task-1"));
    await waitFor(() => expect(screen.getByRole("button", { name: "保存到本地" })).toBeEnabled());
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    // 打开「任务详情」也不该出现错误：取消是「什么都没发生」，不是「出了错但藏起来」。
    fireEvent.click(screen.getByRole("button", { name: "查看详情" }));
    const inspector = await screen.findByRole("complementary", { name: "任务详情" });
    expect(within(inspector).queryByRole("alert")).not.toBeInTheDocument();
  });

  it("失败后可以重新保存，成功提示只出现在真正写入之后", async () => {
    const saveRenderedVideo = vi.fn()
      .mockResolvedValueOnce({ saved: false, error: "ENOSPC: no space left on device, write '/Volumes/素材盘/成片.mp4'" })
      .mockResolvedValueOnce({ saved: true, fileName: "成片.mp4" });
    useElectronSave(saveRenderedVideo);

    render(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={reviewWorkspace()} />);
    fireEvent.click(screen.getByRole("button", { name: "保存到本地" }));

    await screen.findByRole("alert");
    const retry = screen.getByRole("button", { name: "保存到本地" });
    expect(retry).toBeEnabled();
    fireEvent.click(retry);

    await waitFor(() => expect(saveRenderedVideo).toHaveBeenCalledTimes(2));
    expect(await screen.findByRole("status")).toHaveTextContent("成片.mp4");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("浏览器预览没有原生保存能力时说明没有保存成和正确下一步", async () => {
    Reflect.deleteProperty(window, "electronAPI");

    render(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={reviewWorkspace()} />);
    fireEvent.click(screen.getByRole("button", { name: "保存到本地" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/没有保存到本地/);
    expect(alert).toHaveTextContent("请在桌面客户端中使用原生保存功能");
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });
});

function subtitleWorkspace(): WorkspaceState {
  const workspace = reviewWorkspace();
  workspace.subtitle_tracks = [
    {
      id: "subtitle_zh", workspace_id: "local", task_id: workspace.task.id,
      timeline_version_id: "timeline-version-1", language: "zh-CN", source_language: null,
      style: { name: "default", font_family: "PingFang SC", font_size: 40, primary_color: "#fff", emphasis_color: "#ff0", position: "bottom", bilingual_order: "translation_then_source" },
      cues: [{ id: "cue_zh", source_cue_id: null, text: "新中文", start_seconds: 0.4, end_seconds: 2.8, speaker: null, line_breaks: [], emphasis: [] }],
      generation_method: "timeline", model_id: null, artifact_ref: null, transform_id: null,
      status: "valid", failure_reason: null, impact: null, repair_action: null,
      created_at: workspace.task.created_at, updated_at: workspace.task.updated_at,
    },
    {
      id: "subtitle_en", workspace_id: "local", task_id: workspace.task.id,
      timeline_version_id: "timeline-version-1", language: "en-US", source_language: "zh-CN",
      style: { name: "default", font_family: "PingFang SC", font_size: 40, primary_color: "#fff", emphasis_color: "#ff0", position: "bottom", bilingual_order: "translation_then_source" },
      cues: [{ id: "cue_en", source_cue_id: "cue_zh", text: "New English", start_seconds: 0.4, end_seconds: 2.8, speaker: null, line_breaks: [], emphasis: [] }],
      generation_method: "translation", model_id: null, artifact_ref: null, transform_id: null,
      status: "valid", failure_reason: null, impact: null, repair_action: null,
      created_at: workspace.task.created_at, updated_at: workspace.task.updated_at,
    },
  ];
  return workspace;
}

describe("CreationTaskWorkspace 字幕保存", () => {
  it("显式选择双语后传当前版本和两轨，取消无成功提示，失败可重试", async () => {
    const saveSubtitleFile = vi.fn()
      .mockResolvedValueOnce({ saved: false, canceled: true })
      .mockResolvedValueOnce({ saved: false, error: "当前字幕版本已变化" })
      .mockResolvedValueOnce({ saved: true, fileName: "task-1-zh-en.srt" });
    Object.defineProperty(window, "electronAPI", { configurable: true, value: { saveSubtitleFile } });
    const workspace = subtitleWorkspace();
    mocks.getWorkspace.mockResolvedValue(workspace);
    render(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={workspace} />);
    fireEvent.change(screen.getByRole("combobox", { name: "保存字幕语言" }), { target: { value: "zh-en" } });
    const save = screen.getByRole("button", { name: "保存字幕" });
    fireEvent.click(save);
    await waitFor(() => expect(saveSubtitleFile).toHaveBeenCalledWith({
      taskId: "task-1", expectedVersionId: "timeline-version-1", mode: "zh-en",
      zhTrackId: "subtitle_zh", enTrackId: "subtitle_en",
    }));
    await waitFor(() => expect(save).toBeEnabled());
    expect(screen.queryByText(/字幕已保存/)).not.toBeInTheDocument();
    fireEvent.click(save);
    expect(await screen.findByRole("alert")).toHaveTextContent("当前字幕版本已变化");
    fireEvent.click(save);
    expect(await screen.findByText(/字幕已保存：task-1-zh-en.srt/)).toBeInTheDocument();
  });
});
