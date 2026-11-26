import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { CreationTaskBudget, CreationTaskSegmentRevision, CreationTaskSegments } from "@/lib/types/creation-tasks";
import { SegmentProductionPanel } from "../creation-task/NarrationStage";

const api = vi.hoisted(() => ({
  readCreationTaskSegments: vi.fn(),
  readCreationTaskBudget: vi.fn(),
  readCreationTaskSegmentRevision: vi.fn(),
  resumeCreationTaskSegmentRevision: vi.fn(),
}));
vi.mock("@/lib/api/task-media", () => api);

const taskId = "task_original";
const proposalId = "proposal_original";
const revision: CreationTaskSegmentRevision = {
  task_id: taskId, request_id: "revision_request", job_id: "job_original", section_id: "first",
  input_id: "input_original", status: "succeeded", queue_state: "done", waiting_reason: null,
  semantic_review_ref: "review_original", rules_check: { status: "passed" },
  result: { adoption_proposal_id: proposalId }, usage_source: "job",
  adoption: {
    status: "candidate", current_timeline_version_id: "timeline_before", current_timeline_sequence: 1,
    current_segment_version: "prior_segment", result_segment_version: "candidate_segment", reason: null,
  },
  recovery: { action: "none", reason_code: "complete", requires_revalidation: false },
};

function segments(id: string): CreationTaskSegments {
  return {
    task_id: id, total_count: 1, available_video_count: 0, offset: 0, truncated: false,
    segments: [{
      task_id: id, section_id: "first", ordinal: 1, script_id: "script",
      base_version_id: "timeline_before", segment_version: "prior_segment",
      title: "第一段", text: "原文", source_text_sha256: "text_digest", source_references: [],
      locked_body_digest: null, adjacent_sections: [], narration_segment_id: null,
      audio: null, video: null, adopted: true,
    }],
    revision_requests: { items: id === taskId ? [revision] : [], total_count: id === taskId ? 1 : 0, truncated: false },
  };
}

async function openReceipt() {
  fireEvent.click(await screen.findByRole("button", { name: "查看修改回执" }));
  await screen.findByText("本段产物已登记，尚未采用");
}

describe("section candidate adoption from the original revision receipt", () => {
  beforeEach(() => {
    api.readCreationTaskSegments.mockImplementation(async (id: string) => segments(id));
    api.readCreationTaskBudget.mockImplementation(async (id: string): Promise<CreationTaskBudget> => ({
      task_id: id, authorization_coverage: "scope_unbound", scopes: [],
      status: "authorization_scope_unbound", waiting_reason: null,
      resources: { settled_execution_seconds: 0, settled_generated_bytes: 0,
        total_execution_seconds: null, total_generated_bytes: null, coverage: "partial", receipt_count: 0 },
      actual_cost: { status: "pending_verification", amount: null, currency: null,
        supplier_usage_refs: [], pricing_or_invoice_refs: [] },
      estimates: { eta_seconds: null, cost: null },
    }));
    api.readCreationTaskSegmentRevision.mockResolvedValue(revision);
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.resetAllMocks();
  });

  it("offers the existing desktop entry only for a produced candidate with a proposal", async () => {
    vi.stubGlobal("electronAPI", undefined);
    render(<SegmentProductionPanel taskId={taskId}/>);
    await openReceipt();
    expect(screen.getByText("采用此候选需在曜核桌面应用中进行原生确认。")).toBeVisible();
    expect(screen.queryByRole("button", { name: "采用此候选" })).not.toBeInTheDocument();
    api.readCreationTaskSegmentRevision.mockResolvedValue({ ...revision, result: {} });
    fireEvent.click(screen.getByRole("button", { name: "查询原修改结果" }));
    await waitFor(() => expect(screen.queryByText(/需在曜核桌面应用中/)).not.toBeInTheDocument());
  });

  it("passes only the task and proposal, keeps cancellation local and checks uncertain outcomes before retrying", async () => {
    const confirmSectionCandidate = vi.fn().mockResolvedValueOnce({ status: "canceled" })
      .mockResolvedValueOnce({ status: "unknown", error: "原确认结果尚未核实。" });
    const querySectionCandidateConfirmation = vi.fn().mockResolvedValueOnce({ status: "proposed" })
      .mockResolvedValueOnce({ status: "applied", operationId: "operation", resultVersionId: "timeline_after" });
    const onAdopted = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal("electronAPI", { confirmSectionCandidate, querySectionCandidateConfirmation });
    render(<SegmentProductionPanel taskId={taskId} onAdopted={onAdopted}/>);
    await openReceipt();
    const action = screen.getByRole("button", { name: "采用此候选" });
    fireEvent.click(action);
    await waitFor(() => expect(action).toHaveAttribute("aria-disabled", "false"));
    expect(confirmSectionCandidate).toHaveBeenCalledWith(taskId, proposalId);
    expect(api.readCreationTaskSegmentRevision).toHaveBeenCalledTimes(1);
    expect(onAdopted).not.toHaveBeenCalled();
    fireEvent.click(action);
    fireEvent.click(await screen.findByRole("button", { name: "查询原确认结果" }));
    const retry = await screen.findByRole("button", { name: "重新确认同一候选" });
    expect(querySectionCandidateConfirmation).toHaveBeenCalledWith(taskId, proposalId);
    expect(confirmSectionCandidate).toHaveBeenCalledTimes(2);
    confirmSectionCandidate.mockResolvedValueOnce({ status: "unknown", error: "原确认结果尚未核实。" });
    fireEvent.click(retry);
    api.readCreationTaskSegmentRevision.mockResolvedValue({
      ...revision, adoption: { ...revision.adoption, status: "current" },
    });
    fireEvent.click(await screen.findByRole("button", { name: "查询原确认结果" }));
    await screen.findByText("本段结果已被当前作品采用；整片输出仍须核对");
    expect(onAdopted).toHaveBeenCalledOnce();
    expect(api.readCreationTaskSegmentRevision).toHaveBeenLastCalledWith(taskId, revision.request_id);
    expect(api.resumeCreationTaskSegmentRevision).not.toHaveBeenCalled();
  });

  it("ignores a late adoption outcome after the current work changes", async () => {
    let finish: (value: unknown) => void = () => undefined;
    const confirmSectionCandidate = vi.fn(() => new Promise(resolve => { finish = resolve; }));
    const onAdopted = vi.fn();
    vi.stubGlobal("electronAPI", { confirmSectionCandidate, querySectionCandidateConfirmation: vi.fn() });
    const view = render(<SegmentProductionPanel taskId={taskId} onAdopted={onAdopted}/>);
    await openReceipt();
    fireEvent.click(screen.getByRole("button", { name: "采用此候选" }));
    view.rerender(<SegmentProductionPanel taskId="task_other" onAdopted={onAdopted}/>);
    await act(async () => finish({ status: "applied", operationId: "operation", resultVersionId: "timeline_after" }));
    expect(onAdopted).not.toHaveBeenCalled();
    expect(api.readCreationTaskSegmentRevision).toHaveBeenCalledTimes(1);
    expect(screen.queryByLabelText("修改回执详情")).not.toBeInTheDocument();
  });
});
