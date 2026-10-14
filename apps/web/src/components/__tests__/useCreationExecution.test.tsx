import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { CreationExecutionStatus, CreationTaskWorkspace } from "@/lib/types";
import { useCreationExecution } from "../creation-task/useCreationExecution";

const api = vi.hoisted(() => ({ readExecution: vi.fn(), readWorkspace: vi.fn(), preview: vi.fn() }));
vi.mock("@/lib/api", () => ({
  getCreationExecution: api.readExecution,
  getCreationTaskWorkspace: api.readWorkspace,
  previewCreationExecution: api.preview,
  CreationPlanApiError: class extends Error {},
}));

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
  const apply = vi.fn();
  const { result, rerender } = renderHook(({ taskId }) => useCreationExecution({ taskId, confirmedScriptId: null,
    timelineVersionId: null, voiceId: undefined, voiceVersion: undefined, onWorkspaceRead: apply }),
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
    timelineVersionId: null, voiceId: undefined, voiceVersion: undefined, onWorkspaceRead: vi.fn() }),
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
function mountReadback(apply = vi.fn(), confirmedScriptId: string | null = null) {
  return renderHook(() => useCreationExecution({ taskId: "task-a", confirmedScriptId,
    timelineVersionId: "version-1", voiceId: undefined, voiceVersion: undefined, onWorkspaceRead: apply }));
}

it("retries the same terminal job after a failed full read and clears that read error", async () => {
  vi.useFakeTimers();
  api.readExecution.mockResolvedValue(finishedExecution());
  api.readWorkspace.mockRejectedValueOnce(new Error("完整回读失败")).mockResolvedValue(readback("当前事实"));
  const apply = vi.fn();
  const { result } = mountReadback(apply);
  await act(async () => {});
  expect(result.current.executionError?.message).toBe("完整回读失败");
  await act(async () => { await vi.advanceTimersByTimeAsync(15000); });
  expect(apply).toHaveBeenCalledWith(readback("当前事实"));
  expect(api.readWorkspace).toHaveBeenCalledTimes(2);
  expect(result.current.executionError).toBeNull();
});

it("ignores an older full read failure after a newer command read has been accepted", async () => {
  api.readExecution.mockResolvedValue(finishedExecution());
  let reject!: (cause: Error) => void;
  api.readWorkspace.mockReturnValueOnce(new Promise((_, fail) => { reject = fail; })).mockResolvedValue(readback("新事实"));
  const apply = vi.fn();
  const { result } = mountReadback(apply);
  await act(async () => {});
  await act(async () => { await result.current.changeExecution(async () => finishedExecution()); });
  await act(async () => { reject(new Error("旧完整回读失败")); });
  expect(apply).toHaveBeenCalledOnce();
  expect(apply).toHaveBeenCalledWith(readback("新事实"));
  expect(result.current.executionError).toBeNull();
});

it("does not clear a requirement-preview failure merely because the full read succeeds", async () => {
  api.readExecution.mockResolvedValue(finishedExecution());
  api.preview.mockRejectedValue(new Error("制作要求未满足"));
  api.readWorkspace.mockResolvedValue(readback("当前事实"));
  const { result } = mountReadback(vi.fn(), "script-a");
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
