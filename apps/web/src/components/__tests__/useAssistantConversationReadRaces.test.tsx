import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useAssistantConversation } from "../useAssistantConversation";
import type { AssistantConversation, AssistantScriptToolReceipt, CreationTaskAssistantTurnResponse } from "@/lib/types";

const api = vi.hoisted(() => ({
  getCurrentAssistantConversation: vi.fn(),
  listAssistantConversations: vi.fn(),
  getAssistantConversation: vi.fn(),
  updateAssistantConversation: vi.fn(),
  restoreUnpersistedAssistantTurnDraft: vi.fn(),
  createAssistantConversation: vi.fn(),
  resumeAssistantConversation: vi.fn(),
  stopAssistantConversationTurn: vi.fn(),
}));

vi.mock("@/lib/api", () => api);

function conversation(overrides: Partial<AssistantConversation> = {}): AssistantConversation {
  return {
    id: "conversation-one", workspace_id: "workspace-one", scope_kind: "home", scope_id: "home",
    title: "对话", draft_text: "", draft_version: 1, created_at: "2026-10-09T00:00:00Z",
    updated_at: "2026-10-09T00:00:00Z", turns: [], ...overrides,
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: Error) => void;
  const promise = new Promise<T>((accept, fail) => { resolve = accept; reject = fail; });
  return { promise, resolve, reject };
}

beforeEach(() => {
  vi.clearAllMocks();
  const initial = conversation();
  api.getCurrentAssistantConversation.mockResolvedValue(initial);
  api.listAssistantConversations.mockResolvedValue({ items: [initial] });
  api.getAssistantConversation.mockResolvedValue(initial);
  api.updateAssistantConversation.mockImplementation(async (id: string, update: { draft_text?: string; expected_draft_version?: number }) => ({
    ...initial, id, draft_text: update.draft_text ?? initial.draft_text,
    draft_version: (update.expected_draft_version ?? initial.draft_version) + (update.draft_text === undefined ? 0 : 1),
  }));
  api.restoreUnpersistedAssistantTurnDraft.mockImplementation(async (id: string, input: { user_message: string; expected_draft_version: number }) => ({
    status: "restored", conversation: conversation({ id, draft_text: input.user_message, draft_version: input.expected_draft_version + 1 }),
  }));
});

afterEach(() => { vi.useRealTimers(); });

function businessConversation(status: "queued" | "running" | "confirmed" | "unknown" = "queued"): AssistantConversation {
  const current = conversation();
  const response = {
    reply: "预览已安排，尚未应用", provider: "fixture", model: "fixture", edit_preview: null,
    script_draft: null, execution_action: null, execution_status: null, topic_candidates: null,
    media_job: status === "unknown" ? null : {job_id: "job-preview", status: status === "confirmed" ? "succeeded" : status,
      operation: "preview_edit", target_refs: ["clip:second"], input_digest: "fixture-digest", created_at: current.created_at,
      result_kind: "edit_preview", result_url: "/api/creation-tasks/task-one/media-jobs/job-preview",
      device_request_id: null, waiting_reason: null, base_version_id: "version-base", result: null, failure_reason: null, repair_action: null},
    edit_request: status === "unknown" ? {task_id: "task-one", request_id: "00000000-0000-5000-8000-000000000001",
      phase: "preview", status: "unknown", query_url: "/api/creation-tasks/task-one/assistant/edit-requests/request?phase=preview",
      input_digest: null, result: null, submitted_input: {operation_type: "ReplaceClip", clip_id: "second"}} : null,
  };
  return {...current, turns: [{id: "turn-business", sequence: 1, user_message: "换第二镜头并保留旁白",
    assistant_text: response.reply, status: "completed", events: [], created_at: current.created_at, updated_at: current.updated_at,
    result: {kind: "task_turn", response, edit_preview: false,
      ...(status === "confirmed" ? {continuation: {type: "business_result", event_id: "event-applied", task_id: "task-one",
        status: "confirmed", actionable: false, base_version_id: "version-base", result_version_id: "version-saved"}} : {})}}]};
}

function scriptToolConversation(action: AssistantScriptToolReceipt["action"], status: AssistantScriptToolReceipt["status"] = "unknown") {
  const initial = businessConversation("unknown");
  initial.turns[0].result = {...initial.turns[0].result, response: {
    ...initial.turns[0].result!.response as CreationTaskAssistantTurnResponse, edit_request: null,
    script_tool: {action, status, request_id: "original-creation", tool_id: status === "recorded" ? "tool-real" : null,
      tool_version: status === "recorded" ? 1 : null, query_url: "/api/media-script-tools/requests/original-creation", submitted_input: {name: "小标题"}},
  }};
  return initial;
}

function goalConversation(status = "waiting_adoption", currentTurnId = "turn-business") {
  const current = businessConversation("confirmed");
  current.turns[0].result = {...current.turns[0].result, goal_continuation: {
    original_turn_id: "turn-business", current_turn_id: currentTurnId, status,
    conversation_id: current.id, workspace_id: current.workspace_id, task_id: "task-one",
  }};
  return current;
}

function adoptionConversation(): AssistantConversation {
  const current = goalConversation();
  current.turns[0].result!.response = {...current.turns[0].result!.response as CreationTaskAssistantTurnResponse,
    edit_preview: {proposal: {id: "proposal-adopted", task_id: "task-one", workspace_id: current.workspace_id,
      status: "proposed"}} as CreationTaskAssistantTurnResponse["edit_preview"]};
  return current;
}

const actualAdoption = {conversation_id: "conversation-one", original_turn_id: "turn-business",
  task_id: "task-one", proposal_id: "proposal-adopted"};

describe("same-turn business result reads", () => {
  it("immediately reads actual adoption after pausing, preserves the new draft, and reopens only one bounded window", async () => {
    const initial = adoptionConversation();
    api.getCurrentAssistantConversation.mockResolvedValue(initial);
    api.getAssistantConversation.mockResolvedValue(initial);
    vi.useFakeTimers();
    const {result, unmount} = renderHook(() => useAssistantConversation({scope_kind: "home", scope_id: "home"}));
    await act(async () => { await Promise.resolve(); });
    await act(async () => { await vi.advanceTimersByTimeAsync(125000); });
    expect(api.getAssistantConversation).toHaveBeenCalledTimes(24);
    expect(result.current.businessRefreshPaused).toBe(true);
    act(() => result.current.setDraftText("采用之后继续写的新草稿"));
    await act(async () => { await result.current.refreshAfterTaskAdoption(actualAdoption); });
    expect(api.getAssistantConversation).toHaveBeenCalledTimes(25);
    expect(result.current.businessRefreshPaused).toBe(false);
    expect(result.current.draftText).toBe("采用之后继续写的新草稿");
    await act(async () => { await vi.advanceTimersByTimeAsync(125000); });
    expect(api.getAssistantConversation).toHaveBeenCalledTimes(49);
    expect(result.current.businessRefreshPaused).toBe(true);
    await act(async () => { await vi.advanceTimersByTimeAsync(180000); });
    expect(api.getAssistantConversation).toHaveBeenCalledTimes(49);
    expect(api.createAssistantConversation).not.toHaveBeenCalled();
    unmount();
  });

  it.each(["task_id", "proposal_id", "original_turn_id", "conversation_id"] as const)("does not wake an adoption for another %s", async key => {
    const initial = adoptionConversation();
    api.getCurrentAssistantConversation.mockResolvedValue(initial);
    api.getAssistantConversation.mockResolvedValue(initial);
    vi.useFakeTimers();
    const {result, unmount} = renderHook(() => useAssistantConversation({scope_kind: "home", scope_id: "home"}));
    await act(async () => { await Promise.resolve(); });
    await act(async () => { await vi.advanceTimersByTimeAsync(125000); });
    api.getAssistantConversation.mockClear();
    await act(async () => { await result.current.refreshAfterTaskAdoption({...actualAdoption, [key]: "foreign"}); });
    await act(async () => { await vi.advanceTimersByTimeAsync(180000); });
    expect(api.getAssistantConversation).not.toHaveBeenCalled();
    expect(result.current.businessRefreshPaused).toBe(true);
    unmount();
  });

  it("keeps the original paused goal and read error when actual adoption's conversation GET is rejected", async () => {
    const initial = adoptionConversation();
    api.getCurrentAssistantConversation.mockResolvedValue(initial);
    api.getAssistantConversation.mockResolvedValue(initial);
    vi.useFakeTimers();
    const {result, unmount} = renderHook(() => useAssistantConversation({scope_kind: "home", scope_id: "home"}));
    await act(async () => { await Promise.resolve(); });
    await act(async () => { await vi.advanceTimersByTimeAsync(125000); });
    expect(result.current.businessRefreshPaused).toBe(true);
    act(() => result.current.setError("上一次读取的诊断仍保留"));
    api.getAssistantConversation.mockRejectedValue(new Error("403 当前成员不能读取这个对话"));
    await act(async () => { await expect(result.current.refreshAfterTaskAdoption(actualAdoption))
      .rejects.toThrow("403 当前成员不能读取这个对话"); });
    expect(result.current.error).toBe("上一次读取的诊断仍保留");
    expect(result.current.businessRefreshPaused).toBe(true);
    expect(api.getAssistantConversation).toHaveBeenCalledTimes(25);
    await act(async () => { await vi.advanceTimersByTimeAsync(180000); });
    expect(api.getAssistantConversation).toHaveBeenCalledTimes(25);
    expect(api.createAssistantConversation).not.toHaveBeenCalled();
    expect(api.updateAssistantConversation).not.toHaveBeenCalled();
    unmount();
  });

  it("does not let an adoption's old read wake the re-entered scope or replace a newer draft", async () => {
    const initial = {...adoptionConversation(), scope_kind: "task" as const, scope_id: "task-one"};
    api.getCurrentAssistantConversation.mockImplementation(async ({scope_id}: {scope_id: string}) => scope_id === "task-one"
      ? initial : conversation({id: "conversation-two", scope_kind: "task", scope_id: "task-two"}));
    api.getAssistantConversation.mockResolvedValue(initial);
    vi.useFakeTimers();
    const {result, rerender, unmount} = renderHook(({id}) => useAssistantConversation({scope_kind: "task", scope_id: id}),
      {initialProps: {id: "task-one"}});
    await act(async () => { await Promise.resolve(); });
    const late = deferred<AssistantConversation>();
    api.getAssistantConversation.mockReturnValueOnce(late.promise);
    let adopting!: Promise<AssistantConversation | null>;
    act(() => { adopting = result.current.refreshAfterTaskAdoption(actualAdoption); });
    await act(async () => { rerender({id: "task-two"}); });
    await act(async () => { rerender({id: "task-one"}); });
    await act(async () => { await vi.advanceTimersByTimeAsync(125000); });
    expect(result.current.businessRefreshPaused).toBe(true);
    act(() => result.current.setDraftText("返回后写的新草稿"));
    const reads = api.getAssistantConversation.mock.calls.length;
    await act(async () => { late.resolve({...initial, title: "旧采用回包", draft_text: "旧草稿"}); await adopting; });
    expect(result.current.conversation?.title).not.toBe("旧采用回包");
    expect(result.current.draftText).toBe("返回后写的新草稿");
    await act(async () => { await vi.advanceTimersByTimeAsync(180000); });
    expect(api.getAssistantConversation).toHaveBeenCalledTimes(reads);
    expect(result.current.businessRefreshPaused).toBe(true);
    unmount();
  });

  it("does not let a late adoption read replace or restart a new human goal", async () => {
    const initial = adoptionConversation();
    const newer = {...initial, turns: [...initial.turns, {...initial.turns[0], id: "new-human", sequence: 2,
      user_message: "只解释现在的结果", result: null}]};
    api.getCurrentAssistantConversation.mockResolvedValue(initial);
    api.getAssistantConversation.mockResolvedValue(initial);
    vi.useFakeTimers();
    const {result, unmount} = renderHook(() => useAssistantConversation({scope_kind: "home", scope_id: "home"}));
    await act(async () => { await Promise.resolve(); });
    const late = deferred<AssistantConversation>();
    api.getAssistantConversation.mockReturnValueOnce(late.promise).mockResolvedValue(newer);
    let adopting!: Promise<AssistantConversation | null>;
    act(() => { adopting = result.current.refreshAfterTaskAdoption(actualAdoption); });
    await act(async () => { await result.current.refresh(); });
    await act(async () => { late.resolve(initial); await adopting; });
    expect(result.current.conversation?.turns.at(-1)?.id).toBe("new-human");
    api.getAssistantConversation.mockClear();
    await act(async () => { await vi.advanceTimersByTimeAsync(180000); });
    expect(api.getAssistantConversation).not.toHaveBeenCalled();
    unmount();
  });

  it("keeps reading after real adoption, shows the running child, then stops at the persisted goal terminal", async () => {
    const adopted = goalConversation();
    const running = goalConversation("running", "turn-auto");
    running.turns.push({id: "turn-auto", sequence: 2, user_message: "", assistant_text: "正在继续原要求",
      status: "running", events: [], result: null, created_at: running.created_at, updated_at: running.updated_at});
    const terminal = goalConversation("no_progress", "turn-auto");
    terminal.turns.push({...running.turns[1], status: "completed", assistant_text: "实际结果已回读"});
    api.getCurrentAssistantConversation.mockResolvedValue(adopted);
    api.getAssistantConversation.mockResolvedValueOnce(running).mockResolvedValue(terminal);
    vi.useFakeTimers();
    const {result, unmount} = renderHook(() => useAssistantConversation({scope_kind: "home", scope_id: "home"}));
    await act(async () => { await Promise.resolve(); });
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect(result.current.conversation?.turns.at(-1)).toMatchObject({id: "turn-auto", user_message: "", status: "running"});
    await act(async () => { await vi.advanceTimersByTimeAsync(1500); });
    expect(result.current.conversation?.turns.at(-1)).toMatchObject({id: "turn-auto", status: "completed"});
    api.getAssistantConversation.mockClear();
    await act(async () => { await vi.advanceTimersByTimeAsync(180000); });
    expect(api.getAssistantConversation).not.toHaveBeenCalled();
    expect(api.createAssistantConversation).not.toHaveBeenCalled();
    expect(api.updateAssistantConversation).not.toHaveBeenCalled();
    unmount();
  });

  it.each(["invalidated", "no_progress", "limit_reached"])("does not poll a %s goal despite an old queued receipt", async status => {
    const initial = goalConversation(status);
    initial.turns[0].result!.response = businessConversation().turns[0].result!.response;
    api.getCurrentAssistantConversation.mockResolvedValue(initial);
    vi.useFakeTimers();
    const {unmount} = renderHook(() => useAssistantConversation({scope_kind: "home", scope_id: "home"}));
    await act(async () => { await Promise.resolve(); });
    await act(async () => { await vi.advanceTimersByTimeAsync(180000); });
    expect(api.getAssistantConversation).not.toHaveBeenCalled();
    unmount();
  });

  it("does not revive an older active goal after a new real instruction", async () => {
    const initial = goalConversation("waiting_result");
    initial.turns.push({...initial.turns[0], id: "turn-new-user", sequence: 2, user_message: "只查看这个结果",
      status: "completed", result: null});
    api.getCurrentAssistantConversation.mockResolvedValue(initial);
    vi.useFakeTimers();
    const {unmount} = renderHook(() => useAssistantConversation({scope_kind: "home", scope_id: "home"}));
    await act(async () => { await Promise.resolve(); });
    await act(async () => { await vi.advanceTimersByTimeAsync(180000); });
    expect(api.getAssistantConversation).not.toHaveBeenCalled();
    unmount();
  });

  it("does not poll a goal bound to another conversation or a stopped root", async () => {
    const initial = goalConversation();
    initial.turns[0].result!.goal_continuation = {...initial.turns[0].result!.goal_continuation as Record<string, unknown>,
      conversation_id: "unrelated-conversation"};
    api.getCurrentAssistantConversation.mockResolvedValue(initial);
    vi.useFakeTimers();
    const {result, unmount} = renderHook(() => useAssistantConversation({scope_kind: "home", scope_id: "home"}));
    await act(async () => { await Promise.resolve(); });
    await act(async () => { await vi.advanceTimersByTimeAsync(180000); });
    expect(api.getAssistantConversation).not.toHaveBeenCalled();
    const stopped = goalConversation("waiting_result");
    stopped.turns[0].status = "stopped";
    api.getAssistantConversation.mockResolvedValue(stopped);
    await act(async () => { await result.current.refresh(); });
    api.getAssistantConversation.mockClear();
    await act(async () => { await vi.advanceTimersByTimeAsync(180000); });
    expect(api.getAssistantConversation).not.toHaveBeenCalled();
    unmount();
  });

  it.each([true, false])("bounds pending execution reads with persisted goal=%s without re-submitting production", async withGoal => {
    const initial = goalConversation("waiting_result");
    if (!withGoal) {
      delete initial.turns[0].result!.goal_continuation;
      delete initial.turns[0].result!.continuation;
    }
    initial.turns[0].result!.response = {...initial.turns[0].result!.response as CreationTaskAssistantTurnResponse,
      media_job: null, execution_status: {task_id: "task-one", job: {id: "render-job", kind: "creation_execution",
        status: "queued", progress: 0, failure_reason: null, impact: null, repair_action: null}, stages: [],
        current_stage: "render", waiting_reason: null, status_updated_at: null, video_ready: false,
        cover_status: "pending", cover_message: null}};
    api.getCurrentAssistantConversation.mockResolvedValue(initial);
    api.getAssistantConversation.mockResolvedValue(initial);
    vi.useFakeTimers();
    const {result, unmount} = renderHook(() => useAssistantConversation({scope_kind: "home", scope_id: "home"}));
    await act(async () => { await Promise.resolve(); });
    await act(async () => { await vi.advanceTimersByTimeAsync(125000); });
    expect(api.getAssistantConversation).toHaveBeenCalledTimes(24);
    expect(result.current.businessRefreshPaused).toBe(true);
    expect(api.createAssistantConversation).not.toHaveBeenCalled();
    expect(api.updateAssistantConversation).not.toHaveBeenCalled();
    unmount();
  });

  it.each(["save", "update", "revoke"] as const)("bounds unknown tool %s reads without repeating writes", async action => {
    const initial = scriptToolConversation(action);
    api.getCurrentAssistantConversation.mockResolvedValue(initial);
    api.getAssistantConversation.mockResolvedValue(initial);
    vi.useFakeTimers();
    const {result, unmount} = renderHook(() => useAssistantConversation({scope_kind: "home", scope_id: "home"}));
    await act(async () => { await Promise.resolve(); });
    await act(async () => { await vi.advanceTimersByTimeAsync(125000); });
    expect(api.getAssistantConversation).toHaveBeenCalledTimes(24);
    expect(result.current.businessRefreshPaused).toBe(true);
    expect(result.current.conversation?.turns[0].id).toBe(initial.turns[0].id);
    expect(result.current.conversation?.turns[0].result?.response).toMatchObject({script_tool: {action, status: "unknown"}});
    expect(api.createAssistantConversation).not.toHaveBeenCalled();
    expect(api.updateAssistantConversation).not.toHaveBeenCalled();
    unmount();
  });

  it("stops querying when the original tool save has been read back as recorded", async () => {
    api.getCurrentAssistantConversation.mockResolvedValue(scriptToolConversation("save"));
    api.getAssistantConversation.mockResolvedValue(scriptToolConversation("save", "recorded"));
    vi.useFakeTimers();
    const {result, unmount} = renderHook(() => useAssistantConversation({scope_kind: "home", scope_id: "home"}));
    await act(async () => { await Promise.resolve(); });
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect(result.current.conversation?.turns[0].result?.response).toMatchObject({script_tool: {status: "recorded", tool_id: "tool-real"}});
    api.getAssistantConversation.mockClear();
    await act(async () => { await vi.advanceTimersByTimeAsync(180000); });
    expect(api.getAssistantConversation).not.toHaveBeenCalled();
    expect(api.createAssistantConversation).not.toHaveBeenCalled();
    expect(api.updateAssistantConversation).not.toHaveBeenCalled();
    unmount();
  });

  it("keeps a new draft while the unknown tool save is read on the same turn", async () => {
    const initial = scriptToolConversation("save"), late = deferred<AssistantConversation>();
    api.getCurrentAssistantConversation.mockResolvedValue(initial);
    api.getAssistantConversation.mockReturnValue(late.promise);
    const {result, unmount} = renderHook(() => useAssistantConversation({scope_kind: "home", scope_id: "home"}));
    await waitFor(() => expect(result.current.conversation?.id).toBe(initial.id));
    let reading!: Promise<AssistantConversation | null>;
    act(() => { reading = result.current.refresh(); result.current.setDraftText("本轮查询期间的新草稿"); });
    await act(async () => { late.resolve(scriptToolConversation("save", "recorded")); await reading; });
    expect(result.current.draftText).toBe("本轮查询期间的新草稿");
    expect(result.current.conversation?.turns[0].id).toBe(initial.turns[0].id);
    expect(result.current.conversation?.turns[0].result?.response).toMatchObject({script_tool: {status: "recorded"}});
    expect(api.updateAssistantConversation).not.toHaveBeenCalled();
    unmount();
  });

  it("does not poll a stopped turn with an unknown tool write", async () => {
    const initial = scriptToolConversation("save");
    initial.turns[0].status = "stopped";
    api.getCurrentAssistantConversation.mockResolvedValue(initial);
    vi.useFakeTimers();
    const {unmount} = renderHook(() => useAssistantConversation({scope_kind: "home", scope_id: "home"}));
    await act(async () => { await Promise.resolve(); });
    await act(async () => { await vi.advanceTimersByTimeAsync(180000); });
    expect(api.getAssistantConversation).not.toHaveBeenCalled();
    unmount();
  });

  it.each(["queued", "running", "unknown"] as const)("reads a completed model turn while %s still awaits an actual result", async status => {
    const initial = businessConversation(status);
    api.getCurrentAssistantConversation.mockResolvedValue(initial);
    api.getAssistantConversation.mockResolvedValue(initial);
    vi.useFakeTimers();
    const {result, unmount} = renderHook(() => useAssistantConversation({scope_kind: "home", scope_id: "home"}));
    await act(async () => { await Promise.resolve(); });
    expect(result.current.conversation?.turns[0].status).toBe("completed");
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect(api.getAssistantConversation).toHaveBeenCalledWith(initial.id);
    expect(api.createAssistantConversation).not.toHaveBeenCalled();
    expect(api.updateAssistantConversation).not.toHaveBeenCalled();
    unmount();
  });

  it("stops read timers when the original proposal is adopted", async () => {
    api.getCurrentAssistantConversation.mockResolvedValue(businessConversation());
    api.getAssistantConversation.mockResolvedValue(businessConversation("confirmed"));
    vi.useFakeTimers();
    const {result} = renderHook(() => useAssistantConversation({scope_kind: "home", scope_id: "home"}));
    await act(async () => { await Promise.resolve(); });
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect(result.current.conversation?.turns[0].result?.continuation).toMatchObject({status: "confirmed", actionable: false});
    api.getAssistantConversation.mockClear();
    await act(async () => { await vi.advanceTimersByTimeAsync(180000); });
    expect(api.getAssistantConversation).not.toHaveBeenCalled();
  });

  it("bounds unattended pending reads without claiming a queued job completed", async () => {
    const initial = businessConversation();
    api.getCurrentAssistantConversation.mockResolvedValue(initial);
    api.getAssistantConversation.mockResolvedValue(initial);
    vi.useFakeTimers();
    const {result} = renderHook(() => useAssistantConversation({scope_kind: "home", scope_id: "home"}));
    await act(async () => { await Promise.resolve(); });
    await act(async () => { await vi.advanceTimersByTimeAsync(125000); });
    expect(api.getAssistantConversation).toHaveBeenCalledTimes(24);
    expect(result.current.businessRefreshPaused).toBe(true);
    expect(result.current.conversation?.turns[0].result?.response).toMatchObject({media_job: {status: "queued"}});
    await act(async () => { await vi.advanceTimersByTimeAsync(180000); });
    expect(api.getAssistantConversation).toHaveBeenCalledTimes(24);
  });

  it("does not let a previous visit's pending read replace the re-entered scope or new draft", async () => {
    const initial = {...businessConversation(), scope_kind: "task" as const, scope_id: "task-one"};
    api.getCurrentAssistantConversation.mockImplementation(async ({scope_id}: {scope_id: string}) => ({...initial,
      scope_id, id: scope_id === "task-one" ? initial.id : "conversation-task-two"}));
    const late = deferred<AssistantConversation>();
    api.getAssistantConversation.mockReturnValue(late.promise);
    const {result, rerender} = renderHook(({id}) => useAssistantConversation({scope_kind: "task", scope_id: id}),
      {initialProps: {id: "task-one"}});
    await waitFor(() => expect(result.current.conversation?.id).toBe(initial.id));
    let reading!: Promise<AssistantConversation | null>;
    act(() => { reading = result.current.refresh(); });
    rerender({id: "task-two"});
    await waitFor(() => expect(result.current.loading).toBe(false));
    rerender({id: "task-one"});
    await waitFor(() => expect(result.current.loading).toBe(false));
    act(() => result.current.setDraftText("重新进入后写下的草稿"));
    await act(async () => { late.resolve({...initial, title: "旧回包不得替换", draft_text: "旧草稿"}); await reading; });
    expect(result.current.conversation?.title).not.toBe("旧回包不得替换");
    expect(result.current.draftText).toBe("重新进入后写下的草稿");
  });
});

describe("useAssistantConversation read races", () => {
  it("does not let a refresh GET replace text typed after that read started", async () => {
    const initial = conversation({ draft_text: "旧服务端草稿" });
    api.getCurrentAssistantConversation.mockResolvedValue(initial);
    let resolveRead!: (value: AssistantConversation) => void;
    api.getAssistantConversation.mockReturnValue(new Promise((resolve) => { resolveRead = resolve; }));
    const { result } = renderHook(() => useAssistantConversation({ scope_kind: "home", scope_id: "home" }));
    await waitFor(() => expect(result.current.conversation?.id).toBe(initial.id));

    let refresh!: Promise<AssistantConversation | null>;
    act(() => { refresh = result.current.refresh(); });
    act(() => result.current.setDraftText("刷新期间新写的草稿"));
    await act(async () => {
      resolveRead(conversation({ draft_text: "旧服务端草稿", draft_version: 2 }));
      await refresh;
    });

    expect(result.current.draftText).toBe("刷新期间新写的草稿");
    // conversation keeps the persisted snapshot; draftText owns newer local edits.
    expect(result.current.conversation?.draft_text).toBe("旧服务端草稿");
  });

  it("preserves an unsaved local draft even when refresh starts after the edit", async () => {
    const initial = conversation({ draft_text: "旧服务端草稿" });
    api.getCurrentAssistantConversation.mockResolvedValue(initial);
    const { result } = renderHook(() => useAssistantConversation({ scope_kind: "home", scope_id: "home" }));
    await waitFor(() => expect(result.current.conversation?.id).toBe(initial.id));
    act(() => result.current.setDraftText("尚未到自动保存时间的新草稿"));
    api.getAssistantConversation.mockResolvedValue(conversation({ draft_text: "旧服务端草稿", draft_version: 2 }));

    await act(async () => { await result.current.refresh(); });

    expect(result.current.draftText).toBe("尚未到自动保存时间的新草稿");
    expect(result.current.conversation?.draft_text).toBe("旧服务端草稿");
    expect(result.current.conversation?.draft_version).toBe(2);
  });

  it("does not let a failed restore read started after the failure wipe text typed while restore was pending", async () => {
    const initial = conversation();
    api.getCurrentAssistantConversation.mockResolvedValue(initial);
    const restore = deferred<Awaited<ReturnType<typeof api.restoreUnpersistedAssistantTurnDraft>>>();
    api.restoreUnpersistedAssistantTurnDraft.mockReturnValue(restore.promise);
    const { result } = renderHook(() => useAssistantConversation({ scope_kind: "home", scope_id: "home" }));
    await waitFor(() => expect(result.current.conversation?.id).toBe(initial.id));
    let turn!: ReturnType<typeof result.current.beginTurn>;
    await act(async () => { turn = result.current.beginTurn("提交中的原句"); await turn.draftCheckpoint; });

    let fail!: Promise<boolean>;
    act(() => { fail = result.current.failUnpersistedTurn(turn.conversationId, turn.localTurnId, "提交失败"); });
    act(() => result.current.setDraftText("恢复请求期间新写的草稿"));
    const staleRead = deferred<AssistantConversation>();
    api.getAssistantConversation.mockReturnValue(staleRead.promise);
    await act(async () => { restore.reject(new Error("恢复接口不可用")); await Promise.resolve(); });
    await waitFor(() => expect(api.getAssistantConversation).toHaveBeenCalledWith(initial.id));
    await act(async () => {
      staleRead.resolve(conversation({ draft_text: "提交中的原句", draft_version: 2 }));
      await fail;
    });

    expect(result.current.draftText).toBe("恢复请求期间新写的草稿");
    expect(result.current.error).toContain("恢复接口不可用");
  });

  it("drops only the failed optimistic turn when a successful restore races with a newer draft", async () => {
    const initial = conversation();
    api.getCurrentAssistantConversation.mockResolvedValue(initial);
    const restore = deferred<{ status: "restored"; conversation: AssistantConversation }>();
    api.restoreUnpersistedAssistantTurnDraft.mockReturnValue(restore.promise);
    const { result } = renderHook(() => useAssistantConversation({ scope_kind: "home", scope_id: "home" }));
    await waitFor(() => expect(result.current.conversation?.id).toBe(initial.id));
    let turn!: ReturnType<typeof result.current.beginTurn>;
    await act(async () => { turn = result.current.beginTurn("提交失败后可恢复的原句"); await turn.draftCheckpoint; });

    let fail!: Promise<boolean>;
    act(() => { fail = result.current.failUnpersistedTurn(turn.conversationId, turn.localTurnId, "连接中断"); });
    act(() => result.current.setDraftText("恢复完成前写下的新内容"));
    await act(async () => {
      restore.resolve({ status: "restored", conversation: conversation({ draft_text: "提交失败后可恢复的原句", draft_version: 3 }) });
      await fail;
    });

    expect(result.current.draftText).toBe("恢复完成前写下的新内容");
    expect(result.current.conversation?.draft_text).toBe("提交失败后可恢复的原句");
    expect(result.current.conversation?.draft_version).toBe(3);
    expect(result.current.conversation?.turns).toHaveLength(0);
  });

  it("rechecks the draft epoch after an already-persisted receipt's follow-up GET", async () => {
    const initial = conversation();
    api.getCurrentAssistantConversation.mockResolvedValue(initial);
    const { result } = renderHook(() => useAssistantConversation({ scope_kind: "home", scope_id: "home" }));
    await waitFor(() => expect(result.current.conversation?.id).toBe(initial.id));
    let turn!: ReturnType<typeof result.current.beginTurn>;
    let stream!: ReturnType<typeof result.current.bindTurnEvents>;
    await act(async () => {
      turn = result.current.beginTurn("已持久化的原句");
      stream = result.current.bindTurnEvents(initial.id, turn.localTurnId);
      await turn.draftCheckpoint;
    });
    act(() => stream({ type: "turn_started", turn_id: "persisted-turn", turn_state: "running", draft_version: 2 }));
    api.restoreUnpersistedAssistantTurnDraft.mockResolvedValue({
      status: "already_persisted", conversation: conversation({ draft_version: 3,
        turns: [{ id: "persisted-turn", sequence: 1, user_message: "已持久化的原句", assistant_text: "", status: "running", events: [], result: null, created_at: initial.created_at, updated_at: initial.updated_at }] }),
    });
    const latest = deferred<AssistantConversation>();
    api.getAssistantConversation.mockReturnValue(latest.promise);

    let fail!: Promise<boolean>;
    act(() => { fail = result.current.failUnpersistedTurn(turn.conversationId, turn.localTurnId, "连接中断"); });
    await waitFor(() => expect(api.getAssistantConversation).toHaveBeenCalledWith(initial.id));
    act(() => result.current.setDraftText("二次读取期间新写的草稿"));
    act(() => stream({ type: "text_delta", delta: "保留的实时增量" }));
    await act(async () => {
      latest.resolve(conversation({ draft_text: "已持久化的原句", draft_version: 2,
        turns: [{ id: "persisted-turn", sequence: 1, user_message: "已持久化的原句", assistant_text: "", status: "running", events: [], result: null, created_at: initial.created_at, updated_at: initial.updated_at }] }));
      await fail;
    });

    expect(result.current.draftText).toBe("二次读取期间新写的草稿");
    expect(result.current.conversation?.draft_text).toBe("已持久化的原句");
    expect(result.current.conversation?.draft_version).toBe(3);
    expect(result.current.conversation?.turns.at(-1)?.assistant_text).toBe("保留的实时增量");
  });
});
