import { act, cleanup, fireEvent, render, renderHook, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AssistantConversationPanel } from "../AssistantConversationPanel";
import { ConversationMessage } from "../ui/conversation-message";
import { ReferenceChips } from "../ui/reference-chips";
import { referenceAssistantKnowledge, referenceAssistantMaterial, referenceAssistantProfile, referenceAssistantSelection } from "@/lib/assistant-reference";
import { AssistantConversationProvider } from "../AssistantConversationProvider";
import { useAssistantConversation, assistantTaskTurnResult, assistantTurnHasUnknownRequest, assistantTurnNeedsResultRefresh } from "../useAssistantConversation";
import { CreationPlanApiError } from "@/lib/api";
import type { AssistantScriptWindowRequest } from "@/lib/types/assistant";
import type { AssistantConversation, AssistantStreamEvent, ResourceAdmissionReceipt } from "@/lib/types";
import type { AssistantStoryboardRegeneration } from "@/lib/types/assistant";

const api = vi.hoisted(() => ({
  enqueueAssistantInput: vi.fn(),
  readWindow: vi.fn(),
  withdrawAssistantInput: vi.fn(),
  getAssistantCapabilities: vi.fn(),
  getCurrentAssistantConversation: vi.fn(),
  listAssistantConversations: vi.fn(),
  updateAssistantConversation: vi.fn(),
  createAssistantConversation: vi.fn(),
  resumeAssistantConversation: vi.fn(),
  getAssistantConversation: vi.fn(),
  restoreUnpersistedAssistantTurnDraft: vi.fn(),
  stopAssistantConversationTurn: vi.fn(),
  getAssistantConversationReference: vi.fn(),
  saveAssistantConversationReference: vi.fn(),
  referenceSaved:new Map<string, import("@/lib/types").AssistantConversationReferenceState>(),
}));
vi.mock("@/lib/api", async importOriginal => ({
  ...api,
  CreationPlanApiError: (await importOriginal<typeof import("@/lib/api")>()).CreationPlanApiError,
  taskMediaJobLabel: (await importOriginal<typeof import("@/lib/api")>()).taskMediaJobLabel,
  taskMediaResourceCheckLabel: (await importOriginal<typeof import("@/lib/api")>()).taskMediaResourceCheckLabel,
  getAssistantCapabilities: api.getAssistantCapabilities.mockResolvedValue({
    application_commands: ["/new", "/name <名称>", "/resume <会话ID>", "/help"],
    runtime: { owner: "pi-coding-agent-sdk", version: "1.3.0", status: "ready", reason: "已就绪" },
    pi_tui_commands: { available: false, reason: "不接通", commands: [] },
    extension_commands: { available: false, reason: "当前不可用" },
    skills: { available: true, names: ["source-speech","narration-materials","local-edit"], reason: "按需加载" },
    prompt_templates: { available: true, names: ["review-result"], reason: "按需回读" },
    business_tools: { home: ["read_workspace_context"] },
  }),
}));

vi.mock("@/lib/api/assistant", async importOriginal => ({
  ...await importOriginal<typeof import("@/lib/api/assistant")>(),
  enqueueAssistantInput: api.enqueueAssistantInput, withdrawAssistantInput: api.withdrawAssistantInput,
}));

vi.mock("next/navigation", () => ({useRouter: () => ({push: vi.fn()})}));

vi.mock("@/hooks/useLocalMemberIdentity", () => ({ useLocalMemberIdentity: () => ({ token: "trusted-member" }) }));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

vi.mock("@/lib/api/creation-tasks", async original => ({ ...await original<typeof import("@/lib/api/creation-tasks")>(), getCreationTaskScriptWindowRequest: api.readWindow }));

beforeEach(() => {
  api.readWindow.mockReset();
  api.referenceSaved.clear();
  api.getAssistantConversationReference.mockImplementation(async (id:string) => api.referenceSaved.get(id) ?? {selected_reference:null,reference_version:0,status:"ready",reason:null});
  api.saveAssistantConversationReference.mockImplementation(async (id:string,reference:import("@/lib/types").AssistantSavedReference|null,version:number) => {
    const value={selected_reference:reference,reference_version:version+1,status:"ready" as const,reason:null};
    api.referenceSaved.set(id,value);return value;
  });
  api.updateAssistantConversation.mockImplementation(async (id: string, update: { draft_text?: string; expected_draft_version?: number; title?: string }) => ({
    ...conversation(), id, title: update.title ?? "新对话", draft_text: update.draft_text ?? "",
    draft_version: (update.expected_draft_version ?? 0) + 1,
  }));
  api.restoreUnpersistedAssistantTurnDraft.mockImplementation(async (id: string, request: {
    user_message: string; expected_draft_version: number;
  }) => ({
    status: "restored",
    conversation: {
      ...conversation(), id, draft_text: request.user_message,
      draft_version: request.expected_draft_version + 1,
    },
  }));
});

function conversation(scopeKind: "home" | "task" = "home", scopeId = scopeKind === "home" ? "home" : "task-one"): AssistantConversation {
  return {
    id: "conversation-one", workspace_id: "workspace-one", scope_kind: scopeKind, scope_id: scopeId,
    title: "新对话", draft_text: "", draft_version: 0, created_at: "2026-10-09T00:00:00Z", updated_at: "2026-10-09T00:00:00Z",
    turns: [{
      id: "turn-one", sequence: 1, user_message: "请先读当前方案",
      assistant_text: "已读取 **真实方案**。\n\n下一步可查看「本轮结果」。",
      status: "completed", created_at: "2026-10-09T00:00:00Z", updated_at: "2026-10-09T00:00:01Z",
      events: [
        { type: "tool_start", tool_id: 1, label: "读取当前方案" },
        { type: "text_delta", delta: "已读取 **真实方案**。\n\n下一步可查看「本轮结果」。" },
        { type: "tool_end", tool_id: 1, label: "读取当前方案", ok: true, summary: "已读取 1 项方案。" },
        { type: "tool_start", tool_id: 2, label: "检查新建方案" },
        { type: "tool_end", tool_id: 2, label: "检查新建方案", ok: false, summary: "这一步没有完成。" },
      ],
      result: { kind: "plan_preview", plan_preview: { confirmable: false } },
    }],
  };
}

function state(overrides: Record<string, unknown> = {}) {
  return {
    conversation: conversation(), conversations: [], loading: false, error: null,
    createNew: vi.fn(), resume: vi.fn(), rename: vi.fn(), stop: vi.fn(),
    isCurrentConversation: vi.fn((id: string) => id === conversation().id),
    ...overrides,
  } as never;
}

it("shows a real business continuation without fabricating a user message or empty edit action", () => {
  const saved = conversation("task");
  saved.turns = [{...saved.turns[0], user_message: "", assistant_text: "已读取实际输出。", status: "failed",
    events: [{type: "goal_continuation_started", source_kind: "business_event", original_turn_id: "original-human-turn",
      source_turn_id: "source-turn", event_id: "real-event"}] as unknown as AssistantConversation["turns"][number]["events"],
    result: null}];
  const rendered = render(<AssistantConversationPanel state={state({conversation: saved})} input="" onInput={vi.fn()}
    onSend={vi.fn()} busy={false} configured scopeLabel="当前任务"/>);
  expect(screen.getByText("继续完成原要求")).toBeVisible();
  expect(rendered.container.querySelector(".assistant-user-message")).toBeNull();
  expect(screen.queryByRole("button", {name: "继续编辑本条要求"})).not.toBeInTheDocument();
  expect(screen.getByText("接续未完成；原要求和实际结果保留在记录中。")).toBeVisible();
});

function projectedTaskConversation(status: "queued" | "unknown" | "proposed" | "confirmed" | "rejected"): AssistantConversation {
  const saved = conversation("task");
  const continuation = status === "queued" ? undefined : {type: "business_result", event_id: `event-${status}`,
    task_id: "task-one", status, actionable: status === "proposed", job_id: "job-preview", proposal_id: "proposal-preview",
    base_version_id: "version-base", result_version_id: status === "confirmed" ? "version-saved" : null,
    waiting_reason: status === "proposed" ? "请查看实际修改预览，再采用或拒绝。" : null};
  saved.turns = [{...saved.turns[0], assistant_text: "原始模型回复结束，后台结果仍以回读为准。", events: [],
    result: {kind: "task_turn", edit_preview: status === "proposed" || status === "confirmed",
      continuation, response: {reply: "预览结果", provider: "fixture", model: "fixture", script_draft: null,
        execution_action: null, execution_status: null, topic_candidates: null,
        edit_preview: status === "proposed" ? {intent: {task_id: "task-one"}, proposal: {id: "proposal-preview", status: "proposed"}} : null,
        media_job: status === "queued" ? {job_id: "job-preview", status: "queued", result_kind: "edit_preview",
          result_url: "/api/creation-tasks/task-one/media-jobs/job-preview", base_version_id: "version-base",
          result: null, waiting_reason: "等待本机资源可用", failure_reason: null, repair_action: null} : null,
        edit_request: status === "unknown" ? {task_id: "task-one", request_id: "request-original", phase: "preview",
          status: "unknown", query_url: "/api/creation-tasks/task-one/assistant/edit-requests/request-original?phase=preview",
          input_digest: null, result: null, submitted_input: {operation_type: "ReplaceClip", clip_id: "second"}} : null}}}];
  return saved;
}

it("distinguishes a refused output check from discarding an edit proposal", () => {
  const saved = projectedTaskConversation("rejected");
  const response = saved.turns[0].result!.response as Record<string, unknown>;
  response.edit_request = {task_id:"task-one", request_id:"original-observation", phase:"observation",
    status:"rejected", query_url:"/api/creation-tasks/task-one/assistant/edit-requests/original-observation?phase=observation"};
  render(<AssistantConversationPanel state={state({conversation:saved})} input="保留草稿" onInput={vi.fn()}
    onSend={vi.fn()} busy={false} configured scopeLabel="当前任务"/>);
  expect(screen.getByText("本次输出检查已被拒绝；原成片保留。")).toBeVisible();
  expect(screen.queryByText(/修改方案已放弃/)).not.toBeInTheDocument();
  expect(screen.getByRole("textbox")).toHaveValue("保留草稿");
});

function goalTaskConversation(status = "waiting_result") {
  const saved = projectedTaskConversation("queued");
  saved.turns[0].result = {...saved.turns[0].result, goal_continuation: {
    original_turn_id: saved.turns[0].id, current_turn_id: saved.turns[0].id, status,
    workspace_id: saved.workspace_id, conversation_id: saved.id, task_id: "task-one",
    waiting_reason: status === "waiting_adoption" ? "await_real_adoption" : "read_original_request_result",
    unfinished_steps: ["observe_actual_result", "complete_original_instruction"],
  }};
  return saved;
}

describe("actual goal progress", () => {
  it("shows verified production completion without claiming output inspection or reviving an old failure", () => {
    const saved = goalTaskConversation("production_completed");
    saved.turns[0].status = "failed";
    const goal = saved.turns[0].result!.goal_continuation as Record<string, unknown>;
    goal.waiting_reason = "output_not_checked";
    goal.production_result = { scope: "production", status: "succeeded", job_id: "finished-job", timeline_version_id: "v5", video_ready: true };
    render(<AssistantConversationPanel state={state({conversation: saved})} input="" onInput={vi.fn()} onSend={vi.fn()} busy={false} configured scopeLabel="当前任务" />);
    const summary = screen.getByLabelText("本次目标执行状态");
    expect(summary).toHaveTextContent("本次目标 · 成片已生成");
    expect(screen.getByText("本轮回复曾中断；成片已生成，输出尚未检查，历史记录保留。")).toBeVisible();
    fireEvent.click(summary);
    expect(screen.getByText("成片已生成；输出尚未检查。")).toBeVisible();
    expect(screen.queryByLabelText("实际输出检查范围")).not.toBeInTheDocument();
    expect(screen.queryByText("接续未取得新进展")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "查看对应任务与成片" })).toHaveAttribute("href", "/tasks/task-one");
  });
  it("distinguishes a returned submission, a queued job and an actual video, without adding a completion estimate", () => {
    const saved = goalTaskConversation();
    saved.turns[0].events = [{type: "tool_end", tool_id: 1, label: "开始制作", ok: true, summary: "制作请求已返回，当前作业已排队。"}];
    saved.turns[0].result!.response = {...saved.turns[0].result!.response as Record<string, unknown>,
      execution_status: {task_id: "task-one", job: {id: "actual-job", status: "queued"}, current_stage: "render",
        video_ready: false, stages: [{stage: "render", status: "pending"}], waiting_reason: "等待设备空闲"}};
    const view = render(<AssistantConversationPanel state={state({conversation: saved})} input="下一条草稿" onInput={vi.fn()}
      onSend={vi.fn()} busy={false} configured scopeLabel="当前任务"/>);
    const summary = screen.getByLabelText("本次目标执行状态");
    expect(summary).toHaveTextContent("本次目标 · 等待后台结果");
    expect(summary).toHaveTextContent("生成成片 · 已排队");
    const details = summary.closest("details")!;
    expect(details).not.toHaveAttribute("open");
    expect(within(details).getByText("开始制作 · 已返回")).toBeInTheDocument();
    expect(within(details).getByText("生成成片 · 待执行")).toBeInTheDocument();
    expect(within(details).queryByText(/成片已生成/)).not.toBeInTheDocument();
    expect(view.container.querySelector('[role="progressbar"]')).toBeNull();
    const response = saved.turns[0].result!.response as Record<string, unknown>;
    const execution = response.execution_status as Record<string, unknown>;
    execution.job = {id: "actual-job", status: "succeeded"};
    view.rerender(<AssistantConversationPanel state={state({conversation: saved})} input="下一条草稿" onInput={vi.fn()}
      onSend={vi.fn()} busy={false} configured scopeLabel="当前任务"/>);
    expect(within(details).getByText("制作作业已完成，有效成片仍待核实。")).toBeInTheDocument();
    execution.video_ready = true;
    view.rerender(<AssistantConversationPanel state={state({conversation: saved})} input="下一条草稿" onInput={vi.fn()}
      onSend={vi.fn()} busy={false} configured scopeLabel="当前任务"/>);
    expect(within(details).getByText("成片已生成，当前输出有效。")).toBeInTheDocument();
    expect(screen.getByRole("textbox")).toHaveValue("下一条草稿");
  });

  it.each(["waiting_adoption", "waiting_unknown"])("shows the actual %s decision and queries without sending or replacing the next draft", async status => {
    const saved = goalTaskConversation(status);
    const refresh = vi.fn().mockResolvedValue(saved), send = vi.fn(), input = vi.fn();
    render(<AssistantConversationPanel state={state({conversation: saved, refresh})} input="先写下一条" onInput={input}
      onSend={send} busy={false} configured scopeLabel="当前任务"/>);
    const summary = screen.getByLabelText("本次目标执行状态");
    expect(summary).toHaveTextContent(status === "waiting_adoption" ? "等待查看并采用修改" : "请求结果待核实");
    fireEvent.click(summary);
    fireEvent.click(screen.getByRole("button", {name: "查询目标进展"}));
    await waitFor(() => expect(refresh).toHaveBeenCalledOnce());
    expect(send).not.toHaveBeenCalled();
    expect(input).not.toHaveBeenCalled();
    expect(screen.getByRole("textbox")).toHaveValue("先写下一条");
  });

  it("shows the safe, concrete tool rejection and its repair action while retaining the failed record", () => {
    const saved = conversation();
    saved.turns[0].events = [{type: "tool_end", tool_id: 3, label: "调整画面", ok: false, summary: "这一步没有完成。",
      failure: {reason: "曝光数值超出允许范围（-2 到 2）。", repair_action: "请使用范围内的曝光值重新准备修改。"}}];
    render(<AssistantConversationPanel state={state({conversation: saved})} input="" onInput={vi.fn()}
      onSend={vi.fn()} busy={false} configured scopeLabel="当前任务"/>);
    expect(screen.getByLabelText(/工具记录/).closest("details")).not.toHaveAttribute("open");
    expect(within(screen.getByLabelText(/工具记录/)).getByText("曝光数值超出允许范围（-2 到 2）。")).toBeVisible();
    expect(within(screen.getByLabelText(/工具记录/)).getByText("请使用范围内的曝光值重新准备修改。")).toBeVisible();
    fireEvent.click(screen.getByLabelText(/工具记录/));
    expect(screen.getByText("调整画面 · 未完成")).toBeVisible();
    expect(screen.queryByText("这一步没有完成。")).not.toBeInTheDocument();
  });

  it("does not leave an unreturned tool running after the model turn stops", () => {
    const saved = conversation();
    saved.turns[0] = {...saved.turns[0], status: "stopped", events: [{type: "tool_start", tool_id: 5, label: "提交制作"}], result: null};
    render(<AssistantConversationPanel state={state({conversation: saved})} input="" onInput={vi.fn()}
      onSend={vi.fn()} busy={false} configured scopeLabel="当前任务"/>);
    expect(screen.getByText("提交制作 · 未收到结果")).toBeInTheDocument();
    expect(screen.queryByText("提交制作 · 进行中")).not.toBeInTheDocument();
    expect(screen.getByText(/不能据此判断后台作业是否完成/)).toBeInTheDocument();
  });

  it("does not revive an older goal after a newer user instruction or trust another workspace's projection", () => {
    const saved = goalTaskConversation();
    const panel = () => <AssistantConversationPanel state={state({conversation: saved})} input="下一条" onInput={vi.fn()}
      onSend={vi.fn()} busy={false} configured scopeLabel="当前任务"/>;
    const view = render(panel());
    expect(screen.getByLabelText("本次目标执行状态")).toBeInTheDocument();
    saved.turns.push({...conversation().turns[0], id: "new-instruction", sequence: 2, user_message: "先暂停制作，只读当前资料", result: null});
    view.rerender(panel());
    expect(screen.queryByLabelText("本次目标执行状态")).not.toBeInTheDocument();
    saved.turns.pop();
    (saved.turns[0].result!.goal_continuation as Record<string, unknown>).workspace_id = "other-workspace";
    view.rerender(panel());
    expect(screen.queryByLabelText("本次目标执行状态")).not.toBeInTheDocument();
    expect(screen.getByRole("textbox")).toHaveValue("下一条");
  });

  it("uses the host's bounded completion proof to mark recovery and explains the actual inspection limits", () => {
    const saved = goalTaskConversation("no_progress");
    saved.turns[0].status = "failed";
    saved.turns[0].events = [{type: "tool_start", tool_id: 8, label: "开始制作"}];
    const goal = saved.turns[0].result!.goal_continuation as Record<string, unknown>;
    goal.waiting_reason = "bounded_output_checkpoint_completed";
    // Empty unfinished steps alone are deliberately not a completion certificate.
    goal.unfinished_steps = [];
    const panel = () => <AssistantConversationPanel state={state({conversation: saved})} input="下一条草稿" onInput={vi.fn()}
      onSend={vi.fn()} busy={false} configured scopeLabel="当前任务"/>;
    const view = render(panel());
    expect(screen.getByLabelText("本次目标执行状态")).toHaveTextContent("接续未取得新进展");
    expect(screen.queryByText(/制作与技术检查已恢复完成/)).not.toBeInTheDocument();
    goal.completed_output_checkpoint = {status: "completed", scope: "production_and_output_observation",
      job_id: "real-creation", observation_job_id: "real-observation", timeline_version_id: "actual-version",
      sha256: "a".repeat(64), measurement_scope: "actual_output_bounded_frames", unavailable: [
        {scope: "visual_semantics", reason: "像素测量不代表看懂内容。"},
        {scope: "speech_and_listening", reason: "本次没有转写或听感检查。"},
      ]};
    view.rerender(panel());
    const summary = screen.getByLabelText("本次目标执行状态");
    expect(summary).toHaveTextContent("制作与技术检查已完成");
    expect(screen.getByText("本轮回复曾中断；制作与技术检查已恢复完成，历史失败保留。")).toBeVisible();
    fireEvent.click(summary);
    const details = summary.closest("details")!;
    expect(within(details).getByText(/实际成片的有限抽帧与媒体参数，未逐帧检查/)).toBeVisible();
    expect(within(details).getByText("像素测量不代表看懂内容。")).toBeVisible();
    expect(within(details).getByText("本次没有转写或听感检查。")).toBeVisible();
    expect(within(details).getByText("这份技术检查不能替代对全部创作要求的验收。")).toBeVisible();
    expect(within(details).getByText("开始制作 · 未收到结果")).toBeVisible();
    expect(within(details).getByRole("link", {name: "查看对应任务与成片"})).toHaveAttribute("href", "/tasks/task-one");
    expect(saved.turns[0].status).toBe("failed");
    expect(screen.getByRole("textbox")).toHaveValue("下一条草稿");
    goal.status = "invalidated";
    view.rerender(panel());
    expect(summary).toHaveTextContent("原目标已停止接续");
    expect(screen.queryByText(/制作与技术检查已恢复完成/)).not.toBeInTheDocument();
    expect(screen.queryByRole("link", {name: "查看对应任务与成片"})).not.toBeInTheDocument();
  });

  it("retains the expanded goal and middle reading while a bound continuation appends real steps", async () => {
    const saved = goalTaskConversation();
    const positions = {current: new Map<string, number>()}, following = {current: new Map<string, boolean>()};
    const expanded = {current: new Map<string, Map<string, boolean>>()};
    const panel = () => <AssistantConversationPanel state={state({conversation: saved, historyScrollPositions: positions,
      historyFollowBottom: following, historyExpandedDetails: expanded})} input="保留下一条草稿" onInput={vi.fn()}
      onSend={vi.fn()} busy={false} configured scopeLabel="当前任务"/>;
    const view = render(panel());
    const summary = screen.getByLabelText("本次目标执行状态");
    fireEvent.click(summary);
    await waitFor(() => expect(expanded.current.get(saved.id)?.get("goal:turn-one")).toBe(true));
    const viewport = screen.getByRole("region", {name: "持久对话记录"});
    Object.defineProperties(viewport, {scrollHeight: {configurable: true, value: 1400}, clientHeight: {configurable: true, value: 400}});
    viewport.scrollTop = 200;
    fireEvent.scroll(viewport);
    saved.turns.push({...saved.turns[0], id: "real-auto-child", sequence: 2, user_message: "", status: "running", result: null,
      events: [{type: "goal_continuation_started", source_kind: "business_event", original_turn_id: "turn-one", source_turn_id: "turn-one", event_id: "real-job-finished"},
        {type: "tool_start", tool_id: 9, label: "检查实际输出"}]});
    (saved.turns[0].result!.goal_continuation as Record<string, unknown>).current_turn_id = "real-auto-child";
    (saved.turns[0].result!.goal_continuation as Record<string, unknown>).status = "running";
    view.rerender(panel());
    expect(summary.closest("details")).toHaveAttribute("open");
    expect(summary).toHaveTextContent("检查实际输出 · 进行中");
    expect(viewport.scrollTop).toBe(200);
    expect(following.current.get(saved.id)).toBe(false);
    expect(screen.getByRole("textbox")).toHaveValue("保留下一条草稿");
    view.unmount();
    render(panel());
    expect(screen.getByLabelText("本次目标执行状态").closest("details")).toHaveAttribute("open");
    expect(screen.getByRole("textbox")).toHaveValue("保留下一条草稿");
  });
});

const resourceReceipt: ResourceAdmissionReceipt = {
  observation_id: "resource-observation", device_request_id: "device-request", task_id: "task-one", job_id: "job-preview",
  input_digest: "frozen-input", base_version_id: "version-base", attempt_number: 1, last_grant_generation: null,
  phase: "admission", observation_kind: "first_refused", checked_at: "2026-10-13T01:02:03Z",
  observed_interval_seconds: 0.1, context: "pending_request", admitted: false,
  checks: [{metric: "cpu_percent", unit: "percent", actual: 95.6, minimum: null, maximum: 91, passed: false},
    {metric: "available_memory_bytes", unit: "bytes", actual: 1073741824, minimum: 3221225472, maximum: null, passed: false}],
  summary: "首次检查时设备资源余量不足，本请求仍在等待。", repair_action: "请等待资源恢复后查询原请求。",
};

describe('persisted section and restore receipts',()=>{
  it.each(['apply','restore'])('shows saved authorization checks separately from successful %s and device admission',kind=>{
    const saved=projectedTaskConversation('queued');
    const response=saved.turns[0].result!.response as Record<string,unknown>;
    response.media_job=null;response.edit_preview=null;
    const panel=()=> <AssistantConversationPanel state={state({conversation:saved})} input="" onInput={vi.fn()} onSend={vi.fn()} busy={false} configured scopeLabel="当前任务"/>;
    const view=render(panel());
    for (const status of ['not_ready','authorization_ready',null]) {
      const readiness=status ? {status,reason_code:status==='not_ready'?'task_authorization_expired':null,device_admission:'not_checked'} : null;
      if(kind==='apply') {
        saved.turns[0].result!.timeline_action='apply';
        response.timeline_action='apply';
        response.edit_request={phase:'apply',status:'recorded',result:{execution_readiness:readiness}};
      } else response.timeline_restore={kind:'timeline_restore_request',action:'query',request_id:'original',
        receipt:{status:'applied',execution_readiness:readiness}};
      view.rerender(panel());
      if(status==='not_ready') expect(screen.getByText('本次保存时后台执行授权未就绪（原授权已过期）；修改已保留。')).toBeVisible();
      else expect(screen.queryByText(/后台执行授权未就绪/)).not.toBeInTheDocument();
      if(status==='authorization_ready') expect(screen.getByText('本次保存时后台执行授权已核验；设备准入尚未检查。')).toBeVisible();
      else expect(screen.queryByText(/后台执行授权已核验/)).not.toBeInTheDocument();
    }
  });
  it('shows an invalidated section as history and offers original request readback for unknown restoration',()=>{
    const saved=projectedTaskConversation('queued');
    const response=saved.turns[0].result!.response as Record<string,unknown>;
    response.media_job=null;response.edit_preview=null;
    response.segment_revision={kind:'segment_revision',action:'query',request_id:'section-request',revision:{
      task_id:'task-one',request_id:'section-request',status:'succeeded',adoption:{status:'invalidated',reason:'section_render_inputs_changed'}}};
    const panel=()=> <AssistantConversationPanel state={state({conversation:saved})} input="" onInput={vi.fn()} onSend={vi.fn()} busy={false} configured scopeLabel="当前任务"/>;
    const view=render(panel());
    expect(screen.getByText('原段产物仅保留为历史，当前作品未采用')).toBeVisible();
    expect(screen.getByText('当前片段的画面、声音或字幕已变化，原视频仅供历史查看。')).toBeVisible();
    expect(screen.queryByText('section_render_inputs_changed')).not.toBeInTheDocument();
    response.segment_revision=null;
    response.timeline_restore={kind:'timeline_restore_request',action:'undo',request_id:'restore-original',receipt:null,
      failure:{reason:'响应中断',submission_effect:'unknown',repair_action:'查询原请求'}};
    view.rerender(panel());
    expect(screen.getByText('撤销或重做结果尚未知；请查询原请求')).toBeVisible();
    expect(screen.getByRole('button',{name:'查询本轮结果'})).toBeVisible();
  });
});

describe("persisted storyboard regeneration receipts", () => {
  function storyboard(status: Extract<AssistantStoryboardRegeneration, {kind: "storyboard_regeneration_receipt"}>["receipt"]["status"],
    action: "submit" | "query" = "submit"): AssistantStoryboardRegeneration {
    return {kind: "storyboard_regeneration_receipt", action,
      receipt: {request_id: "storyboard-original", task_id: "task-one", job_id: "job-storyboard", status,
        stage: "montage_plan", script_id: "script-original", narration_cache_key: "narration-original",
        base_timeline_version_id: "version-original", base_timeline_sequence: 1,
        result: status === "succeeded" ? {request_id: "storyboard-original", montage_plan_id: "montage-saved",
          timeline_version_id: "version-saved", timeline_sequence: 2, operation_id: "operation-saved"} : null,
        failure_reason: status === "failed" ? "来源已变化，原故事板保留。" : null,
        result_url: "/api/creation-tasks/task-one/montage/plan/requests/storyboard-original"}};
  }
  function withStoryboard(outcome: AssistantStoryboardRegeneration) {
    const saved = projectedTaskConversation("queued");
    saved.turns[0].assistant_text = "";
    const response = saved.turns[0].result!.response as Record<string, unknown>;
    response.media_job = null;
    response.storyboard_regeneration = outcome;
    return saved;
  }

  it.each([
    ["queued", "故事板重算已排队；尚未保存新故事板版本。"],
    ["running", "正在重算故事板；尚未保存新故事板版本。"],
    ["interrupted", "故事板重算已中断；尚未核实新故事板版本是否保存。"],
    ["succeeded", "新故事板版本已保存，可撤销；成片尚未重新生成。"],
    ["failed", "故事板重算失败；原稿保留。"],
    ["cancelled", "故事板重算已取消；未保存新故事板版本。"],
  ] as const)("shows the persisted %s storyboard result without claiming a video", (status, label) => {
    const saved = withStoryboard(storyboard(status));
    const original = structuredClone(saved);
    render(<AssistantConversationPanel state={state({conversation: saved})} input="保留下一条草稿" onInput={vi.fn()}
      onSend={vi.fn()} busy={false} configured scopeLabel="当前任务"/>);
    const details = screen.getByText("本轮结果").closest<HTMLElement>(".assistant-turn-result")!;
    if (status === "failed") expect(details).toHaveAttribute("open");
    else expect(details.querySelector("summary")).toBeNull();
    expect(within(details).getByText(label)).toBeVisible();
    expect(details).not.toHaveTextContent(/预览已安排|成片已生成|视频已完成|已提交开始制作/);
    if (status === "failed") expect(within(details).getByText("来源已变化，原故事板保留。")).toBeVisible();
    expect(screen.getByRole("textbox")).toHaveValue("保留下一条草稿");
    expect(saved).toEqual(original);
  });

  it.each(["unknown", "rejected"] as const)("keeps the original %s request without declaring success or retrying", kind => {
    const outcome: AssistantStoryboardRegeneration = {
      kind: kind === "unknown" ? "storyboard_regeneration_unknown" : "storyboard_regeneration_rejected",
      action: "submit", request_id: "storyboard-original",
      query_url: "/api/creation-tasks/task-one/montage/plan/requests/storyboard-original",
      failure: {reason: "原请求响应未能核验。", repair_action: "请查询原请求，不重复提交。"},
    };
    const saved = withStoryboard(outcome);
    const original = structuredClone(saved);
    const onSend = vi.fn();
    render(<AssistantConversationPanel state={state({conversation: saved})} input="" onInput={vi.fn()}
      onSend={onSend} busy={false} configured scopeLabel="当前任务"/>);
    const details = screen.getByText("本轮结果").closest<HTMLElement>(".assistant-turn-result")!;
    expect(within(details).getByText(kind === "unknown"
      ? "故事板重算结果尚未核实；原请求定位已保留，请查询原请求，不重复提交。"
      : "故事板重算请求已被拒绝；未取得新故事板版本。")).toBeVisible();
    expect(within(details).getByText("原请求响应未能核验。")).toBeVisible();
    expect(within(details).getByText("请查询原请求，不重复提交。")).toBeVisible();
    expect(details).not.toHaveTextContent(/新故事板版本已保存|成片已生成/);
    if (kind === "unknown") expect(details).not.toHaveTextContent(/重算失败|请求已被拒绝/);
    expect(onSend).not.toHaveBeenCalled();
    expect(saved).toEqual(original);
  });

  it.each(["queued", "succeeded"] as const)("identifies a historical query with a %s receipt as the original request", status => {
    const saved = withStoryboard(storyboard(status, "query"));
    render(<AssistantConversationPanel state={state({conversation: saved})} input="" onInput={vi.fn()}
      onSend={vi.fn()} busy={false} configured scopeLabel="当前任务"/>);
    const details = screen.getByText("本轮结果").closest<HTMLElement>(".assistant-turn-result")!;
    expect(within(details).getByText(status === "queued"
      ? "原故事板重算请求：故事板重算已排队；尚未保存新故事板版本。"
      : "原请求记录：故事板版本已保存；不代表当前版本或成片已更新。")).toBeVisible();
    expect(details).not.toHaveTextContent("新故事板版本已保存");
  });
});

describe("persisted business result cards", () => {
  it("shows the first same-request refusal beside the current wait and preserves the next draft", () => {
    const saved = projectedTaskConversation("queued");
    const response = saved.turns[0].result!.response as Record<string, unknown>;
    response.media_job = {...response.media_job as Record<string, unknown>, waiting_reason: "resource_busy", resource_wait: resourceReceipt};
    const original = structuredClone(saved);
    const onInput = vi.fn(), onSend = vi.fn();
    const rendered = render(<AssistantConversationPanel state={state({conversation: saved})} input="保留下一条草稿" onInput={onInput}
      onSend={onSend} busy={false} configured scopeLabel="当前任务"/>);
    const details = screen.getByText("本轮结果").closest<HTMLElement>(".assistant-turn-result")!;
    const receipt = within(details).getByText("首次资源检查 · 未通过").closest("details")!;
    expect(details).toHaveAttribute("open");
    expect(receipt).toHaveAttribute("open");
    expect(within(details).getByText("等待处理：本机资源检查尚未通过，正在等待满足制作条件。")).toBeVisible();
    expect(within(receipt).getByText(resourceReceipt.summary)).toBeVisible();
    expect(within(receipt).getByText("CPU 使用率：实测 95.6%；不高于 91%；检查未通过。")).toBeVisible();
    expect(within(receipt).getByText("可用内存：实测 1,073,741,824 字节；至少 3,221,225,472 字节；检查未通过。")).toBeVisible();
    expect(rendered.container.querySelector('time[datetime="2026-10-13T01:02:03Z"]')).toHaveTextContent("2026/10/13 01:02:03 UTC");
    expect(within(receipt).getByText("采样窗口：0.1 秒。")).toBeVisible();
    expect(within(receipt).getByText(resourceReceipt.repair_action)).toBeVisible();
    expect(details).not.toHaveTextContent(/实时|当前 CPU|预览已生成|device-request|frozen-input/);
    expect(screen.getByRole("textbox")).toHaveValue("保留下一条草稿");
    expect(onInput).not.toHaveBeenCalled();
    expect(onSend).not.toHaveBeenCalled();
    expect(saved).toEqual(original);
  });

  it("follows an arriving receipt for the same queued Job and preserves reading when it becomes history", () => {
    const frames = new Map<number, FrameRequestCallback>();
    let frameId = 0, height = 600;
    const request = vi.spyOn(window, "requestAnimationFrame").mockImplementation(callback => {
      frames.set(++frameId, callback); return frameId;
    });
    const cancel = vi.spyOn(window, "cancelAnimationFrame").mockImplementation(id => { frames.delete(id); });
    const paint = () => { const pending = [...frames.values()]; frames.clear(); pending.forEach(callback => callback(0)); };
    const reading = {historyScrollPositions: {current: new Map<string, number>()},
      historyFollowBottom: {current: new Map<string, boolean>()},
      historyExpandedDetails: {current: new Map<string, Map<string, boolean>>()}};
    const saved = projectedTaskConversation("queued");
    const panel = (conversation: AssistantConversation) => <AssistantConversationPanel state={state({conversation, ...reading})}
      input="保留下一条草稿" onInput={vi.fn()} onSend={vi.fn()} busy={false} configured scopeLabel="当前任务"/>;
    try {
      const view = render(panel(saved));
      const history = screen.getByLabelText("持久对话记录");
      Object.defineProperties(history, {clientHeight: {configurable: true, value: 200},
        scrollHeight: {configurable: true, get: () => height}});
      act(paint);
      history.scrollTop = 400;
      fireEvent.scroll(history);
      const pending = structuredClone(saved);
      const response = pending.turns[0].result!.response as Record<string, unknown>;
      response.media_job = {...response.media_job as Record<string, unknown>, resource_wait: resourceReceipt};
      height = 1000;
      view.rerender(panel(pending));
      act(paint);
      expect(history.scrollTop).toBe(800);
      expect(screen.getByText(resourceReceipt.summary)).toBeVisible();
      history.scrollTop = 120;
      fireEvent.scroll(history);
      const cancelled = structuredClone(pending);
      const cancelledResponse = cancelled.turns[0].result!.response as Record<string, unknown>;
      cancelledResponse.media_job = {...cancelledResponse.media_job as Record<string, unknown>, status: "cancelled",
        resource_wait: {...resourceReceipt, context: "history"}};
      view.rerender(panel(cancelled));
      act(paint);
      expect(history.scrollTop).toBe(120);
      expect(screen.getByText("历史资源检查 · 未通过").closest("details")).not.toHaveAttribute("open");
      expect(screen.getByRole("textbox")).toHaveValue("保留下一条草稿");
    } finally { request.mockRestore(); cancel.mockRestore(); }
  });

  it("shows unknown first observations without manufacturing a refusal, values or a sampling window", () => {
    const saved = projectedTaskConversation("queued");
    const response = saved.turns[0].result!.response as Record<string, unknown>;
    const receipt: ResourceAdmissionReceipt = {...resourceReceipt, observation_kind: "first_unknown", admitted: null,
      observed_interval_seconds: null, summary: "首次检查时暂不能可靠读取可用内存。", checks: [{
        metric: "available_memory_bytes", unit: "bytes", actual: null, minimum: 3221225472, maximum: null, passed: null}]};
    response.media_job = {...response.media_job as Record<string, unknown>, waiting_reason: "resource_unknown", resource_wait: receipt};
    render(<AssistantConversationPanel state={state({conversation: saved})} input="保留下一条草稿" onInput={vi.fn()}
      onSend={vi.fn()} busy={false} configured scopeLabel="当前任务"/>);
    const record = screen.getByText("首次资源检查 · 暂未核实").closest("details")!;
    expect(record).toHaveAttribute("open");
    expect(within(record).getByText("可用内存：实测值未知；至少 3,221,225,472 字节；检查结果未知。")).toBeVisible();
    expect(record).not.toHaveTextContent(/实测 0|检查未通过|采样窗口|预览已生成/);
    expect(screen.getByRole("textbox")).toHaveValue("保留下一条草稿");
  });

  it("describes an expired observation as a check rather than an unapplied edit", () => {
    const saved = projectedTaskConversation("queued");
    const response = saved.turns[0].result!.response as Record<string, unknown>;
    response.media_job = {...response.media_job as Record<string, unknown>, status:"stale", result_kind:"observation"};
    render(<AssistantConversationPanel state={state({conversation:saved})} input="" onInput={vi.fn()}
      onSend={vi.fn()} busy={false} configured scopeLabel="当前任务"/>);
    expect(screen.getByText("原输出检查已失效，仅保留历史；请按当前条件重新检查。")).toBeVisible();
    expect(screen.queryByText(/原修改结果已过期/)).not.toBeInTheDocument();
  });

  it.each([
    ["cancelled", "cancelled", "后台处理已取消，未据此应用修改。"],
    ["stale", "expired", "原修改结果已过期，仅保留为历史，不能沿旧版本应用。"],
    ["succeeded", "proposed", "实际修改预览已返回，等待你查看并决定是否采用；当前草稿尚未改变。"],
  ])("keeps %s state while putting its prior refusal in history", (status, outcome, label) => {
    const saved = projectedTaskConversation("queued");
    const response = saved.turns[0].result!.response as Record<string, unknown>;
    response.media_job = {...response.media_job as Record<string, unknown>, status, waiting_reason: null,
      resource_wait: {...resourceReceipt, context: "history", summary: "首次检查时设备资源余量不足。"}};
    if (status === "succeeded") response.edit_preview = {proposal: {id: "proposal-preview", status: "proposed"}};
    saved.turns[0].result!.continuation = {type: "business_result", status: outcome, actionable: outcome === "proposed"};
    render(<AssistantConversationPanel state={state({conversation: saved})} input="保留下一条草稿" onInput={vi.fn()}
      onSend={vi.fn()} busy={false} configured scopeLabel="当前任务"/>);
    const details = screen.getByText("本轮结果").closest<HTMLElement>(".assistant-turn-result")!;
    const history = within(details).getByText("历史资源检查 · 未通过").closest("details")!;
    expect(within(details).getByText(label)).toBeVisible();
    expect(history).not.toHaveAttribute("open");
    expect(within(history).getByText("首次检查时设备资源余量不足。")).not.toBeVisible();
    if (!details.hasAttribute("open")) fireEvent.click(details.querySelector("summary")!);
    fireEvent.click(within(history).getByText("历史资源检查 · 未通过"));
    expect(within(history).getByText("这是该请求的历史检查记录，不能代表当前设备资源或执行许可。")).toBeVisible();
    expect(within(history).getByText("CPU 使用率：实测 95.6%；不高于 91%；检查未通过。")).toBeVisible();
    expect(screen.queryByText(/等待处理：|首次资源检查 ·/)).not.toBeInTheDocument();
    expect(screen.getByRole("textbox")).toHaveValue("保留下一条草稿");
  });

  it.each([null, undefined])("keeps generic waiting text when resource_wait is %s", resource_wait => {
    const saved = projectedTaskConversation("queued");
    const response = saved.turns[0].result!.response as Record<string, unknown>;
    response.media_job = {...response.media_job as Record<string, unknown>, waiting_reason: "resource_busy", resource_wait};
    render(<AssistantConversationPanel state={state({conversation: saved})} input="保留下一条草稿" onInput={vi.fn()}
      onSend={vi.fn()} busy={false} configured scopeLabel="当前任务"/>);
    expect(screen.getByText("等待处理：本机资源检查尚未通过，正在等待满足制作条件。")).toBeVisible();
    expect(screen.queryByText(/首次资源检查|历史资源检查|CPU|字节|首次检查时间/)).not.toBeInTheDocument();
  });

  it.each([
    ["resource_busy", "本机资源检查尚未通过，正在等待满足制作条件。"],
    ["resource_unknown", "暂不能确认设备资源是否足够。"],
  ])("shows the actual %s wait in the default-open result without inventing resource measurements", (reason, label) => {
    const saved = projectedTaskConversation("queued");
    const response = saved.turns[0].result!.response as Record<string, unknown>;
    response.media_job = {...response.media_job as Record<string, unknown>, waiting_reason: reason};
    saved.turns[0].result!.continuation = {status: "queued", actionable: false, waiting_reason: reason};
    render(<AssistantConversationPanel state={state({conversation: saved})} input="保留下一条草稿" onInput={vi.fn()}
      onSend={vi.fn()} busy={false} configured scopeLabel="当前任务"/>);
    const details = screen.getByText("本轮结果").closest<HTMLElement>(".assistant-turn-result")!;
    expect(details).toHaveAttribute("open");
    expect(within(details).getByText(`等待处理：${label}`)).toBeVisible();
    expect(within(details).queryByText(reason)).not.toBeInTheDocument();
    expect(details).not.toHaveTextContent(/处理未完成|交换空间|GiB|CPU|已生成预览/);
    expect(screen.getByRole("textbox")).toHaveValue("保留下一条草稿");
    expect(response.media_job).toMatchObject({job_id: "job-preview", waiting_reason: reason});
  });

  it("shows the actual cancelled conversation receipt without its generic unfinished-processing reason", () => {
    const saved = projectedTaskConversation("queued");
    const response = saved.turns[0].result!.response as Record<string, unknown>;
    response.media_job = {...response.media_job as Record<string, unknown>, status: "cancelled",
      waiting_reason: null, failure_reason: "user_cancelled", repair_action: null};
    saved.turns[0].result!.continuation = {status: "cancelled", actionable: false,
      waiting_reason: "媒体处理未完成；查看作业原因后再决定下一步。"};
    render(<AssistantConversationPanel state={state({conversation: saved})} input="保留下一条草稿" onInput={vi.fn()}
      onSend={vi.fn()} busy={false} configured scopeLabel="当前任务"/>);
    const details = screen.getByText("本轮结果").closest<HTMLElement>(".assistant-turn-result")!;
    expect(details.querySelector("summary")).toBeNull();
    expect(within(details).queryByText("预览已取消")).not.toBeInTheDocument();
    expect(within(details).getByText("后台处理已取消，未据此应用修改。")).toBeVisible();
    expect(details).not.toHaveTextContent(/user_cancelled|处理未完成|这次操作没有完成|预览已生成|修改已应用/);
    expect(screen.getByRole("textbox")).toHaveValue("保留下一条草稿");
    expect(response.media_job).toMatchObject({job_id: "job-preview", status: "cancelled",
      waiting_reason: null, failure_reason: "user_cancelled", repair_action: null});
    expect(saved.turns[0].result!.continuation).toMatchObject({status: "cancelled",
      waiting_reason: "媒体处理未完成；查看作业原因后再决定下一步。"});
  });

  it("retains an actual failed media job's reason and repair action", () => {
    const saved = projectedTaskConversation("queued");
    const response = saved.turns[0].result!.response as Record<string, unknown>;
    response.media_job = {...response.media_job as Record<string, unknown>, status: "failed", waiting_reason: null,
      failure_reason: "当前镜头源文件不可读取。", repair_action: "请重新定位镜头源素材。"};
    saved.turns[0].result!.continuation = {status: "failed", actionable: false, waiting_reason: null};
    render(<AssistantConversationPanel state={state({conversation: saved})} input="" onInput={vi.fn()}
      onSend={vi.fn()} busy={false} configured scopeLabel="当前任务"/>);
    const details = screen.getByText("本轮结果").closest<HTMLElement>(".assistant-turn-result")!;
    expect(details).toHaveAttribute("open");
    expect(within(details).getByText("后台处理未完成；请查看实际原因，当前草稿未因此改变。")).toBeVisible();
    expect(within(details).getByText("当前镜头源文件不可读取。")).toBeVisible();
    expect(within(details).getByText("请重新定位镜头源素材。")).toBeVisible();
    expect(within(details).queryByText("预览已取消")).not.toBeInTheDocument();
  });

  it("allows a failed model turn's host-verified actionable preview to be read, while stopped and unverified failures remain closed", async () => {
    const saved = projectedTaskConversation("proposed");
    saved.turns[0].status = "failed";
    const refresh = vi.fn().mockResolvedValue(saved), open = vi.fn();
    const panel = () => <AssistantConversationPanel state={state({conversation: saved, refresh})} input="" onInput={vi.fn()}
      onSend={vi.fn()} onOpenEditProposal={open} busy={false} configured scopeLabel="当前任务"/>;
    const view = render(panel());
    fireEvent.click(screen.getByRole("button", {name: "查看修改方案"}));
    await waitFor(() => expect(open).toHaveBeenCalledWith(saved.turns[0]));
    expect(refresh).toHaveBeenCalledOnce();
    saved.turns[0].status = "stopped";
    view.rerender(panel());
    expect(screen.queryByRole("button", {name: "查看修改方案"})).not.toBeInTheDocument();
    saved.turns[0].status = "failed";
    delete saved.turns[0].result!.continuation;
    view.rerender(panel());
    expect(screen.queryByRole("button", {name: "查看修改方案"})).not.toBeInTheDocument();
  });

  it.each([
    ["save", "已保存"], ["query", "已回读原工具"], ["update", "新版本"], ["revoke", "已停用"],
  ] as const)("shows the actual %s tool receipt without offering edit adoption", (action, text) => {
    const saved = projectedTaskConversation("queued");
    saved.turns[0].result = {...saved.turns[0].result, response: {
      ...saved.turns[0].result!.response as Record<string, unknown>, media_job: null,
      script_tool: {action, status: "recorded", tool_id: "tool-real", tool_version: 2,
        expected_version: 1, query_url: "/api/media-script-tools/tool-real", tool: {name: "小标题"}},
    }};
    render(<AssistantConversationPanel state={state({conversation: saved})} input="" onInput={vi.fn()}
      onSend={vi.fn()} onOpenEditProposal={vi.fn()} busy={false} configured scopeLabel="当前任务"/>);
    expect(screen.getByText(new RegExp(text))).toBeVisible();
    expect(screen.getByText(/v2/)).toBeVisible();
    expect(screen.queryByRole("button", {name: "查看修改方案"})).not.toBeInTheDocument();
    expect(screen.queryByRole("button", {name: "查询本轮结果"})).not.toBeInTheDocument();
  });

  it("shows an unknown CAS write as unverified while retaining the actual source and the original query", async () => {
    const saved = projectedTaskConversation("queued");
    saved.turns[0].result = {...saved.turns[0].result, response: {
      ...saved.turns[0].result!.response as Record<string, unknown>,
      media_job: {status: "succeeded", result_kind: "graphic_source", result: {source_ref: "source", artifact_id: "image"}},
      script_tool: {action: "update", status: "unknown", tool_id: "tool-real", tool_version: 3,
        expected_version: 1, query_url: "/api/media-script-tools/tool-real", submitted_input: {expected_version: 1}},
    }};
    const refresh = vi.fn().mockResolvedValue(saved), send = vi.fn();
    render(<AssistantConversationPanel state={state({conversation: saved, refresh})} input="新草稿" onInput={vi.fn()}
      onSend={send} onOpenEditProposal={vi.fn()} busy={false} configured scopeLabel="当前任务"/>);
    expect(screen.getByText(/v3.*不能证明本次更新成功/)).toBeVisible();
    expect(screen.getByText(/图片来源已生成，可以继续预览它在视频里的效果/)).toBeVisible();
    expect(screen.queryByText(/新版本.*已保存/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", {name: "查询本轮结果"}));
    await waitFor(() => expect(refresh).toHaveBeenCalledOnce());
    expect(send).not.toHaveBeenCalled();
    expect(screen.queryByRole("button", {name: "查看修改方案"})).not.toBeInTheDocument();
  });

  it("shows a real succeeded PNG source without presenting it as an applied edit or finished video", () => {
    const saved = projectedTaskConversation("queued");
    const result = saved.turns[0].result!;
    result.response = {...result.response as Record<string, unknown>, media_job: {
      job_id: "script-job", status: "succeeded", result_kind: "graphic_source", base_version_id: "version-base",
      result: {source_ref: "artifact:graphic-actual", artifact_id: "graphic-actual", sha256: "a".repeat(64),
        size_bytes: 156, name: "panel.png", width: 64, height: 64, applied: false}, failure_reason: null,
      waiting_reason: null, repair_action: null}};
    render(<AssistantConversationPanel state={state({conversation: saved})} input="" onInput={vi.fn()}
      onSend={vi.fn()} onOpenEditProposal={vi.fn()} busy={false} configured scopeLabel="当前任务"/>);
    expect(screen.getByText(/图片来源已生成，可以继续预览它在视频里的效果/)).toBeVisible();
    expect(screen.getByText(/尚未应用到草稿或生成成片/)).toBeVisible();
    expect(screen.queryByRole("button", {name: "查看修改方案"})).not.toBeInTheDocument();
  });
  it("opens the actionable proposal from the same freshly read turn", async () => {
    const saved = projectedTaskConversation("proposed");
    const open = vi.fn(), refresh = vi.fn().mockResolvedValue(saved);
    render(<AssistantConversationPanel state={state({conversation: saved, refresh})} input="" onInput={vi.fn()}
      onSend={vi.fn()} onOpenEditProposal={open} busy={false} configured scopeLabel="当前任务"/>);
    fireEvent.click(screen.getByRole("button", {name: "查看修改方案"}));
    await waitFor(() => expect(open).toHaveBeenCalledWith(saved.turns[0]));
  });

  it("uses the expired projection instead of an older unknown-request flag", () => {
    const saved = projectedTaskConversation("unknown");
    saved.turns[0].result = {...saved.turns[0].result, continuation: {type: "business_result", event_id: "event-stale",
      status: "stale", actionable: false, waiting_reason: "仅保留原目标的历史结果"}};
    render(<AssistantConversationPanel state={state({conversation: saved})} input="" onInput={vi.fn()}
      onSend={vi.fn()} onOpenEditProposal={vi.fn()} busy={false} configured scopeLabel="当前任务"/>);
    expect(screen.getByText(/原修改结果已过期/)).toBeVisible();
    expect(screen.queryByRole("button", {name: "查询本轮结果"})).not.toBeInTheDocument();
    expect(screen.queryByRole("button", {name: "查看修改方案"})).not.toBeInTheDocument();
  });

  it.each(["queued", "unknown"] as const)("shows actual %s after the model ends and queries without sending or changing the focused draft", async status => {
    const saved = projectedTaskConversation(status);
    const refresh = vi.fn().mockResolvedValue(saved), send = vi.fn(), input = vi.fn();
    render(<AssistantConversationPanel state={state({conversation: saved, refresh})} input="正在写的新草稿"
      onInput={input} onSend={send} busy={false} configured scopeLabel="当前任务"/>);
    const textbox = screen.getByRole("textbox");
    textbox.focus();
    const query = screen.getByRole("button", {name: "查询本轮结果"});
    expect(query).toBeVisible();
    fireEvent.click(query);
    await waitFor(() => expect(refresh).toHaveBeenCalledOnce());
    expect(send).not.toHaveBeenCalled();
    expect(input).not.toHaveBeenCalled();
    expect(textbox).toHaveValue("正在写的新草稿");
    expect(textbox).toHaveFocus();
    expect(screen.queryByText("正在回复…")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", {name: "查看修改方案"})).not.toBeInTheDocument();
    if (status === "unknown") expect(screen.getByText(/请求结果尚未核实/)).toBeVisible();
    else expect(screen.getByText(/预览已安排，正在等待后台处理/)).toBeVisible();
  });

  it("opens only the freshly read actionable proposal and removes the adopted card's action", async () => {
    const pending = projectedTaskConversation("proposed"), adopted = projectedTaskConversation("confirmed");
    const open = vi.fn(), refresh = vi.fn().mockResolvedValue(adopted);
    const panel = (saved: AssistantConversation) => <AssistantConversationPanel state={state({conversation: saved, refresh})}
      input="" onInput={vi.fn()} onSend={vi.fn()} onOpenEditProposal={open} busy={false} configured scopeLabel="当前任务"/>;
    const view = render(panel(pending));
    fireEvent.click(screen.getByRole("button", {name: "查看修改方案"}));
    await waitFor(() => expect(refresh).toHaveBeenCalledOnce());
    expect(open).not.toHaveBeenCalled();
    view.rerender(panel(adopted));
    expect(screen.queryByRole("button", {name: "查看修改方案"})).not.toBeInTheDocument();
    expect(screen.getByText(/修改已应用并保存到草稿/)).toBeVisible();
    expect(screen.queryByRole("button", {name: "查询本轮结果"})).not.toBeInTheDocument();
  });

  it("preserves scroll position when an earlier turn receives an asynchronous result", () => {
    const saved = projectedTaskConversation("queued");
    saved.turns.push({...conversation().turns[0], id: "later-turn", sequence: 2});
    const positions = {current: new Map<string, number>()}, following = {current: new Map<string, boolean>()};
    const panel = () => <AssistantConversationPanel state={state({conversation: saved, historyScrollPositions: positions,
      historyFollowBottom: following})} input="下一条草稿" onInput={vi.fn()} onSend={vi.fn()} busy={false} configured scopeLabel="当前任务"/>;
    const view = render(panel());
    const viewport = screen.getByRole("region", {name: "持久对话记录"});
    Object.defineProperties(viewport, {scrollHeight: {configurable: true, value: 1400}, clientHeight: {configurable: true, value: 400}});
    viewport.scrollTop = 230;
    fireEvent.scroll(viewport);
    saved.turns[0] = projectedTaskConversation("proposed").turns[0];
    view.rerender(panel());
    expect(viewport.scrollTop).toBe(230);
    expect(following.current.get(saved.id)).toBe(false);
    expect(screen.getByRole("textbox")).toHaveValue("下一条草稿");
  });
});

describe("AssistantConversationPanel", () => {
  it("returns to the latest reply explicitly without enabling follow while reading earlier messages", () => {
    const saved = conversation();
    const positions = { current: new Map<string, number>() }, following = { current: new Map<string, boolean>() };
    render(<AssistantConversationPanel state={state({ conversation: saved, historyScrollPositions: positions, historyFollowBottom: following })}
      input="" onInput={vi.fn()} onSend={vi.fn()} busy={false} configured scopeLabel="当前任务"/>);
    const viewport = screen.getByRole("region", { name: "持久对话记录" });
    Object.defineProperties(viewport, { scrollHeight: { configurable: true, value: 1400 }, clientHeight: { configurable: true, value: 400 } });
    viewport.scrollTop = 200;
    fireEvent.scroll(viewport);
    expect(following.current.get(saved.id)).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "回到最新回复" }));
    expect(viewport.scrollTop).toBe(1000);
    expect(positions.current.get(saved.id)).toBe(1000);
    expect(following.current.get(saved.id)).toBe(true);
    expect(screen.queryByRole("button", { name: "回到最新回复" })).not.toBeInTheDocument();
  });
  it.each(["completed", "stopped", "failed"] as const)("withholds this turn's reply actions while streaming and exposes only truthful %s actions at its terminal state", terminal => {
    const saved = conversation();
    const partial = { ...saved.turns[0], id: "streamed-turn", sequence: 2, user_message: "当前要求",
      assistant_text: "**已经生成的部分正文**", status: "running" as const, events: [], result: null };
    saved.turns = [saved.turns[0], partial];
    const handoff = vi.fn();
    const panel = () => <AssistantConversationPanel state={state({ conversation: saved })} input="下一条草稿"
      onInput={vi.fn()} onSend={vi.fn()} onCreatePlanFromReply={handoff} busy={false} configured scopeLabel="当前任务"/>;
    const view = render(panel());
    const article = () => view.container.querySelectorAll(".assistant-persisted-turn")[1] as HTMLElement;
    expect(within(article()).getByText("已经生成的部分正文")).toBeVisible();
    expect(within(article()).queryByRole("button", { name: "复制回复" })).not.toBeInTheDocument();
    expect(within(article()).queryByRole("button", { name: "回复操作" })).not.toBeInTheDocument();
    expect(within(view.container.querySelectorAll(".assistant-persisted-turn")[0] as HTMLElement).getByRole("button", { name: "复制回复" })).toBeInTheDocument();
    expect(screen.getByRole("textbox")).toHaveValue("下一条草稿");
    expect(screen.getByText("正在回复…")).toBeInTheDocument();
    saved.turns = [saved.turns[0], { ...partial, status: terminal,
      assistant_text: `${partial.assistant_text}\n\n收到的后续正文。` }];
    view.rerender(panel());
    expect(within(article()).getByRole("button", { name: "复制回复" })).toBeInTheDocument();
    expect(within(article()).getByText("收到的后续正文。")).toBeVisible();
    expect(screen.queryByText("正在回复…")).not.toBeInTheDocument();
    if (terminal === "completed") {
      fireEvent.click(within(article()).getByRole("button", { name: "回复操作" }));
      expect(screen.getByRole("menuitem", { name: "带入新任务文案" })).toBeInTheDocument();
    } else {
      expect(within(article()).queryByRole("button", { name: "回复操作" })).not.toBeInTheDocument();
      expect(within(article()).getByRole("button", { name: "继续编辑本条要求" })).toBeDisabled();
    }
    expect(screen.getByRole("textbox")).toHaveValue("下一条草稿");
    expect(handoff).not.toHaveBeenCalled();
  });

  it("keeps a greeting to readable text and small actions, with keyboard access to reply use", () => {
    const saved = conversation();
    saved.turns = [{ ...saved.turns[0], user_message: "你好", assistant_text: "你好，有什么想做的？", events: [], result: { kind: "conversation" } }];
    const handoff = vi.fn();
    render(<AssistantConversationPanel state={state({ conversation: saved })} input="" onInput={vi.fn()} onSend={vi.fn()}
      onCreatePlanFromReply={handoff} busy={false} configured scopeLabel="首页创作"/>);
    expect(screen.getByText("你好，有什么想做的？")).toBeVisible();
    expect(screen.queryByText("本轮结果")).not.toBeInTheDocument();
    expect(screen.queryByText(/未记录领域写入/)).not.toBeInTheDocument();
    expect(screen.queryByRole("menuitem")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "复制回复" }).textContent).toBe("");
    const options = screen.getByRole("button", { name: "回复操作" });
    options.focus();
    fireEvent.keyDown(options, { key: "ArrowDown" });
    expect(screen.getByRole("menuitem", { name: "带入新任务文案" })).toHaveFocus();
    fireEvent.keyDown(document.activeElement!, { key: "Escape" });
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    expect(options).toHaveFocus();
    fireEvent.keyDown(options, { key: "ArrowDown" });
    fireEvent.click(screen.getByRole("menuitem", { name: "带入新任务文案" }));
    expect(handoff).toHaveBeenCalledWith(saved.turns[0]);
  });

  it("mounts a single header action set into its own shell slot and preserves actual change callbacks", async () => {
    const saved = conversation();
    const historic = { ...saved, id: "historic", title: "旧对话", draft_text: "旧草稿" };
    const createNew = vi.fn().mockResolvedValue({ ...saved, id: "new", draft_text: "" });
    const resume = vi.fn().mockResolvedValue(historic);
    const onInput = vi.fn(), changed = vi.fn(), send = vi.fn();
    const controller = state({ createNew, resume, loadList: vi.fn().mockResolvedValue([saved, historic]),
      conversations: [saved, historic], setError: vi.fn() });
    const view = render(<section aria-label="创作助手"><header><div data-assistant-header-actions/></header>
      <AssistantConversationPanel state={controller} input="当前草稿" onInput={onInput} onSend={send}
        onConversationChange={changed} busy={false} configured scopeLabel="首页" legacyHistory={<details><summary>旧版只读历史</summary><p>任务原始旧回复</p></details>}/></section>);
    const slot = view.container.querySelector<HTMLElement>("[data-assistant-header-actions]")!;
    expect(within(slot).getByRole("button", { name: "新对话" })).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "新对话" })).toHaveLength(1);
    expect(view.container.querySelector(".assistant-conversation-caption .assistant-header-actions")).toBeNull();
    expect(screen.queryByText("旧版只读历史")).not.toBeInTheDocument();
    fireEvent.click(within(slot).getByRole("button", { name: "新对话" }));
    await waitFor(() => expect(changed).toHaveBeenCalledTimes(1));
    expect(onInput).toHaveBeenLastCalledWith("");
    fireEvent.click(within(slot).getByRole("button", { name: "历史对话" }));
    const dialog = await screen.findByRole("dialog", { name: "历史对话" });
    expect(within(dialog).getByText("旧版只读历史")).toBeInTheDocument();
    expect(within(dialog).getByText("任务原始旧回复")).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: /旧对话/ }));
    await waitFor(() => expect(resume).toHaveBeenCalledWith("historic"));
    expect(onInput).toHaveBeenLastCalledWith("旧草稿");
    expect(changed).toHaveBeenCalledTimes(2);
    expect(send).not.toHaveBeenCalled();
  });

  it("shows a readable failure first and keeps raw JSON and codes in collapsed diagnostics", () => {
    const raw = '{"detail":{"reason":"provider_output_invalid","request_id":"qa-id"}}';
    render(<AssistantConversationPanel state={state({ error: raw })} input="" onInput={vi.fn()} onSend={vi.fn()}
      busy={false} configured scopeLabel="首页"/>);
    const alert = screen.getByRole("alert");
    expect(alert.firstElementChild).toHaveTextContent("这次操作没有完成，请打开诊断查看原因。");
    expect(alert.firstElementChild).not.toHaveTextContent("provider_output_invalid");
    const diagnostic = within(alert).getByText("诊断详情").closest("details")!;
    expect(diagnostic).not.toHaveAttribute("open");
    expect(diagnostic).toHaveTextContent(raw);
  });

  it("folds the actual Chinese invalid-JSON failure and single-underscore code without filtering successful JSON discussion", () => {
    const saved = conversation();
    saved.turns = [{ ...saved.turns[0], assistant_text: "成功回复可以讨论 JSON，也可以引用‘文字模型返回的内容不是有效 JSON’作为例子。", events: [], result: { kind: "conversation" } }];
    const panel = (error: string | null) => <AssistantConversationPanel state={state({ conversation: saved, error })}
      input="" onInput={vi.fn()} onSend={vi.fn()} busy={false} configured scopeLabel="首页"/>;
    const view = render(panel(null));
    for (const message of ["文字模型返回的内容不是有效 JSON", "provider_error"]) {
      view.rerender(panel(message));
      const alert = screen.getByRole("alert");
      expect(alert.firstElementChild).toHaveTextContent("这次操作没有完成，请打开诊断查看原因。");
      expect(alert.firstElementChild).not.toHaveTextContent(message);
      const diagnostic = within(alert).getByText("诊断详情").closest("details")!;
      expect(diagnostic).not.toHaveAttribute("open");
      expect(diagnostic.querySelector("pre")).toHaveTextContent(message);
      expect(screen.getByText(saved.turns[0].assistant_text)).toBeVisible();
    }
    view.rerender(panel(null));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.queryByText("诊断详情")).not.toBeInTheDocument();
    expect(screen.getByText(saved.turns[0].assistant_text)).toBeVisible();
  });

  it("follows streamed text and tools near the bottom, preserves middle reading across navigation, and resumes only after scrolling down", async () => {
    api.getCurrentAssistantConversation.mockResolvedValue(conversation());
    api.listAssistantConversations.mockResolvedValue({ items: [conversation()] });
    const frames = new Map<number, FrameRequestCallback>();
    let frameId = 0;
    const request = vi.spyOn(window, "requestAnimationFrame").mockImplementation(callback => {
      frames.set(++frameId, callback); return frameId;
    });
    const cancel = vi.spyOn(window, "cancelAnimationFrame").mockImplementation(id => { frames.delete(id); });
    const paint = () => { const pending = [...frames.values()]; frames.clear(); pending.forEach(callback => callback(0)); };
    let controller!: ReturnType<typeof useAssistantConversation>;
    let height = 600;
    function Harness({ page }: { page: string }) {
      controller = useAssistantConversation({ scope_kind: "home", scope_id: "home" });
      return <AssistantConversationPanel key={page} state={controller} input="" onInput={vi.fn()}
        onSend={vi.fn()} busy={false} configured scopeLabel={page} />;
    }
    const measure = () => {
      const node = screen.getByLabelText("持久对话记录");
      Object.defineProperties(node, {
        clientHeight: { configurable: true, value: 200 },
        scrollHeight: { configurable: true, get: () => height },
      });
      return node;
    };
    try {
      const view = render(<Harness page="首页" />);
      await waitFor(() => expect(controller.loading).toBe(false));
      let history = measure();
      act(paint);
      history.scrollTop = 380; // 20 px from the bottom, inside the 48 px tolerance.
      fireEvent.scroll(history);
      let turn!: ReturnType<typeof controller.beginTurn>;
      await act(async () => { turn = controller.beginTurn("读取当前事实"); await turn.draftCheckpoint; });
      const stream = controller.bindTurnEvents(turn.conversationId, turn.localTurnId);
      act(() => {
        stream({ type: "turn_started", turn_id: "streamed-turn", turn_state: "running", draft_version: 2 });
        stream({ type: "text_delta", delta: "第一段真实流式回复。" });
      });
      height = 900;
      act(paint);
      expect(history.scrollTop).toBe(700);
      expect(controller.historyScrollPositions.current.get(turn.conversationId)).toBe(700);
      history.scrollTop = 120;
      fireEvent.scroll(history);
      act(() => stream({ type: "text_delta", delta: "后续回复继续增长。" }));
      height = 1400;
      act(paint);
      expect(history.scrollTop).toBe(120);
      view.rerender(<Harness page="素材库" />);
      history = measure();
      act(paint);
      expect(history.scrollTop).toBe(120);
      act(() => stream({ type: "text_delta", delta: "跨导航后仍在运行。" }));
      height = 1600;
      act(paint);
      expect(history.scrollTop).toBe(120);
      history.scrollTop = 1400;
      fireEvent.scroll(history);
      act(() => stream({ type: "tool_start", tool_id: 1, label: "读取当前任务" }));
      height = 1700;
      act(paint);
      expect(history.scrollTop).toBe(1500);
      act(() => stream({ type: "tool_end", tool_id: 1, label: "读取当前任务", ok: true, summary: "已读取实际资料。" }));
      height = 1800;
      // Scrolling upward before the scheduled frame must cancel its pending follow.
      history.scrollTop = 100;
      fireEvent.scroll(history);
      act(paint);
      expect(history.scrollTop).toBe(100);
      view.unmount();
    } finally { request.mockRestore(); cancel.mockRestore(); }
  });

  it("restores two failed-turn reading expansions before restoring scroll after navigation remount", async () => {
    const saved = conversation();
    saved.turns = [1, 2].map(sequence => ({
      ...saved.turns[0], id: `failed-${sequence}`, sequence, status: "failed" as const,
      assistant_text: "", result: { kind: "failure", message: `API unreachable: result ${sequence}` },
      events: [{ type: "tool_end" as const, tool_id: 1, label: "读取当前任务", ok: false,
                 summary: `API unreachable: tool ${sequence}` }],
    }));
    api.getCurrentAssistantConversation.mockResolvedValue(saved);
    api.listAssistantConversations.mockResolvedValue({ items: [saved] });
    const frames = new Map<number, FrameRequestCallback>();
    let frameId = 0;
    const request = vi.spyOn(window, "requestAnimationFrame").mockImplementation(callback => {
      frames.set(++frameId, callback); return frameId;
    });
    const cancel = vi.spyOn(window, "cancelAnimationFrame").mockImplementation(id => { frames.delete(id); });
    const paint = () => { const pending = [...frames.values()]; frames.clear(); pending.forEach(callback => callback(0)); };
    try {
      const hook = renderHook(() => useAssistantConversation({ scope_kind: "home", scope_id: "home" }));
      await waitFor(() => expect(hook.result.current.loading).toBe(false));
      const panel = () => <AssistantConversationPanel state={hook.result.current} input="" onInput={vi.fn()}
        onSend={vi.fn()} busy={false} configured scopeLabel="当前对话" />;
      const first = render(panel());
      act(paint);
      first.container.querySelectorAll<HTMLDetailsElement>("details[data-reading-key]").forEach(detail => {
        detail.open = true;
        fireEvent(detail, new Event("toggle"));
      });
      const history = screen.getByLabelText("持久对话记录");
      history.scrollTop = 420;
      fireEvent.scroll(history);
      first.unmount();
      // Navigation remount initially has no hydrated turns; readiness must trigger restoration.
      const next = render(<AssistantConversationPanel state={{ ...hook.result.current, loading: true,
        conversation: { ...saved, turns: [] } }} input="" onInput={vi.fn()} onSend={vi.fn()}
        busy={false} configured scopeLabel="素材库" />);
      act(paint);
      next.rerender(panel());
      const restoredDetails = next.container.querySelectorAll<HTMLDetailsElement>("details[data-reading-key]");
      expect(restoredDetails).toHaveLength(8); // Both results, tool groups, and their diagnostic children.
      expect([...restoredDetails].every(detail => detail.open)).toBe(true);
      const restoredHistory = screen.getByLabelText("持久对话记录");
      let scroll = 0;
      Object.defineProperty(restoredHistory, "scrollTop", { configurable: true,
        get: () => scroll, set: value => { scroll = [...restoredDetails].every(detail => detail.open) ? Math.min(value, 500) : 0; },
      });
      expect(scroll).toBe(0);
      act(paint);
      expect(scroll).toBe(420);
      next.rerender(<AssistantConversationPanel state={{ ...hook.result.current,
        conversation: { ...saved, id: "another-conversation" } }} input="" onInput={vi.fn()}
        onSend={vi.fn()} busy={false} configured scopeLabel="另一段对话" />);
      expect([...next.container.querySelectorAll<HTMLDetailsElement>("details[data-reading-key]")].every(detail => !detail.open)).toBe(true);
      act(paint);
      expect(scroll).toBe(0);
      next.unmount();
    } finally { request.mockRestore(); cancel.mockRestore(); }
  });

  it("offers an explicit reply transfer and a second confirmation before replacing a draft", () => {
    const onTransferReply = vi.fn();
    render(<AssistantConversationPanel state={state()} input="" onInput={vi.fn()} onSend={vi.fn()}
      onTransferReply={onTransferReply} transferDraftState="draft" busy={false} configured scopeLabel="当前任务" />);
    fireEvent.click(screen.getByRole("button", { name: "回复操作" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "替换当前草稿并带入回复" }));
    expect(onTransferReply).not.toHaveBeenCalled();
    expect(screen.getByText("这会覆盖当前待确认文案；已确认的文案不会被替换。")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "确认替换并带入回复" }));
    expect(onTransferReply).toHaveBeenCalledWith(expect.objectContaining({ id: "turn-one" }), true);
  });

  it("offers an explicit new-task handoff for a completed home reply", () => {
    const onCreatePlanFromReply = vi.fn();
    render(<AssistantConversationPanel state={state()} input="" onInput={vi.fn()} onSend={vi.fn()}
      onCreatePlanFromReply={onCreatePlanFromReply} busy={false} configured scopeLabel="首页创作" />);
    fireEvent.click(screen.getByRole("button", { name: "回复操作" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "带入新任务文案" }));
    expect(onCreatePlanFromReply).toHaveBeenCalledWith(expect.objectContaining({ id: "turn-one", status: "completed" }));
    expect(screen.queryByText("先确认制作方案和选题；这条回复只作为文案来源，不代替资料事实。")).not.toBeInTheDocument();
  });

  it("renders one final Markdown body and one ordered tool summary with discoverable failures", async () => {
    const { container } = render(<AssistantConversationPanel state={state()} input="" onInput={vi.fn()} onSend={vi.fn()}
      busy={false} configured scopeLabel="首页创作" />);

    expect(screen.getByText("真实方案").tagName).toBe("STRONG");
    expect(screen.getAllByRole("button", { name: "复制回复" })).toHaveLength(1);
    const tools = container.querySelector(".assistant-turn-tools summary")!;
    expect(tools).toHaveAccessibleName("工具记录 · 2 项 · 含失败记录");
    const assistantText = screen.getByText("真实方案");
    expect(assistantText.compareDocumentPosition(tools) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    const toolDetails = tools.closest("details")!;
    expect(toolDetails).not.toHaveAttribute("open");
    fireEvent.click(tools);
    expect(within(toolDetails).getAllByRole("listitem")).toHaveLength(2);
    expect(within(toolDetails).getByText("读取当前方案 · 已返回")).toBeInTheDocument();
    expect(within(toolDetails).getByText("检查新建方案 · 未完成")).toBeInTheDocument();
    fireEvent.click(tools);
    expect(within(toolDetails).getByText("已读取 1 项方案。")).toBeInTheDocument();
    expect(within(toolDetails).getByText("这一步没有完成。")).toBeInTheDocument();
    expect(screen.getByText("本轮结果").closest("details")).not.toHaveAttribute("open");

    await screen.findByRole("button", {name:"技术能力状态（诊断）"});
    expect(screen.queryByText("对话操作")).not.toBeInTheDocument();
    const diagnostics = screen.getByRole("button", { name: "技术能力状态（诊断）" });
    expect(diagnostics.closest(".assistant-conversation-head")).not.toBeInTheDocument();
    expect(diagnostics.closest(".assistant-header-actions")).toBeInTheDocument();
    expect(diagnostics.closest("form")).not.toBeInTheDocument();
    expect(diagnostics).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText(/本机 Pi 隔离版本检查/)).not.toBeInTheDocument();
    fireEvent.click(diagnostics);
    expect(diagnostics).toHaveAttribute("aria-expanded", "true");
    expect(document.getElementById(diagnostics.getAttribute("aria-controls")!)).toBeInTheDocument();
    expect(screen.getByText(/创作技能：原声口播、旁白配素材、局部改片/)).toBeInTheDocument();
  });

  it("uses the complete persisted assistant text when it extends past streamed deltas", () => {
    const saved = conversation();
    saved.turns = [{ ...saved.turns[0], assistant_text: "完整的 Markdown 正文\n\n**终态尾段**。",
      events: [{ type: "text_delta", delta: "完整的 Markdown 正文" }] }];
    render(<AssistantConversationPanel state={state({ conversation: saved })} input="" onInput={vi.fn()} onSend={vi.fn()}
      busy={false} configured scopeLabel="首页创作" />);
    expect(screen.getByText("终态尾段").tagName).toBe("STRONG");
    expect(screen.queryByText("完整的 Markdown 正文完整的 Markdown 正文")).not.toBeInTheDocument();
  });

  it("sends on plain Enter only, preserves Shift+Enter and IME composition, and restores input focus", async () => {
    const onSend = vi.fn().mockResolvedValue(undefined);
    render(<AssistantConversationPanel state={state()} input="下一条要求" onInput={vi.fn()} onSend={onSend}
      busy={false} configured scopeLabel="首页创作" />);
    const input = screen.getByRole("textbox", { name: "对话要求" });

    fireEvent.keyDown(input, { key: " " });
    expect(onSend).not.toHaveBeenCalled();
    fireEvent.keyDown(input, { key: "Enter", shiftKey: true });
    expect(onSend).not.toHaveBeenCalled();
    fireEvent.compositionStart(input);
    fireEvent.keyDown(input, { key: "Enter", isComposing: true, keyCode: 229 });
    expect(onSend).not.toHaveBeenCalled();
    fireEvent.compositionEnd(input);
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onSend).toHaveBeenCalledWith("下一条要求");
    await Promise.resolve();
    expect(document.activeElement).toBe(input);
  });

  it.each(["/new", "/resume conversation-two"]) ("loads the returned draft after %s instead of the previous selection", async (command) => {
    const next = { ...conversation(), id: command.startsWith("/new") ? "conversation-new" : "conversation-two",
      draft_text: "恢复到这个会话的草稿", turns: [] };
    const onInput = vi.fn();
    const current = state({
      createNew: vi.fn().mockResolvedValue(next),
      resume: vi.fn().mockResolvedValue(next),
    });
    render(<AssistantConversationPanel state={current} input={command} onInput={onInput} onSend={vi.fn()}
      busy={false} configured scopeLabel="首页创作" />);

    await screen.findByRole("button", { name: "技术能力状态（诊断）" });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    await waitFor(() => expect(onInput).toHaveBeenCalledWith(next.draft_text));
  });

  it.each(["/new", "/name 会话甲", "/resume conversation-two", "/help"].flatMap((command) =>
    (["button", "enter"] as const).map((activation) => [command, activation] as const))) (
    "keeps %s available without a configured model via %s", async (command, activation) => {
    const next = { ...conversation(), id: "conversation-next", draft_text: "保留的当前草稿" };
    const renamed = { ...conversation(), title: "会话甲", draft_text: "保留的当前草稿" };
    const createNew = vi.fn().mockResolvedValue(next);
    const rename = vi.fn().mockResolvedValue(renamed);
    const resume = vi.fn().mockResolvedValue(next);
    const current = state({
      createNew, rename, resume,
    });
    const onSend = vi.fn();
    const onInput = vi.fn();
    const onConversationChange = vi.fn();
    render(<AssistantConversationPanel state={current} input={command} onInput={onInput} onSend={onSend}
      onConversationChange={onConversationChange} busy={false} configured={false} scopeLabel="首页创作" />);
    await screen.findByRole("button", { name: "技术能力状态（诊断）" });
    const send = screen.getByRole("button", { name: "发送" });
    expect(send).toBeEnabled();
    expect(send).toHaveAccessibleDescription( "Enter 执行本地会话操作；无效命令不会发送给模型");

    if (activation === "button") fireEvent.click(send);
    else fireEvent.keyDown(screen.getByRole("textbox", { name: "对话要求" }), { key: "Enter" });

    if (command === "/new") {
      await waitFor(() => expect(createNew).toHaveBeenCalledOnce());
      expect(onConversationChange).toHaveBeenCalledOnce();
      expect(onInput).toHaveBeenCalledWith(next.draft_text);
    } else if (command.startsWith("/name")) {
      await waitFor(() => expect(rename).toHaveBeenCalledWith("会话甲"));
      expect(onInput).toHaveBeenCalledWith(renamed.draft_text);
      expect(onConversationChange).not.toHaveBeenCalled();
    } else if (command.startsWith("/resume")) {
      await waitFor(() => expect(resume).toHaveBeenCalledWith("conversation-two"));
      expect(onConversationChange).toHaveBeenCalledOnce();
      expect(onInput).toHaveBeenCalledWith(next.draft_text);
    } else {
      await waitFor(() => expect(onInput).toHaveBeenCalledWith("/"));
      expect(createNew).not.toHaveBeenCalled();
      expect(rename).not.toHaveBeenCalled();
      expect(resume).not.toHaveBeenCalled();
    }
    expect(onSend).not.toHaveBeenCalled();
  });

  it.each(["button", "enter"] as const)("keeps invalid commands local when no model is configured via %s", async (activation) => {
    const onSend = vi.fn();
    render(<AssistantConversationPanel state={state()} input="/unknown value" onInput={vi.fn()} onSend={onSend}
      busy={false} configured={false} scopeLabel="首页创作" />);
    await screen.findByRole("button", { name: "技术能力状态（诊断）" });
    const send = screen.getByRole("button", { name: "发送" });
    expect(send).toBeEnabled();

    if (activation === "button") fireEvent.click(send);
    else fireEvent.keyDown(screen.getByRole("textbox", { name: "对话要求" }), { key: "Enter" });

    expect(await screen.findByRole("alert")).toHaveTextContent("此命令格式不正确或当前不可用，没有发送给模型");
    expect(onSend).not.toHaveBeenCalled();
  });

  it("blocks ordinary text consistently when the model is unconfigured", async () => {
    const onSend = vi.fn();
    render(<AssistantConversationPanel state={state()} input="继续创作" onInput={vi.fn()} onSend={onSend}
      busy={false} configured={false} scopeLabel="首页创作" />);
    const send = screen.getByRole("button", { name: "发送" });
    expect(send).toBeDisabled();
    expect(send).toHaveAccessibleDescription( "请先配置对话服务");
    fireEvent.click(send);
    fireEvent.keyDown(screen.getByRole("textbox", { name: "对话要求" }), { key: "Enter" });
    expect(await screen.findByRole("alert")).toHaveTextContent("请先配置对话服务");
    expect(onSend).not.toHaveBeenCalled();
  });

  it.each([
    ["busy", false, true, "本轮正在处理，请稍后再发送"],
    ["running", true, false, "引用版本尚未恢复，请稍后发送。"],
  ] as const)("does not repeat ordinary sends while %s", async (_stateName, running, busy, reason) => {
    const current = state({ conversation: running ? {
      ...conversation(), turns: [{ ...conversation().turns[0], status: "running" as const }],
    } : conversation() });
    const onSend = vi.fn();
    render(<AssistantConversationPanel state={current} input="再发一条" onInput={vi.fn()} onSend={onSend}
      busy={busy} configured scopeLabel="首页创作" />);
    if (running) {
      expect(screen.queryByRole("button", { name: "发送" })).not.toBeInTheDocument();
      expect(screen.getByRole("button", { name: "停止本轮" })).toBeEnabled();
    } else {
      const send = screen.getByRole("button", { name: "正在处理" });
      expect(send).toBeDisabled();
      expect(send).toHaveAccessibleDescription( reason);
    }
    fireEvent.keyDown(screen.getByRole("textbox", { name: "对话要求" }), { key: "Enter" });
    expect(await screen.findByRole("alert")).toHaveTextContent(reason);
    expect(onSend).not.toHaveBeenCalled();
  });

  it("keeps an invalid application command visible and does not send it to Pi", async () => {
    const onSend = vi.fn();
    const createNew = vi.fn();
    const current = state({ createNew });
    render(<AssistantConversationPanel state={current} input="/new unexpected" onInput={vi.fn()} onSend={onSend}
      busy={false} configured scopeLabel="首页创作" />);
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("此命令格式不正确");
    expect(createNew).not.toHaveBeenCalled();
    expect(onSend).not.toHaveBeenCalled();
  });

  it("continues editing a historical failure explicitly and protects a nonempty current draft",()=>{
    const saved=conversation();saved.turns[0].status="failed";
    const onInput=vi.fn(),onSend=vi.fn();
    const view=render(<AssistantConversationPanel state={state({conversation:saved})} input="" onInput={onInput} onSend={onSend} busy={false} configured scopeLabel="工作区"/>);
    fireEvent.click(screen.getByRole("button",{name:"继续编辑本条要求"}));
    expect(onInput).toHaveBeenCalledWith(saved.turns[0].user_message);
    expect(onSend).not.toHaveBeenCalled();
    view.rerender(<AssistantConversationPanel state={state({conversation:saved})} input="当前新草稿" onInput={onInput} onSend={onSend} busy={false} configured scopeLabel="工作区"/>);
    const button=screen.getByRole("button",{name:"继续编辑本条要求"});
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute("title","当前已有草稿，请先处理当前草稿。");
  });

  it("keeps a restored running turn as the send gate and exposes its stop action", () => {
    const running = conversation();
    running.turns = [{ ...running.turns[0], id: "restored-running-turn", status: "running" }];
    const stop = vi.fn();
    render(<AssistantConversationPanel state={state({ conversation: running, stop })} input="继续写" onInput={vi.fn()} onSend={vi.fn()}
      busy={false} configured scopeLabel="首页创作" />);
    expect(screen.queryByRole("button", { name: "发送" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "停止本轮" })).toBeInTheDocument();
  });

  it("keeps a late stream callback bound to the conversation and turn that sent it", async () => {
    const first = conversation();
    const second = { ...conversation(), id: "conversation-two", turns: [], draft_text: "" };
    api.getCurrentAssistantConversation.mockResolvedValue(first);
    api.listAssistantConversations.mockResolvedValue({ items: [first] });
    api.updateAssistantConversation.mockImplementation(async (id: string) => ({ ...(id === first.id ? first : second), draft_text: "" }));
    api.createAssistantConversation.mockResolvedValue(second);
    const { result } = renderHook(() => useAssistantConversation({ scope_kind: "home", scope_id: "home" }));
    await waitFor(() => expect(result.current.conversation?.id).toBe(first.id));

    let stream: (event: AssistantStreamEvent) => void = () => undefined;
    await act(async () => {
      const turn = await result.current.beginTurn("旧会话发送中的请求");
      stream = result.current.bindTurnEvents(turn.conversationId, turn.localTurnId);
    });
    await act(async () => { await result.current.createNew(); });
    expect(result.current.conversation?.id).toBe(second.id);
    expect(result.current.isCurrentConversation(first.id)).toBe(false);
    expect(result.current.isCurrentConversation(second.id)).toBe(true);
    act(() => stream({ type: "text_delta", delta: "迟到的旧流事件" }));
    expect(result.current.conversation?.turns).toHaveLength(0);
  });

  it("records turn_started before the active scope gate so a late failure cannot restore a submitted message", async () => {
    const first = { ...conversation(), turns: [], draft_text: "" };
    const second = { ...conversation("task", "task-one"), id: "task-conversation", turns: [], draft_text: "" };
    api.getCurrentAssistantConversation.mockImplementation(async (scope: { scope_kind: string; scope_id: string }) =>
      scope.scope_kind === "home" ? first : second);
    api.listAssistantConversations.mockImplementation(async (scope: { scope_kind: string; scope_id: string }) => ({
      items: scope.scope_kind === "home" ? [first] : [second],
    }));
    api.updateAssistantConversation.mockImplementation(async (id: string, update: { draft_text?: string; expected_draft_version?: number }) => ({
      ...(id === first.id ? first : second), draft_text: update.draft_text ?? "",
      draft_version: (update.expected_draft_version ?? 0) + 1,
    }));

    const { result, rerender } = renderHook(({ scope }) => useAssistantConversation(scope), {
      initialProps: { scope: { scope_kind: "home" as "home" | "task", scope_id: "home" } },
    });
    await waitFor(() => expect(result.current.conversation?.id).toBe(first.id));
    let pending!: ReturnType<typeof result.current.beginTurn>;
    let stream!: (event: AssistantStreamEvent) => void;
    await act(async () => {
      pending = result.current.beginTurn("上一范围已提交的请求");
      stream = result.current.bindTurnEvents(pending.conversationId, pending.localTurnId);
      await pending.draftCheckpoint;
    });

    rerender({ scope: { scope_kind: "task", scope_id: "task-one" } });
    await waitFor(() => expect(result.current.conversation?.id).toBe(second.id));
    act(() => stream({ type: "turn_started", turn_id: "persisted-old-turn", turn_state: "running", draft_version: 2 }));
    api.restoreUnpersistedAssistantTurnDraft.mockResolvedValue({
      status: "already_persisted",
      conversation: {
        ...first, draft_version: 2,
        turns: [{ ...conversation().turns[0], id: "persisted-old-turn", status: "running" }],
      },
    });
    const updateCount = api.updateAssistantConversation.mock.calls.length;

    await act(async () => {
      expect(await result.current.failUnpersistedTurn(pending.conversationId, pending.localTurnId, "连接中断")).toBe(true);
    });

    expect(api.updateAssistantConversation).toHaveBeenCalledTimes(updateCount);
    expect(result.current.conversation?.id).toBe(second.id);
    expect(result.current.draftText).toBe("");
  });

  it("shows a sent user turn immediately without waiting for the server request", async () => {
    const initial = { ...conversation(), draft_text: "上次未提交草稿", turns: [] };
    api.getCurrentAssistantConversation.mockResolvedValue(initial);
    api.listAssistantConversations.mockResolvedValue({ items: [initial] });
    const { result } = renderHook(() => useAssistantConversation({ scope_kind: "home", scope_id: "home" }));
    await waitFor(() => expect(result.current.loading).toBe(false));
    const updatesBeforeBegin = api.updateAssistantConversation.mock.calls.length;

    let created!: ReturnType<typeof result.current.beginTurn>;
    act(() => { created = result.current.beginTurn("即时显示这条要求"); });
    expect(created.conversationId).toBe(initial.id);
    expect(result.current.conversation?.turns.at(-1)?.user_message).toBe("即时显示这条要求");
    expect(result.current.conversation?.turns.at(-1)?.status).toBe("running");
    expect(result.current.draftText).toBe("");
    expect(api.updateAssistantConversation).toHaveBeenCalledTimes(updatesBeforeBegin);
  });

  it("marks a conversation inactive when its hook unmounts during a pending draft checkpoint", async () => {
    const initial = { ...conversation(), turns: [], draft_text: "" };
    api.getCurrentAssistantConversation.mockResolvedValue(initial);
    api.listAssistantConversations.mockResolvedValue({ items: [initial] });
    const { result, unmount } = renderHook(() => useAssistantConversation({ scope_kind: "home", scope_id: "home" }));
    await waitFor(() => expect(result.current.conversation?.id).toBe(initial.id));
    let turn!: ReturnType<typeof result.current.beginTurn>;
    await act(async () => {
      turn = result.current.beginTurn("卸载前等待保存的请求");
      await turn.draftCheckpoint;
    });

    unmount();

    expect(result.current.isCurrentConversation(initial.id)).toBe(false);
  });

  it("turns an unpersisted failed optimistic turn into a retryable draft", async () => {
    const saved = conversation();
    api.getCurrentAssistantConversation.mockResolvedValue(saved);
    api.listAssistantConversations.mockResolvedValue({ items: [saved] });
    api.getAssistantConversation.mockResolvedValue(saved);
    const { result } = renderHook(() => useAssistantConversation({ scope_kind: "home", scope_id: "home" }));
    await waitFor(() => expect(result.current.conversation?.id).toBe(saved.id));
    let localTurnId = "";
    await act(async () => {
      const turn = result.current.beginTurn("请再试一次读取当前方案");
      localTurnId = turn.localTurnId;
      await turn.draftCheckpoint;
      await result.current.refresh();
      api.restoreUnpersistedAssistantTurnDraft.mockResolvedValue({
        status: "restored", conversation: { ...saved, draft_text: "请再试一次读取当前方案", draft_version: 2 },
      });
      expect(await result.current.failUnpersistedTurn(turn.conversationId, turn.localTurnId, "网络请求未能建立轮次。")).toBe(true);
    });
    expect(result.current.conversation?.turns.some((turn) => turn.id === localTurnId)).toBe(false);
    expect(result.current.draftText).toBe("请再试一次读取当前方案");
    expect(api.restoreUnpersistedAssistantTurnDraft).toHaveBeenCalledWith(saved.id, {
      client_request_id: expect.any(String), user_message: "请再试一次读取当前方案", expected_draft_version: 1,
    });
  });

  it("restores the original request when the draft checkpoint failed before persisting", async () => {
    const initial = { ...conversation(), turns: [], draft_text: "" };
    api.getCurrentAssistantConversation.mockResolvedValue(initial);
    api.listAssistantConversations.mockResolvedValue({ items: [initial] });
    api.updateAssistantConversation.mockRejectedValueOnce(new Error("checkpoint unavailable"));
    const { result } = renderHook(() => useAssistantConversation({ scope_kind: "home", scope_id: "home" }));
    await waitFor(() => expect(result.current.conversation?.id).toBe(initial.id));
    let turn!: ReturnType<typeof result.current.beginTurn>;
    act(() => { turn = result.current.beginTurn("检查点失败仍需保留的原句"); });
    await expect(turn.draftCheckpoint).rejects.toThrow("checkpoint unavailable");

    await act(async () => {
      expect(await result.current.failUnpersistedTurn(turn.conversationId, turn.localTurnId, "连接中断")).toBe(true);
    });

    expect(api.restoreUnpersistedAssistantTurnDraft).toHaveBeenCalledWith(initial.id, {
      client_request_id: turn.clientRequestId, user_message: "检查点失败仍需保留的原句", expected_draft_version: 0,
    });
    expect(result.current.draftText).toBe("检查点失败仍需保留的原句");
    expect(result.current.conversation?.draft_version).toBe(1);
  });

  it("keeps a late unpersisted-turn failure in its original conversation without replacing a newer conversation draft", async () => {
    const first = { ...conversation(), turns: [], draft_text: "" };
    const second = { ...conversation(), id: "conversation-two", turns: [], draft_text: "" };
    api.getCurrentAssistantConversation.mockResolvedValue(first);
    api.listAssistantConversations.mockResolvedValue({ items: [first, second] });
    api.createAssistantConversation.mockResolvedValue(second);
    api.getAssistantConversation.mockResolvedValue(first);
    api.updateAssistantConversation.mockImplementation(async (id: string, update: { draft_text?: string; expected_draft_version?: number }) => ({
      ...(id === first.id ? first : second), draft_text: update.draft_text ?? "",
      draft_version: (update.expected_draft_version ?? 0) + 1,
      updated_at: "2026-10-09T00:01:00Z",
    }));
    const { result } = renderHook(() => useAssistantConversation({ scope_kind: "home", scope_id: "home" }));
    await waitFor(() => expect(result.current.conversation?.id).toBe(first.id));
    let pending!: ReturnType<typeof result.current.beginTurn>;
    await act(async () => { pending = result.current.beginTurn("旧会话原始要求"); await pending.draftCheckpoint; });
    api.restoreUnpersistedAssistantTurnDraft.mockResolvedValue({
      status: "restored", conversation: { ...first, draft_text: "旧会话原始要求", draft_version: 2 },
    });
    await act(async () => { await result.current.createNew(); });
    act(() => result.current.setDraftText("新会话较新的草稿"));

    await act(async () => {
      expect(await result.current.failUnpersistedTurn(pending.conversationId, pending.localTurnId, "旧请求连接失败说明")).toBe(true);
    });

    expect(api.updateAssistantConversation).not.toHaveBeenCalledWith(first.id, expect.objectContaining({ draft_text: "" }));
    expect(api.restoreUnpersistedAssistantTurnDraft).toHaveBeenCalledWith(first.id, {
      client_request_id: expect.any(String), user_message: "旧会话原始要求", expected_draft_version: 1,
    });
    expect(result.current.conversation?.id).toBe(second.id);
    expect(result.current.draftText).toBe("新会话较新的草稿");
  });

  it("does not restore a submitted turn over a newer draft in the same conversation", async () => {
    const initial = { ...conversation(), turns: [], draft_text: "" };
    api.getCurrentAssistantConversation.mockResolvedValue(initial);
    api.listAssistantConversations.mockResolvedValue({ items: [initial] });
    const { result } = renderHook(() => useAssistantConversation({ scope_kind: "home", scope_id: "home" }));
    await waitFor(() => expect(result.current.conversation?.id).toBe(initial.id));
    let pending!: ReturnType<typeof result.current.beginTurn>;
    await act(async () => { pending = result.current.beginTurn("已提交但失败的要求"); await pending.draftCheckpoint; });
    act(() => result.current.setDraftText("发送后新写的草稿"));
    api.restoreUnpersistedAssistantTurnDraft.mockResolvedValue({
      status: "restored", conversation: { ...initial, draft_text: "已提交但失败的要求", draft_version: 2 },
    });

    await act(async () => {
      expect(await result.current.failUnpersistedTurn(pending.conversationId, pending.localTurnId, "网络失败说明")).toBe(true);
    });

    expect(result.current.draftText).toBe("发送后新写的草稿");
    expect(result.current.conversation?.turns.some((turn) => turn.id === pending.localTurnId)).toBe(false);
  });

  it.each(["failed","stopped"] as const)("restores a persisted %s turn original text with draft CAS and keeps its durable record", async terminal => {
    const initial={...conversation(),draft_text:"",turns:[]};
    api.getCurrentAssistantConversation.mockResolvedValue(initial);
    api.listAssistantConversations.mockResolvedValue({items:[initial]});
    const {result}=renderHook(()=>useAssistantConversation({scope_kind:"home",scope_id:"home"}));
    await waitFor(()=>expect(result.current.loading).toBe(false));
    let pending!:ReturnType<typeof result.current.beginTurn>;
    await act(async()=>{pending=result.current.beginTurn("QA 本机文字连接错误验证，不执行任何写入。");await pending.draftCheckpoint;});
    const stream=result.current.bindTurnEvents(initial.id,pending.localTurnId);
    act(()=>stream({type:"turn_started",turn_id:"real-failed-turn",turn_state:"running",draft_version:2}));
    act(()=>stream({type:"turn_status",turn_id:"real-failed-turn",turn_state:terminal}));
    expect(result.current.draftText).toBe("QA 本机文字连接错误验证，不执行任何写入。");
    await waitFor(()=>expect(api.updateAssistantConversation).toHaveBeenLastCalledWith(initial.id,{draft_text:result.current.draftText,expected_draft_version:2}));
    expect(result.current.conversation?.turns).toHaveLength(1);
    expect(result.current.conversation?.turns[0]).toMatchObject({id:"real-failed-turn",status:terminal});
    expect(api.restoreUnpersistedAssistantTurnDraft).not.toHaveBeenCalled();
  });

  it.each(["新写的草稿",""])("does not restore a terminal turn after the user edits the next draft to %j", async nextText=>{
    const initial={...conversation(),draft_text:"",turns:[]};
    api.getCurrentAssistantConversation.mockResolvedValue(initial);
    api.listAssistantConversations.mockResolvedValue({items:[initial]});
    const {result}=renderHook(()=>useAssistantConversation({scope_kind:"home",scope_id:"home"}));
    await waitFor(()=>expect(result.current.loading).toBe(false));
    let pending!:ReturnType<typeof result.current.beginTurn>;
    await act(async()=>{pending=result.current.beginTurn("原始失败要求");await pending.draftCheckpoint;});
    const stream=result.current.bindTurnEvents(initial.id,pending.localTurnId);
    act(()=>stream({type:"turn_started",turn_id:"persisted-turn",turn_state:"running",draft_version:2}));
    act(()=>result.current.setDraftText(nextText));
    const count=api.updateAssistantConversation.mock.calls.length;
    act(()=>stream({type:"turn_status",turn_id:"persisted-turn",turn_state:"failed"}));
    expect(result.current.draftText).toBe(nextText);
    expect(api.updateAssistantConversation).toHaveBeenCalledTimes(count);
  });

  it("does not restore an old stream over the same conversation after an explicit resume epoch",async()=>{
    const initial={...conversation(),draft_text:"",turns:[]};
    api.getCurrentAssistantConversation.mockResolvedValue(initial);
    api.listAssistantConversations.mockResolvedValue({items:[initial]});
    const {result}=renderHook(()=>useAssistantConversation({scope_kind:"home",scope_id:"home"}));
    await waitFor(()=>expect(result.current.loading).toBe(false));
    let pending!:ReturnType<typeof result.current.beginTurn>;
    await act(async()=>{pending=result.current.beginTurn("旧epoch原文");await pending.draftCheckpoint;});
    const stream=result.current.bindTurnEvents(initial.id,pending.localTurnId);
    act(()=>stream({type:"turn_started",turn_id:"persisted-turn",turn_state:"running",draft_version:2}));
    api.resumeAssistantConversation.mockResolvedValue({...result.current.conversation,draft_text:"",draft_version:3});
    await act(async()=>{await result.current.resume(initial.id);});
    const count=api.updateAssistantConversation.mock.calls.length;
    act(()=>stream({type:"turn_status",turn_id:"persisted-turn",turn_state:"failed"}));
    expect(result.current.draftText).toBe("");
    expect(api.updateAssistantConversation).toHaveBeenCalledTimes(count);
  });

  it("restores the original request from a stopped server refresh when the stream has not returned its terminal event",async()=>{
    const initial={...conversation(),draft_text:"",turns:[]};
    api.getCurrentAssistantConversation.mockResolvedValue(initial);
    api.listAssistantConversations.mockResolvedValue({items:[initial]});
    const {result}=renderHook(()=>useAssistantConversation({scope_kind:"home",scope_id:"home"}));
    await waitFor(()=>expect(result.current.loading).toBe(false));
    let pending!:ReturnType<typeof result.current.beginTurn>;
    await act(async()=>{pending=result.current.beginTurn("停止后继续编辑的原文");await pending.draftCheckpoint;});
    const stream=result.current.bindTurnEvents(initial.id,pending.localTurnId);
    act(()=>stream({type:"turn_started",turn_id:"persisted-turn",turn_state:"running",draft_version:2}));
    api.getAssistantConversation.mockResolvedValue({...result.current.conversation,draft_text:"",draft_version:2,turns:[{...result.current.conversation!.turns[0],status:"stopped"}]});
    await act(async()=>{await result.current.stop();});
    expect(api.stopAssistantConversationTurn).toHaveBeenCalledWith(initial.id,"persisted-turn");
    expect(result.current.draftText).toBe("停止后继续编辑的原文");
    await waitFor(()=>expect(api.updateAssistantConversation).toHaveBeenLastCalledWith(initial.id,{draft_text:"停止后继续编辑的原文",expected_draft_version:2}));
    expect(result.current.conversation?.turns[0].status).toBe("stopped");
  });

  it("prefers a newer server draft during terminal refresh instead of restoring the old request",async()=>{
    const initial={...conversation(),draft_text:"",turns:[]};
    api.getCurrentAssistantConversation.mockResolvedValue(initial);
    api.listAssistantConversations.mockResolvedValue({items:[initial]});
    const {result}=renderHook(()=>useAssistantConversation({scope_kind:"home",scope_id:"home"}));
    await waitFor(()=>expect(result.current.loading).toBe(false));
    let pending!:ReturnType<typeof result.current.beginTurn>;
    await act(async()=>{pending=result.current.beginTurn("较旧发送要求");await pending.draftCheckpoint;});
    const stream=result.current.bindTurnEvents(initial.id,pending.localTurnId);
    act(()=>stream({type:"turn_started",turn_id:"persisted-turn",turn_state:"running",draft_version:2}));
    api.getAssistantConversation.mockResolvedValue({...result.current.conversation,draft_text:"服务器新版草稿",draft_version:3,turns:[{...result.current.conversation!.turns[0],status:"failed"}]});
    await act(async()=>{await result.current.refresh();});
    expect(result.current.draftText).toBe("服务器新版草稿");
    expect(api.updateAssistantConversation).toHaveBeenCalledTimes(1);
  });

  it("preserves restored local text when refresh returns before its draft CAS has finished",async()=>{
    const initial={...conversation(),draft_text:"",turns:[]};
    api.getCurrentAssistantConversation.mockResolvedValue(initial);
    api.listAssistantConversations.mockResolvedValue({items:[initial]});
    const {result}=renderHook(()=>useAssistantConversation({scope_kind:"home",scope_id:"home"}));
    await waitFor(()=>expect(result.current.loading).toBe(false));
    let pending!:ReturnType<typeof result.current.beginTurn>;
    await act(async()=>{pending=result.current.beginTurn("等待保存的恢复原文");await pending.draftCheckpoint;});
    const stream=result.current.bindTurnEvents(initial.id,pending.localTurnId);
    act(()=>stream({type:"turn_started",turn_id:"persisted-turn",turn_state:"running",draft_version:2}));
    let resolveSave!:(value:AssistantConversation)=>void;
    api.updateAssistantConversation.mockImplementationOnce(()=>new Promise(resolve=>{resolveSave=resolve;}));
    act(()=>stream({type:"turn_status",turn_id:"persisted-turn",turn_state:"failed"}));
    await waitFor(()=>expect(api.updateAssistantConversation).toHaveBeenCalledTimes(2));
    const failed={...result.current.conversation!,draft_text:"",draft_version:2};
    api.getAssistantConversation.mockResolvedValue(failed);
    api.restoreUnpersistedAssistantTurnDraft.mockResolvedValue({status:"already_persisted",conversation:failed});
    await act(async()=>{await result.current.failUnpersistedTurn(initial.id,pending.localTurnId,"已持久失败");});
    await act(async()=>{await result.current.refresh();});
    expect(result.current.draftText).toBe("等待保存的恢复原文");
    await act(async()=>{await result.current.refresh();});
    expect(result.current.draftText).toBe("等待保存的恢复原文");
    await act(async()=>{resolveSave({...failed,draft_text:"等待保存的恢复原文",draft_version:3});});
    expect(result.current.draftText).toBe("等待保存的恢复原文");
    expect(result.current.conversation?.turns[0].status).toBe("failed");
  });

  it.each([false,true])("preserves draft and diagnostics after restoration CAS conflict (server read fails: %s)",async readFails=>{
    const initial={...conversation(),draft_text:"",turns:[]};
    api.getCurrentAssistantConversation.mockResolvedValue(initial);
    api.listAssistantConversations.mockResolvedValue({items:[initial]});
    const {result}=renderHook(()=>useAssistantConversation({scope_kind:"home",scope_id:"home"}));
    await waitFor(()=>expect(result.current.loading).toBe(false));
    let pending!:ReturnType<typeof result.current.beginTurn>;
    await act(async()=>{pending=result.current.beginTurn("不能覆盖新版的原文");await pending.draftCheckpoint;});
    const stream=result.current.bindTurnEvents(initial.id,pending.localTurnId);
    act(()=>stream({type:"turn_started",turn_id:"persisted-turn",turn_state:"running",draft_version:2}));
    api.updateAssistantConversation.mockRejectedValueOnce(new Error("草稿CAS冲突409"));
    if(readFails) api.getAssistantConversation.mockRejectedValueOnce(new Error("服务器回读不可达"));
    else api.getAssistantConversation.mockResolvedValue({...result.current.conversation,draft_text:"其他窗口较新的草稿",draft_version:3});
    act(()=>stream({type:"turn_status",turn_id:"persisted-turn",turn_state:"failed"}));
    if(readFails) {
      await waitFor(()=>expect(result.current.error).toContain("服务器回读不可达"));
      expect(result.current.draftText).toBe("不能覆盖新版的原文");
    } else await waitFor(()=>expect(result.current.draftText).toBe("其他窗口较新的草稿"));
    expect(result.current.error).toContain("409");
    expect(api.updateAssistantConversation).toHaveBeenCalledTimes(2);
    expect(result.current.conversation?.turns[0]).toMatchObject({id:"persisted-turn",status:"failed"});
  });

  it("reconciles a recovered server turn instead of leaving an optimistic duplicate running", async () => {
    const saved = conversation();
    api.getCurrentAssistantConversation.mockResolvedValue(saved);
    api.listAssistantConversations.mockResolvedValue({ items: [saved] });
    const { result } = renderHook(() => useAssistantConversation({ scope_kind: "home", scope_id: "home" }));
    await waitFor(() => expect(result.current.conversation?.id).toBe(saved.id));
    let localTurnId = "";
    let stream!: (event: AssistantStreamEvent) => void;
    await act(async () => {
      const pending = result.current.beginTurn("检查一条正在处理的请求");
      localTurnId = pending.localTurnId;
      stream = result.current.bindTurnEvents(saved.id, localTurnId);
    });
    const persisted = { ...saved.turns[0], id: "persisted-running", sequence: 2,
      user_message: "检查一条正在处理的请求", status: "running" as const,
      assistant_text: "", events: [], result: null,
      created_at: new Date().toISOString(), updated_at: new Date().toISOString() };
    api.getAssistantConversation.mockResolvedValue({ ...saved, turns: [...saved.turns, persisted] });
    act(() => stream({ type: "turn_started", turn_id: "persisted-running", turn_state: "running" }));
    await act(async () => { await result.current.refresh(); });
    expect(result.current.conversation?.turns.at(-1)?.id).toBe("persisted-running");
    expect(result.current.conversation?.turns.filter((turn) => turn.id === localTurnId)).toHaveLength(0);
    act(() => stream({ type: "text_delta", delta: "服务端已开始" }));
    expect(result.current.conversation?.turns.at(-1)?.assistant_text).toBe("服务端已开始");
  });

  it("does not let a stale conversation refresh replace newer streamed text", async () => {
    const initial = { ...conversation(), turns: [] };
    api.getCurrentAssistantConversation.mockResolvedValue(initial);
    api.listAssistantConversations.mockResolvedValue({ items: [initial] });
    let resolveFetch!: (value: AssistantConversation) => void;
    api.getAssistantConversation.mockReturnValue(new Promise((resolve) => { resolveFetch = resolve; }));
    const { result } = renderHook(() => useAssistantConversation({ scope_kind: "home", scope_id: "home" }));
    await waitFor(() => expect(result.current.loading).toBe(false));

    let stream!: (event: AssistantStreamEvent) => void;
    act(() => {
      const turn = result.current.beginTurn("新一轮");
      stream = result.current.bindTurnEvents(turn.conversationId, turn.localTurnId);
      stream({ type: "turn_started", turn_id: "turn-live", turn_state: "running" });
    });
    let refresh!: Promise<AssistantConversation | null>;
    act(() => { refresh = result.current.refresh(); });
    act(() => { stream({ type: "text_delta", delta: "新鲜回复" }); });
    resolveFetch({ ...initial, turns: [{ id: "turn-live", sequence: 1, user_message: "新一轮",
      assistant_text: "旧快照", status: "running", events: [], result: null,
      created_at: initial.created_at, updated_at: initial.updated_at }] });
    await act(async () => { await refresh; });
    expect(result.current.conversation?.turns.at(-1)?.assistant_text).toBe("新鲜回复");
  });

  it("preserves and persists a local draft created while the initial conversation is loading", async () => {
    const initial = { ...conversation(), turns: [], draft_text: "" };
    let resolveCurrent!: (value: AssistantConversation) => void;
    api.getCurrentAssistantConversation.mockReturnValue(new Promise((resolve) => { resolveCurrent = resolve; }));
    api.listAssistantConversations.mockResolvedValue({ items: [initial] });
    api.updateAssistantConversation.mockImplementation(async (_id: string, update: { draft_text?: string; expected_draft_version?: number }) => ({
      ...initial, draft_text: update.draft_text ?? "",
      draft_version: initial.draft_version + (update.draft_text === undefined ? 0 : 1),
    }));
    const { result } = renderHook(() => useAssistantConversation({ scope_kind: "home", scope_id: "home" }));

    act(() => result.current.setDraftText("提取当前镜头原声"));
    await act(async () => { resolveCurrent(initial); });
    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(result.current.draftText).toBe("提取当前镜头原声");
    expect(api.updateAssistantConversation).toHaveBeenCalledWith(initial.id, {
      draft_text: "提取当前镜头原声", expected_draft_version: initial.draft_version,
    });
  });
});


describe("persistent workbench conversation and command completion", () => {
  it.each(["pending", "unavailable"] as const)("keeps four local commands available across button, Enter and menu with %s references", async referenceState => {
    api.getCurrentAssistantConversation.mockResolvedValue(conversation());
    api.listAssistantConversations.mockResolvedValue({items:[conversation()]});
    api.getAssistantConversationReference.mockImplementation(() => referenceState === "pending" ? new Promise(() => {}) : Promise.resolve({
      selected_reference:{kind:"knowledge",knowledge_item_id:"missing",version:"1",label:"旧资料"},
      reference_version:1,status:"unavailable",reason:"固定知识版本当前不可读取",
    }));
    const next={...conversation(),draft_text:"恢复的草稿"};
    const createNew=vi.fn().mockResolvedValue(next),rename=vi.fn().mockResolvedValue(next),resume=vi.fn().mockResolvedValue(next);
    const onInput=vi.fn(),onSend=vi.fn();
    const current=state({createNew,rename,resume,conversations:[{...conversation(),title:"可恢复记录"}]});
    const panel=(input:string,configured=false)=><AssistantConversationProvider><AssistantConversationPanel state={current} input={input} onInput={onInput} onSend={onSend} busy={false} configured={configured} scopeLabel="工作区"/></AssistantConversationProvider>;
    const view=render(panel("/new"));
    await screen.findByRole("button",{name:"技术能力状态（诊断）"});
    await waitFor(()=>expect(api.getAssistantConversationReference).toHaveBeenCalled());
    if(referenceState === "unavailable") await screen.findByText("引用核验未通过。固定版本保持原值，请重新查看或取消引用后发送。");
    for(const activation of ["button","enter"] as const) {
      for(const command of ["/new","/name 改名","/resume conversation-one","/help"]) {
        onInput.mockClear();createNew.mockClear();rename.mockClear();resume.mockClear();
        view.rerender(panel(command));
        const send=screen.getByRole("button",{name:"发送"});
        expect(send).toBeEnabled();
        expect(send).toHaveAccessibleDescription("Enter 执行本地会话操作；无效命令不会发送给模型");
        if(activation === "button") fireEvent.click(send);
        else fireEvent.keyDown(screen.getByRole("textbox"),{key:"Enter"});
        await waitFor(()=>expect(onInput).toHaveBeenCalledWith(command === "/help" ? "/" : next.draft_text));
        if(command === "/new") expect(createNew).toHaveBeenCalledOnce();
        if(command.startsWith("/name")) expect(rename).toHaveBeenCalledWith("改名");
        if(command.startsWith("/resume")) expect(resume).toHaveBeenCalledWith("conversation-one");
      }
    }
    for(const [name,query] of [["新对话","/ne"],["命名对话","/na"],["恢复对话","/re"],["查看命令","/he"]]) {
      onInput.mockClear();createNew.mockClear();resume.mockClear();
      view.rerender(panel(query));
      const option=screen.getByRole("option",{name:new RegExp(name)});
      expect(option).toBeEnabled();fireEvent.click(option);
      if(name === "新对话") await waitFor(()=>expect(onInput).toHaveBeenCalledWith(next.draft_text));
      if(name === "命名对话") expect(onInput).toHaveBeenCalledWith("/name ");
      if(name === "查看命令") expect(onInput).toHaveBeenCalledWith("/");
      if(name === "恢复对话") {
        fireEvent.click(await screen.findByRole("button",{name:/可恢复记录/}));
        await waitFor(()=>expect(resume).toHaveBeenCalledWith("conversation-one"));
      }
    }
    expect(onSend).not.toHaveBeenCalled();
    view.rerender(panel("/compact"));
    expect(screen.getByRole("button",{name:"发送"})).toBeEnabled();
    fireEvent.click(screen.getByRole("button",{name:"发送"}));
    expect(screen.getByRole("textbox")).toHaveValue("/compact");
    expect(screen.getAllByRole("alert").at(-1)).toHaveTextContent("没有发送给模型");
    view.rerender(panel("普通消息",true));
    expect(screen.getByRole("button",{name:"发送"})).toBeDisabled();
    expect(screen.getByRole("button",{name:"发送"})).toHaveAccessibleDescription(referenceState === "pending" ? "正在保存或核验引用" : "引用当前不可用，请重新查看或取消引用");
    fireEvent.keyDown(screen.getByRole("textbox"),{key:"Enter"});
    expect(onSend).not.toHaveBeenCalled();
  });

  it.each(["loading","missing","busy","running","ime"] as const)("preserves a local command draft and blocks all execution paths while %s", async condition => {
    const currentConversation=conversation();
    if(condition === "running") currentConversation.turns=[{...currentConversation.turns[0],id:"pending-turn",status:"running"}];
    const createNew=vi.fn(),onInput=vi.fn(),onSend=vi.fn();
    render(<AssistantConversationPanel state={state({conversation:condition === "missing" ? null : currentConversation,loading:condition === "loading",createNew})} input="/new" onInput={onInput} onSend={onSend} busy={condition === "busy"} configured={false} scopeLabel="工作区"/>);
    const input=screen.getByRole("textbox");
    await screen.findByRole("option",{name:/新对话/});
    if(condition === "ime") fireEvent.compositionStart(input);
    const option=screen.getByRole("option",{name:/新对话/});
    expect(option).toBeDisabled();expect(option).toHaveAttribute("aria-disabled","true");
    expect(screen.getByRole("button",{name:"发送"})).toBeDisabled();
    fireEvent.click(option);fireEvent.click(screen.getByRole("button",{name:"发送"}));
    fireEvent.keyDown(input,{key:"Enter"});
    fireEvent.submit(input.closest("form")!);
    expect(createNew).not.toHaveBeenCalled();expect(onInput).not.toHaveBeenCalled();expect(onSend).not.toHaveBeenCalled();
    expect(input).toHaveValue("/new");
    if(condition === "ime") {
      fireEvent.compositionEnd(input);
      expect(screen.getByRole("button",{name:"发送"})).toBeEnabled();
      expect(screen.getByRole("option",{name:/新对话/})).toBeEnabled();
    }
  });

  it("preserves a failed local command draft without forwarding it to the model",async()=>{
    const onInput=vi.fn(),onSend=vi.fn();
    render(<AssistantConversationPanel state={state({rename:vi.fn().mockRejectedValue(new Error("名称没有保存，请重试。"))})} input="/name 原草稿名称" onInput={onInput} onSend={onSend} busy={false} configured={false} scopeLabel="工作区"/>);
    await screen.findByRole("button",{name:"技术能力状态（诊断）"});
    fireEvent.click(screen.getByRole("button",{name:"发送"}));
    expect(await screen.findByRole("alert")).toHaveTextContent("名称没有保存，请重试。");
    expect(screen.getByRole("textbox")).toHaveValue("/name 原草稿名称");
    expect(onInput).not.toHaveBeenCalled();expect(onSend).not.toHaveBeenCalled();
  });

  it("keeps one active conversation and next draft when page consumers unmount", async () => {
    api.getCurrentAssistantConversation.mockResolvedValue(conversation());
    api.listAssistantConversations.mockResolvedValue({ items: [conversation()] });
    function Consumer({ kind }: { kind: "home" | "task" }) {
      const value = useAssistantConversation({ scope_kind: kind, scope_id: kind === "home" ? "home" : "different-task" });
      return <><span>{value.conversation?.id}</span><input aria-label="next draft" value={value.draftText} onChange={event => value.setDraftText(event.target.value)}/></>;
    }
    const view = render(<AssistantConversationProvider><Consumer key="home" kind="home"/></AssistantConversationProvider>);
    await screen.findByText("conversation-one");
    fireEvent.change(screen.getByLabelText("next draft"), { target: { value: "跨页保留" } });
    view.rerender(<AssistantConversationProvider><Consumer key="task" kind="task"/></AssistantConversationProvider>);
    expect(screen.getByLabelText("next draft")).toHaveValue("跨页保留");
    expect(screen.getByText("conversation-one")).toBeInTheDocument();
    expect(api.getCurrentAssistantConversation).toHaveBeenCalledTimes(1);
    expect(api.getCurrentAssistantConversation).toHaveBeenCalledWith({scope_kind:"home",scope_id:"home"});
  });

  it("keeps a fixed knowledge reference across consumers and clears it explicitly", async () => {
    api.getCurrentAssistantConversation.mockResolvedValue(conversation());
    api.listAssistantConversations.mockResolvedValue({items:[conversation()]});
    function Consumer() {
      const value = useAssistantConversation({scope_kind:"home",scope_id:"home"});
      return <AssistantConversationPanel state={value} input={value.draftText} onInput={value.setDraftText} onSend={vi.fn()} busy={false} configured scopeLabel="工作区"/>;
    }
    const view = render(<AssistantConversationProvider><Consumer key="knowledge"/></AssistantConversationProvider>);
    await screen.findByRole("textbox");
    act(() => referenceAssistantKnowledge({knowledge_item_id:"knowledge-real",version:"1",locator:"第1段",label:"第一版资料"}));
    expect(screen.getByText(/第一版资料/)).toBeVisible();
    view.rerender(<AssistantConversationProvider><Consumer key="profiles"/></AssistantConversationProvider>);
    expect(screen.getByText(/第一版资料/)).toBeVisible();
    fireEvent.click(screen.getByRole("button", {name:/查看本轮 .* 项引用/}));
    fireEvent.click(await screen.findByRole("button",{name:/取消资料引用：/}));
    expect(screen.queryByText(/第一版资料/)).not.toBeInTheDocument();
    act(() => referenceAssistantKnowledge({knowledge_item_id:"knowledge-real",version:"1",label:"第一版资料"}));
    act(() => referenceAssistantMaterial({selection:{scope:"asset",asset_id:"asset-real",expected_updated_at:"2026-10-10T00:00:00Z"},label:"明确素材"}));
    fireEvent.click(screen.getByRole("button", {name:/查看本轮 .* 项引用/}));
    expect(await screen.findByRole("button", {name:/定位资料 · 第一版资料/})).toBeVisible();
    expect(await screen.findByRole("button", {name:/定位素材 · 明确素材/})).toBeVisible();
  });

  it("restores saved reference after full Provider remount and persists explicit cancel", async () => {
    api.getCurrentAssistantConversation.mockResolvedValue(conversation());
    api.listAssistantConversations.mockResolvedValue({items:[conversation()]});
    function Consumer() {
      const value = useAssistantConversation({scope_kind:"home",scope_id:"home"});
      return <AssistantConversationPanel state={value} input={value.draftText} onInput={value.setDraftText} onSend={vi.fn()} busy={false} configured scopeLabel="工作区"/>;
    }
    const first=render(<AssistantConversationProvider><Consumer/></AssistantConversationProvider>);
    await waitFor(() => expect(api.getAssistantConversationReference).toHaveBeenCalled());
    act(() => referenceAssistantKnowledge({knowledge_item_id:"knowledge-real",version:"1",label:"固定旧版"}));
    await waitFor(() => expect(api.referenceSaved.get("conversation-one")?.selected_reference).toEqual({kind:"selection",items:[{kind:"knowledge",knowledge_item_id:"knowledge-real",version:"1",label:"固定旧版"}]}));
    first.unmount();
    const second=render(<AssistantConversationProvider><Consumer/></AssistantConversationProvider>);
    expect(await screen.findByText(/固定旧版/)).toBeVisible();
    fireEvent.click(screen.getByRole("button", {name:/查看本轮 .* 项引用/}));
    fireEvent.click(await screen.findByRole("button",{name:/取消资料引用：/}));
    await waitFor(() => expect(api.referenceSaved.get("conversation-one")?.selected_reference).toBeNull());
    second.unmount();
    render(<AssistantConversationProvider><Consumer/></AssistantConversationProvider>);
    await waitFor(() => expect(api.getAssistantConversationReference).toHaveBeenCalledTimes(3));
    expect(screen.queryByText(/固定旧版/)).not.toBeInTheDocument();
  });

  it("restores each conversation's own fixed reference when resuming after an explicit selection", async () => {
    api.getCurrentAssistantConversation.mockResolvedValue(conversation());
    api.listAssistantConversations.mockResolvedValue({items:[conversation()]});
    api.resumeAssistantConversation.mockImplementation(async (id:string) => ({...conversation(),id}));
    api.referenceSaved.set("conversation-two",{selected_reference:null,reference_version:0,status:"ready",reason:null});
    function Consumer() {
      const value=useAssistantConversation({scope_kind:"home",scope_id:"home"});
      return <><button onClick={()=>void value.resume("conversation-two")}>第二段</button><button onClick={()=>void value.resume("conversation-one")}>第一段</button>
        <AssistantConversationPanel state={value} input={value.draftText} onInput={value.setDraftText} onSend={vi.fn()} busy={false} configured scopeLabel="工作区"/></>;
    }
    render(<AssistantConversationProvider><Consumer/></AssistantConversationProvider>);
    await waitFor(() => expect(api.getAssistantConversationReference).toHaveBeenCalled());
    act(() => referenceAssistantKnowledge({knowledge_item_id:"knowledge-real",version:"1",label:"第一段固定资料"}));
    await waitFor(() => expect(JSON.stringify(api.referenceSaved.get("conversation-one")?.selected_reference)).toContain("第一段固定资料"));
    fireEvent.click(screen.getByRole("button",{name:"第二段"}));
    await waitFor(() => expect(api.getAssistantConversationReference).toHaveBeenCalledWith("conversation-two"));
    await waitFor(() => expect(screen.queryByText(/第一段固定资料 ·/)).not.toBeInTheDocument());
    fireEvent.click(screen.getByRole("button",{name:"第一段"}));
    expect(await screen.findByText(/第一段固定资料/)).toBeVisible();
  });

  it("does not revive an older selected reference when its save returns after cancel", async () => {
    api.getCurrentAssistantConversation.mockResolvedValue(conversation());
    api.listAssistantConversations.mockResolvedValue({items:[conversation()]});
    let resolveSave!:(value:import("@/lib/types").AssistantConversationReferenceState)=>void;
    api.saveAssistantConversationReference.mockImplementationOnce(() => new Promise(resolve => {resolveSave=resolve;}));
    function Consumer() {
      const value=useAssistantConversation({scope_kind:"home",scope_id:"home"});
      return <AssistantConversationPanel state={value} input={value.draftText} onInput={value.setDraftText} onSend={vi.fn()} busy={false} configured scopeLabel="工作区"/>;
    }
    render(<AssistantConversationProvider><Consumer/></AssistantConversationProvider>);
    await waitFor(() => expect(api.getAssistantConversationReference).toHaveBeenCalled());
    const selected={kind:"knowledge" as const,knowledge_item_id:"knowledge-real",version:"1",label:"不能复活"};
    act(() => referenceAssistantKnowledge(selected));
    await waitFor(() => expect(api.saveAssistantConversationReference).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole("button", {name:/查看本轮 .* 项引用/}));
    fireEvent.click(await screen.findByRole("button",{name:/取消资料引用：/}));
    expect(screen.queryByText(/不能复活/)).not.toBeInTheDocument();
    await act(async () => resolveSave({selected_reference:selected,reference_version:1,status:"ready",reason:null}));
    await waitFor(() => expect(api.saveAssistantConversationReference).toHaveBeenLastCalledWith("conversation-one",null,1));
    expect(screen.queryByText(/不能复活/)).not.toBeInTheDocument();
  });

  it("completes name parameters with Tab and protects IME, no results and Esc", async () => {
    const onInput = vi.fn(); const onSend = vi.fn();
    const view = render(<AssistantConversationPanel state={state()} input="/na" onInput={onInput} onSend={onSend} busy={false} configured scopeLabel="工作区"/>);
    await screen.findByRole("option", { name: /命名对话/ });
    const input = screen.getByRole("textbox"); input.focus();
    fireEvent.compositionStart(input);
    fireEvent.keyDown(input, {key:"Enter", keyCode:229});
    expect(onInput).not.toHaveBeenCalled();
    fireEvent.compositionEnd(input); fireEvent.keyDown(input, {key:"Tab"});
    expect(onInput).toHaveBeenCalledWith("/name "); expect(document.activeElement).toBe(input);
    view.rerender(<AssistantConversationPanel state={state()} input="/not-supported" onInput={onInput} onSend={onSend} busy={false} configured scopeLabel="工作区"/>);
    expect(screen.getByText("没有匹配的已接通命令")).toBeInTheDocument();
    fireEvent.keyDown(input, {key:"Enter"}); expect(onSend).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent("没有发送给模型");
    fireEvent.keyDown(input, {key:"Escape"}); expect(screen.queryByRole("listbox")).not.toBeInTheDocument(); expect(input).toHaveValue("/not-supported");
  });

  it("allows filtering while running but cannot execute a command", async () => {
    const createNew = vi.fn(); const running = conversation(); running.turns[0].status="running";
    render(<AssistantConversationPanel state={state({conversation:running,createNew})} input="/new" onInput={vi.fn()} onSend={vi.fn()} busy configured scopeLabel="工作区"/>);
    await screen.findByRole("option", {name:/新对话/}); fireEvent.keyDown(screen.getByRole("textbox"), {key:"Enter"});
    expect(createNew).not.toHaveBeenCalled(); expect(screen.getByRole("option", {name:/新对话/})).toHaveAttribute("aria-disabled","true");
  });
});


it("retries an unknown running-input receipt with its original request id and frozen versions", async () => {
  const running = conversation(); running.turns[0].status="running";
  api.getCurrentAssistantConversation.mockResolvedValue(running);
  api.listAssistantConversations.mockResolvedValue({items:[running]});
  api.getAssistantConversation.mockResolvedValue(running);
  api.enqueueAssistantInput.mockRejectedValueOnce(new TypeError("连接断开"))
    .mockResolvedValueOnce({input_id:"receipt",status:"accepted"});
  const hook=renderHook(()=>useAssistantConversation({scope_kind:"home",scope_id:"home"}));
  await waitFor(()=>expect(hook.result.current.loading).toBe(false));
  act(()=>hook.result.current.setDraftText("字幕缩短"));
  await act(async()=>{await expect(hook.result.current.sendInput("字幕缩短","steer",2)).rejects.toThrow("连接断开");});
  const first=api.enqueueAssistantInput.mock.calls[0][1];
  await act(async()=>{await hook.result.current.sendInput("字幕缩短","steer",3);});
  expect(api.enqueueAssistantInput.mock.calls[1][1]).toEqual(first);
  expect(first.expected_reference_version).toBe(2);
  expect(api.updateAssistantConversation).toHaveBeenCalledTimes(1);
});

it("delivers running input while retaining stop and preventing duplicate Enter", async () => {
  const running=conversation();running.turns[0].status="running";
  api.getCurrentAssistantConversation.mockResolvedValue(running);
  api.listAssistantConversations.mockResolvedValue({items:[running]});
  let resolve!:()=>void;
  const sendInput=vi.fn(()=>new Promise<void>(done=>{resolve=done;}));
  const onSend=vi.fn();
  render(<AssistantConversationProvider><AssistantConversationPanel state={state({conversation:running,sendInput})}
    input="字幕缩短" onInput={vi.fn()} onSend={onSend} configured busy scopeLabel="当前任务"/></AssistantConversationProvider>);
  const send=screen.getByRole("button",{name:"追加要求"});
  await waitFor(()=>expect(send).toBeEnabled());
  fireEvent.click(send);
  await waitFor(()=>expect(sendInput).toHaveBeenCalledWith("字幕缩短","steer",0));
  fireEvent.keyDown(screen.getByRole("textbox",{name:"对话要求"}),{key:"Enter"});
  expect(sendInput).toHaveBeenCalledTimes(1);expect(onSend).not.toHaveBeenCalled();
  expect(screen.getByRole("button",{name:"停止本轮"})).toBeEnabled();
  await act(async()=>resolve());
});


it("composes explicit references, preserves other kinds on ordinary selection, and removes only one chip", async () => {
  const saved=conversation();
  api.getCurrentAssistantConversation.mockResolvedValue(saved);
  api.listAssistantConversations.mockResolvedValue({items:[saved]});
  function SelectionConsumer() { const shared=useAssistantConversation({scope_kind:"home",scope_id:"home"});
    return <AssistantConversationPanel state={shared} input="" onInput={vi.fn()} onSend={vi.fn()} configured busy={false} scopeLabel="当前工作区"/>; }
  render(<AssistantConversationProvider><SelectionConsumer/></AssistantConversationProvider>);
  await waitFor(()=>expect(api.getAssistantConversationReference).toHaveBeenCalled());
  act(()=>{
    referenceAssistantProfile({kind:"voice",profile_id:"v1",version:"1",label:"声音甲"});
    referenceAssistantMaterial({selection:{scope:"asset",asset_id:"a1",expected_updated_at:"now"},label:"素材甲"});
    referenceAssistantMaterial({selection:{scope:"asset",asset_id:"a2",expected_updated_at:"now"},label:"素材乙"},true);
    referenceAssistantProfile({kind:"person",profile_id:"p1",version:"1",label:"人物甲"},true);
  });
  await waitFor(()=>expect(api.referenceSaved.get(saved.id)?.selected_reference).toMatchObject({kind:"selection",items:expect.any(Array)}));
  const final=api.referenceSaved.get(saved.id)?.selected_reference;
  expect(final?.kind === "selection" && final.items).toHaveLength(4);
  fireEvent.click(screen.getByRole("button",{name:"查看本轮 4 项引用"}));
  await screen.findByRole("button",{name:"取消素材引用：素材甲"});
  const referenceList=within(screen.getByRole("heading",{name:"本轮引用 · 4"}).parentElement!.parentElement!);
  expect(referenceList.getByText("人物甲")).toBeVisible();
  fireEvent.click(screen.getByRole("button",{name:"取消素材引用：素材甲"}));
  expect(screen.queryByText("素材甲")).not.toBeInTheDocument();
  expect(referenceList.getByText("声音甲")).toBeVisible();expect(referenceList.getByText("素材乙")).toBeVisible();
  act(()=>{
    referenceAssistantSelection([{kind:"shot",task_id:"t",object_id:"c1",version:"v",label:"镜头甲"}]);
    referenceAssistantSelection([{kind:"shot",task_id:"t",object_id:"c2",version:"v",label:"镜头乙"}],true);
    referenceAssistantSelection([{kind:"shot",task_id:"t",object_id:"c2",version:"v",label:"镜头乙"}],true);
  });
  await waitFor(()=>{
    const value=api.referenceSaved.get(saved.id)?.selected_reference;
    expect(value?.kind === "selection" && value.items).toHaveLength(5);
  });
  act(()=>referenceAssistantSelection([{kind:"shot",task_id:"t",object_id:"c3",version:"v",label:"镜头丙"}]));
  await waitFor(()=>{
    const value=api.referenceSaved.get(saved.id)?.selected_reference;
    expect(value?.kind === "selection" && value.items).toHaveLength(4);
  });
});

it("keeps pending inputs bounded near the composer and settled receipts in their original turn", async () => {
  const saved = conversation();
  const receipt = { type: "assistant_input" as const, input_id: "input-one", target_turn_id: "turn-one", message: "保留第一条已完成要求的完整正文", mode: "steer" as const, status: "accepted" as const, reason: "waiting_agent", created_at: "2026-10-24T00:00:00Z", updated_at: "2026-10-24T00:00:00Z" };
  saved.turns[0].events.push(receipt);
  saved.turns.push({ ...saved.turns[0], id: "turn-two", sequence: 2, status: "running", user_message: "继续制作", events: [
    { ...receipt, status: "completed", updated_at: "2026-10-24T00:00:01Z" },
    { ...receipt, input_id: "input-two", target_turn_id: "turn-two", message: "仍在等待的追加要求" },
  ] });
  const withdrawInput = vi.fn().mockResolvedValue(undefined);
  const rendered = render(<AssistantConversationPanel state={state({ conversation: saved, withdrawInput })} input="下一条未发送草稿" onInput={vi.fn()} onSend={vi.fn()} busy={false} configured scopeLabel="当前任务"/>);
  const pending = screen.getByRole("region", { name: "待处理追加要求" });
  expect(pending).toHaveTextContent("仍在等待的追加要求");
  expect(pending).not.toHaveTextContent(receipt.message);
  fireEvent.click(within(pending).getByRole("button", { name: "撤回" }));
  expect(withdrawInput).toHaveBeenCalledWith("input-two");
  const original = rendered.container.querySelector('[data-turn-id="turn-one"]')!;
  expect(original).toHaveTextContent(receipt.message);
  expect(original).toHaveTextContent("Agent 已处理此输入");
  expect(screen.getAllByText(receipt.message)).toHaveLength(1);
  expect(rendered.container.querySelector('[data-turn-id="turn-two"]')).not.toHaveTextContent(receipt.message);
  expect(screen.getByRole("textbox")).toHaveValue("下一条未发送草稿");
});


it("keeps different source paragraphs and video ranges individually reachable from one reference summary", async () => {
  const onRemove = vi.fn();
  render(<ReferenceChips onRemove={onRemove} items={[
    {kind:"knowledge",knowledge_item_id:"document",version:"2",locator:"第1段",label:"同一份资料"},
    {kind:"knowledge",knowledge_item_id:"document",version:"2",locator:"第2段",label:"同一份资料"},
    {kind:"material",selection:{scope:"segment",asset_id:"video",segment_id:"range-one"},label:"同一视频 · 00:00–00:03"},
    {kind:"material",selection:{scope:"segment",asset_id:"video",segment_id:"range-two"},label:"同一视频 · 00:04–00:07"},
  ]}/>);
  const summary = screen.getByRole("button", { name:"查看本轮 4 项引用" });
  expect(screen.queryByRole("button", { name:/取消资料引用/ })).not.toBeInTheDocument();
  summary.focus(); fireEvent.click(summary);
  const remove = await screen.findByRole("button", {name:"取消资料引用：同一份资料 · 第2段"});
  expect(screen.getByRole("button", {name:"取消资料引用：同一份资料 · 第1段"})).toBeVisible();
  expect(screen.getByRole("button", {name:"取消素材引用：同一视频 · 00:00–00:03"})).toBeVisible();
  expect(screen.getByRole("button", {name:"取消素材引用：同一视频 · 00:04–00:07"})).toBeVisible();
  fireEvent.click(remove);
  expect(onRemove).toHaveBeenCalledWith(expect.objectContaining({knowledge_item_id:"document",locator:"第2段",version:"2"}));
  fireEvent.keyDown(screen.getByRole("dialog"), { key:"Escape" });
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  await waitFor(() => expect(summary).toHaveFocus());
});

function windowConversation(status:AssistantScriptWindowRequest['status']='unknown',inputId='window-input',requestId='00000000-0000-4000-8000-000000000001') {
  const saved=conversation('task');
  const receipt:AssistantScriptWindowRequest={kind:'script_window_request',operation:status==='confirmed'?'confirm':'patch',task_id:'task-one',script_id:'script-one',request_id:requestId,
    status,input_id:inputId,input_turn_id:'turn-one',...(['unknown','failed'].includes(status)?{submission_effect:'unknown'}:status==='saved'||status==='confirmed'?{updated_at:'2026-10-25T00:00:00Z',total_count:45}:{})};
  saved.turns=[{...saved.turns[0],events:[],assistant_text:'原窗口业务回执',result:{kind:'task_turn',continuation:{status:'confirmed'},response:{reply:'原窗口业务回执',provider:'local',model:'local',edit_preview:null,script_draft:null,execution_action:null,execution_status:null,topic_candidates:null,
    input_id:inputId,input_turn_id:'turn-one',script_window_request:receipt}}}];
  return saved;
}

it.each(['saved','confirmed','unknown','failed','rejected','not_submitted'] as const)('shows the structured chat window %s receipt without confusing saved drafts with adoption or blocking fresh messages',async(status)=>{
  const saved=windowConversation(status);saved.turns[0].status='failed';
  const refresh=vi.fn(async()=>saved),send=vi.fn();
  render(<AssistantConversationPanel state={state({conversation:saved,refresh})} input="全新聊天草稿" onInput={vi.fn()} onSend={send} busy={false} configured scopeLabel="当前作品"/>);
  expect(screen.getByRole('region',{name:'文案窗口回执'})).toHaveTextContent('00000000-0000-4000-8000-000000000001');
  if(status==='saved') expect(screen.getByText(/文案窗口已保存，其他已存段保留；尚未完整核查并采用/)).toBeInTheDocument();
  if(status==='confirmed') expect(screen.getByText('完整采用 45 段。')).toBeInTheDocument();
  if(status==='rejected') expect(screen.getByText('本次文案窗口请求已被拒绝，原稿保留。')).toBeInTheDocument();
  if(status==='not_submitted') expect(screen.getByText('本次文案窗口请求未提交，原稿保留。')).toBeInTheDocument();
  if(status==='unknown'||status==='failed') {
    expect(assistantTurnHasUnknownRequest(saved.turns[0])).toBe(true);
    expect(assistantTurnNeedsResultRefresh(saved.turns[0])).toBe(true);
    expect(screen.queryByRole('button',{name:'继续编辑本条要求'})).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button',{name:'查询本轮结果'}));
    await waitFor(()=>expect(refresh).toHaveBeenCalledWith('turn-one'));
  }
  await waitFor(()=>expect(screen.getByRole('button',{name:'发送'})).toBeEnabled());
  expect(send).not.toHaveBeenCalled();
});

it('reconciles the original window UUID through GET, preserves a newer draft on 404, and still refreshes unrelated chat data',async()=>{
  const initial=windowConversation();
  api.getCurrentAssistantConversation.mockResolvedValue(initial);
  api.listAssistantConversations.mockResolvedValue({items:[initial]});
  api.getAssistantConversation.mockResolvedValue(initial);
  api.readWindow.mockResolvedValue({task_id:'task-one',script_id:'script-one',request_id:'00000000-0000-4000-8000-000000000001',operation:'patch',status:'unknown',submission_effect:'unknown',message:'仍未完成',submitted_input_available:true});
  const {result}=renderHook(()=>useAssistantConversation({scope_kind:'task',scope_id:'task-one'}));
  await waitFor(()=>expect(result.current.loading).toBe(false));
  act(()=>result.current.setDraftText('用户正在写的新草稿'));
  await act(async()=>{await result.current.refresh('turn-one');});
  expect(api.readWindow).toHaveBeenCalledWith('task-one','00000000-0000-4000-8000-000000000001',{operation:'patch',script_id:'script-one'});
  expect(result.current.draftText).toBe('用户正在写的新草稿');
  api.readWindow.mockRejectedValue(new CreationPlanApiError(404,null,'未找到原窗口请求'));
  api.getAssistantConversation.mockResolvedValue({...initial,title:'其他聊天数据已更新'});
  await act(async()=>{await result.current.refresh('turn-one');});
  expect(result.current.conversation?.title).toBe('其他聊天数据已更新');
  expect(result.current.draftText).toBe('用户正在写的新草稿');
  expect(Object.values(result.current.scriptWindowReadErrors)).toContain('尚未查到原窗口回执，不代表请求未提交；原 UUID 和当前草稿仍保留。');
  expect(assistantTurnHasUnknownRequest(result.current.conversation!.turns[0])).toBe(true);
  api.readWindow.mockResolvedValue({task_id:'task-one',script_id:'script-one',request_id:'00000000-0000-4000-8000-000000000001',status:'saved',updated_at:'2026-10-25T00:01:00Z',total_count:45,affected_section_ids:['section-45']});
  await act(async()=>{await result.current.refresh('turn-one');});
  expect(assistantTaskTurnResult(result.current.conversation!.turns[0])?.response?.script_window_request).toMatchObject({status:'saved',total_count:45,input_id:'window-input',request_id:'00000000-0000-4000-8000-000000000001'});
  expect(result.current.draftText).toBe('用户正在写的新草稿');
  expect(assistantTurnHasUnknownRequest(result.current.conversation!.turns[0])).toBe(false);
  expect(Object.values(result.current.scriptWindowReadErrors)).toHaveLength(0);
  expect(api.restoreUnpersistedAssistantTurnDraft).not.toHaveBeenCalled();
});

it('does not apply an old window receipt to a newer input and request in the same chat turn',async()=>{
  const old=windowConversation(),newer=windowConversation('unknown','new-input','00000000-0000-4000-8000-000000000002');
  api.getCurrentAssistantConversation.mockResolvedValue(old);
  api.listAssistantConversations.mockResolvedValue({items:[old]});
  api.getAssistantConversation.mockResolvedValueOnce(old).mockResolvedValue(newer);
  let finish!:(value:unknown)=>void;
  api.readWindow.mockReturnValueOnce(new Promise(resolve=>{finish=resolve;})).mockResolvedValue({task_id:'task-one',script_id:'script-one',request_id:'00000000-0000-4000-8000-000000000002',operation:'patch',status:'unknown',submission_effect:'unknown',message:'新请求尚未完成',submitted_input_available:true});
  const {result}=renderHook(()=>useAssistantConversation({scope_kind:'task',scope_id:'task-one'}));
  await waitFor(()=>expect(result.current.loading).toBe(false));
  let oldRead!:Promise<unknown>;
  act(()=>{oldRead=result.current.refresh('turn-one');});
  await waitFor(()=>expect(finish).toBeDefined());
  await act(async()=>{await result.current.refresh('turn-one');});
  await act(async()=>{finish({task_id:'task-one',script_id:'script-one',request_id:'00000000-0000-4000-8000-000000000001',status:'saved',updated_at:'2026-10-25T00:01:00Z',total_count:45,affected_section_ids:[]});await oldRead;});
  expect(assistantTaskTurnResult(result.current.conversation!.turns[0])?.response?.script_window_request).toMatchObject({request_id:'00000000-0000-4000-8000-000000000002',input_id:'new-input',status:'unknown'});
});

it('does not let a historical window from another input block the current input or fresh messages',()=>{
  const saved=windowConversation();
  assistantTaskTurnResult(saved.turns[0])!.response!.input_id='current-input';
  expect(assistantTurnHasUnknownRequest(saved.turns[0])).toBe(false);
  render(<AssistantConversationPanel state={state({conversation:saved})} input="另一条新消息" onInput={vi.fn()} onSend={vi.fn()} busy={false} configured scopeLabel="当前作品"/>);
  expect(screen.getByRole('alert')).toHaveTextContent('窗口回执与本轮任务或输入不符');
  expect(screen.getByRole('button',{name:'发送'})).toBeEnabled();
});

it('does not automatically restore the original unknown window instruction but still allows a new local chat turn',async()=>{
  const initial={...conversation('task'),turns:[]};
  api.getCurrentAssistantConversation.mockResolvedValue(initial);
  api.listAssistantConversations.mockResolvedValue({items:[initial]});
  const failed=windowConversation();failed.turns[0].status='failed';failed.turns[0].user_message='原窗口请求';
  api.getAssistantConversation.mockResolvedValue(failed);
  api.readWindow.mockResolvedValue({task_id:'task-one',script_id:'script-one',request_id:'00000000-0000-4000-8000-000000000001',operation:'patch',status:'unknown',submission_effect:'unknown',message:'原请求结果未知',submitted_input_available:true});
  const {result}=renderHook(()=>useAssistantConversation({scope_kind:'task',scope_id:'task-one'}));
  await waitFor(()=>expect(result.current.loading).toBe(false));
  let pending!:ReturnType<typeof result.current.beginTurn>;
  await act(async()=>{pending=result.current.beginTurn('原窗口请求');await pending.draftCheckpoint;});
  act(()=>{result.current.bindTurnEvents(initial.id,pending.localTurnId)({type:'turn_started',turn_id:'turn-one',turn_state:'running'});});
  api.updateAssistantConversation.mockClear();
  await act(async()=>{await result.current.refresh('turn-one');});
  expect(result.current.draftText).toBe('');
  expect(api.updateAssistantConversation).not.toHaveBeenCalledWith(initial.id,expect.objectContaining({draft_text:'原窗口请求'}));
  let next!:ReturnType<typeof result.current.beginTurn>;
  await act(async()=>{next=result.current.beginTurn('完全不同的新要求');await next.draftCheckpoint;});
  expect(result.current.conversation?.turns.at(-1)?.user_message).toBe('完全不同的新要求');
});

it('does not query or lock the current task for an unrelated task window receipt',async()=>{
  const saved=windowConversation();
  assistantTaskTurnResult(saved.turns[0])!.response!.script_window_request!.task_id='another-task';
  expect(assistantTurnHasUnknownRequest(saved.turns[0],'task-one')).toBe(false);
  api.getCurrentAssistantConversation.mockResolvedValue(saved);
  api.listAssistantConversations.mockResolvedValue({items:[saved]});
  api.getAssistantConversation.mockResolvedValue(saved);
  const {result}=renderHook(()=>useAssistantConversation({scope_kind:'task',scope_id:'task-one'}));
  await waitFor(()=>expect(result.current.loading).toBe(false));
  await act(async()=>{await result.current.refresh('turn-one');});
  expect(api.readWindow).not.toHaveBeenCalled();
  render(<AssistantConversationPanel state={state({conversation:saved})} input="当前作品的新要求" onInput={vi.fn()} onSend={vi.fn()} busy={false} configured scopeLabel="当前作品"/>);
  expect(screen.getByRole('alert')).toHaveTextContent('窗口回执与本轮任务或输入不符');
  await waitFor(()=>expect(screen.getByRole('button',{name:'发送'})).toBeEnabled());
});

it('shows a failed original window GET diagnostic without treating unknown submission as safely rejected',async()=>{
  const saved=windowConversation();
  api.getCurrentAssistantConversation.mockResolvedValue(saved);
  api.listAssistantConversations.mockResolvedValue({items:[saved]});
  api.getAssistantConversation.mockResolvedValue(saved);
  api.readWindow.mockResolvedValue({task_id:'task-one',script_id:'script-one',request_id:'00000000-0000-4000-8000-000000000001',operation:'patch',status:'failed',submission_effect:'unknown',message:'原请求失败但无最终回执',submitted_input_available:true,error_detail:{reason:'原段事实核查未通过',impact:'未确认本次保存效果',repair_action:'先核对原窗口请求'}});
  const {result}=renderHook(()=>useAssistantConversation({scope_kind:'task',scope_id:'task-one'}));
  await waitFor(()=>expect(result.current.loading).toBe(false));
  await act(async()=>{await result.current.refresh('turn-one');});
  render(<AssistantConversationPanel state={result.current} input="当前新草稿" onInput={vi.fn()} onSend={vi.fn()} busy={false} configured scopeLabel="当前作品"/>);
  expect(screen.getByText('原段事实核查未通过')).toBeInTheDocument();
  expect(screen.getByText('回执状态：请求失败，提交结果仍待核对')).toBeInTheDocument();
  expect(assistantTurnHasUnknownRequest(result.current.conversation!.turns[0])).toBe(true);
  await waitFor(()=>expect(screen.getByRole('button',{name:'发送'})).toBeEnabled());
});

it('does not bind an origin-less historical window receipt to a known current input',async()=>{
  const saved=windowConversation();
  assistantTaskTurnResult(saved.turns[0])!.response!.script_window_request!.input_id=null;
  expect(assistantTurnHasUnknownRequest(saved.turns[0],'task-one')).toBe(false);
  api.getCurrentAssistantConversation.mockResolvedValue(saved);
  api.listAssistantConversations.mockResolvedValue({items:[saved]});
  api.getAssistantConversation.mockResolvedValue(saved);
  const {result}=renderHook(()=>useAssistantConversation({scope_kind:'task',scope_id:'task-one'}));
  await waitFor(()=>expect(result.current.loading).toBe(false));
  await act(async()=>{await result.current.refresh('turn-one');});
  expect(api.readWindow).not.toHaveBeenCalled();
});


describe("conversation panel width changes", () => {
  function observeSizes() {
    const callbacks = new Map<Element, {callback: ResizeObserverCallback; observer: ResizeObserver}>();
    const original = Object.getOwnPropertyDescriptor(globalThis, "ResizeObserver")!;
    class Observer implements ResizeObserver {
      constructor(private callback: ResizeObserverCallback) {}
      observe(target: Element) { callbacks.set(target, {callback: this.callback, observer: this}); }
      unobserve(target: Element) { callbacks.delete(target); }
      disconnect() { for (const [target, entry] of callbacks) if (entry.observer === this) callbacks.delete(target); }
    }
    Object.defineProperty(globalThis, "ResizeObserver", {configurable: true, value: Observer});
    return { resize: (target: Element) => { const entry = callbacks.get(target); entry?.callback([], entry.observer); }, restore: () => { Object.defineProperty(globalThis, "ResizeObserver", original); } };
  }

  it("remeasures the existing draft on width changes without resetting its content", () => {
    const sizes = observeSizes();
    try {
      const onInput = vi.fn();
      render(<AssistantConversationPanel state={state()} input="保留长草稿，缩窄后继续编辑" onInput={onInput}
        onSend={vi.fn()} busy={false} configured scopeLabel="当前任务"/>);
      const textarea = screen.getByRole<HTMLTextAreaElement>("textbox");
      let width = 320, height = 56;
      vi.spyOn(textarea, "getBoundingClientRect").mockImplementation(() => new DOMRect(0, 0, width, height));
      Object.defineProperty(textarea, "scrollHeight", {configurable: true, get: () => height});
      act(() => sizes.resize(textarea));
      expect(textarea.style.height).toBe("56px");
      width = 260; height = 98;
      act(() => sizes.resize(textarea));
      expect(textarea.style.height).toBe("98px");
      height = 140;
      act(() => sizes.resize(textarea));
      expect(textarea.style.height).toBe("98px");
      width = 360; height = 56;
      act(() => sizes.resize(textarea));
      expect(textarea.style.height).toBe("56px");
      expect(textarea).toHaveValue("保留长草稿，缩窄后继续编辑");
      expect(onInput).not.toHaveBeenCalled();
    } finally { cleanup(); sizes.restore(); }
  });

  it("only offers message expansion after actual overflow and preserves the full message", () => {
    const sizes = observeSizes();
    try {
      const onOpenChange = vi.fn();
      const text = "完整要求：保留原声，加入两个素材，最后核对所有镜头。";
      const {container} = render(<ConversationMessage className="assistant-user-message" onOpenChange={onOpenChange}>{text}</ConversationMessage>);
      const content = container.querySelector<HTMLElement>(".assistant-message-content")!;
      let height = 42;
      Object.defineProperty(content, "scrollHeight", {configurable: true, get: () => height});
      act(() => sizes.resize(content));
      expect(screen.queryByRole("button", {name: "展开全文"})).not.toBeInTheDocument();
      height = 105;
      act(() => sizes.resize(content));
      fireEvent.click(screen.getByRole("button", {name: "展开全文"}));
      expect(content).toHaveAttribute("data-expanded");
      expect(content).toHaveTextContent(text);
      expect(onOpenChange).toHaveBeenLastCalledWith(true);
      fireEvent.click(screen.getByRole("button", {name: "收起"}));
      expect(content).not.toHaveAttribute("data-expanded");
      expect(content).toHaveTextContent(text);
      expect(onOpenChange).toHaveBeenLastCalledWith(false);
      height = 42;
      act(() => sizes.resize(content));
      expect(screen.queryByRole("button", {name: "展开全文"})).not.toBeInTheDocument();
    } finally { cleanup(); sizes.restore(); }
  });
});
