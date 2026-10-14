import { chooseSelectValue, inspectSelectOptions } from "./select-interaction";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { MaterialAsset, MaterialIntakeStatus, MaterialSegment } from "@/lib/types";
import type { DesktopVoiceRecorderProps } from "@/components/DesktopVoiceRecorder";
const recorder = vi.hoisted(() => ({ props: null as DesktopVoiceRecorderProps | null }));
const assistantFixtures = vi.hoisted(() => ({
  conversations: new Map<string, import("@/lib/types").AssistantConversation>(),
  current: new Map<string, string>(),
  sequence: 0,
}));
vi.mock("@/components/DesktopVoiceRecorder", () => ({ DesktopVoiceRecorder: (props: DesktopVoiceRecorderProps) => {
  recorder.props = props;
  return <span>录音组件测试替身</span>;
} }));
vi.mock("@/hooks/useLocalMemberIdentity", () => ({ useLocalMemberIdentity: () => ({ token:"test-member", role:"owner", ready:true, error:null, refresh:vi.fn() }) }));
vi.mock("@/components/AppShell", () => ({ AppShell: ({ children, inspector, conversation }: { children: React.ReactNode; inspector: React.ReactNode; conversation: React.ReactNode }) => <>{children}{inspector}{conversation}</> }));
vi.mock("@/lib/api", () => ({
  getProductionMaterials: vi.fn(), getMaterialLibraryInsights: vi.fn(), getMaterialIntakeStatus: vi.fn(), getRuntimeSettings: vi.fn(), sendMaterialAssistantTurn: vi.fn(), bindMaterialIntake: vi.fn(), confirmMaterialCopyComplete: vi.fn(), scanMaterialIntake: vi.fn(), retryMaterialIntake: vi.fn(), updateMaterialUseState: vi.fn(), updateMaterialAssetTags: vi.fn(), updateProductionMaterialSegment: vi.fn(), updateMaterialNotes: vi.fn(), transcribeMaterialVoiceNote: vi.fn(), getMaterialBusinessObjects: vi.fn(), createMaterialBusinessObject: vi.fn(), getAssetBusinessObjects: vi.fn(), saveAssetBusinessObjects: vi.fn(), getAssetSegmentUsageRecords: vi.fn(),
  getCurrentAssistantConversation: vi.fn(async (scope: { scope_kind: "home" | "task" | "material"; scope_id: string }) => {
    const key = `${scope.scope_kind}:${scope.scope_id}`;
    const currentId = assistantFixtures.current.get(key);
    if (currentId) return assistantFixtures.conversations.get(currentId)!;
    const conversation = { id: `conversation-${key}`, workspace_id: "workspace", ...scope, title: "新对话", draft_text: "", draft_version: 0,
      created_at: "2026-10-09T00:00:00Z", updated_at: "2026-10-09T00:00:00Z", turns: [] };
    assistantFixtures.conversations.set(conversation.id, conversation);
    assistantFixtures.current.set(key, conversation.id);
    return conversation;
  }),
  createAssistantConversation: vi.fn(async (scope: { scope_kind: "home" | "task" | "material"; scope_id: string }) => {
    const key = `${scope.scope_kind}:${scope.scope_id}`;
    const id = `conversation-${key}-${++assistantFixtures.sequence}`;
    const conversation = { id, workspace_id: "workspace", ...scope, title: "新对话", draft_text: "", draft_version: 0,
      created_at: "2026-10-09T00:00:00Z", updated_at: "2026-10-09T00:00:00Z", turns: [] };
    assistantFixtures.conversations.set(id, conversation);
    assistantFixtures.current.set(key, id);
    return conversation;
  }),
  listAssistantConversations: vi.fn(async (scope: { scope_kind: string; scope_id: string }) => ({ items: [...assistantFixtures.conversations.values()].filter((item) => item.scope_kind === scope.scope_kind && item.scope_id === scope.scope_id) })),
  getAssistantConversationReference: vi.fn(async () => ({ selected_reference: null, reference_version: 0, status: "ready", reason: null })),
  saveAssistantConversationReference: vi.fn(async (_id, selected_reference, expectedReferenceVersion) => ({ selected_reference, reference_version: expectedReferenceVersion + 1, status: "ready", reason: null })),
  getAssistantConversation: vi.fn(async (id: string) => assistantFixtures.conversations.get(id)!),
  resumeAssistantConversation: vi.fn(async (id: string) => {
    const conversation = assistantFixtures.conversations.get(id)!;
    assistantFixtures.current.set(`${conversation.scope_kind}:${conversation.scope_id}`, id);
    return conversation;
  }),
  updateAssistantConversation: vi.fn(async (id: string, update: { title?: string; draft_text?: string; expected_draft_version?: number }) => {
    const conversation = assistantFixtures.conversations.get(id)!;
    if (update.draft_text !== undefined && update.expected_draft_version !== conversation.draft_version) throw new Error("draft CAS conflict");
    const { expected_draft_version: _expected, ...changes } = update;
    const updated = { ...conversation, ...changes, draft_version: conversation.draft_version + (update.draft_text !== undefined ? 1 : 0) };
    assistantFixtures.conversations.set(id, updated);
    return updated;
  }),
  restoreUnpersistedAssistantTurnDraft: vi.fn(async (id: string, input: { user_message: string; expected_draft_version: number }) => {
    const conversation = assistantFixtures.conversations.get(id)!;
    if (input.expected_draft_version !== conversation.draft_version) throw new Error("draft CAS conflict");
    const restored = { ...conversation, draft_text: input.user_message, draft_version: conversation.draft_version + 1 };
    assistantFixtures.conversations.set(id, restored);
    return { status: "restored", conversation: restored };
  }),
  getAssistantCapabilities: vi.fn().mockResolvedValue({ application_commands: ["/new", "/name <名称>", "/resume <会话ID>", "/help"], pi_tui_commands: { available: false, inventory_source: "静态命令清单", inventory_version: "1.1.0", runtime_version: null, runtime_status: "missing", runtime_reason: "未确认", runtime_check: "隔离检查", product_mode: "headless", reason: "未接通", commands: [] }, extension_commands: { available: false, reason: "未接通" }, skills: { available: false, reason: "未接通" }, prompt_templates: { available: false, reason: "未接通" }, business_tools: {} }),
}));
vi.mock("@/lib/directoryPicker", () => ({ importLocalMaterials: vi.fn(), hasMaterialImporter: () => true }));
vi.mock("@/lib/media-link-api", () => ({
  checkMediaLink: vi.fn(),
  importMediaLink: vi.fn(),
  mediaLinkErrorMessage: (cause: unknown) => (cause instanceof Error ? cause.message : "链接导入失败，请重试。"),
}));
import { getProductionMaterials, getMaterialLibraryInsights, getMaterialIntakeStatus, getRuntimeSettings, sendMaterialAssistantTurn, bindMaterialIntake, confirmMaterialCopyComplete, scanMaterialIntake, retryMaterialIntake, updateMaterialUseState, updateMaterialAssetTags, updateProductionMaterialSegment, updateMaterialNotes, transcribeMaterialVoiceNote, getMaterialBusinessObjects, createMaterialBusinessObject, getAssetBusinessObjects, saveAssetBusinessObjects, getAssetSegmentUsageRecords, getCurrentAssistantConversation, listAssistantConversations, getAssistantCapabilities } from "@/lib/api";
import { importLocalMaterials } from "@/lib/directoryPicker";
import { checkMediaLink, importMediaLink } from "@/lib/media-link-api";
import { MaterialLibraryWorkspace } from "@/components/MaterialLibraryWorkspace";
import { AssistantConversationProvider, useAssistantMaterialReference } from "@/components/AssistantConversationProvider";
const asset: MaterialAsset & { user_tags: string[] } = { id:"asset", workspace_id:"workspace", visibility:"private", file_name:"local.mp4", path:"/local.mp4", size_bytes:100, user_tags:[], user_description:"", upload_context:"",
  modified_at:"2026-09-20", media_type:"video", source_url:null, duration_seconds:4, usage_role:"b_roll", suggested_usage_role:"b_roll", usage_role_confidence:0,
  usage_role_reason:"", review_status:"approved", use_state:"available", analysis_status:"ready", analysis_message:"", segments:[], created_at:"2026-09-20", updated_at:"2026-09-20" };
const library={ assets:[asset], total_count:1, limit:24, offset:0, has_more:false };
const insights={ asset_count:1, segment_count:0, tags:[], recommended_gaps:[] };
const segment: MaterialSegment = { id:"segment", asset_id:"asset", start_seconds:1, end_seconds:3, description:"画面说明", transcript:"", visual_status:"ready", visual_model_id:"", visual_sample_count:0, usage_role:"b_roll", suggested_usage_role:"b_roll", usage_role_confidence:0, usage_role_reason:"", review_status:"approved", system_tags:[], user_tags:["旧标签"], collection_ids:[], usage_count:0, last_used_at:null, created_at:"2026-09-20", updated_at:"2026-09-20" };
const taggedAsset = { ...asset, segments: [segment] };
const taggedLibrary = { ...library, assets: [taggedAsset] };
const otherSegment = { ...segment, id:"segment-b", asset_id:"asset-b", user_tags:[] };
const otherAsset = { ...taggedAsset, id:"asset-b", file_name:"自造素材B.mp4", segments:[otherSegment] };
const twoAssetLibrary = { ...library, assets:[taggedAsset, otherAsset], total_count:2 };
const intakeReady: MaterialIntakeStatus = {
  bound_to_current_workspace: false, binding_available: true, configured_directory: "/Volumes/Test/media",
  bound_directory: null, directory_state: "ready", directory_message: "素材目录可访问；文件稳定后会进入分析。",
  repair_action: null, scan_interval_seconds: 60, pending_files: [],
  file_counts: { observing: 0, running: 0, ready: 0, failed: 0, blocked_disk: 0 },
};
beforeEach(() => {
  vi.clearAllMocks();
  assistantFixtures.conversations.clear();
  assistantFixtures.current.clear();
  assistantFixtures.sequence = 0;
  vi.mocked(transcribeMaterialVoiceNote).mockReset();
  recorder.props = null;
  vi.mocked(getProductionMaterials).mockResolvedValue(library);
  vi.mocked(getMaterialLibraryInsights).mockResolvedValue(insights);
  vi.mocked(getMaterialIntakeStatus).mockResolvedValue(intakeReady);
  vi.mocked(getRuntimeSettings).mockResolvedValue({ status: { llm: { configured: true, provider: "test", model: "test" } } } as Awaited<ReturnType<typeof getRuntimeSettings>>);
  vi.mocked(sendMaterialAssistantTurn).mockResolvedValue({ reply: "已读取。", provider: "test", model: "test", material_result: null });
  vi.mocked(getCurrentAssistantConversation).mockClear();
  vi.mocked(listAssistantConversations).mockClear();
  vi.mocked(getAssistantCapabilities).mockClear();
  vi.mocked(getMaterialBusinessObjects).mockResolvedValue({ items: [], total_count: 0 });
  vi.mocked(getAssetBusinessObjects).mockResolvedValue({ items: [], total_count: 0 });
  vi.mocked(getAssetSegmentUsageRecords).mockResolvedValue({ items: [], total_count: 0 });
});
it("switches the material collection between cover grid and object rows", async () => {
  render(<MaterialLibraryWorkspace initialLibrary={library} initialInsights={insights} initialCollections={{collections:[],total_count:0}} />);
  const collection = screen.getByRole("group", { name:"列表显示方式" });
  const results = document.querySelector(".library-grid") as HTMLElement;
  expect(within(collection).getByRole("button", { name:"封面网格" })).toHaveAttribute("aria-pressed", "true");
  fireEvent.click(within(collection).getByRole("button", { name:"对象列表" }));
  expect(results).toHaveClass("is-list");
  expect(within(collection).getByRole("button", { name:"对象列表" })).toHaveAttribute("aria-pressed", "true");
});

it("shows pending analysis and unknown duration without inventing a queue or manual execution action", async () => {
  const waiting = { ...asset, analysis_status:"pending" as const, analysis_message:"复制完成确认已保存，正在分析；确认不代表文件通过校验。", duration_seconds:null, usage_role:"unassigned" as const, suggested_usage_role:"a_roll" as const, review_status:"raw" as const };
  const waitingLibrary = { ...library, assets:[waiting] };
  vi.mocked(getProductionMaterials).mockResolvedValue(waitingLibrary);
  render(<MaterialLibraryWorkspace initialLibrary={waitingLibrary} initialInsights={insights} initialCollections={{collections:[],total_count:0}} />);
  const card = screen.getByRole("button", { name:/local\.mp4/ });
  expect(within(card).getByText(/等待分析/)).toBeInTheDocument();
  expect(within(card).getByText("时长未知")).toBeInTheDocument();
  expect(within(card).getByText(/建议：人物主镜头/)).toBeInTheDocument();
  fireEvent.click(card);
  expect(screen.getByText("等待分析，完成时间暂未知。")).toBeInTheDocument();
  expect(screen.getByText(waiting.analysis_message)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name:/开始分析|重新分析|重试分析/ })).not.toBeInTheDocument();
  expect(screen.queryByText(/队列中|预计.*秒/)).not.toBeInTheDocument();
});

it("folds a pending backend diagnostic while keeping its original text", async () => {
  const waiting = { ...asset, analysis_status:"pending" as const, analysis_message:"Error: source probe\n/Volumes/Test/source.mp4", duration_seconds:null };
  const waitingLibrary = { ...library, assets:[waiting] };
  render(<MaterialLibraryWorkspace initialLibrary={waitingLibrary} initialInsights={insights} initialCollections={{collections:[],total_count:0}} />);
  fireEvent.click(screen.getByRole("button", { name:/local\.mp4/ }));
  const diagnostic = screen.getByText("后台分析记录").closest("details")!;
  expect(diagnostic).not.toHaveAttribute("open");
  expect(diagnostic.textContent).toContain(waiting.analysis_message);
  fireEvent.click(screen.getByText("后台分析记录"));
  expect(screen.getByText("后台分析记录")).toBeInTheDocument();
});

it("keeps the reviewed role label when the saved role differs from a suggestion", async () => {
  const reviewed = { ...asset, usage_role:"b_roll" as const, suggested_usage_role:"a_roll" as const };
  render(<MaterialLibraryWorkspace initialLibrary={{ ...library, assets:[reviewed] }} initialInsights={insights} initialCollections={{collections:[],total_count:0}} />);
  const card = screen.getByRole("button", { name:/local\.mp4/ });
  expect(within(card).getByText(/补充画面/)).toBeInTheDocument();
  expect(within(card).queryByText(/建议：/)).not.toBeInTheDocument();
});

it("does not bind the automatic first material, then binds only explicit asset and segment picks", async () => {
  vi.mocked(getProductionMaterials).mockResolvedValue(taggedLibrary);
  render(<MaterialLibraryWorkspace initialLibrary={taggedLibrary} initialInsights={insights} initialCollections={{collections:[],total_count:0}} />);
  expect(screen.queryByLabelText("当前素材对话范围"))
    .not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: /local\.mp4/ }));
  expect(screen.getByText(/当前范围：整条素材 · local\.mp4/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: /0:01.*0:03/ }));
  expect(screen.getByText(/当前范围：local\.mp4 · 片段 0:01–0:03/)).toBeInTheDocument();
});

it("shows asset labels and applies assistant readback only to the exact selected segment", async () => {
  vi.mocked(getProductionMaterials).mockResolvedValue(twoAssetLibrary);
  let resolveTurn!: (value: Awaited<ReturnType<typeof sendMaterialAssistantTurn>>) => void;
  vi.mocked(sendMaterialAssistantTurn).mockImplementationOnce((message, conversationId, _selection, _requestId, expectedVersion, onEvent) => {
    const conversation = assistantFixtures.conversations.get(conversationId)!;
    if (conversation.draft_version !== expectedVersion || conversation.draft_text !== message) throw new Error("draft CAS conflict");
    const turnId = `material-turn-${++assistantFixtures.sequence}`;
    const startedAt = "2026-10-09T00:00:01Z";
    const turn: import("@/lib/types").AssistantConversationTurn = {
      id: turnId, sequence: assistantFixtures.sequence, user_message: message, assistant_text: "", status: "running",
      events: [], result: null, created_at: startedAt, updated_at: startedAt,
    };
    assistantFixtures.conversations.set(conversationId, {
      ...conversation, draft_text: "", draft_version: expectedVersion + 1, turns: [...conversation.turns, turn], updated_at: startedAt,
    });
    onEvent({ type: "turn_started", turn_id: turnId, turn_state: "running", draft_version: expectedVersion + 1 });
    return new Promise((resolve) => {
      resolveTurn = (value) => {
        const latest = assistantFixtures.conversations.get(conversationId)!;
        const currentTurn = latest.turns.find((item) => item.id === turnId)!;
        const textEvent = { type: "text_delta" as const, delta: value.reply };
        const completedAt = "2026-10-09T00:00:02Z";
        assistantFixtures.conversations.set(conversationId, {
          ...latest,
          turns: latest.turns.map((item) => item.id === turnId
            ? { ...currentTurn, assistant_text: value.reply, status: "completed", events: [textEvent], result: { kind: "material_turn", material_result: value.material_result }, updated_at: completedAt }
            : item),
          updated_at: completedAt,
        });
        onEvent(textEvent);
        onEvent({ type: "turn_status", turn_id: turnId, turn_state: "completed" });
        resolve(value);
      };
    });
  });
  vi.mocked(updateMaterialAssetTags).mockResolvedValue({ ...taggedAsset, user_tags: ["整条标签"] });
  render(<MaterialLibraryWorkspace initialLibrary={twoAssetLibrary} initialInsights={insights} initialCollections={{collections:[],total_count:0}} />);
  fireEvent.click(screen.getByRole("button", { name: /local\.mp4/ }));
  expect(screen.getByText("整条素材还没有我的标签。")).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("添加到整条素材"), { target: { value: "整条标签" } });
  fireEvent.keyDown(screen.getByLabelText("添加到整条素材"), { key: "Enter" });
  await waitFor(() => expect(updateMaterialAssetTags).toHaveBeenCalledWith("asset", ["整条标签"]));
  await waitFor(() => expect(screen.getByLabelText("移除整条素材标签 整条标签")).toBeInTheDocument());

  fireEvent.click(screen.getByRole("button", { name: /0:01.*0:03/ }));
  fireEvent.change(screen.getByRole("textbox", { name: "素材对话" }), { target: { value: "给片段加上产品演示" } });
  fireEvent.click(screen.getByRole("button", { name: "发送" }));
  await waitFor(() => expect(sendMaterialAssistantTurn).toHaveBeenCalledWith(
    "给片段加上产品演示", expect.any(String), { scope: "segment", asset_id: "asset", segment_id: "segment" },
    expect.any(String), expect.any(Number), expect.any(Function),
  ));
  fireEvent.click(screen.getByRole("button", { name: /自造素材B\.mp4/ }));
  resolveTurn({
    reply: "已按你的要求更新这个片段。", provider: "test", model: "test",
    material_result: {
      scope: "segment", asset_ref: "asset:asset", segment_ref: "segment:segment",
      user_tags: ["旧标签", "产品演示"], system_tags: [], action: "add", changed: true,
    },
  });
  await waitFor(() => expect(screen.getByRole("button", { name: /自造素材B\.mp4/ })).toBeInTheDocument());
  expect(screen.queryByLabelText("移除标签 产品演示")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: /local\.mp4/ }));
  fireEvent.click(screen.getByRole("button", { name: /0:01.*0:03/ }));
  await waitFor(() => expect(screen.getByLabelText("移除标签 产品演示")).toBeInTheDocument());
  expect(screen.getByLabelText("移除整条素材标签 整条标签")).toBeInTheDocument();

  vi.mocked(sendMaterialAssistantTurn).mockResolvedValueOnce({
    reply: "整条素材标签已更新。", provider: "test", model: "test",
    material_result: {
      scope: "asset", asset_ref: "asset:asset", segment_ref: null,
      user_tags: ["整条标签", "整条AI标签"], action: "add", changed: true,
    },
  });
  fireEvent.click(screen.getByRole("button", { name: /local\.mp4/ }));
  fireEvent.change(screen.getByRole("textbox", { name: "素材对话" }), { target: { value: "给整条素材添加整条AI标签" } });
  fireEvent.submit(screen.getByRole("textbox", { name: "素材对话" }).closest("form")!);
  await waitFor(() => expect(sendMaterialAssistantTurn).toHaveBeenCalledWith(
    "给整条素材添加整条AI标签", expect.any(String), { scope: "asset", asset_id: "asset" },
    expect.any(String), expect.any(Number), expect.any(Function),
  ));
  await waitFor(() => expect(screen.getByLabelText("移除整条素材标签 整条AI标签")).toBeInTheDocument());
});

it("persists the selected restriction and removes obsolete proof displays", async () => {
  vi.mocked(updateMaterialUseState).mockResolvedValue({...asset,use_state:"reference_only"});
  render(<MaterialLibraryWorkspace initialLibrary={library} initialInsights={insights} initialCollections={{collections:[],total_count:0}} />);
  fireEvent.click(screen.getByRole("button",{name:"仅参考"}));
  await waitFor(() => expect(updateMaterialUseState).toHaveBeenCalledWith("asset","reference_only"));
  await waitFor(() => expect(screen.getByRole("button",{name:"仅参考"})).toHaveAttribute("aria-pressed","true"));
  expect(screen.queryByText("版权确认")).not.toBeInTheDocument();
  expect(screen.queryByText("品牌安全")).not.toBeInTheDocument();
});
it("retains the saved restriction and shows failure if persistence fails", async () => {
  vi.mocked(updateMaterialUseState).mockRejectedValue(new Error("外接盘暂不可写"));
  render(<MaterialLibraryWorkspace initialLibrary={library} initialInsights={insights} initialCollections={{collections:[],total_count:0}} />);
  fireEvent.click(screen.getByRole("button",{name:"不使用"}));
  await screen.findByText("外接盘暂不可写");
  expect(screen.getByRole("button",{name:"可用于创作"})).toHaveAttribute("aria-pressed","true");
});

it("adds a tag only after the segment update succeeds and keeps the time range visible", async () => {
  vi.mocked(getProductionMaterials).mockResolvedValue(taggedLibrary);
  vi.mocked(updateProductionMaterialSegment).mockResolvedValue({ ...taggedAsset, segments: [{ ...segment, user_tags: ["旧标签", "产品演示"] }] });
  render(<MaterialLibraryWorkspace initialLibrary={taggedLibrary} initialInsights={insights} initialCollections={{collections:[],total_count:0}} />);
  expect(screen.getByText(/当前片段：0:01–0:03/)).toBeInTheDocument();
  const input = screen.getByLabelText("添加标签");
  fireEvent.change(input, { target: { value: "产品演示" } });
  fireEvent.keyDown(input, { key: "Enter" });
  await waitFor(() => expect(updateProductionMaterialSegment).toHaveBeenCalledWith("asset", "segment", expect.objectContaining({ user_tags: ["旧标签", "产品演示"], description: "画面说明" })));
  await waitFor(() => expect(screen.getByLabelText("移除标签 产品演示")).toBeInTheDocument());
  expect(input).toHaveValue("");
});

it("keeps a saved tag when the separate insights refresh fails", async () => {
  vi.mocked(getProductionMaterials).mockResolvedValue(taggedLibrary);
  vi.mocked(getMaterialLibraryInsights).mockRejectedValue(new Error("统计暂不可用"));
  vi.mocked(updateProductionMaterialSegment).mockResolvedValue({ ...taggedAsset, segments: [{ ...segment, user_tags: ["旧标签", "产品演示"] }] });
  render(<MaterialLibraryWorkspace initialLibrary={taggedLibrary} initialInsights={insights} initialCollections={{collections:[],total_count:0}} />);
  const input = screen.getByLabelText("添加标签");
  fireEvent.change(input, { target: { value: "产品演示" } });
  fireEvent.keyDown(input, { key: "Enter" });
  await waitFor(() => expect(screen.getByLabelText("移除标签 产品演示")).toBeInTheDocument());
  await waitFor(() => expect(getMaterialLibraryInsights).toHaveBeenCalled());
  expect(input).toHaveValue("");
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

it("keeps a failed tag save attached to its original asset after selecting another", async () => {
  vi.mocked(getProductionMaterials).mockResolvedValue(twoAssetLibrary);
  let rejectSave!: (reason: Error) => void;
  vi.mocked(updateProductionMaterialSegment).mockImplementation(() => new Promise((_, reject) => { rejectSave = reject; }));
  render(<MaterialLibraryWorkspace initialLibrary={twoAssetLibrary} initialInsights={insights} initialCollections={{collections:[],total_count:0}} />);
  const input = screen.getByLabelText("添加标签");
  fireEvent.change(input, { target: { value:"A片段标签" } });
  fireEvent.keyDown(input, { key:"Enter" });
  fireEvent.click(screen.getByRole("button", { name:/自造素材B\.mp4/ }));
  rejectSave(new Error("A片段暂时无法保存"));
  await waitFor(() => expect(screen.getByLabelText("添加标签")).not.toBeDisabled());
  expect(screen.queryByText("A片段暂时无法保存")).not.toBeInTheDocument();
  expect(screen.getByText(/当前片段：0:01–0:03/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name:/local\.mp4/ }));
  expect(screen.getByLabelText("添加标签")).toHaveValue("A片段标签");
  expect(screen.getByText("A片段暂时无法保存")).toBeInTheDocument();
});

it("updates the original asset without clearing the newly selected asset after success", async () => {
  vi.mocked(getProductionMaterials).mockResolvedValue(twoAssetLibrary);
  let resolveSave!: (value: MaterialAsset) => void;
  vi.mocked(updateProductionMaterialSegment).mockImplementation(() => new Promise((resolve) => { resolveSave = resolve; }));
  render(<MaterialLibraryWorkspace initialLibrary={twoAssetLibrary} initialInsights={insights} initialCollections={{collections:[],total_count:0}} />);
  const input = screen.getByLabelText("添加标签");
  fireEvent.change(input, { target: { value:"A片段标签" } });
  fireEvent.keyDown(input, { key:"Enter" });
  fireEvent.click(screen.getByRole("button", { name:/自造素材B\.mp4/ }));
  resolveSave({ ...taggedAsset, segments:[{ ...segment, user_tags:["旧标签", "A片段标签"] }] });
  await waitFor(() => expect(screen.getByLabelText("添加标签")).not.toBeDisabled());
  expect(screen.queryByLabelText("移除标签 A片段标签")).not.toBeInTheDocument();
  expect(screen.getByLabelText("添加标签")).toHaveValue("");
  fireEvent.click(screen.getByRole("button", { name:/local\.mp4/ }));
  expect(screen.getByLabelText("移除标签 A片段标签")).toBeInTheDocument();
});

it("does not submit while Chinese input is composing and preserves input on failure", async () => {
  vi.mocked(getProductionMaterials).mockResolvedValue(taggedLibrary);
  vi.mocked(updateProductionMaterialSegment).mockRejectedValue(new Error('{"detail":"暂时无法保存"}'));
  render(<MaterialLibraryWorkspace initialLibrary={taggedLibrary} initialInsights={insights} initialCollections={{collections:[],total_count:0}} />);
  const input = screen.getByLabelText("添加标签");
  fireEvent.compositionStart(input);
  fireEvent.change(input, { target: { value: "人物" } });
  fireEvent.keyDown(input, { key: "Enter" });
  expect(updateProductionMaterialSegment).not.toHaveBeenCalled();
  fireEvent.compositionEnd(input);
  fireEvent.keyDown(input, { key: "Enter" });
  await screen.findByText("暂时无法保存");
  expect(input).toHaveValue("人物");
  expect(screen.queryByLabelText("移除标签 人物")).not.toBeInTheDocument();
});

it("removes a tag through the segment API and leaves it visible if saving fails", async () => {
  vi.mocked(getProductionMaterials).mockResolvedValue(taggedLibrary);
  vi.mocked(updateProductionMaterialSegment).mockRejectedValue(new Error("暂时无法保存"));
  render(<MaterialLibraryWorkspace initialLibrary={taggedLibrary} initialInsights={insights} initialCollections={{collections:[],total_count:0}} />);
  fireEvent.click(screen.getByLabelText("移除标签 旧标签"));
  await waitFor(() => expect(updateProductionMaterialSegment).toHaveBeenCalledWith("asset", "segment", expect.objectContaining({ user_tags: [] })));
  await screen.findByText("暂时无法保存");
  expect(screen.getByLabelText("移除标签 旧标签")).toBeInTheDocument();
});

it("filters folders and saves the selected folder for the current segment", async () => {
  vi.mocked(getProductionMaterials).mockResolvedValue(taggedLibrary);
  vi.mocked(updateProductionMaterialSegment).mockResolvedValue({ ...taggedAsset, segments: [{ ...segment, collection_ids: ["folder-2"] }] });
  render(<MaterialLibraryWorkspace initialLibrary={taggedLibrary} initialInsights={insights} initialCollections={{ collections: [
    { id:"folder-1", workspace_id:"workspace", name:"人物", parent_id:null, created_at:"2026-09-20", updated_at:"2026-09-20" },
    { id:"folder-2", workspace_id:"workspace", name:"产品", parent_id:null, created_at:"2026-09-20", updated_at:"2026-09-20" },
  ], total_count:2 }} />);
  fireEvent.change(screen.getByRole("searchbox", { name:"标签夹" }), { target: { value:"产品" } });
  expect(screen.queryByRole("button", { name:"人物" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name:"产品" }));
  fireEvent.click(screen.getByRole("button", { name:"保存片段修改" }));
  await waitFor(() => expect(updateProductionMaterialSegment).toHaveBeenCalledWith("asset", "segment", expect.objectContaining({ collection_ids:["folder-2"] })));
});

it.each([
  ["b_roll", "a_roll", "补充画面", "人物主镜头"],
  ["a_roll", "b_roll", "人物主镜头", "补充画面"],
] as const)("saves segment usage from %s to %s and displays the returned tags after refresh", async (original, chosen, oldLabel, newLabel) => {
  const initial = { ...taggedLibrary, assets: [{ ...taggedAsset, segments: [{ ...segment, usage_role: original, system_tags: [`用途:${original}`] }] }] };
  const saved = { ...taggedAsset, segments: [{ ...segment, usage_role: chosen, system_tags: [`用途:${chosen}`] }] };
  vi.mocked(getProductionMaterials).mockResolvedValue(initial);
  let resolveSave!: (value: MaterialAsset) => void;
  vi.mocked(updateProductionMaterialSegment).mockImplementationOnce(() => new Promise(resolve => { resolveSave = resolve; }));
  let view!: ReturnType<typeof render>;
  await act(async () => {
    view = render(<MaterialLibraryWorkspace initialLibrary={initial} initialInsights={insights} initialCollections={{collections:[],total_count:0}} />);
  });
  const usage = screen.getByRole("combobox", { name: "片段用途" });
  expect(usage).toHaveAttribute("data-selected-value", original);
  await chooseSelectValue(usage, chosen);
  expect(updateProductionMaterialSegment).not.toHaveBeenCalled();
  expect(within(screen.getByLabelText("系统标签")).getByText(`用途:${oldLabel}`)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "保存片段修改" }));
  await waitFor(() => expect(updateProductionMaterialSegment).toHaveBeenCalledWith("asset", "segment", {
    description: segment.description, transcript: segment.transcript, review_status: segment.review_status,
    usage_role: chosen, user_tags: segment.user_tags, collection_ids: [],
  }));
  expect(usage).toBeDisabled();
  await act(async () => resolveSave(saved));
  await waitFor(() => expect(usage).not.toBeDisabled());
  const tags = within(screen.getByLabelText("系统标签"));
  expect(tags.getByText(`用途:${newLabel}`)).toBeInTheDocument();
  expect(tags.queryByText(`用途:${oldLabel}`)).not.toBeInTheDocument();
  expect(screen.getByLabelText("片段描述")).toHaveValue(segment.description);
  expect(screen.getByLabelText("移除标签 旧标签")).toBeInTheDocument();
  view.unmount();
  const refreshed = { ...initial, assets: [saved] };
  vi.mocked(getProductionMaterials).mockResolvedValue(refreshed);
  await act(async () => {
    render(<MaterialLibraryWorkspace initialLibrary={refreshed} initialInsights={insights} initialCollections={{collections:[],total_count:0}} />);
  });
  expect(screen.getByRole("combobox", { name: "片段用途" })).toHaveAttribute("data-selected-value", chosen);
  expect(within(screen.getByLabelText("系统标签")).getByText(`用途:${newLabel}`)).toBeInTheDocument();
});

it("distinguishes an empty search result from an empty library without warning on the initial preview", async () => {
  vi.mocked(getProductionMaterials).mockResolvedValueOnce({ ...library, assets: [], total_count: 0 });
  render(<MaterialLibraryWorkspace initialLibrary={library} initialInsights={insights} initialCollections={{collections:[],total_count:0}} />);
  fireEvent.change(screen.getByRole("textbox", { name: "搜索素材" }), { target: { value: "不存在的词" } });
  await screen.findByText("没有匹配的素材");
  expect(screen.queryByText(/选中的素材已不在当前列表/)).not.toBeInTheDocument();
  expect(screen.queryByText("素材库还是空的")).not.toBeInTheDocument();
});

it("preserves an explicitly selected material's unsaved description while it leaves the filtered list", async () => {
  vi.mocked(getProductionMaterials).mockResolvedValueOnce({ ...taggedLibrary, assets: [], total_count: 0 });
  vi.mocked(getProductionMaterials).mockResolvedValueOnce(taggedLibrary);
  render(<MaterialLibraryWorkspace initialLibrary={taggedLibrary} initialInsights={insights} initialCollections={{collections:[],total_count:0}} />);
  fireEvent.click(screen.getByRole("button", { name: /local\.mp4/ }));
  const description = screen.getByLabelText("片段描述");
  fireEvent.change(description, { target: { value: "尚未保存的说明" } });
  fireEvent.change(screen.getByRole("textbox", { name: "搜索素材" }), { target: { value: "无匹配" } });
  await screen.findByText(/选中的素材已不在当前列表/);
  fireEvent.change(screen.getByRole("textbox", { name: "搜索素材" }), { target: { value: "" } });
  await waitFor(() => expect(screen.getByLabelText("片段描述")).toHaveValue("尚未保存的说明"));
});

it("clears only the query with focus retained and resets all filters separately", async () => {
  render(<MaterialLibraryWorkspace initialLibrary={library} initialInsights={insights} initialCollections={{collections:[],total_count:0}} />);
  await chooseSelectValue(screen.getByRole("combobox", { name: "审核状态筛选" }), "raw");
  const search = screen.getByRole("textbox", { name: "搜索素材" });
  fireEvent.change(search, { target: { value: "本周" } });
  fireEvent.click(screen.getByRole("button", { name: "清除搜索" }));
  expect(search).toHaveValue("");
  expect(search).toHaveFocus();
  expect(screen.getByRole("combobox", { name: "审核状态筛选" })).toHaveAttribute("data-selected-value", "raw");
  fireEvent.click(screen.getByRole("button", { name: "重置全部" }));
  expect(screen.getByRole("combobox", { name: "审核状态筛选" })).toHaveAttribute("data-selected-value", "all");
  await waitFor(() => expect(getProductionMaterials).toHaveBeenLastCalledWith(expect.objectContaining({ query:"", reviewStatus:"all" })));
});

it("does not let a late search response replace a newer explicit selection", async () => {
  vi.mocked(getProductionMaterials).mockResolvedValue(twoAssetLibrary);
  let resolveSearch!: (value: typeof library) => void;
  vi.mocked(getProductionMaterials).mockImplementationOnce(() => new Promise((resolve) => { resolveSearch = resolve; }));
  render(<MaterialLibraryWorkspace initialLibrary={twoAssetLibrary} initialInsights={insights} initialCollections={{collections:[],total_count:0}} />);
  fireEvent.change(screen.getByRole("textbox", { name: "搜索素材" }), { target: { value: "延迟搜索" } });
  await waitFor(() => expect(getProductionMaterials).toHaveBeenCalled());
  fireEvent.click(screen.getByRole("button", { name: /自造素材B\.mp4/ }));
  await act(async () => resolveSearch({ ...library, assets: [taggedAsset], total_count: 1 }));
  await screen.findByText(/选中的素材已不在当前列表/);
  expect(screen.getByText(/素材说明不会写入其他素材|未保存的说明不会写入其他素材/)).toBeInTheDocument();
});

it("reports visual, speech, and OCR analysis separately instead of treating visual completion as all analysis", async () => {
  const partial = { ...taggedAsset, speech_analysis: { status: "blocked" as const, source_fingerprint: "", model_id: "", observations: [], message: "" }, segments: [{ ...segment, visual_status: "ready" as const, understanding: { status: "ready" as const, fingerprint: "", model_id: "", sample_times: [], summary: "", facts: [], unknowns: [], ocr_status: "blocked" as const, ocr_model_id: "", ocr_frames: [], message: "" } }] };
  vi.mocked(getProductionMaterials).mockResolvedValue({ ...library, assets: [partial] });
  render(<MaterialLibraryWorkspace initialLibrary={{ ...library, assets: [partial] }} initialInsights={insights} initialCollections={{collections:[],total_count:0}} />);
  expect(screen.getByText(/画面已分析 · 声音转写受阻 · 文字识别受阻/)).toBeInTheDocument();
});

it("keeps edits made from the initial preview anchored across filtering without creating an assistant reference", async () => {
  vi.mocked(getProductionMaterials)
    .mockResolvedValueOnce({ ...taggedLibrary, assets: [], total_count: 0 })
    .mockResolvedValueOnce({ ...twoAssetLibrary, assets: [otherAsset, taggedAsset] });
  vi.mocked(updateProductionMaterialSegment).mockResolvedValue({ ...taggedAsset, segments: [{ ...segment, description: "预览草稿" }] });
  render(<MaterialLibraryWorkspace initialLibrary={taggedLibrary} initialInsights={insights} initialCollections={{collections:[],total_count:0}} />);
  fireEvent.change(screen.getByLabelText("片段描述"), { target: { value: "预览草稿" } });
  expect(screen.queryByLabelText("当前素材对话范围")).not.toBeInTheDocument();
  fireEvent.change(screen.getByRole("textbox", { name: "搜索素材" }), { target: { value: "筛掉当前素材" } });
  await screen.findByText(/草稿已保留；找回该素材后可继续保存/);
  fireEvent.change(screen.getByRole("textbox", { name: "搜索素材" }), { target: { value: "" } });
  await waitFor(() => expect(screen.getByLabelText("片段描述")).toHaveValue("预览草稿"));
  fireEvent.click(screen.getByRole("button", { name: "保存片段修改" }));
  await waitFor(() => expect(updateProductionMaterialSegment).toHaveBeenCalledWith("asset", "segment", expect.objectContaining({ description: "预览草稿" })));
});

it("counts a missing OCR result for any segment as partial rather than all complete", () => {
  const readyUnderstanding = { status: "ready" as const, fingerprint: "", model_id: "", sample_times: [], summary: "", facts: [], unknowns: [], ocr_status: "ready" as const, ocr_model_id: "", ocr_frames: [], message: "" };
  const twoSegments = { ...taggedAsset, segments: [
    { ...segment, understanding: readyUnderstanding },
    { ...otherSegment, understanding: undefined },
  ] };
  render(<MaterialLibraryWorkspace initialLibrary={{ ...library, assets: [twoSegments] }} initialInsights={insights} initialCollections={{collections:[],total_count:0}} />);
  expect(screen.getByText(/文字识别部分完成/)).toBeInTheDocument();
  expect(screen.queryByText(/文字识别已完成/)).not.toBeInTheDocument();
});

it("keeps the last usable list visible and reports a failed search instead of showing an empty library", async () => {
  vi.mocked(getProductionMaterials).mockRejectedValueOnce(new Error("network unavailable"));
  render(<MaterialLibraryWorkspace initialLibrary={library} initialInsights={insights} initialCollections={{collections:[],total_count:0}} />);
  fireEvent.change(screen.getByRole("textbox", { name: "搜索素材" }), { target: { value: "查询失败" } });
  await screen.findByText("搜索素材失败，请重试。");
  expect(screen.getByRole("button", { name: /local\.mp4/ })).toBeInTheDocument();
  expect(screen.queryByText("素材库还是空的")).not.toBeInTheDocument();
});

it("keeps the directory location available while hiding it in the collapsed intake details", async () => {
  render(<MaterialLibraryWorkspace initialLibrary={library} initialInsights={insights} initialCollections={{collections:[],total_count:0}} />);
  fireEvent.click(screen.getByRole("button", { name: "导入素材" }));
  expect(await screen.findByText("查看接入位置和检查结果")).toBeInTheDocument();
  expect(screen.getByText("/Volumes/Test/media")).not.toBeVisible();
  expect(screen.getByRole("button", { name: "确认接入" })).toBeInTheDocument();
});

it("preserves the segment usage choice and existing error when saving fails, then allows retry", async () => {
  vi.mocked(getProductionMaterials).mockResolvedValue(taggedLibrary);
  vi.mocked(updateProductionMaterialSegment)
    .mockRejectedValueOnce(new Error('{"detail":"片段暂时不可写"}'))
    .mockResolvedValueOnce({ ...taggedAsset, segments: [{ ...segment, usage_role: "a_roll", system_tags: ["用途:a_roll"] }] });
  render(<MaterialLibraryWorkspace initialLibrary={taggedLibrary} initialInsights={insights} initialCollections={{collections:[],total_count:0}} />);
  await chooseSelectValue(screen.getByRole("combobox", { name: "片段用途" }), "a_roll");
  fireEvent.click(screen.getByRole("button", { name: "保存片段修改" }));
  await screen.findByText("片段暂时不可写");
  expect(screen.getByRole("combobox", { name: "片段用途" })).toHaveAttribute("data-selected-value", "a_roll");
  expect(screen.getByRole("combobox", { name: "片段用途" })).not.toBeDisabled();
  expect(within(screen.getByLabelText("系统标签")).queryByText("用途:人物主镜头")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "保存片段修改" }));
  await screen.findByText("用途:人物主镜头");
  expect(screen.queryByText("片段暂时不可写")).not.toBeInTheDocument();
  expect(updateProductionMaterialSegment).toHaveBeenLastCalledWith("asset", "segment", expect.objectContaining({ usage_role: "a_roll" }));
});

it("rebinds the usage draft when switching segments or assets instead of saving another segment's choice", async () => {
  const second: MaterialSegment = { ...segment, id: "second", start_seconds: 3, end_seconds: 4, description: "第二片段", usage_role: "a_roll" };
  const firstAsset = { ...taggedAsset, segments: [segment, second] };
  const initial = { ...twoAssetLibrary, assets: [firstAsset, { ...otherAsset, segments: [{ ...otherSegment, usage_role: "exclude" as const }] }] };
  vi.mocked(getProductionMaterials).mockResolvedValue(initial);
  vi.mocked(updateProductionMaterialSegment).mockResolvedValueOnce({ ...firstAsset, segments: [segment, { ...second, usage_role: "b_roll" }] });
  render(<MaterialLibraryWorkspace initialLibrary={initial} initialInsights={insights} initialCollections={{collections:[],total_count:0}} />);
  await chooseSelectValue(screen.getByRole("combobox", { name: "片段用途" }), "a_roll");
  fireEvent.click(screen.getByRole("button", { name: /第二片段/ }));
  expect(screen.getByRole("combobox", { name: "片段用途" })).toHaveAttribute("data-selected-value", "a_roll");
  await chooseSelectValue(screen.getByRole("combobox", { name: "片段用途" }), "b_roll");
  fireEvent.click(screen.getByRole("button", { name: /画面说明/ }));
  expect(screen.getByRole("combobox", { name: "片段用途" })).toHaveAttribute("data-selected-value", "b_roll");
  fireEvent.click(screen.getByRole("button", { name: /自造素材B\.mp4/ }));
  expect(screen.getByRole("combobox", { name: "片段用途" })).toHaveAttribute("data-selected-value", "exclude");
  fireEvent.click(screen.getByRole("button", { name: /local\.mp4/ }));
  fireEvent.click(screen.getByRole("button", { name: /第二片段/ }));
  expect(screen.getByRole("combobox", { name: "片段用途" })).toHaveAttribute("data-selected-value", "a_roll");
  await chooseSelectValue(screen.getByRole("combobox", { name: "片段用途" }), "b_roll");
  fireEvent.click(screen.getByRole("button", { name: "保存片段修改" }));
  await waitFor(() => expect(updateProductionMaterialSegment).toHaveBeenCalledWith("asset", "second", expect.objectContaining({ usage_role: "b_roll", description: "第二片段" })));
  await waitFor(() => expect(screen.getByRole("combobox", { name: "片段用途" })).not.toBeDisabled());
});

it.each([
  ["unassigned", "未指定（当前用途）"],
  ["exclude", "不使用（已排除）"],
] as const)("preserves the existing %s segment usage until the user explicitly chooses a new role", async (role, label) => {
  const preserved = { ...taggedAsset, segments: [{ ...segment, usage_role: role }] };
  const initial = { ...taggedLibrary, assets: [preserved] };
  vi.mocked(getProductionMaterials).mockResolvedValue(initial);
  vi.mocked(updateProductionMaterialSegment).mockResolvedValueOnce(preserved);
  render(<MaterialLibraryWorkspace initialLibrary={initial} initialInsights={insights} initialCollections={{collections:[],total_count:0}} />);
  expect(screen.getByRole("combobox", { name: "片段用途" })).toHaveAttribute("data-selected-value", role);
  await inspectSelectOptions(screen.getByRole("combobox", { name: "片段用途" }),()=>{expect(screen.getByRole("option", { name: label })).toHaveAttribute("aria-disabled", "true");
  expect(screen.getByRole("option", { name: "人物主镜头" })).not.toHaveAttribute("aria-disabled", "true");
  expect(screen.getByRole("option", { name: "补充画面" })).not.toHaveAttribute("aria-disabled", "true");});
  fireEvent.click(screen.getByRole("button", { name: "保存片段修改" }));
  await waitFor(() => expect(updateProductionMaterialSegment).toHaveBeenCalledWith("asset", "segment", expect.objectContaining({ usage_role: role })));
  await waitFor(() => expect(screen.getByRole("combobox", { name: "片段用途" })).not.toBeDisabled());
  expect(screen.getByRole("combobox", { name: "片段用途" })).toHaveAttribute("data-selected-value", role);
});

it("requires confirmation of the configured directory before automatic intake", async () => {
  const bound = { ...intakeReady, bound_to_current_workspace: true, bound_directory: intakeReady.configured_directory, file_counts: { observing: 2, running: 1, ready: 3, failed: 0, blocked_disk: 0 } };
  vi.mocked(bindMaterialIntake).mockResolvedValue(bound);
  render(<MaterialLibraryWorkspace initialLibrary={library} initialInsights={insights} initialCollections={{collections:[],total_count:0}} />);
  fireEvent.click(screen.getByRole("button", { name: "导入素材" }));
  expect(await screen.findByText("/Volumes/Test/media")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name:"立即检查" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name:"确认接入" }));
  await waitFor(() => expect(bindMaterialIntake).toHaveBeenCalledWith("/Volumes/Test/media"));
  expect(await screen.findByText("待确认复制 0")).toBeInTheDocument();
  expect(screen.getByRole("button", { name:"立即检查" })).toBeInTheDocument();
});

it("confirms the displayed file revision and uses the server intake readback", async () => {
  const file = { relative_path: "external.mp4", expected_revision: { size_bytes: "100", mtime_ns: "9007199254740999", ctime_ns: "200", inode: "3" } };
  const pending = { ...intakeReady, bound_to_current_workspace: true, bound_directory: intakeReady.configured_directory, pending_files: [file] };
  vi.mocked(getMaterialIntakeStatus).mockResolvedValue(pending);
  vi.mocked(confirmMaterialCopyComplete).mockResolvedValue({ confirmed: true, intake: { ...pending, pending_files: [] } });
  render(<MaterialLibraryWorkspace initialLibrary={library} initialInsights={insights} initialCollections={{collections:[],total_count:0}} />);
  fireEvent.click(screen.getByRole("button", { name: "导入素材" }));
  const button = await screen.findByRole("button", { name: "复制完成，开始接收" });
  expect(screen.getByText(/此声明不代表文件已经通过解析或内容校验/)).toBeInTheDocument();
  fireEvent.click(button);
  await waitFor(() => expect(confirmMaterialCopyComplete).toHaveBeenCalledWith({ ...file, confirmation_source: "manual" }));
  await waitFor(() => expect(screen.queryByRole("button", { name: "复制完成，开始接收" })).not.toBeInTheDocument());
  expect(scanMaterialIntake).not.toHaveBeenCalled();
});

it("keeps a failed copy confirmation visible and submits the revised file after refresh", async () => {
  const file = { relative_path: "external.mp4", expected_revision: { size_bytes: "100", mtime_ns: "100", ctime_ns: "200", inode: "3" } };
  const pending = { ...intakeReady, bound_to_current_workspace: true, bound_directory: intakeReady.configured_directory, pending_files: [file] };
  vi.mocked(getMaterialIntakeStatus).mockResolvedValue(pending);
  vi.mocked(confirmMaterialCopyComplete).mockRejectedValueOnce(new Error("文件修订已变化，请重新确认复制完成"));
  render(<MaterialLibraryWorkspace initialLibrary={library} initialInsights={insights} initialCollections={{collections:[],total_count:0}} />);
  fireEvent.click(screen.getByRole("button", { name: "导入素材" }));
  fireEvent.click(await screen.findByRole("button", { name: "复制完成，开始接收" }));
  expect(await screen.findByText(/文件修订已变化，请重新确认复制完成/)).toBeInTheDocument();
  expect(screen.getByText("external.mp4")).toBeInTheDocument();
  const revised = { ...file, expected_revision: { ...file.expected_revision, size_bytes: "200" } };
  vi.mocked(getMaterialIntakeStatus).mockResolvedValue({ ...pending, pending_files: [revised] });
  fireEvent.click(screen.getByRole("button", { name: "重新检查" }));
  await waitFor(() => expect(screen.queryByText(/文件修订已变化，请重新确认复制完成/)).not.toBeInTheDocument());
  vi.mocked(confirmMaterialCopyComplete).mockResolvedValue({ confirmed: true, intake: { ...pending, pending_files: [] } });
  fireEvent.click(screen.getByRole("button", { name: "复制完成，开始接收" }));
  await waitFor(() => expect(confirmMaterialCopyComplete).toHaveBeenLastCalledWith({ ...revised, confirmation_source: "manual" }));
});

it("does not reveal or bind a directory owned by another workspace", async () => {
  vi.mocked(getMaterialIntakeStatus).mockResolvedValue({ ...intakeReady, binding_available:false, configured_directory:null, directory_state:"unknown", directory_message:"素材目录已关联其他工作区；当前工作区不能读取。" });
  render(<MaterialLibraryWorkspace initialLibrary={library} initialInsights={insights} initialCollections={{collections:[],total_count:0}} />);
  fireEvent.click(screen.getByRole("button", { name: "导入素材" }));
  expect(await screen.findByText(/已接入其他工作区/)).toBeInTheDocument();
  expect(screen.queryByText("/Volumes/Test/media")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name:"确认接入" })).not.toBeInTheDocument();
  expect(bindMaterialIntake).not.toHaveBeenCalled();
});

it("requires a fresh confirmation if the configured directory changes during binding", async () => {
  vi.mocked(bindMaterialIntake).mockRejectedValue(new Error('{"detail":"素材目录配置已变化，请刷新后重新确认"}'));
  vi.mocked(getMaterialIntakeStatus)
    .mockResolvedValueOnce(intakeReady)
    .mockResolvedValue({ ...intakeReady, configured_directory:"/Volumes/Test/new-media" });
  render(<MaterialLibraryWorkspace initialLibrary={library} initialInsights={insights} initialCollections={{collections:[],total_count:0}} />);
  fireEvent.click(screen.getByRole("button", { name: "导入素材" }));
  fireEvent.click(await screen.findByRole("button", { name:"确认接入" }));
  expect(await screen.findByText("素材目录配置已变化，请刷新后重新确认")).toBeInTheDocument();
  expect(await screen.findByText("/Volumes/Test/new-media")).toBeInTheDocument();
  expect(bindMaterialIntake).toHaveBeenCalledTimes(1);
});

it("blocks rebinding when the configured directory changed", async () => {
  vi.mocked(getMaterialIntakeStatus).mockResolvedValue({ ...intakeReady, bound_to_current_workspace:true, bound_directory:"/Volumes/Old/media", directory_state:"changed", directory_message:"当前设置的素材目录与本工作区原绑定目录不同。", repair_action:"先检查目录设置并处理原绑定，再重新确认。" });
  render(<MaterialLibraryWorkspace initialLibrary={library} initialInsights={insights} initialCollections={{collections:[],total_count:0}} />);
  fireEvent.click(screen.getByRole("button", { name: "导入素材" }));
  expect(await screen.findByText(/原接入|原绑定/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name:"确认接入" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name:"立即检查" })).not.toBeInTheDocument();
});

it("shows disk repair instead of offering a scan while the bound disk is missing", async () => {
  vi.mocked(getMaterialIntakeStatus).mockResolvedValue({ ...intakeReady, bound_to_current_workspace:true, bound_directory:intakeReady.configured_directory, directory_state:"unmounted", directory_message:"素材所在的外接盘没有挂载。", repair_action:"连接原数据盘后重试。", file_counts:{ observing:0, running:0, ready:0, failed:0, blocked_disk:1 } });
  render(<MaterialLibraryWorkspace initialLibrary={library} initialInsights={insights} initialCollections={{collections:[],total_count:0}} />);
  fireEvent.click(screen.getByRole("button", { name:"导入素材" }));
  expect(await screen.findByText(/素材所在的外接盘没有挂载/)).toBeInTheDocument();
  expect(screen.getByText("等待磁盘 1")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name:"立即检查" })).not.toBeInTheDocument();
});

it("names a read-only directory and offers a repair instead of binding it", async () => {
  vi.mocked(getMaterialIntakeStatus).mockResolvedValue({ ...intakeReady, directory_state:"read_only", directory_message:"素材目录当前不可写，素材无法进入分析。", repair_action:"检查磁盘是否以只读方式挂载或目录权限是否被改动，再重新检查。" });
  render(<MaterialLibraryWorkspace initialLibrary={library} initialInsights={insights} initialCollections={{collections:[],total_count:0}} />);
  fireEvent.click(screen.getByRole("button", { name: "导入素材" }));
  expect(await screen.findByText("目录不可写")).toBeInTheDocument();
  expect(screen.getByText(/检查磁盘是否以只读方式挂载/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name:"确认接入" })).toBeDisabled();
});

it("keeps failed intake visible and retries the selected asset", async () => {
  const failed = { ...asset, analysis_status:"failed" as const, analysis_message:"本地分析服务暂不可用；检查服务后重试。" };
  const retried = { ...failed, analysis_status:"pending" as const, analysis_message:"已请求重试，等待素材稳定后重新分析。" };
  vi.mocked(getProductionMaterials).mockResolvedValue({ ...library, assets:[failed] });
  vi.mocked(getMaterialIntakeStatus).mockResolvedValue({ ...intakeReady, bound_to_current_workspace:true, bound_directory:intakeReady.configured_directory, file_counts:{ observing:0, running:0, ready:0, failed:1, blocked_disk:0 } });
  vi.mocked(retryMaterialIntake).mockResolvedValue(retried);
  render(<MaterialLibraryWorkspace initialLibrary={{ ...library, assets:[failed] }} initialInsights={insights} initialCollections={{collections:[],total_count:0}} />);
  fireEvent.click(screen.getByRole("button", { name: "导入素材" }));
  expect((await screen.findAllByText("本地分析服务暂不可用；检查服务后重试。")).length).toBeGreaterThan(0);
  fireEvent.click(screen.getByRole("button", { name:"重试分析" }));
  await waitFor(() => expect(retryMaterialIntake).toHaveBeenCalledWith("asset"));
  await waitFor(() => expect(screen.queryByRole("button", { name:"重试分析" })).not.toBeInTheDocument());
});

it("uses manual scan only as a secondary repair action for a bound ready directory", async () => {
  vi.mocked(getMaterialIntakeStatus).mockResolvedValue({ ...intakeReady, bound_to_current_workspace:true, bound_directory:intakeReady.configured_directory });
  vi.mocked(scanMaterialIntake).mockResolvedValue(library);
  render(<MaterialLibraryWorkspace initialLibrary={library} initialInsights={insights} initialCollections={{collections:[],total_count:0}} />);
  fireEvent.click(screen.getByRole("button", { name: "导入素材" }));
  fireEvent.click(await screen.findByRole("button", { name:"立即检查" }));
  await waitFor(() => expect(scanMaterialIntake).toHaveBeenCalledTimes(1));
  expect(screen.queryByRole("button", { name:"找新素材" })).not.toBeInTheDocument();
});

it("keeps a loaded second page, selected asset and draft during background refresh", async () => {
  vi.useFakeTimers({ toFake:["setInterval", "clearInterval"] });
  try {
    const firstPage = { ...library, assets:[taggedAsset], total_count:2, limit:1, has_more:true };
    const secondPage = { ...firstPage, assets:[{ ...otherAsset, review_status:"raw" as const, analysis_status:"pending" as const }], offset:1, has_more:false };
    const updatedSecond = { ...secondPage.assets[0], analysis_status:"failed" as const, analysis_message:"分析服务暂不可用" };
    vi.mocked(getProductionMaterials).mockImplementation(async (input) => {
      if (input?.offset === 1) return secondPage;
      return { ...firstPage, assets:input?.limit === 2 ? [taggedAsset, updatedSecond] : [taggedAsset], has_more:input?.limit !== 2 };
    });
    vi.mocked(getMaterialIntakeStatus).mockResolvedValue({ ...intakeReady, bound_to_current_workspace:true, bound_directory:intakeReady.configured_directory });
    render(<MaterialLibraryWorkspace initialLibrary={firstPage} initialInsights={insights} initialCollections={{collections:[],total_count:0}} />);
    fireEvent.click(screen.getByRole("button", { name:/载入更多/ }));
    await waitFor(() => expect(screen.getByRole("button", { name:/自造素材B\.mp4/ })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name:/自造素材B\.mp4/ }));
    fireEvent.change(screen.getByLabelText("添加标签"), { target:{ value:"尚未保存的草稿" } });
    await act(async () => { await vi.advanceTimersByTimeAsync(15000); });
    expect(getProductionMaterials).toHaveBeenCalledWith(expect.objectContaining({ limit:2, offset:0 }));
    expect(screen.getByRole("button", { name:/自造素材B\.mp4/ })).toHaveClass("is-selected");
    expect(screen.getByLabelText("添加标签")).toHaveValue("尚未保存的草稿");
    expect(screen.getAllByText("分析服务暂不可用")).toHaveLength(1);
    expect(screen.getByText(/已载入 2 \/ 2/)).toBeInTheDocument();
  } finally {
    vi.useRealTimers();
  }
});

const boundReadyIntake = () => ({
  ...intakeReady,
  bound_to_current_workspace: true,
  bound_directory: intakeReady.configured_directory,
});
const renderBound = (props = {}) => {
  const result = render(<MaterialLibraryWorkspace initialLibrary={library} initialInsights={insights} initialCollections={{collections:[],total_count:0}} {...props} />);
  fireEvent.click(screen.getByRole("button", { name:"导入素材" }));
  return result;
};

it("offers local import only for a bound ready directory and sends no path", async () => {
  vi.mocked(getMaterialIntakeStatus).mockResolvedValue(boundReadyIntake());
  vi.mocked(importLocalMaterials).mockResolvedValue({ canceled: true });
  renderBound();
  fireEvent.click(await screen.findByRole("button", { name:"导入本地素材" }));
  await waitFor(() => expect(importLocalMaterials).toHaveBeenCalledTimes(1));
  // The renderer must not be able to name a write target.
  expect(importLocalMaterials).toHaveBeenCalledWith();
  expect(screen.getByText("已取消导入，源文件保持不变。")).toBeInTheDocument();
});

it("hides local import while the directory is not bound", async () => {
  vi.mocked(getMaterialIntakeStatus).mockResolvedValue(intakeReady);
  renderBound();
  await screen.findByRole("button", { name:"确认接入" });
  expect(screen.queryByRole("button", { name:"导入本地素材" })).not.toBeInTheDocument();
});

it("hides local import while a bound directory is not ready", async () => {
  vi.mocked(getMaterialIntakeStatus).mockResolvedValue({
    ...boundReadyIntake(), directory_state:"missing", directory_message:"素材盘未连接。",
  });
  renderBound();
  await waitFor(() => expect(screen.getByText(/素材盘未连接/)).toBeInTheDocument());
  expect(screen.queryByRole("button", { name:"导入本地素材" })).not.toBeInTheDocument();
});

it("reports copied files as confirmed for intake, never as already analyzed", async () => {
  vi.mocked(getMaterialIntakeStatus).mockResolvedValue(boundReadyIntake());
  vi.mocked(importLocalMaterials).mockResolvedValue({
    canceled: false,
    copied: [{ sourceName:"a.mp4", fileName:"a.mp4" }, { sourceName:"b.mp4", fileName:"b (1).mp4" }],
    skipped: [],
    failures: [],
  });
  renderBound();
  fireEvent.click(await screen.findByRole("button", { name:"导入本地素材" }));
  const message = await screen.findByText(/复制完成确认已保存，等待接收并分析/);
  expect(message).toHaveTextContent("已复制 2 个文件到素材目录");
  expect(message).toHaveTextContent("a.mp4");
  expect(message).toHaveTextContent("b (1).mp4");
  expect(message).toHaveTextContent("源文件保持不变");
  // The copy state must not be presented as registration or analysis.
  expect(screen.queryByText(/已入库/)).not.toBeInTheDocument();
  expect(message).not.toHaveTextContent("已入库");
});

it("shows a per-file import failure and a top-level error without claiming a fix", async () => {
  vi.mocked(getMaterialIntakeStatus).mockResolvedValue(boundReadyIntake());
  vi.mocked(importLocalMaterials).mockResolvedValue({
    canceled: false,
    copied: [],
    skipped: [],
    failures: [{ sourceName:"x.mp4", error:"该素材盘的文件系统不支持本导入入口所需的原子提交方式，本次未导入，源文件保持不变。" }],
  });
  renderBound();
  fireEvent.click(await screen.findByRole("button", { name:"导入本地素材" }));
  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent("导入失败 1 个");
  expect(alert).toHaveTextContent("x.mp4");
  expect(alert).toHaveTextContent("不支持本导入入口所需的原子提交方式");
  expect(alert).not.toHaveTextContent("格式化");
});

it("does not offer a formatting suggestion when the intake bridge cannot be read", async () => {
  vi.mocked(getMaterialIntakeStatus).mockResolvedValue(boundReadyIntake());
  vi.mocked(importLocalMaterials).mockResolvedValue({
    canceled: false, copied: [], skipped: [], failures: [],
    error: "本地服务要求登录，桌面端当前没有可用凭据，无法确认素材接入目录。本次未导入，源文件保持不变。",
  });
  renderBound();
  fireEvent.click(await screen.findByRole("button", { name:"导入本地素材" }));
  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent("没有可用凭据");
  expect(alert).not.toHaveTextContent("登录后重试");
  expect(alert).not.toHaveTextContent("格式化");
});

it("explains which files were skipped as unsupported", async () => {
  vi.mocked(getMaterialIntakeStatus).mockResolvedValue(boundReadyIntake());
  vi.mocked(importLocalMaterials).mockResolvedValue({ canceled:false, copied:[], skipped:["notes.txt"], failures:[] });
  renderBound();
  fireEvent.click(await screen.findByRole("button", { name:"导入本地素材" }));
  const status = await screen.findByText(/没有可导入的文件：notes\.txt/);
  expect(status).toHaveAttribute("role", "status");
  expect(status).toHaveTextContent("notes.txt");
  expect(status).toHaveTextContent("仅支持 mp4、mov、m4v");
});

it("surfaces a cleanup warning without turning a completed copy into a failure", async () => {
  vi.mocked(getMaterialIntakeStatus).mockResolvedValue(boundReadyIntake());
  vi.mocked(importLocalMaterials).mockResolvedValue({
    canceled:false, copied:[{ sourceName:"a.mp4", fileName:"a.mp4" }], skipped:[], failures:[],
    warning: "部分临时文件未能自动清理（素材已导入成功），这些文件对素材接入不可见，可稍后重试导入或联系支持。",
  });
  renderBound();
  fireEvent.click(await screen.findByRole("button", { name:"导入本地素材" }));
  const status = await screen.findByText(/已复制 1 个文件到素材目录/);
  expect(status).toHaveAttribute("role", "status");
  expect(status).toHaveTextContent("已复制 1 个文件到素材目录");
  expect(status).toHaveTextContent("未能自动清理");
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

// The 15s poll used to re-seed `assets` while silently falling back to
// assets[0], so a material that left the library left its description draft
// under another material's name and 保存片段修改 wrote that draft onto the
// wrong item. These pin the corrected behaviour: the loss is reported, nothing
// is savable while it stands, and picking a material again re-seeds from that
// material rather than restoring the stale draft.
const dropPoll = (
  render_: () => void,
  answer: (dropped: boolean) => typeof twoAssetLibrary,
) => {
  let drop = false;
  vi.mocked(getProductionMaterials).mockImplementation(async () => answer(drop));
  render_();
  return {
    startDropping: () => { drop = true; },
    async tick() {
      await act(async () => { await vi.advanceTimersByTimeAsync(30000); });
    },
  };
};

const withBoundIntake = async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  vi.mocked(getMaterialIntakeStatus).mockResolvedValue(boundReadyIntake());
};

it("stops editing when a refresh drops the selected material instead of saving its draft elsewhere", async () => {
  await withBoundIntake();
  try {
    const poll = dropPoll(
      () => render(
        <MaterialLibraryWorkspace
          initialLibrary={twoAssetLibrary}
          initialInsights={insights}
          initialCollections={{ collections: [], total_count: 0 }}
        />,
      ),
      (dropped) => (dropped
        ? { ...library, assets: [otherAsset], total_count: 1 }
        : twoAssetLibrary),
    );
    await act(async () => { await vi.advanceTimersByTimeAsync(15000); });

    fireEvent.click(screen.getByRole("button", { name: /local\.mp4/ }));
    fireEvent.change(screen.getByLabelText("片段描述"), { target: { value: "A的说明" } });

    poll.startDropping();
    await poll.tick();

    // The material is gone from the list, editing has stopped, and the reason is
    // stated rather than the panel quietly showing a different material.
    expect(screen.queryByRole("button", { name: /local\.mp4/ })).not.toBeInTheDocument();
    expect(screen.queryByLabelText("片段描述")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "保存片段修改" })).not.toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("选中的素材已不在当前列表中");

    // Re-selecting is the way out, and it must seed from B, not restore A's text.
    fireEvent.click(screen.getByRole("button", { name: /自造素材B\.mp4/ }));
    expect(screen.getByLabelText("片段描述")).not.toHaveValue("A的说明");
    fireEvent.click(screen.getByRole("button", { name: "保存片段修改" }));
    await waitFor(() => expect(updateProductionMaterialSegment).toHaveBeenCalledWith(
      "asset-b",
      "segment-b",
      expect.not.objectContaining({ description: "A的说明" }),
    ));
  } finally {
    vi.useRealTimers();
  }
});

// A full page of results is exactly the case the old `retained` push was meant
// to cover. It must not keep a removed material alive: doing so would hide the
// loss behind a stale entry that still looks editable.
it("does not keep a removed material listed just because the refreshed page is full", async () => {
  await withBoundIntake();
  try {
    const thirdSegment = { ...otherSegment, id: "segment-c", asset_id: "asset-c" };
    const thirdAsset = { ...otherAsset, id: "asset-c", file_name: "自造素材C.mp4", segments: [thirdSegment] };
    const poll = dropPoll(
      () => render(
        <MaterialLibraryWorkspace
          initialLibrary={{ ...twoAssetLibrary, limit: 1 }}
          initialInsights={insights}
          initialCollections={{ collections: [], total_count: 0 }}
        />,
      ),
      (dropped) => (dropped
        ? { assets: [otherAsset, thirdAsset], total_count: 2, limit: 2, offset: 0, has_more: false }
        : { ...twoAssetLibrary, limit: 1 }),
    );
    await act(async () => { await vi.advanceTimersByTimeAsync(15000); });

    fireEvent.click(screen.getByRole("button", { name: /local\.mp4/ }));
    poll.startDropping();
    await poll.tick();

    expect(screen.queryByRole("button", { name: /local\.mp4/ })).not.toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("选中的素材已不在当前列表中");
  } finally {
    vi.useRealTimers();
  }
});

// Losing the selection is a durable state, not a one-tick flicker. Repeated
// refreshes must not oscillate back into an editable panel for a material that
// is gone.
it("keeps the selection reported as lost across repeated refreshes", async () => {
  await withBoundIntake();
  try {
    const poll = dropPoll(
      () => render(
        <MaterialLibraryWorkspace
          initialLibrary={twoAssetLibrary}
          initialInsights={insights}
          initialCollections={{ collections: [], total_count: 0 }}
        />,
      ),
      (dropped) => (dropped
        ? { ...library, assets: [otherAsset], total_count: 1 }
        : twoAssetLibrary),
    );
    await act(async () => { await vi.advanceTimersByTimeAsync(15000); });

    fireEvent.click(screen.getByRole("button", { name: /local\.mp4/ }));
    poll.startDropping();
    await poll.tick();
    await poll.tick();
    await poll.tick();

    expect(screen.queryByRole("button", { name: "保存片段修改" })).not.toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("选中的素材已不在当前列表中");
    expect(updateProductionMaterialSegment).not.toHaveBeenCalled();
  } finally {
    vi.useRealTimers();
  }
});

// The same loss one level down: the material survives, but re-analysis replaced
// the segment the draft was typed against. Falling back to segments[0] would
// put that draft on a different segment of the same file.
it("stops editing when the selected segment disappears from a material that stays", async () => {
  await withBoundIntake();
  try {
    const resegmented = { ...taggedAsset, segments: [{ ...segment, id: "segment-new", description: "重新分段后的说明" }] };
    const poll = dropPoll(
      () => render(
        <MaterialLibraryWorkspace
          initialLibrary={twoAssetLibrary}
          initialInsights={insights}
          initialCollections={{ collections: [], total_count: 0 }}
        />,
      ),
      (dropped) => (dropped
        ? { ...library, assets: [resegmented], total_count: 1 }
        : twoAssetLibrary),
    );
    await act(async () => { await vi.advanceTimersByTimeAsync(15000); });

    fireEvent.click(screen.getByRole("button", { name: /local\.mp4/ }));
    fireEvent.change(screen.getByLabelText("片段描述"), { target: { value: "A的说明" } });

    poll.startDropping();
    await poll.tick();

    // The material is still listed and still selected; only its segment moved on.
    expect(screen.getByRole("button", { name: /local\.mp4/ })).toBeInTheDocument();
    expect(screen.queryByLabelText("片段描述")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "保存片段修改" })).not.toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("选中的片段已不在该素材中");
  } finally {
    vi.useRealTimers();
  }
});

it("does not invent target-specific shortages from taxonomy counts", async () => {
  const withGaps = { ...insights, recommended_gaps: ["场景:办公", "镜头:特写"] };
  vi.mocked(getMaterialLibraryInsights).mockResolvedValue(withGaps);
  render(
    <MaterialLibraryWorkspace
      initialLibrary={library}
      initialInsights={withGaps}
      initialCollections={{ collections: [], total_count: 0 }}
    />,
  );

  expect(screen.queryByText("覆盖建议")).not.toBeInTheDocument();
  expect(screen.queryByText(/素材库暂时没有/)).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "查看素材统计" }));
  expect(await screen.findByRole("dialog", { name: "素材统计" })).toHaveTextContent("具体缺口会在制作任务检查素材时显示");
});

it("leaves the coverage suggestion out when every taxonomy tag already has material", async () => {
  render(<MaterialLibraryWorkspace initialLibrary={library} initialInsights={insights} initialCollections={{collections:[],total_count:0}} />);

  fireEvent.click(screen.getByRole("button", { name: "查看素材统计" }));
  await waitFor(() => expect(screen.getByLabelText("素材覆盖")).toBeInTheDocument());
  expect(screen.queryByText("覆盖建议")).not.toBeInTheDocument();
});

afterEach(() => vi.restoreAllMocks());

const laterSegment = { ...segment, id: "later-segment", start_seconds: 2.5, end_seconds: 3.5, description: "后段声音" };

it.each(["audio", "video"] as const)("seeks loaded %s to the clicked segment without changing playback", async (mediaType) => {
  const mediaLibrary = { ...library, assets: [{ ...taggedAsset, media_type: mediaType, segments: [segment, laterSegment] }] };
  render(<MaterialLibraryWorkspace initialLibrary={mediaLibrary} initialInsights={insights} initialCollections={{collections:[],total_count:0}} />);
  const media = await screen.findByLabelText<HTMLMediaElement>("原素材预览");
  Object.defineProperty(media, "readyState", { configurable: true, value: HTMLMediaElement.HAVE_METADATA });
  const play = vi.spyOn(media, "play").mockResolvedValue();
  const pause = vi.spyOn(media, "pause").mockImplementation(() => {});
  fireEvent.loadedMetadata(media);
  expect(media.currentTime).toBe(0);
  fireEvent.click(screen.getByRole("button", { name: /后段声音/ }));
  expect(media.currentTime).toBe(2.5);
  expect(screen.getByLabelText("当前素材对话范围")).toHaveTextContent("片段 0:02.5–0:03.5");
  Object.defineProperty(media, "paused", { configurable: true, value: false });
  fireEvent.click(screen.getByRole("button", { name: /画面说明/ }));
  expect(media.currentTime).toBe(1);
  expect(media.paused).toBe(false);
  media.currentTime = 3.9;
  fireEvent.timeUpdate(media);
  expect(media.currentTime).toBe(3.9);
  expect(play).not.toHaveBeenCalled();
  expect(pause).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole("button", { name:"切回整条素材" }));
  expect(media.currentTime).toBe(0);
  media.currentTime = 3.9;
  fireEvent.timeUpdate(media);
  expect(pause).toHaveBeenCalledTimes(1);
});

it.each(["audio", "video"] as const)("defers unloaded %s seeking until metadata for the latest selected segment", async (mediaType) => {
  const mediaLibrary = { ...library, assets: [{ ...taggedAsset, media_type: mediaType, segments: [segment, laterSegment] }] };
  render(<MaterialLibraryWorkspace initialLibrary={mediaLibrary} initialInsights={insights} initialCollections={{collections:[],total_count:0}} />);
  const media = await screen.findByLabelText<HTMLMediaElement>("原素材预览");
  const seek = vi.spyOn(media, "currentTime", "set");
  fireEvent.click(screen.getByRole("button", { name: /后段声音/ }));
  fireEvent.click(screen.getByRole("button", { name: /画面说明/ }));
  fireEvent.click(screen.getByRole("button", { name: /后段声音/ }));
  expect(seek).not.toHaveBeenCalled();
  Object.defineProperty(media, "readyState", { configurable: true, value: HTMLMediaElement.HAVE_METADATA });
  fireEvent.loadedMetadata(media);
  expect(seek).toHaveBeenCalledExactlyOnceWith(2.5);
});

it("switches audio assets and video without seeking the previous media element", async () => {
  const audioA = { ...taggedAsset, media_type: "audio" as const, file_name: "声音A.wav", segments: [segment, laterSegment] };
  const audioB = { ...otherAsset, media_type: "audio" as const, file_name: "声音B.wav", segments: [{ ...otherSegment, start_seconds: 0.5 }, { ...laterSegment, id: "later-b", asset_id: otherAsset.id, start_seconds: 2 }] };
  const mediaLibrary = { ...library, assets: [audioA, audioB, { ...taggedAsset, id: "video-c", file_name: "画面C.mp4", segments: [{ ...segment, asset_id: "video-c", start_seconds: 1.5 }] }], total_count: 3 };
  render(<MaterialLibraryWorkspace initialLibrary={mediaLibrary} initialInsights={insights} initialCollections={{collections:[],total_count:0}} />);
  const first = await screen.findByLabelText<HTMLMediaElement>("原素材预览");
  Object.defineProperty(first, "readyState", { configurable: true, value: HTMLMediaElement.HAVE_METADATA });
  fireEvent.loadedMetadata(first);
  fireEvent.click(screen.getByRole("button", { name: /后段声音/ }));
  expect(first.currentTime).toBe(2.5);
  fireEvent.click(screen.getByRole("button", { name: /声音B\.wav/ }));
  const second = screen.getByLabelText<HTMLMediaElement>("原素材预览");
  expect(second).not.toBe(first);
  Object.defineProperty(second, "readyState", { configurable: true, value: HTMLMediaElement.HAVE_METADATA });
  fireEvent.loadedMetadata(second);
  expect(second.currentTime).toBe(0);
  fireEvent.click(screen.getByRole("button", { name: /后段声音/ }));
  expect(second.currentTime).toBe(2);
  fireEvent.loadedMetadata(first);
  expect(first.currentTime).toBe(2.5);
  expect(second.currentTime).toBe(2);
  fireEvent.click(screen.getByRole("button", { name: /画面C\.mp4/ }));
  const video = screen.getByLabelText<HTMLMediaElement>("原素材预览");
  Object.defineProperty(video, "readyState", { configurable: true, value: HTMLMediaElement.HAVE_METADATA });
  fireEvent.loadedMetadata(video);
  expect(video.currentTime).toBe(0);
  fireEvent.click(screen.getByRole("button", { name: /声音B\.wav/ }));
  const returnedAudio = screen.getByLabelText<HTMLMediaElement>("原素材预览");
  Object.defineProperty(returnedAudio, "readyState", { configurable: true, value: HTMLMediaElement.HAVE_METADATA });
  fireEvent.loadedMetadata(returnedAudio);
  expect(returnedAudio.currentTime).toBe(0);
  expect(video.currentTime).toBe(0);
});

const linkUrl = "https://media.example.com/clip.mp4";

it("shows the media type before importing and keeps the source visible after a complete import", async () => {
  vi.mocked(getMaterialIntakeStatus).mockResolvedValue(boundReadyIntake());
  vi.mocked(scanMaterialIntake).mockResolvedValue(library);
  vi.mocked(checkMediaLink).mockResolvedValue({ url: linkUrl, supported: true, media_type: "video", file_name: "link-abcd.mp4", size_bytes: 4096, message: "可接入的视频直链，约 4.0 KB" });
  vi.mocked(importMediaLink).mockResolvedValue({ url: linkUrl, media_type: "video", file_name: "link-abcd.mp4", size_bytes: 4096, file_path: "/Volumes/Test/media/link-abcd.mp4", asset_id: "asset-abcd", provenance_id: "asset-abcd", reused_existing_file: false, analysis_status: "pending", analysis_message: "待分析镜头和画面内容。" });
  renderBound();
  await waitFor(() => expect(screen.getByLabelText("媒体文件直链")).toBeInTheDocument());

  fireEvent.change(screen.getByLabelText("媒体文件直链"), { target: { value: linkUrl } });
  fireEvent.click(screen.getByRole("button", { name: "检查直链" }));

  await waitFor(() => expect(screen.getByText("可接入的视频直链，约 4.0 KB")).toBeInTheDocument());
  expect(importMediaLink).not.toHaveBeenCalled();

  fireEvent.click(screen.getByRole("button", { name: "导入这条链接" }));
  await waitFor(() => expect(importMediaLink).toHaveBeenCalledWith(linkUrl));
  await waitFor(() => expect(screen.getByText(/已导入：link-abcd.mp4，4096 字节/)).toBeInTheDocument());
  expect(screen.getByText(/来源：https:\/\/media\.example\.com\/clip\.mp4/)).toBeInTheDocument();
});

it("previews an imported image with its own element and shows the recorded source", async () => {
  const stillLibrary = {
    ...library,
    assets: [{
      ...asset,
      id: "asset-still",
      file_name: "link-abcd.jpg",
      media_type: "image" as const,
      source_url: "https://media.example.com/still.png",
      duration_seconds: null,
      analysis_status: "blocked" as const,
      analysis_message: "这条图片已入库；首批只支持视频的镜头与画面分析，内容分析尚未接入。",
    }],
  };
  vi.mocked(getProductionMaterials).mockResolvedValue(stillLibrary);
  renderBound({ initialLibrary: stillLibrary });

  const preview = await screen.findByAltText(/原素材预览：link-abcd\.jpg/);
  expect(preview).toHaveAttribute("src", "/api/production/materials/asset-still/media");
  expect(screen.queryByLabelText("原素材预览")).not.toBeInTheDocument();
  fireEvent.click(screen.getByText("原文件与来源"));
  expect(screen.getByRole("link", { name: "https://media.example.com/still.png" })).toBeInTheDocument();
  expect(screen.getAllByText(/内容分析尚未接入/).length).toBeGreaterThan(0);
});

it("does not report an import when the source is refused before submission", async () => {
  vi.mocked(getMaterialIntakeStatus).mockResolvedValue(boundReadyIntake());
  vi.mocked(checkMediaLink).mockRejectedValue(new Error("这是社交平台页面，不是媒体文件直链，暂不支持。"));
  renderBound();
  await waitFor(() => expect(screen.getByLabelText("媒体文件直链")).toBeInTheDocument());

  fireEvent.change(screen.getByLabelText("媒体文件直链"), { target: { value: "https://youtube.com/watch?v=abc" } });
  fireEvent.click(screen.getByRole("button", { name: "检查直链" }));

  await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("这是社交平台页面，不是媒体文件直链，暂不支持。"));
  expect(importMediaLink).not.toHaveBeenCalled();
  expect(screen.queryByText(/已导入/)).not.toBeInTheDocument();
});


it.each([
  ["unreachable service", "API server unreachable", "暂时无法连接本地服务，请确认应用正常运行后重试"],
  ["other English failure", "Unexpected upstream response", "说明保存失败"],
  ["existing Chinese detail", "目录暂不可写", "目录暂不可写"],
])("shows the %s note-save cause while retaining the draft without success", async (_kind, detail, expected) => {
  const { ApiRequestError } = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  vi.mocked(updateMaterialNotes).mockRejectedValueOnce(new ApiRequestError(502, JSON.stringify({ detail })));
  render(<MaterialLibraryWorkspace initialLibrary={library} initialInsights={insights} initialCollections={{collections:[],total_count:0}} />);
  fireEvent.change(screen.getByLabelText("本素材说明（可选）"), { target: { value: "保留这份未保存的草稿" } });
  fireEvent.click(screen.getByRole("button", { name: "保存本素材说明" }));
  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent(`${expected}。草稿已保留，请重试保存。`);
  expect(alert.textContent).not.toMatch(/API server unreachable|Unexpected upstream response/);
  expect(screen.getByLabelText("本素材说明（可选）")).toHaveValue("保留这份未保存的草稿");
  expect(screen.queryByText("本素材说明已保存。")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "保存本素材说明" })).toBeEnabled();
  expect(updateMaterialNotes).toHaveBeenCalledExactlyOnceWith([{ asset_id: asset.id, user_description: "保留这份未保存的草稿", upload_context: asset.upload_context }]);
});

it("keeps material note drafts and save failures on their own asset when switching", async () => {
  vi.mocked(getProductionMaterials).mockResolvedValue(twoAssetLibrary);
  let rejectSave!: (cause: Error) => void;
  vi.mocked(updateMaterialNotes).mockImplementationOnce(() => new Promise((_, reject) => { rejectSave = reject; }));
  render(<MaterialLibraryWorkspace initialLibrary={twoAssetLibrary} initialInsights={insights} initialCollections={{collections:[],total_count:0}} />);
  fireEvent.change(screen.getByLabelText("本素材说明（可选）"), { target: { value:"A自己的说明" } });
  fireEvent.click(screen.getByRole("button", { name:/自造素材B\.mp4/ }));
  expect(screen.getByLabelText("本素材说明（可选）")).toHaveValue("");
  fireEvent.change(screen.getByLabelText("本素材说明（可选）"), { target: { value:"B自己的说明" } });
  fireEvent.click(screen.getByRole("button", { name:/local\.mp4/ }));
  expect(screen.getByLabelText("本素材说明（可选）")).toHaveValue("A自己的说明");
  fireEvent.click(screen.getByRole("button", { name:"保存本素材说明" }));
  expect(updateMaterialNotes).toHaveBeenLastCalledWith([{asset_id:"asset",user_description:"A自己的说明",upload_context:""}]);
  fireEvent.click(screen.getByRole("button", { name:/自造素材B\.mp4/ }));
  await act(async () => rejectSave(new Error("目录暂不可写")));
  expect(screen.getByLabelText("本素材说明（可选）")).toHaveValue("B自己的说明");
  expect(screen.queryByText(/目录暂不可写/)).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name:/local\.mp4/ }));
  expect(screen.getByRole("alert")).toHaveTextContent("目录暂不可写");
  expect(screen.getByRole("alert")).toHaveTextContent("草稿已保留，请重试保存");
  expect(screen.getByLabelText("本素材说明（可选）")).toHaveValue("A自己的说明");
  expect(screen.queryByText("本素材说明已保存。")).not.toBeInTheDocument();
});

it("writes a shared context only to explicitly checked loaded files without saving another description draft", async () => {
  const a = {...taggedAsset,user_description:"A原说明",upload_context:"A原背景"};
  const b = {...otherAsset,user_description:"B原说明",upload_context:"B原背景"};
  const current = {...twoAssetLibrary, assets:[a,b]};
  vi.mocked(getProductionMaterials).mockResolvedValue(current);
  vi.mocked(updateMaterialNotes).mockResolvedValue({assets:[{...b,upload_context:"这批是产品演示"}]});
  render(<MaterialLibraryWorkspace initialLibrary={current} initialInsights={insights} initialCollections={{collections:[],total_count:0}} />);
  fireEvent.change(screen.getByLabelText("本素材说明（可选）"), { target: { value:"A未保存草稿" } });
  fireEvent.click(screen.getByText("给选中素材保存公共背景（可选）"));
  expect(screen.getByRole("button", { name:"保存选中素材背景" })).toBeDisabled();
  fireEvent.click(screen.getByRole("checkbox", { name:"自造素材B.mp4" }));
  fireEvent.change(screen.getByLabelText("公共背景（可选）"), { target: { value:"这批是产品演示" } });
  fireEvent.click(screen.getByRole("button", { name:"保存选中素材背景" }));
  await screen.findByText("选中素材的公共背景已保存。");
  expect(updateMaterialNotes).toHaveBeenLastCalledWith([{asset_id:"asset-b",user_description:"B原说明",upload_context:"这批是产品演示"}]);
  expect(screen.getByLabelText("本素材说明（可选）")).toHaveValue("A未保存草稿");
  expect(screen.getByText("已保存背景：A原背景")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name:/自造素材B\.mp4/ }));
  expect(screen.getByLabelText("本素材说明（可选）")).toHaveValue("B原说明");
  expect(screen.getByText("已保存背景：这批是产品演示")).toBeInTheDocument();
});

it("shows the returned material note after saving and rereading, and allows an empty note", async () => {
  const saved = {...asset,user_description:"服务端保存事实",upload_context:"原背景"};
  vi.mocked(updateMaterialNotes).mockResolvedValue({assets:[saved]});
  const view = render(<MaterialLibraryWorkspace initialLibrary={library} initialInsights={insights} initialCollections={{collections:[],total_count:0}} />);
  fireEvent.change(screen.getByLabelText("本素材说明（可选）"), { target: { value:"待保存文字" } });
  fireEvent.click(screen.getByRole("button", { name:"保存本素材说明" }));
  await screen.findByText("本素材说明已保存。");
  expect(screen.getByLabelText("本素材说明（可选）")).toHaveValue("服务端保存事实");
  view.unmount();
  const reread = {...library,assets:[saved]};
  vi.mocked(getProductionMaterials).mockResolvedValue(reread);
  render(<MaterialLibraryWorkspace initialLibrary={reread} initialInsights={insights} initialCollections={{collections:[],total_count:0}} />);
  expect(screen.getByLabelText("本素材说明（可选）")).toHaveValue("服务端保存事实");
  vi.mocked(updateMaterialNotes).mockResolvedValue({assets:[{...saved,user_description:""}]});
  fireEvent.change(screen.getByLabelText("本素材说明（可选）"), { target: { value:"" } });
  expect(screen.getByRole("button", { name:"保存本素材说明" })).toBeEnabled();
  fireEvent.click(screen.getByRole("button", { name:"保存本素材说明" }));
  await screen.findByText("本素材说明已保存。");
  expect(updateMaterialNotes).toHaveBeenLastCalledWith([{asset_id:"asset",user_description:"",upload_context:"原背景"}]);
  expect(screen.getByLabelText("本素材说明（可选）")).toHaveValue("");
});

it("transcribes a material recording through multipart audio into an editable unsaved note, then saves only that material", async () => {
  const a = { ...taggedAsset, user_description: "A原说明", upload_context: "A原背景" };
  const b = { ...otherAsset, user_description: "B原说明", upload_context: "B原背景" };
  const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ transcript: "测试转写草稿", provider: "unit-test", segments: [] })));
  const actualApi = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  vi.mocked(transcribeMaterialVoiceNote).mockImplementationOnce(actualApi.transcribeMaterialVoiceNote);
  vi.mocked(updateMaterialNotes).mockResolvedValue({ assets: [{ ...a, user_description: "编辑后的说明" }] });
  try {
    render(<MaterialLibraryWorkspace initialLibrary={{ ...twoAssetLibrary, assets: [a, b] }} initialInsights={insights} initialCollections={{ collections: [], total_count: 0 }} />);
    fireEvent.click(screen.getByRole("button", { name: "补口述说明" }));
    expect(recorder.props?.purpose).toBe("material-notes");
    const blob = new Blob(["unit-test-audio"], { type: "audio/webm" });
    await act(async () => { await recorder.props!.onRecorded(blob, { mimeType: blob.type, durationMs: 1000 }); });
    expect(fetchMock).toHaveBeenCalledOnce();
    const [url, options] = fetchMock.mock.calls[0];
    expect(url).toEqual(expect.stringContaining("/api/voice/transcribe"));
    expect(options?.method).toBe("POST");
    expect(options?.body).toBeInstanceOf(FormData);
    expect((options!.body as FormData).get("local_only")).toBe("true");
    const audio = (options!.body as FormData).get("audio") as File;
    expect(audio.type).toBe(blob.type);
    expect(audio.name).toBe("素材口述录音.webm");
    expect(new Headers(options?.headers).has("Content-Type")).toBe(false);
    expect(screen.getByLabelText("本素材说明（可选）")).toHaveValue("测试转写草稿");
    expect(updateMaterialNotes).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText("本素材说明（可选）"), { target: { value: "编辑后的说明" } });
    fireEvent.click(screen.getByRole("button", { name: "保存本素材说明" }));
    await screen.findByText("本素材说明已保存。");
    expect(updateMaterialNotes).toHaveBeenCalledExactlyOnceWith([{ asset_id: a.id, user_description: "编辑后的说明", upload_context: "A原背景" }]);
    fireEvent.click(screen.getByRole("button", { name: /自造素材B\.mp4/ }));
    expect(screen.getByLabelText("本素材说明（可选）")).toHaveValue("B原说明");
    expect(screen.getByText("已保存背景：B原背景")).toBeInTheDocument();
  } finally { fetchMock.mockRestore(); }
});

it("discards late transcription after material switching, cancellation or editing and retains typed fallback on failure", async () => {
  const a = { ...taggedAsset, user_description: "A原说明", upload_context: "A原背景" };
  const b = { ...otherAsset, user_description: "B原说明", upload_context: "B原背景" };
  render(<MaterialLibraryWorkspace initialLibrary={{ ...twoAssetLibrary, assets: [a, b] }} initialInsights={insights} initialCollections={{ collections: [], total_count: 0 }} />);
  const blob = new Blob(["unit-test-audio"], { type: "audio/webm" });
  const metadata = { mimeType: blob.type, durationMs: 1000 };
  for (const action of ["switch", "cancel", "edit"]) {
    let resolve!: (value: string) => void;
    vi.mocked(transcribeMaterialVoiceNote).mockImplementationOnce(() => new Promise<string>(done => { resolve = done; }));
    fireEvent.click(screen.getByRole("button", { name: "补口述说明" }));
    let pending!: void | Promise<void>;
    act(() => { pending = recorder.props!.onRecorded(blob, metadata); });
    const signal = vi.mocked(transcribeMaterialVoiceNote).mock.calls.at(-1)![1]!;
    if (action === "switch") fireEvent.click(screen.getByRole("button", { name: /自造素材B\.mp4/ }));
    else if (action === "cancel") fireEvent.click(screen.getByRole("button", { name: "取消口述说明" }));
    else fireEvent.change(screen.getByLabelText("本素材说明（可选）"), { target: { value: "B手工草稿" } });
    expect(signal.aborted).toBe(true);
    await act(async () => { resolve("不应覆盖的迟到转写"); await pending; });
    expect(screen.getByLabelText("本素材说明（可选）")).toHaveValue(action === "edit" ? "B手工草稿" : "B原说明");
  }
  expect(updateMaterialNotes).not.toHaveBeenCalled();
  vi.mocked(transcribeMaterialVoiceNote).mockRejectedValueOnce(new Error("本地转写不可用"));
  fireEvent.click(screen.getByRole("button", { name: "补口述说明" }));
  await act(async () => { await expect(recorder.props!.onRecorded(blob, metadata)).rejects.toThrow("原文字保留，可重试或直接输入说明"); });
  expect(screen.getByLabelText("本素材说明（可选）")).toHaveValue("B手工草稿");
  vi.mocked(updateMaterialNotes).mockResolvedValue({ assets: [{ ...b, user_description: "B手工草稿" }] });
  fireEvent.click(screen.getByRole("button", { name: "保存本素材说明" }));
  await screen.findByText("本素材说明已保存。");
  expect(updateMaterialNotes).toHaveBeenCalledExactlyOnceWith([{ asset_id: b.id, user_description: "B手工草稿", upload_context: "B原背景" }]);
  fireEvent.click(screen.getByRole("button", { name: /local\.mp4/ }));
  expect(screen.getByLabelText("本素材说明（可选）")).toHaveValue("A原说明");
});

it("keeps the URL tag on the first load, on a new search and on the next page, then clears back to the whole library", async () => {
  const paged = { ...library, total_count: 2, has_more: true };
  vi.mocked(getProductionMaterials).mockResolvedValue(paged);
  render(<MaterialLibraryWorkspace initialLibrary={paged} initialInsights={insights} initialCollections={{collections:[],total_count:0}} initialTag="产品演示" />);
  expect(screen.getByText("标签：产品演示")).toBeInTheDocument();
  // 清除走 URL，不在客户端另存一份标签状态，避免清除后仍带着旧筛选
  expect(screen.getByRole("link", { name: "清除标签筛选" })).toHaveAttribute("href", "/library");

  fireEvent.change(screen.getByLabelText("搜索素材"), { target: { value: "门店" } });
  await waitFor(() => expect(getProductionMaterials).toHaveBeenCalledWith(expect.objectContaining({ offset: 0, tag: "产品演示" })));

  fireEvent.click(screen.getByRole("button", { name: /载入更多/ }));
  await waitFor(() => expect(getProductionMaterials).toHaveBeenCalledWith(expect.objectContaining({ offset: 1, tag: "产品演示" })));
});

it("keeps the URL tag when the background refresh re-reads the loaded pages", async () => {
  vi.mocked(getMaterialIntakeStatus).mockResolvedValue({ ...intakeReady, bound_to_current_workspace: true });
  vi.useFakeTimers();
  try {
    render(<MaterialLibraryWorkspace initialLibrary={library} initialInsights={insights} initialCollections={{collections:[],total_count:0}} initialTag="产品演示" />);
    await act(async () => { await Promise.resolve(); });
    vi.mocked(getProductionMaterials).mockClear();
    await act(async () => { await vi.advanceTimersByTimeAsync(15000); });
    expect(getProductionMaterials).toHaveBeenCalledWith(expect.objectContaining({ offset: 0, tag: "产品演示" }));
  } finally {
    vi.useRealTimers();
  }
});

// 128 业务名称关联：接在 199 的 URL 标签筛选用例之后，两组断言各自独立。
const product = { id:"bo-product", kind:"product" as const, name:"曜核一体机" };
const dish = { id:"bo-dish", kind:"dish" as const, name:"红烧肉" };
const entries = { items:[product, dish], total_count:2 };

it("reads the workspace name entries and the current material's saved associations", async () => {
  vi.mocked(getMaterialBusinessObjects).mockResolvedValue(entries);
  vi.mocked(getAssetBusinessObjects).mockResolvedValue({ items:[dish], total_count:1 });
  render(<MaterialLibraryWorkspace initialLibrary={library} initialInsights={insights} initialCollections={{collections:[],total_count:0}} />);

  await waitFor(() => expect(screen.getByRole("checkbox",{name:/红烧肉/})).toBeChecked());
  expect(screen.getByRole("checkbox",{name:/曜核一体机/})).not.toBeChecked();
  expect(getAssetBusinessObjects).toHaveBeenCalledWith("asset");
  // 读回来的关联就是已保存的选择，用户还没改动就没什么可保存的。
  expect(screen.getByRole("button",{name:"保存关联到当前素材"})).toBeDisabled();
});

it("creates a name entry but writes it to the material only after an explicit save", async () => {
  const person = { id:"bo-person", kind:"person" as const, name:"老张" };
  vi.mocked(getMaterialBusinessObjects).mockResolvedValue(entries);
  vi.mocked(createMaterialBusinessObject).mockResolvedValue(person);
  vi.mocked(saveAssetBusinessObjects).mockResolvedValue({ items:[person], total_count:1 });
  render(<MaterialLibraryWorkspace initialLibrary={library} initialInsights={insights} initialCollections={{collections:[],total_count:0}} />);
  await waitFor(() => expect(screen.getByRole("checkbox",{name:/曜核一体机/})).toBeEnabled());

  fireEvent.change(screen.getByLabelText("名称"), { target: { value:"老张" } });
  await chooseSelectValue(screen.getByLabelText("类型"), "person");
  fireEvent.click(screen.getByRole("button",{name:"新增并选中"}));

  expect(createMaterialBusinessObject).toHaveBeenCalledWith({ kind:"person", name:"老张" });
  // 新增只是条目本身：还没写进任何素材。
  expect(saveAssetBusinessObjects).not.toHaveBeenCalled();
  await waitFor(() => expect(screen.getByRole("checkbox",{name:/老张/})).toBeChecked());

  fireEvent.click(screen.getByRole("button",{name:"保存关联到当前素材"}));
  await screen.findByText("已保存 1 条名称关联。");
  expect(saveAssetBusinessObjects).toHaveBeenCalledWith("asset", ["bo-person"]);
});

it("does not let a late association read overwrite the material selected after it", async () => {
  vi.mocked(getProductionMaterials).mockResolvedValue(twoAssetLibrary);
  vi.mocked(getMaterialBusinessObjects).mockResolvedValue(entries);
  let resolveFirst!: (value: { items: typeof product[]; total_count: number }) => void;
  vi.mocked(getAssetBusinessObjects).mockImplementation((assetId: string) => assetId === "asset"
    ? new Promise(resolve => { resolveFirst = resolve; })
    : Promise.resolve({ items:[dish], total_count:1 }));
  render(<MaterialLibraryWorkspace initialLibrary={twoAssetLibrary} initialInsights={insights} initialCollections={{collections:[],total_count:0}} />);

  fireEvent.click(screen.getByRole("button",{ name:/自造素材B\.mp4/ }));
  await waitFor(() => expect(screen.getByRole("checkbox",{name:/红烧肉/})).toBeChecked());
  // 第一条素材的读取这时才回来，不能盖到已经切过去的这条素材上。
  resolveFirst({ items:[product], total_count:1 });
  await waitFor(() => expect(screen.getByRole("button",{name:"保存关联到当前素材"})).toBeDisabled());
  expect(screen.getByRole("checkbox",{name:/曜核一体机/})).not.toBeChecked();
  expect(screen.getByRole("checkbox",{name:/红烧肉/})).toBeChecked();
});

it("saves the association without touching the material's note or upload context", async () => {
  const noted = { ...asset, user_description:"我的补充说明", upload_context:"这批是产品演示" };
  const notedLibrary = { ...library, assets:[noted] };
  vi.mocked(getProductionMaterials).mockResolvedValue(notedLibrary);
  vi.mocked(getMaterialBusinessObjects).mockResolvedValue(entries);
  vi.mocked(saveAssetBusinessObjects).mockResolvedValue({ items:[product], total_count:1 });
  render(<MaterialLibraryWorkspace initialLibrary={notedLibrary} initialInsights={insights} initialCollections={{collections:[],total_count:0}} />);
  await waitFor(() => expect(screen.getByRole("checkbox",{name:/曜核一体机/})).toBeEnabled());

  fireEvent.click(screen.getByRole("checkbox",{name:/曜核一体机/}));
  fireEvent.click(screen.getByRole("button",{name:"保存关联到当前素材"}));
  await screen.findByText("已保存 1 条名称关联。");

  expect(updateMaterialNotes).not.toHaveBeenCalled();
  expect(screen.getByLabelText("本素材说明（可选）")).toHaveValue("我的补充说明");
  expect(screen.getByText("已保存背景：这批是产品演示")).toBeInTheDocument();
});

it("keeps a late create/save failure on the material that asked, not on the one now shown", async () => {
  vi.mocked(getProductionMaterials).mockResolvedValue(twoAssetLibrary);
  vi.mocked(getMaterialBusinessObjects).mockResolvedValue(entries);
  vi.mocked(getAssetBusinessObjects).mockResolvedValue({ items:[dish], total_count:1 });
  let rejectCreate!: (reason: unknown) => void;
  vi.mocked(createMaterialBusinessObject).mockImplementation(() => new Promise((_, reject) => { rejectCreate = reject; }));
  render(<MaterialLibraryWorkspace initialLibrary={twoAssetLibrary} initialInsights={insights} initialCollections={{collections:[],total_count:0}} />);
  await waitFor(() => expect(screen.getByRole("checkbox",{name:/红烧肉/})).toBeChecked());

  fireEvent.change(screen.getByLabelText("名称"), { target: { value:"老张" } });
  fireEvent.click(screen.getByRole("button",{name:"新增并选中"}));
  expect(createMaterialBusinessObject).toHaveBeenCalled();

  // 请求还挂在素材 A 上时切到 B。
  fireEvent.click(screen.getByRole("button",{ name:/自造素材B\.mp4/ }));
  await waitFor(() => expect(screen.getByRole("checkbox",{name:/红烧肉/})).toBeChecked());

  rejectCreate(new Error("名称条目新增失败，请重试"));
  // B 的读取已经完成，这条失败如果照旧 setObjectError 会显示在 B 上。
  await waitFor(() => expect(screen.getByRole("checkbox",{name:/红烧肉/})).toBeEnabled());
  expect(screen.queryByText(/名称条目新增失败/)).not.toBeInTheDocument();
});

// 132 媒体类型筛选：接在 128 的名称关联用例之后，两组断言各自独立。
const typeGroup = () => screen.getByRole("combobox", { name: "媒体类型筛选" });
const typeButton = (label: string) => screen.getByRole("option", { name: label === "全部" ? "全部类型" : label });
const activeType = () => ({ all: "全部", video: "视频", audio: "音频", image: "图片" })[typeGroup().getAttribute("data-selected-value") as "all" | "video" | "audio" | "image"];

it("switches the flat media types and starts the new type from the first page", async () => {
  const paged = { ...library, total_count: 2, has_more: true };
  vi.mocked(getProductionMaterials).mockResolvedValue(paged);
  render(<MaterialLibraryWorkspace initialLibrary={paged} initialInsights={insights} initialCollections={{collections:[],total_count:0}} />);

  await inspectSelectOptions(typeGroup(),()=>expect(["全部", "视频", "音频", "图片"].map(label => typeButton(label).textContent)).toEqual(["全部类型", "视频", "音频", "图片"]));
  expect(activeType()).toBe("全部");

  // 先翻到第二页，再切类型：新类型必须从第一页开始，不能接着旧的页码。
  fireEvent.click(screen.getByRole("button", { name: /载入更多/ }));
  await waitFor(() => expect(getProductionMaterials).toHaveBeenCalledWith(expect.objectContaining({ offset: 1 })));

  await chooseSelectValue(typeGroup(), "audio");
  expect(activeType()).toBe("音频");
  await waitFor(() => expect(getProductionMaterials).toHaveBeenCalledWith(expect.objectContaining({ offset: 0, mediaType: "audio" })));

  // 类型和审核状态是各自独立的一条条件，叠上以后类型不能被冲掉。
  await chooseSelectValue(screen.getByRole("combobox", { name: "审核状态筛选" }), "approved");
  await waitFor(() => expect(getProductionMaterials).toHaveBeenCalledWith(expect.objectContaining({ offset: 0, mediaType: "audio", reviewStatus: "approved" })));

  await chooseSelectValue(typeGroup(), "all");
  await waitFor(() => expect(getProductionMaterials).toHaveBeenCalledWith(expect.objectContaining({ offset: 0, mediaType: "all" })));
});

it("keeps the chosen media type in the URL and restores it after a refresh", async () => {
  try {
    vi.mocked(getProductionMaterials).mockResolvedValue(library);
    const first = render(<MaterialLibraryWorkspace initialLibrary={library} initialInsights={insights} initialCollections={{collections:[],total_count:0}} />);

    await chooseSelectValue(typeGroup(), "video");
    expect(window.location.search).toBe("?media_type=video");
    first.unmount();

    // 刷新：服务端渲染的第一页不带类型，读回地址栏后要按这个类型重新取第一页。
    vi.mocked(getProductionMaterials).mockClear();
    render(<MaterialLibraryWorkspace initialLibrary={library} initialInsights={insights} initialCollections={{collections:[],total_count:0}} />);
    await waitFor(() => expect(getProductionMaterials).toHaveBeenCalledWith(expect.objectContaining({ offset: 0, mediaType: "video" })));
    expect(activeType()).toBe("视频");

    await chooseSelectValue(typeGroup(), "all");
    expect(window.location.search).toBe("");
  } finally {
    window.history.replaceState(null, "", "/library");
  }
});

it("ignores a media type in the URL that the list does not know", async () => {
  window.history.replaceState(null, "", "/library?media_type=text");
  try {
    render(<MaterialLibraryWorkspace initialLibrary={library} initialInsights={insights} initialCollections={{collections:[],total_count:0}} />);
    await waitFor(() => expect(activeType()).toBe("全部"));
    expect(getProductionMaterials).not.toHaveBeenCalledWith(expect.objectContaining({ mediaType: "text" }));
  } finally {
    window.history.replaceState(null, "", "/library");
  }
});

it("shows what used the selected segment and keeps the current version labelled", async () => {
  vi.mocked(getProductionMaterials).mockResolvedValue(taggedLibrary);
  vi.mocked(getAssetSegmentUsageRecords).mockResolvedValue({ total_count: 2, items: [
    { plan_id: "plan-1", clip_id: "clip-1", used_at: "2026-10-03T01:32:15Z", role: "a_roll",
      manually_selected: true, resolution: "current", timeline_version_id: "timeline-current",
      task: { id: "task-1", name: "第一条成片", stage: "review", status: "waiting_for_user", current_timeline_version_id: "timeline-current" } },
    { plan_id: "plan-2", clip_id: "clip-2", used_at: "2026-10-03T02:29:22Z", role: null,
      manually_selected: null, resolution: "unknown", timeline_version_id: null, task: null },
  ] });
  render(<MaterialLibraryWorkspace initialLibrary={taggedLibrary} initialInsights={insights} initialCollections={{collections:[],total_count:0}} />);
  await waitFor(() => expect(getAssetSegmentUsageRecords).toHaveBeenCalledWith("asset","segment"));
  const taskLink = await screen.findByRole("link", { name: /打开任务：第一条成片/ });
  expect(taskLink).toHaveAttribute("href", "/tasks/task-1");
  expect(screen.getByText(/第一条成片 · 人物主镜头/)).toBeInTheDocument();
  expect(screen.getByText(/关联任务当前时间线（当次使用版本未记录）/)).toBeInTheDocument();
  expect(screen.getByText("时间线版本未记录")).toBeInTheDocument();
  expect(screen.getByText(/关联任务未记录/)).toBeInTheDocument();
});

it("keeps the previous segment's records off screen while the next one loads", async () => {
  vi.mocked(getProductionMaterials).mockResolvedValue({ ...library, assets: [
    { ...taggedAsset, segments: [segment, { ...segment, id: "segment-2", description: "另一段画面" }] },
  ] });
  const pending = new Promise<{ items: never[]; total_count: number }>(() => {});
  vi.mocked(getAssetSegmentUsageRecords).mockImplementation((_assetId, segmentId) =>
    segmentId === "segment"
      ? Promise.resolve({ total_count: 1, items: [{
          plan_id: "plan-1", clip_id: "clip-1", used_at: "2026-10-03T01:32:15Z", role: "b_roll",
          manually_selected: true, resolution: "unknown", timeline_version_id: null,
          task: { id: "task-1", name: "第一条成片", stage: "review", status: "waiting_for_user", current_timeline_version_id: null },
        }] })
      : pending);
  render(<MaterialLibraryWorkspace initialLibrary={{ ...library, assets: [{ ...taggedAsset, segments: [segment, { ...segment, id: "segment-2", description: "另一段画面" }] }] }} initialInsights={insights} initialCollections={{collections:[],total_count:0}} />);
  await screen.findByRole("link", { name: /打开任务：第一条成片/ });

  fireEvent.click(screen.getAllByRole("button").find(button => button.textContent?.includes("另一段画面"))!);

  // 第二个片段还没读到时，不能看到上一片段的任务名或计划。
  expect(screen.queryByText(/第一条成片/)).not.toBeInTheDocument();
  expect(screen.queryByText(/plan-1/)).not.toBeInTheDocument();
  expect(screen.getByText("正在读取使用记录……")).toBeInTheDocument();
});


it("opens intake only from the toolbar and closes the sidecard without losing the collection", async () => {
  vi.mocked(getMaterialIntakeStatus).mockResolvedValue(boundReadyIntake());
  render(<MaterialLibraryWorkspace initialLibrary={library} initialInsights={insights} initialCollections={{collections:[],total_count:0}} />);
  expect(screen.queryByRole("dialog", { name:"导入素材" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name:"导入本地素材" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name:"导入素材" }));
  await screen.findByRole("button", { name:"导入本地素材" });
  fireEvent.click(within(screen.getByRole("dialog", { name:"导入素材" })).getByRole("button", { name:"关闭卡片" }));
  expect(screen.queryByRole("dialog", { name:"导入素材" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name:/local\.mp4/ })).toBeInTheDocument();
});

it("retains the usable collection on explicit refresh failure and retries the real query", async () => {
  vi.mocked(getProductionMaterials).mockRejectedValueOnce(new Error("素材列表暂不可用"));
  render(<MaterialLibraryWorkspace initialLibrary={library} initialInsights={insights} initialCollections={{collections:[],total_count:0}} />);
  fireEvent.click(screen.getByRole("button", { name:"刷新素材列表" }));
  await screen.findByText("素材列表暂不可用");
  expect(screen.getByRole("button", { name:/local\.mp4/ })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name:"刷新素材列表" }));
  await waitFor(() => expect(getProductionMaterials).toHaveBeenCalledTimes(2));
  await waitFor(() => expect(screen.queryByText("素材列表暂不可用")).not.toBeInTheDocument());
});

it("does not let a late explicit refresh overwrite the active search", async () => {
  let resolveRefresh!: (value: typeof library) => void;
  vi.mocked(getProductionMaterials).mockImplementationOnce(() => new Promise(resolve => { resolveRefresh = resolve; }));
  vi.mocked(getProductionMaterials).mockResolvedValue({ ...library, assets:[], total_count:0 });
  render(<MaterialLibraryWorkspace initialLibrary={library} initialInsights={insights} initialCollections={{collections:[],total_count:0}} />);
  fireEvent.click(screen.getByRole("button", { name:"刷新素材列表" }));
  fireEvent.change(screen.getByRole("textbox", { name:"搜索素材" }), { target:{ value:"没有结果" } });
  await screen.findByText("没有匹配的素材");
  await act(async () => resolveRefresh(library));
  expect(screen.getByText("没有匹配的素材")).toBeInTheDocument();
});

it("reopens the actual original media after a visible preview failure", async () => {
  render(<MaterialLibraryWorkspace initialLibrary={library} initialInsights={insights} initialCollections={{collections:[],total_count:0}} />);
  const failedPreview = screen.getByLabelText("原素材预览");
  fireEvent.error(failedPreview);
  expect(screen.getByRole("alert")).toHaveTextContent("原素材暂时无法预览");
  fireEvent.click(screen.getByRole("button", { name:"重新读取预览" }));
  const retryPreview = screen.getByLabelText("原素材预览");
  expect(retryPreview).not.toBe(failedPreview);
  expect(retryPreview).toHaveAttribute("src", "/api/production/materials/asset/media");
  expect(screen.queryByText(/原素材暂时无法预览/)).not.toBeInTheDocument();
});

it("retries a failed usage read for the same real segment", async () => {
  vi.mocked(getAssetSegmentUsageRecords).mockRejectedValueOnce(new Error("使用记录暂不可用"));
  render(<MaterialLibraryWorkspace initialLibrary={taggedLibrary} initialInsights={insights} initialCollections={{collections:[],total_count:0}} />);
  await screen.findByText("使用记录暂不可用");
  fireEvent.click(screen.getByRole("button", { name:"重新读取使用记录" }));
  await screen.findByText("这个片段还没有被任何成片使用过。");
  expect(getAssetSegmentUsageRecords).toHaveBeenLastCalledWith("asset", "segment");
});


it("publishes only explicit material references with actual versions and leaves cancellation cleared", async () => {
  const fractional = { ...taggedAsset, segments:[{ ...segment, start_seconds:1.25, end_seconds:3.75, updated_at:"2026-10-10T02:00:00Z" }] };
  function ReferenceProbe() {
    const reference = useAssistantMaterialReference();
    return <output aria-label="共享素材引用">{JSON.stringify(reference?.reference ?? null)}</output>;
  }
  render(<AssistantConversationProvider><MaterialLibraryWorkspace initialLibrary={{...library, assets:[fractional]}} initialInsights={insights} initialCollections={{collections:[],total_count:0}} /><ReferenceProbe /></AssistantConversationProvider>);
  expect(screen.getByLabelText("共享素材引用")).toHaveTextContent("null");
  fireEvent.click(screen.getByRole("button", { name:/local\.mp4/ }));
  await waitFor(() => expect(JSON.parse(screen.getByLabelText("共享素材引用").textContent!)).toEqual({label:"整条素材 · local.mp4", selection:{scope:"asset", asset_id:"asset", expected_updated_at:fractional.updated_at}}));
  fireEvent.click(screen.getByRole("button", { name:/0:01.25.*0:03.75/ }));
  await waitFor(() => expect(JSON.parse(screen.getByLabelText("共享素材引用").textContent!)).toEqual({label:"local.mp4 · 片段 0:01.25–0:03.75", selection:{scope:"segment", asset_id:"asset", segment_id:"segment", expected_updated_at:fractional.updated_at, expected_segment_updated_at:"2026-10-10T02:00:00Z"}}));
  fireEvent.click(screen.getByRole("button", { name:"取消素材引用" }));
  await waitFor(() => expect(screen.getByLabelText("共享素材引用")).toHaveTextContent("null"));
  fireEvent.click(screen.getByRole("button", { name:"对象列表" }));
  expect(screen.getByLabelText("共享素材引用")).toHaveTextContent("null");
  const refreshed = { ...fractional, updated_at:"2026-10-10T03:00:00Z", segments:[{...fractional.segments[0], updated_at:"2026-10-10T03:00:00Z"}] };
  vi.mocked(getProductionMaterials).mockResolvedValue({ ...library, assets:[refreshed] });
  fireEvent.click(screen.getByRole("button", { name:"刷新素材列表" }));
  await waitFor(() => expect(screen.getByRole("button", { name:"刷新素材列表" })).not.toBeDisabled());
  expect(screen.getByLabelText("共享素材引用")).toHaveTextContent("null");
  fireEvent.click(screen.getByRole("button", { name:/0:01.25.*0:03.75/ }));
  await waitFor(() => expect(JSON.parse(screen.getByLabelText("共享素材引用").textContent!).selection).toEqual({scope:"segment", asset_id:"asset", segment_id:"segment", expected_updated_at:refreshed.updated_at, expected_segment_updated_at:refreshed.segments[0].updated_at}));
});

it("keeps observation tag suggestions separate until explicit adoption returns the same saved segment", async () => {
  const observed: MaterialSegment = { ...segment, description: "", user_tags: ["旧标签"], understanding: {
    status: "ready", fingerprint: "test", model_id: "test", sample_times: [1.2, 2.8], summary: "戴眼镜的人走过街道",
    facts: [{ kind: "action", label: "人物走路", text: "人物从街道左边走向右边", sample_times: [1.2, 2.8] }],
    unknowns: ["身份不可确认"], ocr_status: "blocked", ocr_model_id: "", ocr_frames: [], message: "",
  }};
  const observedAsset = { ...taggedAsset, segments: [observed] };
  const observedLibrary = { ...library, assets: [observedAsset] };
  vi.mocked(getProductionMaterials).mockResolvedValue(observedLibrary);
  vi.mocked(updateProductionMaterialSegment).mockResolvedValue({ ...observedAsset, segments: [{ ...observed, user_tags: ["旧标签", "人物走路"] }] });
  render(<MaterialLibraryWorkspace initialLibrary={observedLibrary} initialInsights={insights} initialCollections={{collections:[],total_count:0}} />);
  expect(screen.getByText("标签建议 · 1 项待采纳")).toBeInTheDocument();
  expect(screen.getByText("动作观察 · 采样 0:01.2、0:02.8")).toBeInTheDocument();
  expect(updateProductionMaterialSegment).not.toHaveBeenCalled();
  expect(screen.queryByLabelText("移除标签 人物走路")).not.toBeInTheDocument();
  fireEvent.click(screen.getByText("标签建议 · 1 项待采纳"));
  fireEvent.click(screen.getByRole("button", { name: "采纳标签 人物走路" }));
  await waitFor(() => expect(screen.getByLabelText("移除标签 人物走路")).toBeInTheDocument());
  expect(updateProductionMaterialSegment).toHaveBeenCalledWith("asset", "segment", expect.objectContaining({ user_tags: ["旧标签", "人物走路"] }));
  expect(screen.queryByText("标签建议 · 1 项待采纳")).not.toBeInTheDocument();
});

function materialListCards() {
  return Array.from(document.querySelectorAll(".library-grid .library-card strong")).map(element => element.getAttribute("title"));
}
function materialListDeferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (cause: Error) => void;
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
}

it.each(["success", "failure"] as const)("material list rejects an old page %s after a newer query has been accepted", async outcome => {
  const old = materialListDeferred<typeof library>();
  const first = { ...asset, id: "old-first", file_name: "old-first.mp4" };
  const newer = { ...asset, id: "new-target", file_name: "new-target.mp4" };
  vi.mocked(getProductionMaterials).mockReset();
  vi.mocked(getProductionMaterials).mockReturnValueOnce(old.promise).mockResolvedValue({ ...library, assets: [newer], limit: 1 });
  render(<MaterialLibraryWorkspace initialLibrary={{ ...library, assets: [first], limit: 1, total_count: 3, has_more: true }} initialInsights={insights} initialCollections={{ collections: [], total_count: 0 }} />);
  fireEvent.click(screen.getByRole("button", { name: /载入更多/ }));
  fireEvent.change(screen.getByRole("textbox", { name: "搜索素材" }), { target: { value: "new" } });
  await waitFor(() => expect(materialListCards()).toEqual(["new-target.mp4"]));
  await act(async () => {
    if (outcome === "success") old.resolve({ ...library, assets: [{ ...asset, id: "old-page", file_name: "old-page.mp4" }], total_count: 3 });
    else old.reject(new Error("old page failure"));
  });
  expect(materialListCards()).toEqual(["new-target.mp4"]);
  expect(screen.queryByText("old page failure")).not.toBeInTheDocument();
});

it("material list does not page a new query with the old collection count before its first read", () => {
  vi.mocked(getProductionMaterials).mockReset();
  vi.mocked(getProductionMaterials).mockReturnValue(new Promise(() => {}));
  render(<MaterialLibraryWorkspace initialLibrary={{ ...library, assets: [asset, otherAsset], limit: 2, total_count: 5, has_more: true }} initialInsights={insights} initialCollections={{ collections: [], total_count: 0 }} />);
  fireEvent.change(screen.getByRole("textbox", { name: "搜索素材" }), { target: { value: "new" } });
  fireEvent.click(screen.getByRole("button", { name: /载入更多/ }));
  expect(vi.mocked(getProductionMaterials).mock.calls.some(([input]) => input?.query === "new" && (input.offset ?? 0) > 0)).toBe(false);
});

it("material list reopening identical parameters rejects old completion and still accepts the new first page", async () => {
  const old = materialListDeferred<typeof library>();
  const middle = materialListDeferred<typeof library>();
  const latest = materialListDeferred<typeof library>();
  vi.mocked(getProductionMaterials).mockReset();
  vi.mocked(getProductionMaterials).mockReturnValueOnce(old.promise).mockReturnValueOnce(middle.promise).mockReturnValueOnce(latest.promise);
  render(<MaterialLibraryWorkspace initialLibrary={{ ...library, limit: 1, total_count: 3, has_more: true }} initialInsights={insights} initialCollections={{ collections: [], total_count: 0 }} />);
  fireEvent.click(screen.getByRole("button", { name: /载入更多/ }));
  const input = screen.getByRole("textbox", { name: "搜索素材" });
  fireEvent.change(input, { target: { value: "middle" } });
  await waitFor(() => expect(getProductionMaterials).toHaveBeenCalledTimes(2));
  fireEvent.change(input, { target: { value: "" } });
  await waitFor(() => expect(getProductionMaterials).toHaveBeenCalledTimes(3));
  await act(async () => old.resolve({ ...library, assets: [otherAsset], total_count: 3 }));
  expect(materialListCards()).toEqual(["local.mp4"]);
  expect(screen.getByRole("button", { name: /载入更多/ })).toBeDisabled();
  await act(async () => latest.resolve({ ...library, assets: [{ ...asset, file_name: "reopened-head.mp4" }], total_count: 3, limit: 1, has_more: true }));
  expect(materialListCards()).toEqual(["reopened-head.mp4"]);
  expect(screen.getByRole("button", { name: /载入更多/ })).toBeEnabled();
  await act(async () => middle.reject(new Error("middle stale error")));
  expect(screen.queryByText("middle stale error")).not.toBeInTheDocument();
});

it("material list normal paging keeps tag count deduplication selection and unsaved inputs", async () => {
  const pending = materialListDeferred<typeof taggedLibrary>();
  vi.mocked(getProductionMaterials).mockReset();
  vi.mocked(getProductionMaterials).mockReturnValue(pending.promise);
  const first = { ...taggedAsset, user_tags: ["fixture-tag"] };
  const second = { ...otherAsset, user_tags: ["fixture-tag"] };
  const third = { ...asset, id: "asset-c", file_name: "third.mp4", user_tags: ["fixture-tag"], segments: [{ ...segment, id: "segment-c", asset_id: "asset-c" }] };
  render(<MaterialLibraryWorkspace initialLibrary={{ ...library, assets: [first, second], total_count: 5, limit: 2, has_more: true }} initialInsights={insights} initialCollections={{ collections: [], total_count: 0 }} initialTag="fixture-tag" />);
  fireEvent.click(screen.getByRole("button", { name: /local\.mp4/ }));
  fireEvent.change(screen.getByLabelText("片段描述"), { target: { value: "保留片段输入" } });
  fireEvent.change(screen.getByLabelText("本素材说明（可选）"), { target: { value: "保留素材笔记" } });
  fireEvent.click(screen.getByRole("button", { name: /载入更多/ }));
  const input = screen.getByRole("textbox", { name: "搜索素材" });
  input.focus();
  await act(async () => pending.resolve({ ...library, assets: [second, third], total_count: 5, limit: 2, offset: 2, has_more: true }));
  expect(getProductionMaterials).toHaveBeenCalledWith(expect.objectContaining({ limit: 2, offset: 2, tag: "fixture-tag" }));
  expect(materialListCards()).toEqual(["local.mp4", "自造素材B.mp4", "third.mp4"]);
  expect(screen.getByRole("button", { name: /载入更多.*2/ })).toBeEnabled();
  expect(screen.getByLabelText("片段描述")).toHaveValue("保留片段输入");
  expect(screen.getByLabelText("本素材说明（可选）")).toHaveValue("保留素材笔记");
  expect(input).toHaveFocus();
});

it("material list background head read preserves tag selection and focused unsaved inputs", async () => {
  vi.useFakeTimers();
  const visibility = vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
  vi.mocked(getMaterialIntakeStatus).mockResolvedValue({ ...intakeReady, bound_to_current_workspace: true, bound_directory: intakeReady.configured_directory });
  vi.mocked(getProductionMaterials).mockReset();
  vi.mocked(getProductionMaterials).mockResolvedValue({ ...taggedLibrary, assets: [{ ...taggedAsset, user_description: "服务端说明" }], limit: 1 });
  try {
    render(<MaterialLibraryWorkspace initialLibrary={{ ...taggedLibrary, limit: 1 }} initialInsights={insights} initialCollections={{ collections: [], total_count: 0 }} initialTag="fixture-tag" />);
    await act(async () => {});
    fireEvent.click(screen.getByRole("button", { name: /local\.mp4/ }));
    fireEvent.change(screen.getByLabelText("片段描述"), { target: { value: "背景回读保留片段" } });
    const note = screen.getByLabelText("本素材说明（可选）");
    fireEvent.change(note, { target: { value: "背景回读保留笔记" } });
    note.focus();
    await act(async () => { await vi.advanceTimersByTimeAsync(15000); });
    expect(getProductionMaterials).toHaveBeenCalledWith(expect.objectContaining({ offset: 0, tag: "fixture-tag" }));
    expect(screen.getByLabelText("片段描述")).toHaveValue("背景回读保留片段");
    expect(note).toHaveValue("背景回读保留笔记");
    expect(note).toHaveFocus();
  } finally { visibility.mockRestore(); vi.useRealTimers(); }
});

async function beginMaterialScan() {
  fireEvent.click(screen.getByRole("button", { name: "导入素材" }));
  fireEvent.click(await screen.findByRole("button", { name: "立即检查" }));
  fireEvent.click(screen.getByRole("button", { name: "关闭卡片" }));
}
function bindMaterialScan() {
  vi.mocked(getMaterialIntakeStatus).mockResolvedValue({ ...intakeReady, bound_to_current_workspace: true, bound_directory: intakeReady.configured_directory });
}

it("material scan rereads the reset query with its retained tag/media and keeps explicit drafts", async () => {
  window.history.replaceState({}, "", "/library?tag=wanted&media_type=audio");
  try {
    bindMaterialScan();
    const wanted = { ...taggedAsset, file_name: "old-wanted.wav", media_type: "audio" as const, review_status: "raw" as const, user_tags: ["wanted"] };
    vi.mocked(getProductionMaterials).mockReset().mockResolvedValue({ ...library, assets: [wanted] });
    vi.mocked(scanMaterialIntake).mockResolvedValue({ ...library, assets: [{ ...asset, id: "unrelated", file_name: "unrelated.mp4" }] });
    render(<MaterialLibraryWorkspace initialLibrary={{ ...library, assets: [wanted] }} initialInsights={insights} initialCollections={{ collections: [], total_count: 0 }} initialTag="wanted" />);
    await waitFor(() => expect(getProductionMaterials).toHaveBeenCalledWith(expect.objectContaining({ mediaType: "audio" })));
    fireEvent.change(screen.getByRole("textbox", { name: "搜索素材" }), { target: { value: "old" } });
    await waitFor(() => expect(getProductionMaterials).toHaveBeenCalledWith(expect.objectContaining({ query: "old" })));
    await waitFor(() => expect(screen.queryByText("正在更新素材列表…")).not.toBeInTheDocument());
    await chooseSelectValue(screen.getByRole("combobox", { name: "审核状态筛选" }), "raw");
    await waitFor(() => expect(getProductionMaterials).toHaveBeenCalledWith(expect.objectContaining({ reviewStatus: "raw" })));
    await waitFor(() => expect(screen.queryByText("正在更新素材列表…")).not.toBeInTheDocument());
    fireEvent.click(document.querySelector(".library-card")!);
    fireEvent.change(screen.getByLabelText("片段描述"), { target: { value: "扫描保留片段草稿" } });
    fireEvent.change(screen.getByLabelText("本素材说明（可选）"), { target: { value: "扫描保留笔记草稿" } });
    const before = vi.mocked(getProductionMaterials).mock.calls.length;
    await beginMaterialScan();
    await waitFor(() => expect(getProductionMaterials).toHaveBeenCalledTimes(before + 1));
    expect(getProductionMaterials).toHaveBeenLastCalledWith(expect.objectContaining({ offset: 0, query: "", reviewStatus: "all", tag: "wanted", mediaType: "audio" }));
    await waitFor(() => expect(screen.queryByText("正在更新素材列表…")).not.toBeInTheDocument());
    expect(materialListCards()).toEqual(["old-wanted.wav"]);
    expect(screen.getByRole("textbox", { name: "搜索素材" })).toHaveValue("");
    expect(screen.getByLabelText("片段描述")).toHaveValue("扫描保留片段草稿");
    expect(screen.getByLabelText("本素材说明（可选）")).toHaveValue("扫描保留笔记草稿");
  } finally { window.history.replaceState({}, "", "/library"); }
});

it("material scan preserves the confirmed collection when its scoped first page fails", async () => {
  bindMaterialScan();
  vi.mocked(getProductionMaterials).mockReset().mockRejectedValue(new Error("扫描后的列表读取失败"));
  vi.mocked(scanMaterialIntake).mockResolvedValue({ ...library, assets: [{ ...asset, id: "unrelated", file_name: "unrelated.mp4" }] });
  render(<MaterialLibraryWorkspace initialLibrary={library} initialInsights={insights} initialCollections={{ collections: [], total_count: 0 }} initialTag="wanted" />);
  await beginMaterialScan();
  await screen.findByText("扫描后的列表读取失败");
  expect(materialListCards()).toEqual(["local.mp4"]);
  expect(screen.getByText(/已载入 1 \/ 1/)).toBeInTheDocument();
});

it.each(["success", "failure"] as const)("material scan discards an old intake %s after a later query has been accepted", async outcome => {
  bindMaterialScan();
  const old = materialListDeferred<typeof library>();
  vi.mocked(scanMaterialIntake).mockReturnValueOnce(old.promise);
  vi.mocked(getProductionMaterials).mockReset().mockResolvedValue({ ...library, assets: [{ ...asset, id: "new", file_name: "new.mp4" }] });
  render(<MaterialLibraryWorkspace initialLibrary={library} initialInsights={insights} initialCollections={{ collections: [], total_count: 0 }} />);
  await beginMaterialScan();
  const search = screen.getByRole("textbox", { name: "搜索素材" });
  fireEvent.change(search, { target: { value: "new" } }); search.focus();
  await waitFor(() => expect(materialListCards()).toEqual(["new.mp4"]));
  await act(async () => { if (outcome === "success") old.resolve(library); else old.reject(new Error("旧扫描失败")); });
  expect(materialListCards()).toEqual(["new.mp4"]);
  expect(search).toHaveValue("new"); expect(search).toHaveFocus();
  expect(screen.queryByText("旧扫描失败")).not.toBeInTheDocument();
  expect(getProductionMaterials).toHaveBeenCalledTimes(1);
});

it("material scan cannot reset a reopened identical query after its old intake completes", async () => {
  bindMaterialScan();
  const old = materialListDeferred<typeof library>();
  vi.mocked(scanMaterialIntake).mockReturnValueOnce(old.promise);
  vi.mocked(getProductionMaterials).mockReset().mockResolvedValueOnce({ ...library, assets: [{ ...asset, id: "middle", file_name: "middle.mp4" }] })
    .mockResolvedValue({ ...library, assets: [{ ...asset, id: "reopened", file_name: "reopened.mp4" }] });
  render(<MaterialLibraryWorkspace initialLibrary={library} initialInsights={insights} initialCollections={{ collections: [], total_count: 0 }} />);
  await beginMaterialScan();
  const search = screen.getByRole("textbox", { name: "搜索素材" });
  fireEvent.change(search, { target: { value: "middle" } });
  await waitFor(() => expect(materialListCards()).toEqual(["middle.mp4"]));
  fireEvent.change(search, { target: { value: "" } });
  await waitFor(() => expect(materialListCards()).toEqual(["reopened.mp4"]));
  await act(async () => old.resolve(library));
  expect(materialListCards()).toEqual(["reopened.mp4"]);
  expect(getProductionMaterials).toHaveBeenCalledTimes(2);
});
