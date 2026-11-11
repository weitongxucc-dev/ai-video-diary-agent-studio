import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { AssistantConversation, AssistantConversationScope, AssistantSavedReference } from "@/lib/types";
import { AssistantConversationProvider, useAssistantSelection } from "../AssistantConversationProvider";
import { useAssistantConversation } from "../useAssistantConversation";
import { WorkbenchTargetProvider } from "../workbench/WorkbenchTarget";

const navigation = vi.hoisted(() => ({ path: "/tasks/a", push: vi.fn() }));
const api = vi.hoisted(() => ({ getCurrentAssistantConversation: vi.fn(), listAssistantConversations: vi.fn(),
  getAssistantConversation: vi.fn(), updateAssistantConversation: vi.fn(), getAssistantConversationReference: vi.fn(),
  saveAssistantConversationReference: vi.fn(), createAssistantConversation: vi.fn(), resumeAssistantConversation: vi.fn(),
  stopAssistantConversationTurn: vi.fn(), restoreUnpersistedAssistantTurnDraft: vi.fn() }));
vi.mock("next/navigation", () => ({ usePathname: () => navigation.path, useRouter: () => ({ push: navigation.push }) }));
vi.mock("@/hooks/useLocalMemberIdentity", () => ({ useLocalMemberIdentity: () => ({ token: "member" }) }));
vi.mock("@/lib/api", () => api);

let current: ReturnType<typeof useAssistantConversation>;
const records = new Map<string, AssistantConversation>();
const references = new Map<string, AssistantSavedReference | null>();
function Consumer() {
  current = useAssistantConversation({ scope_kind: "home", scope_id: "home" });
  const selection = useAssistantSelection();
  return <><output aria-label="conversation">{current.conversation?.id}</output>
    <output aria-label="messages">{current.conversation?.turns.map(turn => turn.assistant_text).join("|")}</output>
    <output aria-label="references">{selection?.items.map(item => item.label).join("|")}</output>
    <input aria-label="draft" value={current.draftText} onChange={event => current.setDraftText(event.target.value)} />
    <button onClick={() => selection?.select([{ kind: "material", label: "A 的素材", selection: { scope: "asset", asset_id: "asset-a" } }])}>引用</button></>;
}
function fixture() { return <WorkbenchTargetProvider><AssistantConversationProvider><Consumer /></AssistantConversationProvider></WorkbenchTargetProvider>; }
beforeEach(() => {
  vi.clearAllMocks(); records.clear(); references.clear(); navigation.path = "/tasks/a";
  api.getCurrentAssistantConversation.mockImplementation(async (scope: AssistantConversationScope) => {
    const id = `${scope.scope_kind}:${scope.scope_id}`;
    if (!records.has(id)) records.set(id, { id, workspace_id: "workspace", ...scope, title: id, draft_text: "", draft_version: 0,
      turns: [], created_at: "2026-11-11T00:00:00Z", updated_at: "2026-11-11T00:00:00Z" });
    return records.get(id);
  });
  api.listAssistantConversations.mockResolvedValue({ items: [] });
  api.getAssistantConversation.mockImplementation(async (id: string) => records.get(id));
  api.updateAssistantConversation.mockImplementation(async (id: string, update: { draft_text: string }) => {
    const result = { ...records.get(id)!, draft_text: update.draft_text, draft_version: records.get(id)!.draft_version + 1 };
    records.set(id, result); return result;
  });
  api.getAssistantConversationReference.mockImplementation(async (id: string) => ({ reference_version: 1, selected_reference: references.get(id) ?? null }));
  api.saveAssistantConversationReference.mockImplementation(async (id: string, ref: AssistantSavedReference | null) => {
    references.set(id, ref); return { reference_version: 2, selected_reference: ref };
  });
});
afterEach(cleanup);

it("restores task A → B → A drafts and references while resource browsing keeps A", async () => {
  const view = render(fixture());
  await screen.findByText("task:a");
  fireEvent.change(screen.getByLabelText("draft"), { target: { value: "A 的未发送要求" } });
  fireEvent.click(screen.getByText("引用"));
  await waitFor(() => expect(api.saveAssistantConversationReference).toHaveBeenCalledWith("task:a", expect.anything(), 1));
  navigation.path = "/library"; view.rerender(fixture());
  expect(screen.getByLabelText("conversation")).toHaveTextContent("task:a");
  expect(screen.getByLabelText("draft")).toHaveValue("A 的未发送要求");
  navigation.path = "/tasks/b"; view.rerender(fixture());
  await screen.findByText("task:b");
  expect(screen.getByLabelText("draft")).toHaveValue("");
  await waitFor(() => expect(screen.getByLabelText("references")).toHaveTextContent(/^$/));
  fireEvent.change(screen.getByLabelText("draft"), { target: { value: "B 的未发送要求" } });
  navigation.path = "/tasks/a"; view.rerender(fixture());
  await screen.findByText("task:a");
  expect(screen.getByLabelText("draft")).toHaveValue("A 的未发送要求");
  await waitFor(() => expect(screen.getByLabelText("references")).toHaveTextContent("A 的素材"));
  expect(records.get("task:b")?.draft_text).toBe("B 的未发送要求");
  expect(api.createAssistantConversation).not.toHaveBeenCalled();
});

it("keeps A's late stream and stop identity out of B and reads A's durable result on return", async () => {
  const view = render(fixture()); await screen.findByText("task:a");
  let turn!: ReturnType<typeof current.beginTurn>;
  await act(async () => { turn = current.beginTurn("只改A"); await turn.draftCheckpoint; });
  const emitA = current.bindTurnEvents(turn.conversationId, turn.localTurnId);
  const savedTurn = { ...records.get("task:a")!.turns[0], id: "turn-a", sequence: 1, user_message: "只改A", assistant_text: "", status: "running" as const, events: [], result: null, created_at: "now", updated_at: "now" };
  records.set("task:a", { ...records.get("task:a")!, turns: [savedTurn] });
  act(() => emitA({ type: "turn_started", turn_id: "turn-a", turn_state: "running" }));
  navigation.path = "/tasks/b"; view.rerender(fixture()); await screen.findByText("task:b");
  act(() => emitA({ type: "text_delta", delta: "A 的迟到回复" }));
  expect(screen.getByLabelText("messages")).not.toHaveTextContent("A 的迟到回复");
  expect(api.stopAssistantConversationTurn).not.toHaveBeenCalled();
  records.set("task:a", { ...records.get("task:a")!, turns: [{ ...savedTurn, assistant_text: "A 的持久回复" }] });
  navigation.path = "/tasks/a"; view.rerender(fixture()); await screen.findByText("task:a");
  expect(screen.getByLabelText("messages")).toHaveTextContent("A 的持久回复");
  await act(async () => { await current.stop(); });
  expect(api.stopAssistantConversationTurn).toHaveBeenCalledWith("task:a", "turn-a");
});
