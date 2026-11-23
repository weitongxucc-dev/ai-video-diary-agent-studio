import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { AssistantConversation, AssistantConversationScope, AssistantSavedReference } from "@/lib/types";
import { AssistantConversationProvider, useAssistantSelection } from "../AssistantConversationProvider";
import { useAssistantConversation } from "../useAssistantConversation";
import { WorkbenchTargetProvider } from "../workbench/WorkbenchTarget";

const navigation = vi.hoisted(() => ({ path: "/tasks/a", search: "", push: vi.fn() }));
const api = vi.hoisted(() => ({ getCurrentAssistantConversation: vi.fn(), listAssistantConversations: vi.fn(),
  getAssistantConversation: vi.fn(), updateAssistantConversation: vi.fn(), getAssistantConversationReference: vi.fn(),
  saveAssistantConversationReference: vi.fn(), createAssistantConversation: vi.fn(), resumeAssistantConversation: vi.fn(),
  stopAssistantConversationTurn: vi.fn(), restoreUnpersistedAssistantTurnDraft: vi.fn() }));
vi.mock("next/navigation", () => ({ usePathname: () => navigation.path, useSearchParams: () => new URLSearchParams(navigation.search), useRouter: () => ({ push: navigation.push }) }));
vi.mock("@/hooks/useLocalMemberIdentity", () => ({ useLocalMemberIdentity: () => ({ token: "member" }) }));
vi.mock("@/lib/api", () => api);

let current: ReturnType<typeof useAssistantConversation>;
const records = new Map<string, AssistantConversation>();
const selectedByScope = new Map<string, string>();
const references = new Map<string, AssistantSavedReference | null>();
const scopeKey = (scope: AssistantConversationScope) => `${scope.scope_kind}:${scope.scope_id}`;
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
  vi.clearAllMocks(); records.clear(); selectedByScope.clear(); references.clear(); navigation.path = "/tasks/a"; navigation.search = "";
  api.getCurrentAssistantConversation.mockImplementation(async (scope: AssistantConversationScope) => {
    const key = scopeKey(scope);
    const id = selectedByScope.get(key) ?? key;
    if (!records.has(id)) records.set(id, { id, workspace_id: "workspace", ...scope, title: id, draft_text: "", draft_version: 0,
      turns: [], created_at: "2026-11-11T00:00:00Z", updated_at: "2026-11-11T00:00:00Z" });
    return records.get(id);
  });
  api.listAssistantConversations.mockResolvedValue({ items: [] });
  api.getAssistantConversation.mockImplementation(async (id: string) => records.get(id));
  api.resumeAssistantConversation.mockImplementation(async (id: string) => {
    const conversation = records.get(id)!;
    selectedByScope.set(scopeKey(conversation), id);
    return conversation;
  });
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

it("isolates draft A → B → A messages, unsent drafts and references while preserving home history", async () => {
  const draftA = "a7872828-c7ba-449c-a988-e5679d163a81";
  const draftB = "5491c773-93e9-4e3f-86d8-1f2762af9268";
  const home = "home:home";
  const a = `home:draft:${draftA}`;
  const b = `home:draft:${draftB}`;
  navigation.path = "/";
  const view = render(fixture());
  const navigate = (path: string, search = "") => {
    navigation.path = path; navigation.search = search; view.rerender(fixture());
  };
  const savedTurn = { id: "turn-a", sequence: 1, user_message: "只改A", assistant_text: "", status: "running" as const, events: [], result: null, created_at: "now", updated_at: "now" };
  await screen.findByText(home);
  records.set(home, { ...records.get(home)!, turns: [{ ...savedTurn, id: "turn-home", user_message: "首页问题", assistant_text: "首页历史", status: "completed" }] });
  fireEvent.change(screen.getByLabelText("draft"), { target: { value: "首页的未发送要求" } });

  navigate("/production", `new=1&draft_id=${draftA.toUpperCase()}`);
  await screen.findByText(a);
  expect(screen.getByLabelText("draft")).toHaveValue("");
  expect(screen.getByLabelText("messages")).toBeEmptyDOMElement();
  let turn!: ReturnType<typeof current.beginTurn>;
  await act(async () => { turn = current.beginTurn("只改A"); await turn.draftCheckpoint; });
  const emitA = current.bindTurnEvents(turn.conversationId, turn.localTurnId);
  records.set(a, { ...records.get(a)!, turns: [savedTurn] });
  act(() => emitA({ type: "turn_started", turn_id: "turn-a", turn_state: "running" }));
  fireEvent.change(screen.getByLabelText("draft"), { target: { value: "A 的未发送要求" } });
  navigate("/library", "mode=manage");
  expect(screen.getByLabelText("conversation")).toHaveTextContent(a);
  expect(screen.getByLabelText("draft")).toHaveValue("A 的未发送要求");
  fireEvent.click(screen.getByText("引用"));
  await waitFor(() => expect(api.saveAssistantConversationReference).toHaveBeenCalledWith(a, expect.anything(), 1));

  navigate("/production", `new=1&draft_id=${draftA}`);
  expect(screen.getByLabelText("conversation")).toHaveTextContent(a);
  navigate("/production", `new=1&draft_id=${draftB}`);
  await screen.findByText(b);
  expect(screen.getByLabelText("draft")).toHaveValue("");
  expect(screen.getByLabelText("references")).toBeEmptyDOMElement();
  act(() => emitA({ type: "text_delta", delta: "A 的迟到回复" }));
  expect(screen.getByLabelText("messages")).toBeEmptyDOMElement();
  expect(api.stopAssistantConversationTurn).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText("draft"), { target: { value: "B 的未发送要求" } });
  records.set(a, { ...records.get(a)!, turns: [{ ...savedTurn, assistant_text: "A 的持久回复" }] });

  navigate("/production", `new=1&draft_id=${draftA}`);
  await screen.findByText(a);
  act(() => emitA({ type: "text_delta", delta: "A 上次访问的迟到回复" }));
  expect(screen.getByLabelText("messages")).toHaveTextContent("A 的持久回复");
  expect(screen.getByLabelText("messages")).not.toHaveTextContent("迟到回复");
  expect(screen.getByLabelText("draft")).toHaveValue("A 的未发送要求");
  await waitFor(() => expect(screen.getByLabelText("references")).toHaveTextContent("A 的素材"));
  await act(async () => { await current.stop(); });
  expect(api.stopAssistantConversationTurn).toHaveBeenCalledWith(a, "turn-a");
  expect(records.get(b)?.draft_text).toBe("B 的未发送要求");

  navigate("/");
  await screen.findByText(home);
  expect(screen.getByLabelText("messages")).toHaveTextContent("首页历史");
  expect(screen.getByLabelText("draft")).toHaveValue("首页的未发送要求");
  expect(screen.getByLabelText("references")).toBeEmptyDOMElement();
});

it("reopens the original draft workbench after an explicit Home history resume", async () => {
  const draftId = "a7872828-c7ba-449c-a988-e5679d163a81";
  const historic: AssistantConversation = { id: "draft-history", workspace_id: "workspace", scope_kind: "home", scope_id: `draft:${draftId}`,
    title: "之前的新视频对话", draft_text: "继续补充视频目标", draft_version: 1, turns: [], created_at: "now", updated_at: "now" };
  records.set(historic.id, historic);
  api.listAssistantConversations.mockResolvedValueOnce({ items: [historic] });

  navigation.path = "/";
  const view = render(fixture());
  await screen.findByText("home:home");
  await waitFor(() => expect(current.conversations).toContainEqual(historic));
  expect(navigation.push).not.toHaveBeenCalled();
  await act(async () => { await current.resume("home:home"); });
  expect(navigation.push).not.toHaveBeenCalled();
  await act(async () => { await current.resume(historic.id); });
  expect(navigation.push).toHaveBeenCalledExactlyOnceWith(`/production?new=1&draft_id=${draftId}`);

  navigation.path = "/production"; navigation.search = `new=1&draft_id=${draftId}`; view.rerender(fixture());
  await waitFor(() => expect(api.getCurrentAssistantConversation).toHaveBeenLastCalledWith({ scope_kind: "home", scope_id: `draft:${draftId}` }));
  expect(await screen.findByText(historic.id)).toBeVisible();
  expect(screen.getByLabelText("draft")).toHaveValue(historic.draft_text);
});
