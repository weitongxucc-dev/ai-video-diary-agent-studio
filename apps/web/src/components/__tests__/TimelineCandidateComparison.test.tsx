import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import * as api from "@/lib/api/timeline-candidates";
import { TimelineCandidateComparison } from "../creation-task/TimelineCandidateComparison";

afterEach(() => vi.restoreAllMocks());

it("saves two shots on one candidate and adopts the reviewed pair with one replayable confirmation", async () => {
  const base = { id: "v12", sequence: 12 };
  const details = ["a", "b"].map((id): api.TimelineCandidateDetail => ({
    id, workspace_id: "local", task_id: "work", base_version_id: base.id, head_version_id: `${id}13`,
    version: { id: `${id}13`, parent_version_id: base.id, sequence: 13, clips: [
      { id: "clip", fit_mode: "fill", crop_x: id === "a" ? .35 : .8, crop_y: .5 },
      { id: "clip-2", fit_mode: "fill", crop_x: .5, crop_y: .5 },
    ] },
  }));
  const first = { ...details[1].version, id: "b14", sequence: 14, parent_version_id: "b13",
    clips: [details[1].version.clips[0], { ...details[1].version.clips[1], crop_x: .65 }] };
  const secondVersion = { ...first, id: "b15", sequence: 15, parent_version_id: "b14",
    clips: [{ ...first.clips[0], crop_x: .8, crop_y: .4 }, first.clips[1]] };
  const binding = { task_id: "work", candidate_id: "b", candidate_version_id: "b15", candidate_sequence: 15,
    base_version_id: base.id, base_sequence: base.sequence, all_crops: true as const };
  const selection = { ...binding, selection_digest: "digest", changes: [
    { clip_id: "clip", clip_number: 1, before: { fit_mode: "fill", crop_x: .5, crop_y: .5 },
      after: { fit_mode: "fill", crop_x: .8, crop_y: .4 } },
    { clip_id: "clip-2", clip_number: 2, before: { fit_mode: "fill", crop_x: .5, crop_y: .5 },
      after: { fit_mode: "fill", crop_x: .65, crop_y: .5 } },
  ] } satisfies api.TimelineCandidateAdoptionSelection;
  const result = { ...binding, changes: selection.changes,
    result_version_id: "formal13", result_sequence: 13, operation_id: "operation" };
  vi.spyOn(api, "listTimelineCandidates").mockResolvedValue(details);
  vi.spyOn(api, "readTimelineCandidate").mockImplementation(async ({ candidateId }) => details.find(d => d.id === candidateId)!);
  vi.spyOn(api, "readLatestTimelineCandidatePreview").mockResolvedValue(null);
  const save = vi.spyOn(api, "saveTimelineCandidateCrop")
    .mockResolvedValueOnce({ savedVersion: first, headVersionId: first.id })
    .mockResolvedValueOnce({ savedVersion: secondVersion, headVersionId: secondVersion.id });
  const prepare = vi.spyOn(api, "prepareTimelineCandidateAdoption").mockResolvedValue(selection);
  const confirm = vi.spyOn(api, "confirmTimelineCandidateAdoption")
    .mockRejectedValueOnce(new Error("连接中断，查询原结果。"))
    .mockResolvedValueOnce(result);
  const onAdopted = vi.fn().mockResolvedValue(undefined);
  render(<TimelineCandidateComparison taskId="work" base={base} clipId="clip" clipNumber={2}
    currentFormalId={base.id} onAdopted={onAdopted} />);

  const second = within(await screen.findByRole("region", { name: "候选 B" }));
  const switchShot = await second.findByRole("combobox", { name: "编辑镜头" });
  const saveCrop = () => second.getByRole("button", { name: "保存候选裁切" });
  fireEvent.change(switchShot, { target: { value: "clip-2" } });
  fireEvent.change(second.getByLabelText("水平位置（%）"), { target: { value: "65" } });
  expect(switchShot).toBeDisabled();
  fireEvent.click(saveCrop());
  await second.findByText("候选版本 #14 · 已保存");
  fireEvent.change(switchShot, { target: { value: "clip" } });
  fireEvent.change(second.getByLabelText("垂直位置（%）"), { target: { value: "40" } });
  fireEvent.click(saveCrop());
  await second.findByText("候选版本 #15 · 已保存");
  expect(save.mock.calls.map(([, candidate, edit]) => [candidate.head_version_id, edit.clip_id])).toEqual([
    ["b13", "clip-2"], ["b14", "clip"],
  ]);
  const choose = () => second.findByRole("button", { name: "采用候选 B" });
  fireEvent.click(await choose());
  let form = within(await second.findByRole("form", { name: "确认采用候选 B" }));
  expect(form.getByText(/采用后.*80%/)).toBeInTheDocument();
  expect(form.getByText(/采用后.*65%/)).toBeInTheDocument();
  expect(prepare).toHaveBeenCalledWith({ taskId: "work", candidateId: "b" },
    { ...details[1], head_version_id: "b15", version: secondVersion }, base);
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
