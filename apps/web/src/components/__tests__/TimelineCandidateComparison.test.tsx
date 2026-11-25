import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import * as api from "@/lib/api/timeline-candidates";
import { TimelineCandidateComparison } from "../creation-task/TimelineCandidateComparison";

afterEach(() => vi.restoreAllMocks());

it("adopts only the explicitly confirmed head and retries that request after a lost response", async () => {
  const base = { id: "v12", sequence: 12 };
  const details = ["a", "b"].map((id): api.TimelineCandidateDetail => ({
    id, workspace_id: "local", task_id: "work", base_version_id: base.id, head_version_id: `${id}13`,
    version: { id: `${id}13`, parent_version_id: base.id, sequence: 13, clips: [
      { id: "clip", fit_mode: "fill", crop_x: id === "a" ? .35 : .8, crop_y: .5 },
    ] },
  }));
  const binding = { task_id: "work", candidate_id: "b", candidate_version_id: "b13", candidate_sequence: 13,
    base_version_id: base.id, base_sequence: base.sequence, clip_id: "clip" };
  const selection: api.TimelineCandidateAdoptionSelection = { ...binding, clip_number: 2, selection_digest: "digest",
    before: { fit_mode: "fill", crop_x: .5, crop_y: .5 }, after: { fit_mode: "fill", crop_x: .8, crop_y: .5 } };
  const result = { ...binding, result_version_id: "formal13", result_sequence: 13, operation_id: "operation" };
  vi.spyOn(api, "listTimelineCandidates").mockResolvedValue(details);
  vi.spyOn(api, "readTimelineCandidate").mockImplementation(async ({ candidateId }) => details.find(d => d.id === candidateId)!);
  vi.spyOn(api, "readLatestTimelineCandidatePreview").mockResolvedValue(null);
  const prepare = vi.spyOn(api, "prepareTimelineCandidateAdoption").mockResolvedValue(selection);
  const confirm = vi.spyOn(api, "confirmTimelineCandidateAdoption")
    .mockRejectedValueOnce(new Error("连接中断，查询原结果。"))
    .mockResolvedValueOnce(result);
  const onAdopted = vi.fn().mockResolvedValue(undefined);
  render(<TimelineCandidateComparison taskId="work" base={base} clipId="clip" clipNumber={2}
    currentFormalId={base.id} onAdopted={onAdopted} />);

  const second = within(await screen.findByRole("region", { name: "候选 B" }));
  const choose = () => second.findByRole("button", { name: "采用候选 B" });
  fireEvent.click(await choose());
  let form = within(await second.findByRole("form", { name: "确认采用候选 B" }));
  expect(form.getByText(/采用后.*80%/)).toBeInTheDocument();
  expect(prepare).toHaveBeenCalledWith({ taskId: "work", candidateId: "b" }, details[1], base, "clip");
  expect(confirm).not.toHaveBeenCalled();
  fireEvent.click(form.getByRole("button", { name: "取消" }));
  expect(confirm).not.toHaveBeenCalled();
  fireEvent.click(await choose());
  form = within(await second.findByRole("form", { name: "确认采用候选 B" }));
  fireEvent.click(form.getByRole("button", { name: "确认采用候选 B" }));
  expect(await second.findByRole("alert")).toHaveTextContent("连接中断");
  expect(onAdopted).not.toHaveBeenCalled();
  fireEvent.click(form.getByRole("button", { name: "确认采用候选 B" }));
  await waitFor(() => expect(onAdopted).toHaveBeenCalledExactlyOnceWith(result));
  expect(confirm.mock.calls[0]).toEqual([selection, expect.any(String)]);
  expect(confirm.mock.calls[1]).toEqual(confirm.mock.calls[0]);
  expect(within(screen.getByRole("region", { name: "候选 A" })).getByLabelText("水平位置（%）")).toHaveValue(35);
  expect(second.getByRole("status")).toHaveTextContent("已采用为正式第 13 版");
});
