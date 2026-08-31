import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CreationTaskWorkspace } from "../CreationTaskWorkspace";
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
    confirmScript: vi.fn(),
    generateNarration: vi.fn(),
    getWorkspace: vi.fn(),
    selectVoice: vi.fn(),
    updateClip: vi.fn(),
    updateSubtitle: vi.fn(),
  };
});

vi.mock("@/components/AppShell", () => ({
  AppShell: ({ children, inspector }: { children: React.ReactNode; inspector: React.ReactNode }) => (
    <><main>{children}</main><aside aria-label="任务详情">{inspector}</aside></>
  ),
}));

vi.mock("@/lib/api", () => ({
  apiUrl: (path: string) => path,
  CreationPlanApiError: mocks.MockCreationPlanApiError,
  confirmCreationTaskScript: mocks.confirmScript,
  confirmCreationTaskTopic: vi.fn(),
  createCreationTaskMontagePlan: vi.fn(),
  generateCreationTaskNarration: mocks.generateNarration,
  generateCreationTaskScript: vi.fn(),
  generateCreationTaskTopics: vi.fn(),
  getCreationTaskMontageClipCandidates: vi.fn(),
  getCreationTaskTopics: vi.fn(),
  getCreationTaskWorkspace: mocks.getWorkspace,
  renderCreationTaskMontage: vi.fn(),
  selectCreationTaskNarrationVoice: mocks.selectVoice,
  updateCreationTaskMontageClip: mocks.updateClip,
  updateCreationTaskSubtitleCue: mocks.updateSubtitle,
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
    montage,
  };
}

afterEach(() => {
  vi.clearAllMocks();
  Reflect.deleteProperty(window, "electronAPI");
});

describe("CreationTaskWorkspace", () => {
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
    expect(screen.queryByRole("button", { name: "生成真实配音" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "去登记授权声音" })).toHaveAttribute("href", "/profiles");
    expect(screen.getByText(/不需要重做选题或文案/)).toBeInTheDocument();
  });

  it("lets the user revise and reconfirm the persisted script after regeneration fails", async () => {
    const failedTask = {
      ...baseTask,
      stage: "script" as const,
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
    expect(screen.getByRole("heading", { name: "压缩并重新确认文案" })).toBeInTheDocument();
    fireEvent.change(screen.getByDisplayValue("原来的正文过长。"), { target: { value: "压缩后的三十秒正文。" } });
    fireEvent.click(screen.getByRole("button", { name: "确认修订文案" }));

    await waitFor(() => expect(mocks.confirmScript).toHaveBeenCalledWith("task-1", "script-1", expect.objectContaining({
      sections: [expect.objectContaining({ content: "压缩后的三十秒正文。" })],
    })));
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

    render(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    fireEvent.click(screen.getByRole("button", { name: "使用这个声音继续" }));

    await waitFor(() => expect(mocks.selectVoice).toHaveBeenCalledWith("task-1", { voice_profile_id: "voice-1", voice_version: "v1" }));
    expect(await screen.findByRole("button", { name: "生成真实配音" })).toBeInTheDocument();
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

    render(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    expect(screen.queryByText("人物与口型")).not.toBeInTheDocument();
    expect(screen.queryByText("字幕与封面")).not.toBeInTheDocument();
    expect(screen.queryByText("数据监控")).not.toBeInTheDocument();
    expect(screen.getByTitle("检查成片：当前")).toHaveAttribute("aria-current", "step");
    fireEvent.click(screen.getByRole("button", { name: "保存到本地" }));

    await waitFor(() => expect(saveRenderedVideo).toHaveBeenCalledWith("task-1"));
    expect(saveRenderedVideo).toHaveBeenCalledTimes(1);
    expect(await screen.findByRole("status")).toHaveTextContent("成片.mp4");
    fireEvent.click(screen.getByRole("button", { name: "修改镜头与字幕" }));
    expect(screen.getByRole("heading", { name: "检查镜头、字幕和裁剪" })).toBeInTheDocument();
  });

  it("persists a reviewed clip trim through the CreationTask montage API", async () => {
    const task = { ...baseTask, stage: "montage" as const, stage_status: "waiting_for_user" as const, status: "waiting_for_user" as const, progress: 70 };
    const montage = {
      id: "montage-1", task_id: task.id, script_id: "script-1", script_title: "故事板",
      status: "ready" as const,
      cues: [{ id: "cue-1", source_section_id: "section-1", text: "第一段旁白", duration_seconds: 3 }],
      clips: [{ id: "clip-1", cue_id: "cue-1", asset_id: "asset-1", segment_id: "segment-1", source_reference: "workspace://clip.mp4", source_start_seconds: 1, source_end_seconds: 4, timeline_start_seconds: 0, timeline_end_seconds: 3, match_score: .9, match_reasons: ["已审核"], needs_confirmation: false, role: "b_roll" as const, locked: false, manually_selected: false }],
      duration_seconds: 3, low_confidence_clip_count: 0,
      narration_reference: "narration://artifact/audio-1", output_url: null, subtitle_url: null,
      message: "可以渲染", created_at: task.created_at, updated_at: task.updated_at,
    };
    const initial = workspace(task, montage);
    mocks.updateClip.mockResolvedValue(montage);
    mocks.updateSubtitle.mockResolvedValue(montage);
    mocks.getWorkspace.mockResolvedValue(initial);

    render(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    fireEvent.change(screen.getByLabelText("镜头 1 裁剪起点"), { target: { value: "1.5" } });
    fireEvent.blur(screen.getByLabelText("镜头 1 裁剪起点"));

    await waitFor(() => expect(mocks.updateClip).toHaveBeenCalledWith("task-1", "clip-1", { source_start_seconds: 1.5 }));

    fireEvent.change(screen.getByLabelText("镜头 1 字幕"), { target: { value: "修改后的字幕" } });
    fireEvent.blur(screen.getByLabelText("镜头 1 字幕"));

    await waitFor(() => expect(mocks.updateSubtitle).toHaveBeenCalledWith("task-1", "cue-1", { text: "修改后的字幕" }));
  });
});
