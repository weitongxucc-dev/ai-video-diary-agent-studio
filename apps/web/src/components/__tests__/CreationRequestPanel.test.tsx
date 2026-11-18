import userEvent from "@testing-library/user-event";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiRequestError } from "@/lib/api/transport";
import type { CreationPlanPreview, CreationRequest, CreationTask, MaterialAsset } from "@/lib/types";
import { CreationRequestPanel } from "../CreationRequestPanel";

const voiceApi = vi.hoisted(() => ({ list: vi.fn() }));
vi.mock("@/lib/media-profiles-api", () => ({ listVoiceProfiles: voiceApi.list, mediaProfilesErrorMessage: (cause: unknown) => cause instanceof Error ? cause.message : "声音读取失败" }));
const api = vi.hoisted(() => ({ get: vi.fn(), items: vi.fn(), update: vi.fn(), prepare: vi.fn(), confirm: vi.fn(), replan: vi.fn() }));
vi.mock("@/lib/creation-request-api", () => ({ getCreationRequest: api.get, getCreationRequestItems: api.items, updateCreationRequest: api.update, prepareCreationRequestWindow: api.prepare, confirmCreationRequestWindow: api.confirm, replanCreationRequestWindow: api.replan }));

const materialApi = vi.hoisted(() => ({ list: vi.fn(), read: vi.fn(), confirm: vi.fn() }));
vi.mock("@/lib/api/materials", async importOriginal => ({
  ...await importOriginal<typeof import("@/lib/api/materials")>(),
  getProductionMaterials: materialApi.list, getProductionMaterial: materialApi.read,
  confirmMaterialSegmentUsage: materialApi.confirm,
}));
vi.mock("../DeviceProductionStatus", () => ({ DeviceProductionStatus: () => <p>本机制作条件</p> }));

function header(changes: Partial<CreationRequest> = {}): CreationRequest {
  return { id: "request-a", goal: "制作一万条设备使用短片", target_count: 10000, output_relation: null,
    requirements: { goal: "设备使用", target_count: 1, language: "zh-CN", aspect_ratio: "9:16", duration_target_seconds: 120 },
    material_scope: { kind: "workspace_library" }, clarification_questions: [], revision: 4, status: "awaiting_clarification",
    expanded_count: 0, confirmed_count: 0, task_status_counts: {}, current_window: null, ...changes };
}
function plan(goal: string): CreationPlanPreview {
  return { ...header().requirements, name: goal, goal, knowledge_references: [], source_references: [], selected_voice: null,
    pronunciation_references: [], approved_asset_count: 0, approved_clip_count: 0, material_rule_summary: "本地素材",
    material_matches: [], model_snapshot: [], confirmation_points: [], blockers: [], confirmable: true,
    preflight_fingerprint: `facts:${goal}` };
}
function proposed(): CreationRequest {
  return header({ output_relation: "different_topics", status: "awaiting_confirmation", revision: 6, expanded_count: 2,
    current_window: { id: "window-a", request_id: "request-a", ordinal_start: 1, status: "proposed", input_fingerprint: "bound-window-facts",
      created_at: "2026-10-11", confirmed_at: null, entries: ["键盘操作", "屏幕设置"].map((goal, index) => ({ ordinal: index + 1,
        plan_id: `plan-${index}`, task_id: `reserved-${index}`, plan: plan(goal), task: { id: `reserved-${index}`, name: goal } as CreationTask })) } });
}
beforeEach(() => { vi.resetAllMocks(); voiceApi.list.mockResolvedValue({ items: [], total_count: 0 }); api.items.mockResolvedValue({ items: [], next_after_ordinal: null, has_more: false }); });

describe("persistent creation request panel", () => {
  it("rechecks a stale proposed group without losing already created tasks", async () => {
    const stale = proposed(); stale.confirmed_count = 8; stale.expanded_count = 10;
    api.get.mockResolvedValue(stale);
    const updated = structuredClone(stale); updated.revision++; updated.current_window!.id = "replacement-window";
    api.replan.mockResolvedValue(updated);
    render(<CreationRequestPanel requestId="request-a" scopeKey="member-a" isCurrentScope={() => true} />);
    fireEvent.click(await screen.findByRole("button", { name: "重新核对本组方案" }));
    await waitFor(() => expect(api.replan).toHaveBeenCalledWith("request-a", "window-a", 6));
    expect(screen.getByText("目标 10,000 条视频 · 已建 8 条任务")).toBeInTheDocument();
    expect(api.confirm).not.toHaveBeenCalled();
  });

  it("checks a clip with zero candidates, rejects stale versions and rechecks the group without leaving the work", async () => {
    const initial = proposed();
    const rechecked = structuredClone(initial);
    rechecked.revision = initial.revision + 1;
    rechecked.current_window!.id = "window-refreshed";
    for (const entry of rechecked.current_window!.entries) entry.plan!.approved_clip_count = 1;

    const asset = {
      id: "video", media_type: "video", file_name: "conveyor.webm", updated_at: "2026-11-18T01:00:00Z",
      review_status: "raw", usage_role: "unassigned", analysis_status: "ready", use_state: "available",
      segments: [{ id: "clip-2", start_seconds: 4.25, end_seconds: 7.75, description: "传送带",
        review_status: "raw", usage_role: "unassigned", updated_at: "2026-11-18T01:00:01Z" }],
    } as MaterialAsset;
    const latest = { ...asset, updated_at: "2026-11-18T01:00:15Z",
      segments: [{ ...asset.segments[0], description: "同事刚保存的说明", updated_at: "2026-11-18T01:00:15Z" }] };
    const saved: MaterialAsset = { ...latest, segments: [{ ...latest.segments[0], usage_role: "b_roll" }] };
    api.get.mockResolvedValue(initial);
    api.replan.mockResolvedValue(rechecked);
    materialApi.list.mockResolvedValue({ assets: [asset], total_count: 1, limit: 12, offset: 0, has_more: false });
    materialApi.read.mockResolvedValue(latest);
    materialApi.confirm.mockRejectedValueOnce(new ApiRequestError(409, "素材版本已变化"))
      .mockResolvedValueOnce(saved);

    const user = userEvent.setup();
    render(<CreationRequestPanel requestId="request-a" scopeKey="member-a" isCurrentScope={() => true} />);
    await user.click(await screen.findByRole("button", { name: "核实镜头与用途" }));
    expect(screen.getAllByText("本机制作条件")).toHaveLength(1);
    await user.click(await screen.findByRole("button", { name: /conveyor\.webm/ }));
    const player = screen.getByLabelText("原片段预览") as HTMLVideoElement;
    fireEvent.loadedMetadata(player);
    expect(player.currentTime).toBe(4.25);
    await user.click(screen.getByRole("radio", { name: "补充画面" }));
    await user.click(screen.getByRole("button", { name: "保存片段用途" }));
    await screen.findByText("素材版本已变化");
    expect(screen.getByRole("button", { name: "保存片段用途" })).toBeDisabled();
    expect(materialApi.confirm).toHaveBeenCalledWith("video", "clip-2", {
      usage_role: "b_roll",
      expected_asset_updated_at: asset.updated_at,
      expected_segment_updated_at: asset.segments[0].updated_at,
    });
    await user.click(screen.getByRole("button", { name: "重新读取片段" }));
    await screen.findByText(/同事刚保存的说明/);
    await user.click(screen.getByRole("radio", { name: "补充画面" }));
    await user.click(screen.getByRole("button", { name: "保存片段用途" }));
    await screen.findByText(/已保存片段用途：补充画面/);
    expect(materialApi.confirm).toHaveBeenLastCalledWith("video", "clip-2", {
      usage_role: "b_roll",
      expected_asset_updated_at: latest.updated_at,
      expected_segment_updated_at: latest.segments[0].updated_at,
    });

    await user.keyboard("{Escape}");
    expect(screen.getByRole("button", { name: "确认创建本组任务" })).toBeDisabled();
    expect(api.replan).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "重新核对本组方案" }));
    expect(await screen.findAllByText(/1 个补充镜头/)).toHaveLength(2);
    expect(api.replan).toHaveBeenCalledWith("request-a", "window-a", 6);
    expect(api.confirm).not.toHaveBeenCalled();
  });

  it("clarifies the relation, prepares distinct bounded goals and only links actual confirmed tasks", async () => {
    api.get.mockResolvedValue(header());
    api.update.mockResolvedValue(header({ revision: 5, output_relation: "different_topics", status: "awaiting_plan" }));
    api.prepare.mockResolvedValue(proposed());
    const confirmed = proposed(); confirmed.revision = 7; confirmed.status = "active"; confirmed.confirmed_count = 2;
    confirmed.current_window!.status = "confirmed";
    const actual = confirmed.current_window!.entries.map((entry, index) => ({ ...entry,
      task: { id: `actual-${index}`, name: entry.plan!.name, stage: "topic", stage_status: "waiting_for_user" } as CreationTask }));
    api.confirm.mockResolvedValue(confirmed);
    api.items.mockImplementation(async () => ({ items: api.confirm.mock.calls.length ? actual : proposed().current_window!.entries.map(entry => ({ ...entry, task: null, plan: null })), next_after_ordinal: null, has_more: false }));
    render(<CreationRequestPanel requestId="request-a" scopeKey="member-a:conversation-a" isCurrentScope={() => true} />);
    expect(await screen.findByText("目标 10,000 条视频 · 已建 0 条任务")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /^第 \d+ 条/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "不同主题" }));
    await waitFor(() => expect(api.update).toHaveBeenCalledWith("request-a", { expected_revision: 4, output_relation: "different_topics" }));
    const goals = await screen.findByRole("textbox", { name: "每行写一条视频的具体目标" });
    fireEvent.change(goals, { target: { value: Array.from({ length: 9 }, (_, i) => `设备主题 ${i}`).join("\n") } });
    expect(screen.getByRole("button", { name: "准备本组方案" })).toBeDisabled();
    fireEvent.change(goals, { target: { value: "同一目标\n同一目标" } });
    expect(screen.getByRole("button", { name: "准备本组方案" })).toBeDisabled();
    fireEvent.change(goals, { target: { value: "键盘操作\n屏幕设置" } });
    fireEvent.click(screen.getByRole("button", { name: "准备本组方案" }));
    await waitFor(() => expect(api.prepare).toHaveBeenCalledWith("request-a", { expected_revision: 5, goals: [{ goal: "键盘操作" }, { goal: "屏幕设置" }] }));
    expect(await screen.findByRole("region", { name: "本组制作方案" })).toBeInTheDocument();
    expect(screen.getAllByLabelText("可用内容检查结果")).toHaveLength(2);
    expect(screen.queryByRole("link", { name: /^第 \d+ 条/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "确认创建本组任务" }));
    await waitFor(() => expect(api.confirm).toHaveBeenCalledWith("request-a", "window-a", { expected_revision: 6, input_fingerprint: "bound-window-facts" }));
    expect(await screen.findByText("目标 10,000 条视频 · 已建 2 条任务")).toBeInTheDocument();
    expect(await screen.findByRole("link", { name: "第 1 条 · 键盘操作" })).toHaveAttribute("href", "/tasks/actual-0");
    expect(screen.getByText("不同主题")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "系列分集" })).not.toBeInTheDocument();
    expect(screen.getAllByText("选题待确认")).toHaveLength(2);
    expect(screen.queryByText("reserved-0")).not.toBeInTheDocument();
  });

  it("prepares two language deliveries for the same product brief", async () => {
    const request = header({ target_count: 2, output_relation: "variants", status: "awaiting_plan" });
    api.get.mockResolvedValue(request);
    api.prepare.mockResolvedValue(proposed());
    render(<CreationRequestPanel requestId={request.id} scopeKey="member" isCurrentScope={() => true} />);
    fireEvent.click(await screen.findByRole("button", { name: "准备中文版和英文版" }));
    await waitFor(() => expect(api.prepare).toHaveBeenCalledWith(request.id, {
      expected_revision: request.revision,
      goals: [
        { goal: request.goal, name: expect.stringContaining("中文版"), requirements: { language: "zh-CN" } },
        { goal: request.goal, name: expect.stringContaining("英文版"), requirements: { language: "en-US" } },
      ],
    }));
    expect(api.confirm).not.toHaveBeenCalled();
  });

  it("binds each delivery to its selected voice version and language", async () => {
    const request = header({ target_count: 2, output_relation: "variants", status: "awaiting_plan" });
    api.get.mockResolvedValue(request);
    api.prepare.mockResolvedValue(proposed());
    voiceApi.list.mockResolvedValue({ items: [
      { id: "studio-zh", version: "v2", display_name: "中文讲解", language: "zh", runnable: true },
      { id: "studio-en", version: "v3", display_name: "English narrator", language: "en", runnable: true },
    ] });
    const user = userEvent.setup();
    render(<CreationRequestPanel requestId={request.id} scopeKey="member" isCurrentScope={() => true} />);
    await user.click(await screen.findByText("分别指定中英文声音"));
    await user.click(await screen.findByRole("combobox", { name: "中文版声音" }));
    expect(screen.queryByRole("option", { name: "English narrator · v3" })).toBeNull();
    await user.click(await screen.findByRole("option", { name: "中文讲解 · v2" }));
    await user.click(screen.getByRole("combobox", { name: "英文版声音" }));
    await user.click(await screen.findByRole("option", { name: "English narrator · v3" }));
    await user.click(screen.getByRole("button", { name: "准备中文版和英文版" }));
    expect(api.prepare).toHaveBeenCalledWith(request.id, { expected_revision: request.revision, goals: [
      expect.objectContaining({ requirements: { language: "zh-CN", selected_voice: { profile_id: "studio-zh", version: "v2" } } }),
      expect.objectContaining({ requirements: { language: "en-US", selected_voice: { profile_id: "studio-en", version: "v3" } } }),
    ] });
  });

  it("uses the exact single goal without another relationship or per-item question", async () => {
    const single = header({ goal: "做一条键盘演示", target_count: 1, output_relation: "single", status: "awaiting_plan" });
    api.get.mockResolvedValue(single); api.prepare.mockResolvedValue(single);
    render(<CreationRequestPanel requestId="request-a" scopeKey="conversation-a" isCurrentScope={() => true} />);
    const prepare = await screen.findByRole("button", { name: "准备本组方案" });
    await waitFor(() => expect(prepare).toBeEnabled());
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(screen.queryByText("这些视频的关系")).not.toBeInTheDocument();
    fireEvent.click(prepare);
    await waitFor(() => expect(api.prepare).toHaveBeenCalledWith("request-a", { expected_revision: 4, goals: [{ goal: single.goal }] }));
  });

  it("keeps unverified today-received scope blocked until the user explicitly chooses available library material", async () => {
    api.get.mockResolvedValue(header({ output_relation: "series", material_scope: { kind: "today_received" }, clarification_questions: ["今天接收的素材范围尚未确认"] }));
    api.update.mockResolvedValue(header({ revision: 5, output_relation: "series", status: "awaiting_plan" }));
    render(<CreationRequestPanel requestId="request-a" scopeKey="conversation-a" isCurrentScope={() => true} />);
    expect(await screen.findByText("今天接收的素材范围尚未确认")).toBeInTheDocument();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(api.update).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.getByRole("button", { name: "使用当前素材库" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "使用当前素材库" }));
    await waitFor(() => expect(api.update).toHaveBeenCalledWith("request-a", { expected_revision: 4, output_relation: "series", material_scope: "workspace_library" }));
    expect(await screen.findByRole("textbox")).toBeInTheDocument();
  });

  it("ignores a late read and confirmation after the member/conversation scope changes", async () => {
    let resolveRead!: (value: CreationRequest) => void;
    api.get.mockReturnValueOnce(new Promise(resolve => { resolveRead = resolve; })).mockResolvedValue(header({ id: "request-b", goal: "当前成员目标" }));
    const view = render(<CreationRequestPanel requestId="request-a" scopeKey="member-a:conversation-a" isCurrentScope={() => true} />);
    view.rerender(<CreationRequestPanel requestId="request-b" scopeKey="member-b:conversation-b" isCurrentScope={() => true} />);
    expect(await screen.findByText("当前成员目标")).toBeInTheDocument();
    await act(async () => resolveRead(header({ goal: "不该显示的旧目标" })));
    expect(screen.queryByText("不该显示的旧目标")).not.toBeInTheDocument();
    api.get.mockResolvedValueOnce(proposed());
    view.rerender(<CreationRequestPanel requestId="request-a" scopeKey="member-a:conversation-c" isCurrentScope={() => true} />);
    const confirm = await screen.findByRole("button", { name: "确认创建本组任务" });
    await waitFor(() => expect(confirm).toBeEnabled());
    let resolveConfirm!: (value: CreationRequest) => void;
    api.confirm.mockReturnValue(new Promise(resolve => { resolveConfirm = resolve; }));
    fireEvent.click(confirm);
    view.rerender(<CreationRequestPanel requestId="request-b" scopeKey="member-b:conversation-d" isCurrentScope={() => true} />);
    expect(await screen.findByText("当前成员目标")).toBeInTheDocument();
    const calls = api.items.mock.calls.length;
    await act(async () => resolveConfirm(header({ goal: "旧范围确认结果", confirmed_count: 2 })));
    expect(screen.queryByText("旧范围确认结果")).not.toBeInTheDocument();
    expect(api.items).toHaveBeenCalledTimes(calls);
  });

  it("does not turn a conflict into created tasks and reads a newer revision before retry", async () => {
    api.get.mockResolvedValue(proposed()); api.confirm.mockRejectedValue(new Error("创作目标已变化，请重新查看"));
    render(<CreationRequestPanel requestId="request-a" scopeKey="conversation-a" isCurrentScope={() => true} />);
    const confirm = await screen.findByRole("button", { name: "确认创建本组任务" });
    await waitFor(() => expect(confirm).toBeEnabled()); fireEvent.click(confirm);
    expect(await screen.findByRole("alert")).toHaveTextContent("创作目标已变化");
    expect(screen.queryByRole("link", { name: /^第 \d+ 条/ })).not.toBeInTheDocument();
    expect(screen.getByText("目标 10,000 条视频 · 已建 0 条任务")).toBeInTheDocument();
    api.get.mockResolvedValue({ ...proposed(), revision: 8 });
    fireEvent.click(screen.getByRole("button", { name: "刷新当前状态" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "确认创建本组任务" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "确认创建本组任务" }));
    await waitFor(() => expect(api.confirm).toHaveBeenLastCalledWith("request-a", "window-a", { expected_revision: 8, input_fingerprint: "bound-window-facts" }));
  });

  it("replaces bounded pages and reads aggregate task status from the current header", async () => {
    api.get.mockResolvedValue(header({ output_relation: "series", status: "active", expanded_count: 21, confirmed_count: 21, task_status_counts: { created: 20, failed: 1 } }));
    const entries = Array.from({ length: 21 }, (_, i) => ({ ordinal: i + 1, plan_id: `plan-${i}`, task_id: `task-${i}`, plan: null,
      task: { id: `task-${i}`, name: `设备技巧 ${i + 1}`, stage: "topic", stage_status: "not_started" } as CreationTask }));
    api.items.mockResolvedValueOnce({ items: entries.slice(0, 20), next_after_ordinal: 20, has_more: true })
      .mockResolvedValueOnce({ items: entries.slice(20), next_after_ordinal: 21, has_more: false });
    render(<CreationRequestPanel requestId="request-a" scopeKey="conversation-a" isCurrentScope={() => true} />);
    expect(await screen.findByText("待开始 20 条 · 需要处理 1 条")).toBeInTheDocument();
    await waitFor(() => expect(screen.getAllByRole("link", { name: /^第 \d+ 条/ })).toHaveLength(20));
    fireEvent.click(screen.getByRole("button", { name: "查看下一组任务" }));
    expect(await screen.findByRole("link", { name: "第 21 条 · 设备技巧 21" })).toBeInTheDocument();
    expect(api.items).toHaveBeenLastCalledWith("request-a", 20);
    expect(screen.getAllByRole("link", { name: /^第 \d+ 条/ })).toHaveLength(1);
    expect(screen.queryByRole("link", { name: "第 1 条 · 设备技巧 1" })).not.toBeInTheDocument();
  });

  it("keeps a blocked window unconfirmed and provides the existing material repair link", async () => {
    const blocked = proposed();
    blocked.current_window!.entries[0].plan!.confirmable = false;
    blocked.current_window!.entries[0].plan!.blockers = [{ reason: "approved_material_clips_missing", impact: "缺少符合当前主题的镜头", repair_action: "补充可用素材后重新检查", stage: "montage", blocks_confirmation: true }];
    api.get.mockResolvedValue(blocked);
    render(<CreationRequestPanel requestId="request-a" scopeKey="conversation-a" isCurrentScope={() => true} />);
    expect(await screen.findByRole("button", { name: "确认创建本组任务" })).toBeDisabled();
    expect(screen.getByText("缺少符合当前主题的镜头 补充可用素材后重新检查")).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: "前往素材库补充内容" })[0]).toHaveAttribute("href", "/library");
    expect(api.confirm).not.toHaveBeenCalled();
  });
});


const availableVoice = { id: "voice-existing", version: "v2", display_name: "我的个人声音", language: "zh", runnable: true };

async function choosePersonalVoice() {
  const user = userEvent.setup();
  const select = await screen.findByRole("combobox", { name: "声音版本" });
  await user.click(select);
  await user.click(await screen.findByRole("option", { name: "我的个人声音 · v2 · 中文" }));
}

it("selects an existing runnable voice for this request and rechecks exact frozen goals with the saved CAS revision", async () => {
  const before = proposed();
  const english = before.current_window!.entries[1].plan!;
  english.language = "en-US";
  english.aspect_ratio = "16:9";
  before.requirements = { ...before.requirements, presenter_share_target: 0,
    selected_knowledge: { knowledge_item_id: "facts-owned", version: "2" },
    template_id: "template-real", template_version: 3, selected_person: { profile_id: "person-real", version: "v1" } };
  api.get.mockResolvedValue(before);
  voiceApi.list.mockResolvedValue({ items: [availableVoice, { ...availableVoice, id: "not-runnable", display_name: "不可用声音", runnable: false }], total_count: 2 });
  const saved = { ...before, revision: 7, status: "awaiting_plan", current_window: null,
    requirements: { ...before.requirements, selected_voice: { profile_id: availableVoice.id, version: "v2" } } };
  api.update.mockResolvedValue(saved);
  api.prepare.mockResolvedValue({ ...saved, revision: 8, status: "awaiting_confirmation", current_window: before.current_window });
  const draftChanged = vi.fn();
  render(<CreationRequestPanel requestId="request-a" scopeKey="member-a" isCurrentScope={() => true}
    goalsDraft="尚未提交的新目标草稿" onGoalsDraftChange={draftChanged} />);
  await choosePersonalVoice();
  fireEvent.click(screen.getByRole("button", { name: "保存声音并重新核对" }));
  await waitFor(() => expect(api.prepare).toHaveBeenCalledWith("request-a", { expected_revision: 7,
    goals: [
      { goal: before.current_window!.entries[0].plan!.goal, name: before.current_window!.entries[0].plan!.name },
      { goal: english.goal, name: english.name, requirements: { language: "en-US", aspect_ratio: "16:9" } },
    ] }));
  expect(api.update).toHaveBeenCalledWith("request-a", { expected_revision: 6, output_relation: before.output_relation,
    requirements: { ...before.requirements, selected_voice: { profile_id: availableVoice.id, version: "v2" } } });
  expect(api.replan).not.toHaveBeenCalled();
  expect(api.confirm).not.toHaveBeenCalled();
  expect(draftChanged).not.toHaveBeenCalled();
});

it("does not recheck or replace request state when the voice PATCH is rejected", async () => {
  api.get.mockResolvedValue(proposed());
  voiceApi.list.mockResolvedValue({ items: [availableVoice], total_count: 1 });
  api.update.mockRejectedValue(new Error("制作要求已变化，请重新读取"));
  render(<CreationRequestPanel requestId="request-a" scopeKey="member-a" isCurrentScope={() => true} />);
  await choosePersonalVoice();
  fireEvent.click(screen.getByRole("button", { name: "保存声音并重新核对" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("制作要求已变化");
  expect(api.update).toHaveBeenCalledTimes(1);
  expect(api.prepare).not.toHaveBeenCalled();
  expect(api.confirm).not.toHaveBeenCalled();
});

it("reports a saved voice separately from failed planning and retains the exact group for an explicit retry", async () => {
  const before = proposed(); api.get.mockResolvedValue(before);
  voiceApi.list.mockResolvedValue({ items: [availableVoice], total_count: 1 });
  const saved = { ...before, revision: 7, status: "awaiting_plan", current_window: null,
    requirements: { ...before.requirements, selected_voice: { profile_id: availableVoice.id, version: "v2" } } };
  api.update.mockResolvedValue(saved); api.prepare.mockRejectedValueOnce(new Error("暂时无法核对资料"));
  render(<CreationRequestPanel requestId="request-a" scopeKey="member-a" isCurrentScope={() => true} />);
  await choosePersonalVoice(); fireEvent.click(screen.getByRole("button", { name: "保存声音并重新核对" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("声音已保存，本组方案尚未重新核对");
  expect(api.update).toHaveBeenCalledTimes(1); expect(api.prepare).toHaveBeenCalledTimes(1);
  api.prepare.mockResolvedValue({ ...saved, current_window: before.current_window });
  fireEvent.click(screen.getByRole("button", { name: "重新核对已保存声音的方案" }));
  await waitFor(() => expect(api.prepare).toHaveBeenCalledTimes(2));
  expect(api.prepare.mock.calls[1][1]).toEqual({ expected_revision: 7,
    goals: before.current_window!.entries.map(entry => ({ goal: entry.plan!.goal, name: entry.plan!.name })) });
  expect(api.update).toHaveBeenCalledTimes(1);
  expect(api.confirm).not.toHaveBeenCalled();
});

it("does not issue a prepare request or apply a late voice PATCH after the member/request scope changes", async () => {
  api.get.mockResolvedValue(proposed());
  voiceApi.list.mockResolvedValue({ items: [availableVoice], total_count: 1 });
  let finishPatch!: (value: CreationRequest) => void;
  api.update.mockReturnValue(new Promise(resolve => { finishPatch = resolve; }));
  const view = render(<CreationRequestPanel requestId="request-a" scopeKey="member-a" isCurrentScope={() => true} />);
  await choosePersonalVoice(); fireEvent.click(screen.getByRole("button", { name: "保存声音并重新核对" }));
  await waitFor(() => expect(api.update).toHaveBeenCalledTimes(1));
  api.get.mockResolvedValue(header({ id: "request-b", goal: "另一成员的目标" }));
  view.rerender(<CreationRequestPanel requestId="request-b" scopeKey="member-b" isCurrentScope={() => true} />);
  expect(await screen.findByText("另一成员的目标")).toBeInTheDocument();
  finishPatch({ ...proposed(), revision: 7, current_window: null });
  await waitFor(() => expect(api.prepare).not.toHaveBeenCalled());
  expect(screen.getByText("另一成员的目标")).toBeInTheDocument();
});

it("does not offer voice requirement mutations after any task was confirmed", async () => {
  api.get.mockResolvedValue({ ...proposed(), confirmed_count: 1 });
  render(<CreationRequestPanel requestId="request-a" scopeKey="member-a" isCurrentScope={() => true} />);
  expect(await screen.findByRole("region", { name: "本组制作方案" })).toBeInTheDocument();
  expect(screen.queryByRole("region", { name: "本请求声音" })).not.toBeInTheDocument();
  expect(voiceApi.list).not.toHaveBeenCalled();
});


it("lets the user retry a voice list failure without changing request requirements or choosing a voice automatically", async () => {
  api.get.mockResolvedValue(proposed());
  voiceApi.list.mockRejectedValueOnce(new Error("声音暂时读取失败"))
    .mockResolvedValueOnce({ items: [availableVoice], total_count: 1 });
  render(<CreationRequestPanel requestId="request-a" scopeKey="member-a" isCurrentScope={() => true} />);
  expect(await screen.findByRole("alert")).toHaveTextContent("声音暂时读取失败");
  fireEvent.click(screen.getByRole("button", { name: "重新读取声音" }));
  expect(await screen.findByRole("combobox", { name: "声音版本" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "保存声音并重新核对" })).toBeDisabled();
  expect(voiceApi.list).toHaveBeenCalledTimes(2);
  expect(api.update).not.toHaveBeenCalled(); expect(api.prepare).not.toHaveBeenCalled();
});
