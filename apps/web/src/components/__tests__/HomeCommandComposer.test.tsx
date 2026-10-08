import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { HomeCommandComposer } from "@/components/HomeCommandComposer";
import type { CreationTask } from "@/lib/types";

const mocks = vi.hoisted(() => ({ ask: vi.fn(), preflight: vi.fn(), confirm: vi.fn(), settings: vi.fn(), editAsk: vi.fn(), confirmEdit: vi.fn(), undoEdit: vi.fn() }));
vi.mock("@/lib/api", () => ({
  apiUrl: (path: string) => path,
  getRuntimeSettings: mocks.settings,
  sendWorkspaceAssistantPlanTurn: mocks.ask,
  previewCreationPlan: mocks.preflight,
  confirmCreationPlan: mocks.confirm,
  sendCreationTaskAssistantTurn: mocks.editAsk,
  confirmCreationTaskEdit: mocks.confirmEdit,
  undoCreationTaskTimeline: mocks.undoEdit,
}));

afterEach(() => vi.clearAllMocks());

describe("HomeCommandComposer", () => {
  function configuredModel() {
    mocks.settings.mockResolvedValue({ status: { llm: { configured: true, provider: "minimax", model: "MiniMax-M3" } } });
  }

  it("asks the assistant before creating anything, then confirms the real preflight", async () => {
    configuredModel();
    const assignLocation = vi.fn();
    mocks.ask.mockResolvedValue({
      reply: "可制作一条 60 秒产品视频。", provider: "minimax", model: "MiniMax-M3",
      plan_preview: {
        name: "产品视频", goal: "介绍产品", target_count: 1, language: "zh-CN",
        aspect_ratio: "9:16", duration_target_seconds: 60, approved_clip_count: 4,
        approved_asset_count: 2, knowledge_references: [], source_references: [],
        material_matches: [], material_rule_summary: "已审核素材", model_snapshot: [],
        confirmation_points: [], pronunciation_references: [],
        selected_voice: { profile_id: "voice-1" }, blockers: [], confirmable: true,
        preflight_fingerprint: "a".repeat(64),
      },
    });
    mocks.confirm.mockResolvedValue({ creation_task: { id: "task-new" } });
    render(<HomeCommandComposer assignLocation={assignLocation} />);

    expect(screen.getByRole("button", { name: "发送" })).toBeDisabled();
    fireEvent.change(screen.getByRole("textbox", { name: "制作指令" }), { target: { value: "介绍产品" } });
    await waitFor(() => expect(screen.getByRole("button", { name: "发送" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    await waitFor(() => expect(mocks.ask).toHaveBeenCalledWith("介绍产品", expect.any(String)));
    expect(mocks.confirm).not.toHaveBeenCalled();
    expect(await screen.findByRole("dialog", { name: "制作方案" })).toBeVisible();
    expect(await screen.findByRole("region", { name: "制作方案" })).toHaveTextContent("镜头 4");

    fireEvent.click(screen.getByRole("button", { name: "关闭卡片" }));
    expect(screen.queryByRole("dialog", { name: "制作方案" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "查看方案" }));

    fireEvent.click(await screen.findByRole("button", { name: "创建任务" }));
    await waitFor(() => expect(mocks.confirm).toHaveBeenCalledWith({
      goal: "介绍产品", name: "产品视频", target_count: 1, language: "zh-CN",
      aspect_ratio: "9:16", duration_target_seconds: 60, presenter_share_target: 20, lipsync_quality: "high", preflight_fingerprint: "a".repeat(64),
    }));
    expect(assignLocation).toHaveBeenCalledWith("/tasks/task-new");
  });

  it("rechecks a selected face-share stop before allowing creation", async () => {
    configuredModel();
    const base = {
      name: "产品视频", goal: "介绍产品", target_count: 1, language: "zh-CN" as const,
      aspect_ratio: "9:16" as const, duration_target_seconds: 60,
      presenter_share_target: 20 as const, approved_clip_count: 2, approved_asset_count: 1,
      knowledge_references: [], source_references: [], material_matches: [], material_rule_summary: "已审核素材",
      model_snapshot: [], confirmation_points: [], pronunciation_references: [], selected_voice: null,
      blockers: [], confirmable: true, preflight_fingerprint: "a".repeat(64),
    };
    mocks.ask.mockResolvedValue({ reply: "方案已备好", provider: "minimax", model: "MiniMax-M3", plan_preview: base });
    mocks.preflight.mockResolvedValue({ ...base, presenter_share_target: 40, preflight_fingerprint: "b".repeat(64) });
    mocks.confirm.mockResolvedValue({ creation_task: { id: "task-40" } });
    render(<HomeCommandComposer assignLocation={vi.fn()} />);
    fireEvent.change(screen.getByRole("textbox", { name: "制作指令" }), { target: { value: "介绍产品" } });
    await waitFor(() => expect(screen.getByRole("button", { name: "发送" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    const slider = await screen.findByRole("slider", { name: "露脸占比" });
    fireEvent.change(slider, { target: { value: "40" } });
    await waitFor(() => expect(mocks.preflight).toHaveBeenCalledWith(expect.objectContaining({ presenter_share_target: 40 })));
    await waitFor(() => expect(screen.getByRole("button", { name: "创建任务" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "创建任务" }));
    await waitFor(() => expect(mocks.confirm).toHaveBeenCalledWith(expect.objectContaining({ presenter_share_target: 40, preflight_fingerprint: "b".repeat(64) })));
  });

  it("keeps a blocked preflight visible without creating a task", async () => {
    configuredModel();
    mocks.ask.mockResolvedValue({
      reply: "还没有可用声音。", provider: "minimax", model: "MiniMax-M3",
      plan_preview: {
        name: "演示", goal: "演示产品", target_count: 1, language: "zh-CN",
        aspect_ratio: "9:16", duration_target_seconds: 60, approved_clip_count: 0,
        approved_asset_count: 0, knowledge_references: [], source_references: [],
        material_matches: [], material_rule_summary: "无可用素材", model_snapshot: [],
        confirmation_points: [], pronunciation_references: [],
        selected_voice: null, confirmable: false, preflight_fingerprint: "b".repeat(64),
        blockers: [{ reason: "voice_missing", impact: "缺少声音。", repair_action: "请先选择授权声音。", blocks_confirmation: true }],
      },
    });
    render(<HomeCommandComposer />);
    fireEvent.change(screen.getByRole("textbox", { name: "制作指令" }), { target: { value: "演示产品" } });
    await waitFor(() => expect(screen.getByRole("button", { name: "发送" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    expect(await screen.findByText(/先处理/)).toBeInTheDocument();
    expect(screen.getByText(/请先选择授权声音/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "创建任务" })).toBeDisabled();
    expect(mocks.confirm).not.toHaveBeenCalled();
  });

  it("shows a voice blocker before allowing a draft task", async () => {
    configuredModel();
    mocks.ask.mockResolvedValue({
      reply: "可以先建任务，配音还需处理。", provider: "minimax", model: "MiniMax-M3",
      plan_preview: {
        name: "产品视频", goal: "介绍产品", target_count: 1, language: "zh-CN",
        aspect_ratio: "9:16", duration_target_seconds: 60, approved_clip_count: 4,
        approved_asset_count: 2, knowledge_references: [], source_references: [],
        material_matches: [], material_rule_summary: "已审核素材", model_snapshot: [],
        confirmation_points: [], pronunciation_references: [],
        selected_voice: { profile_id: "voice-1" }, confirmable: true,
        preflight_fingerprint: "a".repeat(64),
        blockers: [{ reason: "voice_model_unavailable", stage: "narration", impact: "当前不能配音。", repair_action: "请先安装声音模型。", blocks_confirmation: false }],
      },
    });
    render(<HomeCommandComposer />);
    fireEvent.change(screen.getByRole("textbox", { name: "制作指令" }), { target: { value: "介绍产品" } });
    await waitFor(() => expect(screen.getByRole("button", { name: "发送" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    expect(await screen.findByText("声音待处理")).toBeInTheDocument();
    expect(screen.getByText("当前不能配音。")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "创建任务" })).toBeEnabled();
    expect(mocks.confirm).not.toHaveBeenCalled();
  });

  it("shows setup instead of an active assistant when no text model is connected", async () => {
    mocks.settings.mockResolvedValue({ status: { llm: { configured: false, provider: "minimax", model: "MiniMax-M3" } } });
    render(<HomeCommandComposer />);
    fireEvent.change(screen.getByRole("textbox", { name: "制作指令" }), { target: { value: "介绍产品" } });
    expect(screen.getByRole("button", { name: "发送" })).toBeDisabled();
    expect(await screen.findByRole("link", { name: "去设置" })).toHaveAttribute("href", "/settings");
    expect(mocks.ask).not.toHaveBeenCalled();
  });

  it("focuses the conversation when opened from a new-video entry", async () => {
    configuredModel();
    render(<HomeCommandComposer autoFocus />);
    expect(screen.getByRole("textbox", { name: "制作指令" })).toHaveFocus();
  });

  it("keeps an existing task in the chat, previews a change, confirms and undoes it", async () => {
    configuredModel();
    const task = { id: "task-1", stage: "montage", name: "产品介绍" } as CreationTask;
    const onTaskUpdated = vi.fn();
    mocks.editAsk.mockResolvedValue({
      reply: "会调亮这一段。", provider: "minimax", model: "MiniMax-M3",
      edit_preview: { proposal: {
        id: "proposal-1", summary: "调亮画面", invalidated_refs: [], external_effects: [],
        preview: { before: { label: "原画面", detail: "偏暗" }, after: { label: "新画面", detail: "更亮" } },
      } },
    });
    mocks.confirmEdit.mockResolvedValue({});
    mocks.undoEdit.mockResolvedValue({});
    render(<HomeCommandComposer onTaskUpdated={onTaskUpdated} task={task} />);

    fireEvent.change(screen.getByRole("textbox", { name: "制作指令" }), { target: { value: "把画面调亮" } });
    await waitFor(() => expect(screen.getByRole("button", { name: "发送" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    await waitFor(() => expect(mocks.editAsk).toHaveBeenCalledWith("task-1", {
      message: "把画面调亮", context_stage: "montage", selected_object_refs: [], playhead_ms: null,
    }));
    expect(await screen.findByRole("dialog", { name: "修改预览" })).toHaveTextContent("调亮画面");
    expect(mocks.confirmEdit).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "确认修改" }));
    await waitFor(() => expect(mocks.confirmEdit).toHaveBeenCalledWith("task-1", "proposal-1"));
    expect(onTaskUpdated).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "撤销修改" }));
    await waitFor(() => expect(mocks.undoEdit).toHaveBeenCalledWith("task-1"));
    expect(onTaskUpdated).toHaveBeenCalledTimes(2);
    expect(mocks.ask).not.toHaveBeenCalled();
  });

  it("keeps a queued person proposal pending without enabling an uncommitted undo", async () => {
    configuredModel();
    const task = { id: "task-1", stage: "montage", name: "产品介绍" } as CreationTask;
    const onTaskUpdated = vi.fn();
    mocks.editAsk.mockResolvedValue({
      reply: "人物修改待确认。", provider: "minimax", model: "MiniMax-M3",
      edit_preview: { proposal: {
        id: "proposal-person", summary: "重做所选人物口型", invalidated_refs: [], external_effects: [],
        preview: { before: { label: "原镜头", detail: "当前版本" }, after: { label: "人物口型", detail: "待生成" } },
      } },
    });
    mocks.confirmEdit.mockResolvedValue({ status: "queued", job_id: "person-job", request_id: "device-request" });
    render(<HomeCommandComposer onTaskUpdated={onTaskUpdated} task={task} />);
    fireEvent.change(screen.getByRole("textbox", { name: "制作指令" }), { target: { value: "这里只重做口型，其他都不要动" } });
    await waitFor(() => expect(screen.getByRole("button", { name: "发送" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    await screen.findByRole("dialog", { name: "修改预览" });
    fireEvent.click(screen.getByRole("button", { name: "确认修改" }));
    expect(await screen.findByText("人物修改已排队，等待设备许可和实际完成；当前版本保持不变，请查看任务作业进度。")).toBeInTheDocument();
    expect(mocks.confirmEdit).toHaveBeenCalledWith("task-1", "proposal-person");
    expect(onTaskUpdated).toHaveBeenCalledOnce();
    expect(screen.queryByText("修改已完成。")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "撤销修改" })).not.toBeInTheDocument();
    expect(mocks.undoEdit).not.toHaveBeenCalled();
  });

  it("does not show an old task reply after switching tasks", async () => {
    configuredModel();
    let finish: (value: unknown) => void = () => undefined;
    mocks.editAsk.mockReturnValue(new Promise((resolve) => { finish = resolve; }));
    const first = { id: "task-1", stage: "montage", name: "第一条" } as CreationTask;
    const second = { id: "task-2", stage: "montage", name: "第二条" } as CreationTask;
    const view = render(<HomeCommandComposer task={first} />);
    fireEvent.change(screen.getByRole("textbox", { name: "制作指令" }), { target: { value: "调亮" } });
    await waitFor(() => expect(screen.getByRole("button", { name: "发送" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    await waitFor(() => expect(mocks.editAsk).toHaveBeenCalledTimes(1));
    view.rerender(<HomeCommandComposer task={second} />);
    finish({ reply: "第一条已处理", provider: "minimax", model: "MiniMax-M3", edit_preview: null });
    await waitFor(() => expect(screen.getByRole("button", { name: "发送" })).toBeDisabled());
    expect(screen.queryByText("第一条已处理")).not.toBeInTheDocument();
    expect(screen.queryByRole("dialog", { name: "修改预览" })).not.toBeInTheDocument();
  });
});
