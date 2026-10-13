import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import type { CreationExecutionStatus } from "@/lib/types";
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
  vi.clearAllMocks();
  api.readExecution.mockImplementation(async taskId => execution(taskId));
});

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
