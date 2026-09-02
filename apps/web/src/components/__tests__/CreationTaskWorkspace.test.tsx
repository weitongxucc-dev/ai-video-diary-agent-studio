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
    confirmEdit: vi.fn(),
    confirmScript: vi.fn(),
    exactEdit: vi.fn(),
    generateNarration: vi.fn(),
    getCandidates: vi.fn(),
    getWorkspace: vi.fn(),
    previewEdit: vi.fn(),
    renderMontage: vi.fn(),
    selectVoice: vi.fn(),
    undoTimeline: vi.fn(),
  };
});

vi.mock("@/components/AppShell", () => ({
  AppShell: ({ children, inspector }: { children: React.ReactNode; inspector: React.ReactNode }) => (
    <><main>{children}</main><aside aria-label="任务详情">{inspector}</aside></>
  ),
}));

vi.mock("@/lib/api", () => ({
  apiUrl: (path: string) => path,
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
  previewCreationTaskEdit: mocks.previewEdit,
  renderCreationTaskMontage: mocks.renderMontage,
  selectCreationTaskNarrationVoice: mocks.selectVoice,
  undoCreationTaskTimeline: mocks.undoTimeline,
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
    mocks.renderMontage.mockResolvedValue(initial.montage);
    mocks.getWorkspace.mockResolvedValue(initial);

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
    fireEvent.click(screen.getByRole("button", { name: "生成可编辑版本" }));
    await waitFor(() => expect(mocks.renderMontage).toHaveBeenCalledWith("task-1"));
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
    const refreshedSubtitle = workspace(
      { ...task, current_timeline_version_id: "timeline-version-4", timeline_sequence: 4 },
      { ...montage, cues: [{ ...montage.cues[0], text: "修改后的字幕\n保留换行" }], message: "字幕已更新，需要重新渲染。" },
    );
    mocks.exactEdit.mockResolvedValue({ montage: { ...montage, message: "不直接采用编辑响应" }, operation: { id: "operation-1" } });
    mocks.getWorkspace.mockResolvedValueOnce(refreshed).mockResolvedValue(refreshedSubtitle);

    render(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    expect(screen.getByText("当前版本 2")).toHaveAttribute("title", "timeline-version-2");
    expect(screen.getByText("匹配理由：已审核")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("镜头 1 裁剪起点"), { target: { value: "1.5" } });
    fireEvent.blur(screen.getByLabelText("镜头 1 裁剪起点"));

    await waitFor(() => expect(mocks.exactEdit).toHaveBeenCalledWith("task-1", {
      operation_type: "TrimClip",
      clip_id: "clip-1",
      source_start_seconds: 1.5,
    }));
    expect(await screen.findByText("当前版本 3")).toHaveAttribute("title", "timeline-version-3");
    expect(screen.getByText("镜头已更新，需要重新渲染。")).toBeInTheDocument();
    expect(screen.queryByText("不直接采用编辑响应")).not.toBeInTheDocument();

    mocks.exactEdit.mockClear();
    const subtitle = screen.getByLabelText("镜头 1 字幕");
    expect(subtitle).not.toHaveAttribute("maxlength");
    fireEvent.focus(subtitle);
    expect(screen.getByText("已选 字幕 1")).toBeInTheDocument();
    fireEvent.change(subtitle, { target: { value: "修改后的字幕\n保留换行" } });
    fireEvent.blur(subtitle);

    await waitFor(() => expect(mocks.exactEdit).toHaveBeenCalledWith("task-1", {
      operation_type: "UpdateSubtitleCue",
      cue_id: "cue-1",
      text: "修改后的字幕\n保留换行",
    }));
    await waitFor(() => expect(mocks.getWorkspace).toHaveBeenCalledTimes(2));
    expect(await screen.findByText("字幕已更新，需要重新渲染。")).toBeInTheDocument();

    mocks.previewEdit.mockResolvedValue({
      intent: {
        id: "intent-cue", workspace_id: "local", task_id: task.id,
        raw_command: "把这句字幕改得更简洁", context_stage: "montage",
        selected_object_refs: ["cue:cue-1"], playhead_ms: null,
        intent_type: "update_subtitle_cue", scope: { cue_id: "cue-1" }, constraints: {},
        confidence: .99, status: "compiled", created_at: task.created_at,
      },
      proposal: {
        id: "proposal-cue", workspace_id: "local", task_id: task.id, intent_id: "intent-cue",
        summary: "缩短当前字幕。", target_refs: ["cue:cue-1"],
        operation: { operation_type: "UpdateSubtitleCue", payload: { cue_id: "cue-1", text: "简洁字幕" } },
        invalidated_refs: ["artifact:subtitle:v3", "artifact:preview:v3"],
        preview: {
          before: { ref: "cue:cue-1", label: "当前字幕", detail: "修改后的字幕" },
          after: { ref: "cue:cue-1", label: "简洁字幕", detail: "简洁字幕" },
        },
        external_effects: ["配音和镜头保持不变。"], status: "proposed",
        created_at: task.created_at, updated_at: task.updated_at,
      },
    });
    fireEvent.change(screen.getByLabelText("局部修改要求"), { target: { value: "把这句字幕改得更简洁" } });
    fireEvent.click(screen.getByRole("button", { name: "预览修改" }));
    await waitFor(() => expect(mocks.previewEdit).toHaveBeenCalledWith("task-1", {
      raw_command: "把这句字幕改得更简洁",
      context_stage: "montage",
      selected_object_refs: ["cue:cue-1"],
      playhead_ms: null,
    }));
    expect(await screen.findByLabelText("变更预览")).toHaveTextContent("artifact:subtitle:v3");
    expect(screen.getByLabelText("变更预览")).toHaveTextContent("配音和镜头保持不变");
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

    fireEvent.click(screen.getAllByRole("button", { name: "向后移动" })[0]);
    await waitFor(() => expect(mocks.exactEdit).toHaveBeenCalledWith("task-1", {
      operation_type: "ReorderClip", clip_id: "clip-1", position: 1,
    }));
    await waitFor(() => expect(screen.getAllByRole("button", { name: "锁定" })[0]).toBeEnabled());

    mocks.exactEdit.mockClear();
    fireEvent.click(screen.getAllByRole("button", { name: "锁定" })[0]);
    await waitFor(() => expect(mocks.exactEdit).toHaveBeenCalledWith("task-1", {
      operation_type: "SetClipLock", clip_id: "clip-1", locked: true,
    }));
    await waitFor(() => expect(screen.getByRole("button", { name: "确认镜头" })).toBeEnabled());

    mocks.exactEdit.mockClear();
    fireEvent.click(screen.getByRole("button", { name: "确认镜头" }));
    await waitFor(() => expect(mocks.exactEdit).toHaveBeenCalledWith("task-1", {
      operation_type: "ConfirmClip", clip_id: "clip-1",
    }));
    await waitFor(() => expect(screen.getAllByRole("button", { name: "替换" })[0]).toBeEnabled());

    mocks.exactEdit.mockClear();
    fireEvent.click(screen.getAllByRole("button", { name: "替换" })[0]);
    await waitFor(() => expect(mocks.getCandidates).toHaveBeenCalledWith("task-1", "clip-1"));
    fireEvent.change(await screen.findByLabelText("替换为已审核镜头"), { target: { value: "asset-3:segment-3" } });
    await waitFor(() => expect(mocks.exactEdit).toHaveBeenCalledWith("task-1", {
      operation_type: "ReplaceClip", clip_id: "clip-1", asset_id: "asset-3", segment_id: "segment-3", source_start_seconds: 5,
    }));
    await waitFor(() => expect(screen.getByRole("button", { name: "撤销" })).toBeEnabled());

    fireEvent.click(screen.getByRole("button", { name: "撤销" }));
    await waitFor(() => expect(mocks.undoTimeline).toHaveBeenCalledWith("task-1"));
    await waitFor(() => expect(mocks.getWorkspace).toHaveBeenCalledWith("task-1"));
    expect(screen.queryByText("不直接采用 exact 响应")).not.toBeInTheDocument();
    expect(screen.queryByText("不直接采用 undo 响应")).not.toBeInTheDocument();
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
    expect(screen.getByText("已选 配音 1")).toBeInTheDocument();
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
    fireEvent.change(screen.getByLabelText("局部修改要求"), { target: { value: "换成另一个镜头" } });
    fireEvent.click(screen.getByRole("button", { name: "预览修改" }));

    await waitFor(() => expect(mocks.previewEdit).toHaveBeenCalledWith("task-1", {
      raw_command: "换成另一个镜头",
      context_stage: "montage",
      selected_object_refs: ["clip:clip-1"],
      playhead_ms: null,
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
    expect(await screen.findByText("镜头已替换，需要重新渲染。")).toBeInTheDocument();
    expect(screen.queryByText("这个 confirm 响应不应直接显示")).not.toBeInTheDocument();
  });
});
