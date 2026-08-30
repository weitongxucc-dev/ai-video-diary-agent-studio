import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { AnchorHTMLAttributes } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  runProductionBatch: vi.fn(),
  selectEpisodeVoice: vi.fn(),
  createMontagePlan: vi.fn(),
  listVoiceProfiles: vi.fn(),
  assignLocation: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  runProductionBatch: api.runProductionBatch,
  selectEpisodeVoice: api.selectEpisodeVoice,
  createMontagePlan: api.createMontagePlan,
}));

vi.mock("@/lib/media-profiles-api", () => ({
  listVoiceProfiles: api.listVoiceProfiles,
}));

vi.mock("next/link", () => ({
  default: ({ children, href, ...props }: AnchorHTMLAttributes<HTMLAnchorElement>) => <a href={href} {...props}>{children}</a>,
}));

import { ProductionTaskControls } from "@/components/ProductionTaskControls";

function renderControls() {
  return render(<ProductionTaskControls assignLocation={api.assignLocation} batchId="task-1" canRun episodes={[{ id: "content-1", title: "已确认文案" }]} runLabel="开始制作" />);
}

async function openPlan() {
  const details = screen.getByText("为已确认文案生成视频剪辑").closest("details")!;
  details.open = true;
  fireEvent(details, new Event("toggle", { bubbles: true }));
  await waitFor(() => expect(api.listVoiceProfiles).toHaveBeenCalledTimes(1));
}

describe("ProductionTaskControls", () => {
  beforeEach(() => {
    Object.values(api).forEach((mock) => mock.mockReset());
    api.listVoiceProfiles.mockResolvedValue({
      items: [{ id: "voice-1", version: "v2" }],
      total_count: 1,
    });
  });

  it("starts a task once and assigns the returned canonical task location", async () => {
    let resolveRun: (value: { id: string }) => void = () => undefined;
    api.runProductionBatch.mockImplementation(() => new Promise((resolve) => { resolveRun = resolve; }));
    renderControls();

    const button = screen.getByRole("button", { name: "开始制作" });
    fireEvent.click(button);
    fireEvent.click(button);
    await waitFor(() => expect(api.runProductionBatch).toHaveBeenCalledTimes(1));
    expect(screen.getByRole("button", { name: "正在开始…" })).toBeDisabled();

    resolveRun({ id: "task-2" });
    await waitFor(() => expect(api.assignLocation).toHaveBeenCalledWith(
      "/production?batch=task-2&toast=success&toast_message=%E5%B7%B2%E5%BC%80%E5%A7%8B%E5%88%B6%E4%BD%9C%EF%BC%8C%E4%BB%BB%E5%8A%A1%E4%BC%9A%E5%9C%A8%E5%AE%8C%E6%88%90%E5%BD%93%E5%89%8D%E6%AD%A5%E9%AA%A4%E5%90%8E%E6%9B%B4%E6%96%B0%E3%80%82",
    ));
    expect(api.assignLocation).toHaveBeenCalledTimes(1);
  });

  it("sends the selected voice id and version before creating one video edit", async () => {
    api.selectEpisodeVoice.mockResolvedValue({});
    api.createMontagePlan.mockResolvedValue({ batch_id: "task-1", episode_id: "content-1" });
    renderControls();
    await openPlan();

    fireEvent.change(screen.getByLabelText("选择已确认文案"), { target: { value: "content-1" } });
    fireEvent.change(screen.getByLabelText("本次配音"), { target: { value: JSON.stringify(["voice-1", "v2"]) } });
    fireEvent.submit(screen.getByRole("button", { name: "生成视频剪辑" }).closest("form")!);

    await waitFor(() => expect(api.selectEpisodeVoice).toHaveBeenCalledWith("content-1", { voice_profile_id: "voice-1", voice_version: "v2" }));
    expect(api.createMontagePlan).toHaveBeenCalledWith("task-1", { episode_id: "content-1" });
    await waitFor(() => expect(api.assignLocation).toHaveBeenCalledWith(
      "/production/task-1/episodes/content-1/montage?toast=success&toast_message=%E8%A7%86%E9%A2%91%E5%89%AA%E8%BE%91%E5%B7%B2%E5%88%9B%E5%BB%BA%EF%BC%8C%E8%AF%B7%E7%A1%AE%E8%AE%A4%E9%95%9C%E5%A4%B4%E5%90%8E%E7%94%9F%E6%88%90%E6%88%90%E7%89%87%E3%80%82",
    ));
  });

  it("sends null id and version together for the default voice", async () => {
    api.selectEpisodeVoice.mockResolvedValue({});
    api.createMontagePlan.mockResolvedValue({ batch_id: "task-1", episode_id: "content-1" });
    renderControls();
    await openPlan();

    fireEvent.change(screen.getByLabelText("选择已确认文案"), { target: { value: "content-1" } });
    fireEvent.submit(screen.getByRole("button", { name: "生成视频剪辑" }).closest("form")!);

    await waitFor(() => expect(api.selectEpisodeVoice).toHaveBeenCalledWith("content-1", { voice_profile_id: null, voice_version: null }));
  });

  it("reports a saved voice selection when plan creation then fails without navigation", async () => {
    api.selectEpisodeVoice.mockResolvedValue({});
    api.createMontagePlan.mockRejectedValue(new Error("素材不足"));
    renderControls();
    await openPlan();

    fireEvent.change(screen.getByLabelText("选择已确认文案"), { target: { value: "content-1" } });
    fireEvent.submit(screen.getByRole("button", { name: "生成视频剪辑" }).closest("form")!);

    expect(await screen.findByRole("alert")).toHaveTextContent("声音选择已保存，但生成视频剪辑失败：素材不足");
    expect(api.assignLocation).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "生成视频剪辑" })).not.toBeDisabled();
  });
});
