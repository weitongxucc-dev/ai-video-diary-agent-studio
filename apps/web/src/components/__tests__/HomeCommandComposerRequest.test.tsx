import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import type { AssistantConversation, AssistantConversationTurn, CreationRequest } from "@/lib/types";
import { HomeCommandComposer } from "../HomeCommandComposer";

const mocks = vi.hoisted(() => ({ conversation: null as AssistantConversation | null, send: vi.fn(), get: vi.fn(), items: vi.fn(), checkpoint: Promise.resolve(0), refresh: vi.fn() }));
vi.mock("@/components/useAssistantConversation", () => ({ useAssistantConversation: () => ({
  conversation: mocks.conversation, draftText: "制作一万条视频", setDraftText: vi.fn(), createNew: vi.fn(),
  beginTurn: () => ({ conversationId: mocks.conversation!.id, localTurnId: "local-turn", clientRequestId: "client-request", draftCheckpoint: mocks.checkpoint }),
  bindTurnEvents: () => vi.fn(), isCurrentConversation: (id: string) => mocks.conversation?.id === id,
  refresh: mocks.refresh, failUnpersistedTurn: vi.fn(),
}) }));
vi.mock("@/components/AssistantConversationProvider", () => ({ useAssistantMaterialReference: () => null, useAssistantKnowledgeReference: () => null, useAssistantProfileReference: () => null }));
vi.mock("@/components/HomeCardDialog", () => ({ HomeCardDialog: ({ children, title, onClose }: { children: ReactNode; title: string; onClose: () => void }) => <div role="dialog" aria-label={title}><button onClick={onClose}>关闭目标</button>{children}</div> }));
vi.mock("@/components/AssistantConversationPanel", () => ({
  AssistantConversationPanel: ({ onSend, state, onOpenCreationRequest }: { onSend: (message: string) => void; state: { conversation: AssistantConversation | null }; onOpenCreationRequest: (turn: AssistantConversationTurn) => void }) => <><button onClick={() => onSend("制作一万条视频")}>发送</button>{state.conversation?.turns.filter(turn => turn.result?.kind === "creation_request").map(turn => <button key={turn.id} onClick={() => onOpenCreationRequest(turn)}>查看这次创作目标</button>)}</>,
  AssistantFailureMessage: ({ message }: { message: string }) => <>{message}</>,
}));
vi.mock("@/lib/api", () => ({ apiUrl: (url: string) => url, getRuntimeSettings: async () => ({ status: { llm: { configured: true } } }), sendWorkspaceAssistantPlanTurn: mocks.send,
  confirmCreationPlan: vi.fn(), confirmCreationTaskEdit: vi.fn(), previewCreationPlan: vi.fn(), sendCreationTaskAssistantTurn: vi.fn(), sendMaterialAssistantTurn: vi.fn(), undoCreationTaskTimeline: vi.fn(), updateAssistantPlanPreview: vi.fn(),
}));
vi.mock("@/lib/creation-request-api", () => ({ getCreationRequest: mocks.get, getCreationRequestItems: mocks.items, updateCreationRequest: vi.fn(), prepareCreationRequestWindow: vi.fn(), confirmCreationRequestWindow: vi.fn() }));

function request(confirmed = 2): CreationRequest {
  return { id: "request-a", goal: "当前持久目标", target_count: 10000, output_relation: "series", revision: 7,
    requirements: { goal: "每集设备技巧", target_count: 1, language: "zh-CN", aspect_ratio: "9:16", duration_target_seconds: 120 },
    material_scope: { kind: "workspace_library" }, clarification_questions: [], expanded_count: confirmed, confirmed_count: confirmed,
    task_status_counts: {}, status: "active", current_window: null };
}
function conversation(): AssistantConversation {
  return { id: "conversation-a", workspace_id: "workspace-a", scope_kind: "home", scope_id: "home", title: "设备短片", draft_text: "", draft_version: 0,
    turns: [{ id: "turn-a", sequence: 1, user_message: "做一万条", assistant_text: "已保存目标", status: "completed", events: [],
      result: { kind: "creation_request", creation_request_id: "request-a", creation_request: { ...request(9999), goal: "旧进度不能展示" } }, created_at: "2026-10-11", updated_at: "2026-10-11" }],
    created_at: "2026-10-11", updated_at: "2026-10-11" };
}
beforeEach(() => { vi.resetAllMocks(); mocks.conversation = conversation(); mocks.checkpoint = Promise.resolve(0); mocks.get.mockResolvedValue(request()); mocks.items.mockResolvedValue({ items: [], next_after_ordinal: null, has_more: false }); });

it("restores only the request identity and rereads actual progress on each reopening", async () => {
  render(<HomeCommandComposer />);
  fireEvent.click(await screen.findByRole("button", { name: "查看这次创作目标" }));
  expect(await screen.findByText("目标 10,000 条视频 · 已建 2 条任务")).toBeInTheDocument();
  expect(screen.queryByText("旧进度不能展示")).not.toBeInTheDocument();
  fireEvent.change(screen.getByRole("textbox", { name: "每行写一条视频的具体目标" }), { target: { value: "下一集：安装\n下一集：维护" } });
  fireEvent.click(screen.getByRole("button", { name: "关闭目标" }));
  mocks.get.mockResolvedValue(request(8));
  fireEvent.click(screen.getByRole("button", { name: "查看这次创作目标" }));
  expect(await screen.findByText("目标 10,000 条视频 · 已建 8 条任务")).toBeInTheDocument();
  expect(screen.getByRole("textbox", { name: "每行写一条视频的具体目标" })).toHaveValue("下一集：安装\n下一集：维护");
  expect(mocks.get).toHaveBeenCalledTimes(2);
});

it("prioritizes a new request over a simultaneous legacy plan preview", async () => {
  mocks.conversation = { ...conversation(), turns: [] };
  mocks.send.mockResolvedValue({ reply: "已保存", creation_request: request(0), plan_preview: { name: "不能当单片方案" } });
  render(<HomeCommandComposer />);
  fireEvent.click(screen.getByRole("button", { name: "发送" }));
  expect(await screen.findByRole("dialog", { name: "创作目标" })).toBeInTheDocument();
  expect(await screen.findByText("当前持久目标")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "查看方案" })).not.toBeInTheDocument();
  expect(screen.queryByText("不能当单片方案")).not.toBeInTheDocument();
  expect(mocks.get).toHaveBeenCalledWith("request-a");
});

it("does not open a late request response on a different page or conversation", async () => {
  mocks.conversation = { ...conversation(), turns: [] };
  let resolve!: (value: unknown) => void;
  mocks.send.mockReturnValue(new Promise(done => { resolve = done; }));
  const view = render(<HomeCommandComposer pageContext={{ pathname: "/" }} />);
  fireEvent.click(screen.getByRole("button", { name: "发送" }));
  await waitFor(() => expect(mocks.send).toHaveBeenCalledTimes(1));
  mocks.conversation = { ...conversation(), id: "conversation-b", turns: [] };
  view.rerender(<HomeCommandComposer pageContext={{ pathname: "/profile" }} />);
  await act(async () => resolve({ reply: "旧页返回", creation_request: request(), plan_preview: null }));
  expect(screen.queryByRole("dialog", { name: "创作目标" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "查看这次创作目标" })).not.toBeInTheDocument();
  expect(mocks.get).not.toHaveBeenCalled();
});
