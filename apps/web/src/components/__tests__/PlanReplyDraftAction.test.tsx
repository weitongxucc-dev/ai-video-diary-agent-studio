import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { PlanReplyDraftAction } from "@/components/CreationTaskWorkspace";
import type { CreationTask } from "@/lib/types";

const api = vi.hoisted(() => ({
  getAssistantScriptContext: vi.fn(), getCreationPlan: vi.fn(), transferAssistantReplyToScript: vi.fn(),
}));
vi.mock("@/lib/api", () => api);

const origin = {
  conversation_id: "11111111-1111-4111-8111-111111111111",
  turn_id: "22222222-2222-4222-8222-222222222222",
  request_id: "33333333-3333-4333-8333-333333333333",
};
const task = { id: "task-one", plan_id: "plan-one", updated_at: "2026-10-09T00:00:00Z" } as CreationTask;

beforeEach(() => {
  vi.resetAllMocks(); sessionStorage.clear();
  api.getAssistantScriptContext.mockResolvedValue({ reply_origin: origin, sources: [], draft: null });
  api.getCreationPlan.mockResolvedValue({ updated_at: "2026-10-09T00:00:01Z" });
  api.transferAssistantReplyToScript.mockResolvedValue({});
});

describe("PlanReplyDraftAction", () => {
  it("uses the server-bound home reply only after topic confirmation and reports saved draft", async () => {
    const onComplete = vi.fn().mockResolvedValue(undefined);
    render(<PlanReplyDraftAction task={task} onComplete={onComplete} />);
    fireEvent.click(await screen.findByRole("button", { name: "保存这条回复为文案草稿" }));
    await waitFor(() => expect(api.transferAssistantReplyToScript).toHaveBeenCalledWith("task-one", expect.objectContaining({
      conversation_id: origin.conversation_id, turn_id: origin.turn_id,
      plan_conversion_request_id: origin.request_id,
      expected_plan_updated_at: "2026-10-09T00:00:01Z", expected_task_updated_at: task.updated_at,
    })));
    expect(onComplete).toHaveBeenCalledOnce();
  });

  it("keeps the actual API failure visible and does not report completion", async () => {
    api.transferAssistantReplyToScript.mockRejectedValue(new Error("当前选题已变化"));
    const onComplete = vi.fn();
    render(<PlanReplyDraftAction task={task} onComplete={onComplete} />);
    fireEvent.click(await screen.findByRole("button", { name: "保存这条回复为文案草稿" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("当前选题已变化");
    expect(onComplete).not.toHaveBeenCalled();
  });
});
