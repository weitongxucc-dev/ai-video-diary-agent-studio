import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { CreationExecutionStatus, CreationTaskWorkspace } from "@/lib/types";
import { useCreationExecution } from "../creation-task/useCreationExecution";

const api = vi.hoisted(() => ({ readExecution: vi.fn(), readWorkspace: vi.fn(), preview: vi.fn(), readVisual: vi.fn(), adoptVisual: vi.fn(), regenerateStoryboard: vi.fn(), readStoryboard: vi.fn() }));
vi.mock("@/lib/api", () => ({
  getCreationExecution: api.readExecution,
  getCreationTaskWorkspace: api.readWorkspace,
  previewCreationExecution: api.preview,
  CreationPlanApiError: class extends Error {},
}));

vi.mock('@/lib/api/creation-tasks', () => ({ getSegmentVisualDecision: api.readVisual, adoptSegmentVisualDecision: api.adoptVisual, regenerateCreationTaskStoryboard: api.regenerateStoryboard, readCreationTaskStoryboardRegeneration: api.readStoryboard }));

function execution(taskId: string): CreationExecutionStatus {
  return { task_id: taskId, job: null, stages: [], current_stage: null, waiting_reason: null,
    status_updated_at: null, video_ready: false, cover_status: "pending", cover_message: null };
}

beforeEach(() => {
  vi.resetAllMocks();
  api.readExecution.mockImplementation(async taskId => execution(taskId));
});
afterEach(() => vi.useRealTimers());

it("keeps a late execution command from replacing the task entered afterwards", async () => {
  const apply = vi.fn(() => true);
  const { result, rerender } = renderHook(({ taskId }) => useCreationExecution({ taskId, confirmedScriptId: null,
    timelineVersionId: null, voiceId: undefined, voiceVersion: undefined, workspaceReadRevision: 0, onWorkspaceRead: apply }),
  { initialProps: { taskId: "task-a" } });
  await waitFor(() => expect(result.current.execution?.task_id).toBe("task-a"));
  let resolve!: (value: CreationExecutionStatus) => void;
  const command = new Promise<CreationExecutionStatus>(done => { resolve = done; });
  let pending!: Promise<void>;
  act(() => { pending = result.current.changeExecution(() => command); });
  rerender({ taskId: "task-b" });
  await waitFor(() => expect(result.current.execution?.task_id).toBe("task-b"));
  await act(async () => { resolve(execution("task-a")); await pending; });
  expect(result.current.execution?.task_id).toBe("task-b");
  expect(api.readWorkspace).not.toHaveBeenCalled();
  expect(apply).not.toHaveBeenCalled();
});

it("does not attach a late command failure to the newly entered task", async () => {
  const { result, rerender } = renderHook(({ taskId }) => useCreationExecution({ taskId, confirmedScriptId: null,
    timelineVersionId: null, voiceId: undefined, voiceVersion: undefined, workspaceReadRevision: 0, onWorkspaceRead: vi.fn(() => true) }),
  { initialProps: { taskId: "task-a" } });
  let reject!: (cause: Error) => void;
  const command = new Promise<CreationExecutionStatus>((_, fail) => { reject = fail; });
  let pending!: Promise<void>;
  act(() => { pending = result.current.changeExecution(() => command); });
  rerender({ taskId: "task-b" });
  await waitFor(() => expect(result.current.execution?.task_id).toBe("task-b"));
  await act(async () => { reject(new Error("原任务制作未完成")); await pending; });
  expect(result.current.executionError).toBeNull();
});

function finishedExecution(): CreationExecutionStatus {
  return { ...execution("task-a"), job: { id: "job-a", kind: "creation", status: "succeeded", progress: 100,
    failure_reason: null, impact: null, repair_action: null } };
}
function readback(name: string): CreationTaskWorkspace {
  return { task: { id: "task-a", name, current_timeline_version_id: "version-1" } } as CreationTaskWorkspace;
}
function mountReadback(apply = vi.fn(() => true), confirmedScriptId: string | null = null) {
  return renderHook(() => useCreationExecution({ taskId: "task-a", confirmedScriptId,
    timelineVersionId: "version-1", voiceId: undefined, voiceVersion: undefined, workspaceReadRevision: 0, onWorkspaceRead: apply }));
}

it("retries the same terminal job after a failed full read and clears that read error", async () => {
  vi.useFakeTimers();
  api.readExecution.mockResolvedValue(finishedExecution());
  api.readWorkspace.mockRejectedValueOnce(new Error("完整回读失败")).mockResolvedValue(readback("当前事实"));
  const apply = vi.fn(() => true);
  const { result } = mountReadback(apply);
  await act(async () => {});
  expect(result.current.executionError?.message).toBe("完整回读失败");
  await act(async () => { await vi.advanceTimersByTimeAsync(15000); });
  expect(apply).toHaveBeenCalledWith(readback("当前事实"), 0);
  expect(api.readWorkspace).toHaveBeenCalledTimes(2);
  expect(result.current.executionError).toBeNull();
});

it("ignores an older full read failure after a newer command read has been accepted", async () => {
  api.readExecution.mockResolvedValue(finishedExecution());
  let reject!: (cause: Error) => void;
  api.readWorkspace.mockReturnValueOnce(new Promise((_, fail) => { reject = fail; })).mockResolvedValue(readback("新事实"));
  const apply = vi.fn(() => true);
  const { result } = mountReadback(apply);
  await act(async () => {});
  await act(async () => { await result.current.changeExecution(async () => finishedExecution()); });
  await act(async () => { reject(new Error("旧完整回读失败")); });
  expect(apply).toHaveBeenCalledOnce();
  expect(apply).toHaveBeenCalledWith(readback("新事实"), 0);
  expect(result.current.executionError).toBeNull();
});

it("does not clear a requirement-preview failure merely because the full read succeeds", async () => {
  api.readExecution.mockResolvedValue(finishedExecution());
  api.preview.mockRejectedValue(new Error("制作要求未满足"));
  api.readWorkspace.mockResolvedValue(readback("当前事实"));
  const { result } = mountReadback(vi.fn(() => true), "script-a");
  await waitFor(() => expect(result.current.executionError?.message).toBe("制作要求未满足"));
});

it("keeps a newer command refusal after an older execution poll fails and continues automatic polling", async () => {
  vi.useFakeTimers();
  let reject!: (cause: Error) => void;
  api.readExecution.mockReturnValueOnce(new Promise((_, fail) => { reject = fail; })).mockResolvedValue(execution("task-a"));
  const { result } = mountReadback();
  await act(async () => {});
  await act(async () => { await result.current.changeExecution(async () => { throw new Error("new command refusal"); }); });
  expect(result.current.executionError?.message).toBe("new command refusal");
  await act(async () => { reject(new Error("stale execution poll error")); });
  expect(result.current.executionError?.message).toBe("new command refusal");
  await act(async () => { await vi.advanceTimersByTimeAsync(15000); });
  expect(api.readExecution).toHaveBeenCalledTimes(2);
  expect(result.current.execution?.task_id).toBe("task-a");
  expect(result.current.executionError?.message).toBe("new command refusal");
});

it("keeps a newer command refusal when an older full workspace read fails", async () => {
  api.readExecution.mockResolvedValue(finishedExecution());
  let reject!: (cause: Error) => void;
  api.readWorkspace.mockReturnValueOnce(new Promise((_, fail) => { reject = fail; }));
  const { result } = mountReadback();
  await act(async () => {});
  await act(async () => { await result.current.changeExecution(async () => { throw new Error("new command refusal"); }); });
  await act(async () => { reject(new Error("stale workspace error")); });
  expect(result.current.executionError?.message).toBe("new command refusal");
});

it("keeps a newer command refusal when an older command fails afterwards", async () => {
  let reject!: (cause: Error) => void;
  const old = new Promise<CreationExecutionStatus>((_, fail) => { reject = fail; });
  const { result } = mountReadback();
  let pending!: Promise<void>;
  act(() => { pending = result.current.changeExecution(() => old); });
  await act(async () => { await result.current.changeExecution(async () => { throw new Error("new command refusal"); }); });
  await act(async () => { reject(new Error("old command refusal")); await pending; });
  expect(result.current.executionError?.message).toBe("new command refusal");
});

it("retries a terminal workspace read rejected by the parent until its newer receipt is accepted", async () => {
  vi.useFakeTimers();
  api.readExecution.mockResolvedValue(finishedExecution());
  api.readWorkspace.mockResolvedValueOnce(readback("旧事实")).mockResolvedValue(readback("新事实"));
  const apply = vi.fn().mockReturnValueOnce(false).mockReturnValue(true);
  const { rerender } = renderHook(({ revision }) => useCreationExecution({ taskId: "task-a", confirmedScriptId: null,
    timelineVersionId: "version-1", voiceId: undefined, voiceVersion: undefined, workspaceReadRevision: revision, onWorkspaceRead: apply }),
  { initialProps: { revision: 0 } });
  await act(async () => {});
  expect(apply).toHaveBeenNthCalledWith(1, readback("旧事实"), 0);
  rerender({ revision: 1 });
  await act(async () => { await vi.advanceTimersByTimeAsync(15000); });
  expect(apply).toHaveBeenNthCalledWith(2, readback("新事实"), 1);
  await act(async () => { await vi.advanceTimersByTimeAsync(15000); });
  expect(api.readWorkspace).toHaveBeenCalledTimes(2);
});

it("ignores an old full-read failure after a newer parent receipt and still retries the terminal job", async () => {
  vi.useFakeTimers();
  api.readExecution.mockResolvedValue(finishedExecution());
  let reject!: (cause: Error) => void;
  api.readWorkspace.mockReturnValueOnce(new Promise((_, fail) => { reject = fail; })).mockResolvedValue(readback("新事实"));
  const apply = vi.fn(() => true);
  const { result, rerender } = renderHook(({ revision }) => useCreationExecution({ taskId: "task-a", confirmedScriptId: null,
    timelineVersionId: "version-1", voiceId: undefined, voiceVersion: undefined, workspaceReadRevision: revision, onWorkspaceRead: apply }),
  { initialProps: { revision: 0 } });
  await act(async () => {});
  rerender({ revision: 1 });
  await act(async () => { reject(new Error("旧完整回读失败")); });
  expect(result.current.executionError).toBeNull();
  await act(async () => { await vi.advanceTimersByTimeAsync(15000); });
  expect(apply).toHaveBeenCalledWith(readback("新事实"), 1);
  expect(result.current.executionError).toBeNull();
});


function visual() {
  return { task_id:'task-a',job_id:'job-a',section_id:'section-a',decision_digest:'original-digest',status:'waiting_for_user',
    adopted:false,audio:{persisted:true},usage:{settled:true},candidate_clips:[],candidate_total_count:0,candidate_offset:0,
    next_offset:null,required_confirmation_ids:['original-clip'],confirmed_clip_ids:[],device_request_id:null };
}

it('does not attach a late visual decision to the next task', async () => {
  let resolve!: (value: unknown) => void;
  api.readVisual.mockImplementation(() => new Promise(done => { resolve=done; }));
  const { result,rerender }=renderHook(({taskId})=>useCreationExecution({taskId,confirmedScriptId:null,timelineVersionId:null,
    voiceId:undefined,voiceVersion:undefined,workspaceReadRevision:0,onWorkspaceRead:vi.fn(()=>true)}),{initialProps:{taskId:'task-a'}});
  let pending!: Promise<void>;
  act(()=>{pending=result.current.readVisualDecision({job_id:'job-a',section_id:'section-a'});});
  rerender({taskId:'task-b'});
  await act(async()=>{resolve(visual());await pending;});
  expect(result.current.visualDecision).toBeNull();
});

it('submits a visible choice once with the original digest and never automatically retries an unknown queue result', async () => {
  api.readVisual.mockResolvedValue(visual());
  let reject!: (cause: Error) => void;
  api.adoptVisual.mockImplementation(()=>new Promise((_,fail)=>{reject=fail;}));
  const {result}=mountReadback();
  await act(async()=>{await result.current.readVisualDecision({job_id:'job-a',section_id:'section-a'});});
  let pending!: Promise<void>;
  act(()=>{pending=result.current.adoptVisualDecision(['original-clip']); void result.current.adoptVisualDecision(['original-clip']);});
  expect(api.adoptVisual).toHaveBeenCalledTimes(1);
  expect(api.adoptVisual).toHaveBeenCalledWith('task-a','job-a','section-a',{decision_digest:'original-digest',confirmed_clip_ids:['original-clip']});
  await act(async()=>{reject(new Error('原采用可能已保存'));await pending;});
  expect(result.current.visualError).toContain('回查原决定');
  expect(api.adoptVisual).toHaveBeenCalledTimes(1);
  expect(api.readVisual).toHaveBeenCalledTimes(1);
  expect(result.current.visualDecision?.decision_digest).toBe('original-digest');
  expect(result.current.visualReadRequired).toBe(true);
  await act(async()=>{await result.current.adoptVisualDecision(['original-clip']);});
  expect(api.adoptVisual).toHaveBeenCalledTimes(1);
  api.readVisual.mockRejectedValueOnce(new Error('读取原决定失败'));
  await act(async()=>{await result.current.readVisualDecision({job_id:'job-a',section_id:'section-a'});});
  await act(async()=>{await result.current.adoptVisualDecision(['original-clip']);});
  expect(api.adoptVisual).toHaveBeenCalledTimes(1);
  const recorded={...visual(),adopted:true,status:'adopted',confirmed_clip_ids:['original-clip']};
  api.readVisual.mockResolvedValueOnce(recorded);
  await act(async()=>{await result.current.readVisualDecision({job_id:'job-a',section_id:'section-a'});});
  expect(result.current.visualReadRequired).toBe(false);
  expect(api.adoptVisual).toHaveBeenCalledTimes(1);
  api.adoptVisual.mockResolvedValueOnce({decision:recorded,execution:execution('task-a')});
  await act(async()=>{await result.current.adoptVisualDecision(['original-clip']);});
  expect(api.adoptVisual).toHaveBeenCalledTimes(2);
});

const storyboardInput = { script_id: "script-a", input_digest: "a".repeat(64),
  expected_timeline_version_id: "version-1", expected_timeline_sequence: 1 };
function storyboardReceipt(requestId: string, status: "queued" | "succeeded" = "queued", taskId = "task-a") {
  return { request_id: requestId, task_id: taskId, job_id: `storyboard-${taskId}`, status, stage: "montage_plan" as const,
    script_id: "script-a", narration_cache_key: "original-voice", base_timeline_version_id: "version-1",
    base_timeline_sequence: 1, result: null, failure_reason: null, result_url: `/api/creation-tasks/${taskId}/montage/plan/requests/${requestId}` };
}

it("submits the canonical storyboard input once and refreshes actual execution and workspace", async () => {
  api.readWorkspace.mockResolvedValue(readback("原作品"));
  api.regenerateStoryboard.mockImplementation(async (_taskId, input) => storyboardReceipt(input.request_id));
  const { result } = mountReadback();
  await act(async () => { await Promise.all([result.current.regenerateStoryboard(storyboardInput), result.current.regenerateStoryboard(storyboardInput)]); });
  expect(api.regenerateStoryboard).toHaveBeenCalledTimes(1);
  const input = api.regenerateStoryboard.mock.calls[0][1];
  expect(input).toEqual({ ...storyboardInput, request_id: expect.stringMatching(/^[0-9a-f-]{36}$/) });
  expect(result.current.storyboard.receipt).toEqual(storyboardReceipt(input.request_id));
  expect(result.current.storyboard.canSubmit).toBe(false);
  expect(api.readWorkspace).toHaveBeenCalledWith("task-a");
  expect(result.current.execution?.task_id).toBe("task-a");
});

it("retains the original UUID after an unknown submit and failed or mismatched reads, without another POST", async () => {
  api.regenerateStoryboard.mockRejectedValue(new Error("连接中断"));
  api.readWorkspace.mockResolvedValue(readback("当前作品"));
  const { result } = mountReadback();
  await act(async () => { await result.current.regenerateStoryboard(storyboardInput); });
  const requestId = api.regenerateStoryboard.mock.calls[0][1].request_id;
  api.readStoryboard.mockRejectedValueOnce(new Error("原请求暂不可查"))
    .mockResolvedValueOnce(storyboardReceipt(requestId, "succeeded", "task-b"))
    .mockResolvedValueOnce(storyboardReceipt("other-request", "succeeded"))
    .mockResolvedValue(storyboardReceipt(requestId, "succeeded"));
  for (let i = 0; i < 3; i++) {
    await act(async () => { await result.current.readStoryboard(); await result.current.regenerateStoryboard(storyboardInput); });
    expect(result.current.storyboard.requestId).toBe(requestId);
    expect(result.current.storyboard.canSubmit).toBe(false);
    expect(result.current.storyboard.receipt).toBeNull();
  }
  expect(api.regenerateStoryboard).toHaveBeenCalledTimes(1);
  api.readWorkspace.mockRejectedValue(new Error("当前任务刷新失败"));
  await act(async () => { await result.current.readStoryboard(); });
  expect(api.readStoryboard).toHaveBeenLastCalledWith("task-a", requestId);
  expect(result.current.storyboard.receipt?.status).toBe("succeeded");
  expect(result.current.storyboard.refreshPending).toBe(true);
  expect(result.current.storyboard.canSubmit).toBe(false);
  await act(async () => { await result.current.regenerateStoryboard(storyboardInput); });
  expect(api.regenerateStoryboard).toHaveBeenCalledTimes(1);
  api.readWorkspace.mockResolvedValue(readback("当前事实已刷新"));
  await act(async () => { await result.current.readStoryboard(); });
  expect(api.readStoryboard).toHaveBeenLastCalledWith("task-a", requestId);
  expect(result.current.storyboard.refreshPending).toBe(false);
  expect(result.current.storyboard.canSubmit).toBe(true);
  expect(result.current.storyboard.error).toBeNull();
});

it("does not publish a late storyboard receipt into another task and retains its locator when returning", async () => {
  let resolve!: (value: ReturnType<typeof storyboardReceipt>) => void;
  api.regenerateStoryboard.mockReturnValue(new Promise(done => { resolve = done; }));
  const { result, rerender } = renderHook(({ taskId }) => useCreationExecution({ taskId, confirmedScriptId: null,
    timelineVersionId: null, voiceId: undefined, voiceVersion: undefined, workspaceReadRevision: 0, onWorkspaceRead: vi.fn(() => true) }),
    { initialProps: { taskId: "task-a" } });
  let pending!: Promise<void>;
  act(() => { pending = result.current.regenerateStoryboard(storyboardInput); });
  const requestId = api.regenerateStoryboard.mock.calls[0][1].request_id;
  rerender({ taskId: "task-b" });
  await act(async () => { resolve(storyboardReceipt(requestId)); await pending; });
  expect(result.current.storyboard.requestId).toBeNull();
  expect(api.readWorkspace).not.toHaveBeenCalled();
  rerender({ taskId: "task-a" });
  expect(result.current.storyboard.requestId).toBe(requestId);
  expect(result.current.storyboard.busy).toBe(false);
  await act(async () => { await result.current.regenerateStoryboard(storyboardInput); });
  expect(api.regenerateStoryboard).toHaveBeenCalledTimes(1);
});

it("uses the existing execution poll to read the same storyboard receipt when that job completes", async () => {
  api.readWorkspace.mockResolvedValue(readback("当前作品"));
  api.regenerateStoryboard.mockImplementation(async (_taskId, input) => storyboardReceipt(input.request_id));
  const current = { ...finishedExecution(), job: { ...finishedExecution().job!, id: "storyboard-task-a", status: "queued" as const } };
  api.readExecution.mockResolvedValue(current);
  const { result } = mountReadback();
  await act(async () => { await result.current.regenerateStoryboard(storyboardInput); });
  const requestId = result.current.storyboard.requestId!;
  api.readStoryboard.mockResolvedValue(storyboardReceipt(requestId, "succeeded"));
  api.readExecution.mockResolvedValue({ ...current, job: { ...current.job, status: "succeeded" } });
  act(() => { result.current.pollExecution(); });
  await waitFor(() => expect(result.current.storyboard.receipt?.status).toBe("succeeded"));
  expect(api.readStoryboard).toHaveBeenCalledWith("task-a", requestId);
  expect(result.current.storyboard.canSubmit).toBe(true);
});


it("never posts adoption for a recovered planning record without a decision digest", async () => {
  const recovered = { ...visual(), status:"adopted",adopted:true,can_adopt:false,decision_digest:null,
    required_confirmation_ids:[],reason:"合法画面已准备，原请求继续执行",
    recovery:{action:"resume_original",requires_revalidation:true,detail:"保留原请求"} };
  api.readVisual.mockResolvedValue(recovered);
  const {result} = renderHook(() => useCreationExecution({taskId:"task-a",confirmedScriptId:null,
    timelineVersionId:"version-1",voiceId:undefined,voiceVersion:undefined,workspaceReadRevision:0,onWorkspaceRead:vi.fn(()=>true)}));
  await act(async()=>{await result.current.readVisualDecision({job_id:"job-a",section_id:"section-a"});});
  expect(result.current.visualDecision?.decision_digest).toBeNull();
  await act(async()=>{await result.current.adoptVisualDecision([]);});
  expect(api.adoptVisual).not.toHaveBeenCalled();
  expect(api.readWorkspace).not.toHaveBeenCalled();
});
