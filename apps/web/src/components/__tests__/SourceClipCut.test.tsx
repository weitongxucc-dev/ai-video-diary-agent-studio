import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { taskMediaJobMatchesContext } from "@/lib/api/task-media";
import type { TaskMediaJob } from "@/lib/types";
import { SourceClipCut, type SourceClipCutTarget } from "../creation-task/SourceClipCut";

const preparationPath = "/api/device/resource-preparations";
const mediaPath = "/api/creation-tasks/work/media-jobs";
const jobPath = `${mediaPath}/cut-job`;
const preparationId = "a".repeat(32);
const actorScope = "b".repeat(64);
const target: SourceClipCutTarget = { taskId: "work", baseVersionId: "base", baseSequence: 12,
  assetId: "source-asset", clipNumber: 2, sourceName: "source.mp4", start: 1.25, end: 4.5 };
const audioCutJob: TaskMediaJob = {
  job_id: "cut-job", status: "queued", result_kind: "media_output", operation: "ffmpeg_execution",
  result_url: jobPath, base_version_id: "base", target_refs: [], input_digest: "digest", created_at: "2026-11-26T00:00:00Z",
  result: null, device_request_id: "cut-device-request", waiting_reason: null, failure_reason: null, repair_action: null, resource_wait: null,
};
const version = { id: "published", sequence: 13 };
const recorded = { id: preparationId, plan_id: "old-plan", state: "done", wait_reason: null, mp4_cut_measurement: "recorded" };
const json = (value: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(value), { status }));

function mockRequests(media: (path: string, init?: RequestInit) => Promise<Response>, receipt = () => json(recorded)) {
  const fetchMock = vi.fn((path: string, init?: RequestInit) => {
    if (path === preparationPath) return json({ actor_scope: actorScope, cpu_preparation_supported: true,
      preparation_configured: true, profile_status: "available", media_execution_ready: false,
      status_endpoint: preparationPath, prepare_endpoint: preparationPath, message: "已存档", available_operations: [] });
    if (path === `${preparationPath}/${preparationId}`) {
      expect(init?.headers).toMatchObject({ "X-Device-Preparation-Scope": actorScope });
      return receipt();
    }
    return media(path, init);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

async function verifyPreparation() {
  await waitFor(() => expect(screen.getByRole("button", { name: "核对回执" })).toBeEnabled());
  fireEvent.change(screen.getByRole("textbox", { name: "截取准备回执编号" }), { target: { value: preparationId } });
  fireEvent.click(screen.getByRole("button", { name: "核对回执" }));
}

const renderCut = (currentVersionId = target.baseVersionId, onPublished = vi.fn().mockResolvedValue(version.sequence), selected = target) =>
  render(<SourceClipCut target={selected} currentVersionId={currentVersionId} onPublished={onPublished} onJob={vi.fn()} />);

afterEach(() => {
  cleanup();
  localStorage.clear();
  vi.unstubAllGlobals();
});

it.each([
  ["older receipts", { ...recorded, mp4_cut_measurement: undefined }, 200, "截取测量状态未知"],
  ["unselected measurements", { ...recorded, mp4_cut_measurement: "not_requested" }, 200, "未选择 MP4 截取测量"],
  ["unfinished preparations", { ...recorded, state: "running" }, 200, "准备尚未完成"],
  ["cross-member receipts", { detail: "当前成员无权读取该准备请求" }, 403, "当前成员无权读取"],
])("keeps submission disabled for %s", async (_, body, status, message) => {
  const media = vi.fn();
  mockRequests(media, () => json(body, status));
  renderCut();
  await verifyPreparation();
  expect(await screen.findByRole("alert")).toHaveTextContent(message);
  expect(screen.getByRole("button", { name: "截取并保存" })).toBeDisabled();
  expect(media).not.toHaveBeenCalled();
});

it("reconciles a lost cut receipt with the same request, then reopens the published output by GET", async () => {
  let original: Record<string, unknown> | undefined;
  let submissions = 0;
  let publishedHeadAdvanced = false;
  const media = vi.fn((path: string, init?: RequestInit) => {
    if (path === mediaPath && init?.method === "POST") {
      const input = JSON.parse(init.body as string);
      if (++submissions === 1) {
        original = input;
        return Promise.reject(new Error("lost response"));
      }
      if (publishedHeadAdvanced) return json({ ...audioCutJob, job_id: "next-cut", result_url: `${mediaPath}/next-cut`,
        base_version_id: "advanced" }, 202);
      expect(input).toEqual(original);
      return json(audioCutJob, 202);
    }
    if (path === jobPath && publishedHeadAdvanced) return json({ ...audioCutJob, status: "stale" });
    if (path === jobPath) return json({ ...audioCutJob, status: "succeeded", result: {
      request_id: original?.request_id, base_version_id: "base", timeline_version_id: version.id,
      output_refs: ["artifact:mp4"], output_urls: [`${jobPath}/outputs/1`],
    } });
    if (path === `${mediaPath}/next-cut`) return json({ ...audioCutJob, job_id: "next-cut", result_url: path,
      base_version_id: "advanced", status: "queued" });
    if (path === `${jobPath}/outputs/1`) return Promise.resolve(new Response("mp4", { headers: { "Content-Type": "video/mp4" } }));
    throw new Error(`Unexpected request ${path}`);
  });
  mockRequests(media);
  const first = renderCut();
  expect(screen.getByRole("spinbutton", { name: "原片起点（秒）" })).toHaveValue(1.25);
  expect(screen.getByRole("spinbutton", { name: "原片终点（秒）" })).toHaveValue(4.5);
  await verifyPreparation();
  await screen.findByText(/已核对这份准备回执/);
  fireEvent.change(screen.getByRole("spinbutton", { name: "原片终点（秒）" }), { target: { value: "5" } });
  expect(screen.getByRole("button", { name: "截取并保存" })).toBeDisabled();
  fireEvent.change(screen.getByRole("spinbutton", { name: "原片终点（秒）" }), { target: { value: "2.25" } });
  fireEvent.click(screen.getByRole("button", { name: "截取并保存" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("本地服务未接通");
  expect(original).toEqual({ request_id: expect.any(String), base_version_id: "base", asset_id: "source-asset",
    start_seconds: 1.25, end_seconds: 2.25, preparation_request_id: preparationId });
  first.unmount();

  const onPublished = vi.fn().mockResolvedValue(version.sequence);
  const second = renderCut(version.id, onPublished);
  expect(submissions).toBe(1);
  fireEvent.click(await screen.findByRole("button", { name: "核对原提交" }));
  await screen.findByText(/MP4 已保存.*第 13 版/);
  expect(onPublished).toHaveBeenCalledExactlyOnceWith(version.id, expect.any(AbortSignal));
  expect(submissions).toBe(2);
  second.unmount();

  const changed = renderCut("unrelated-version", vi.fn().mockResolvedValue(null));
  expect(await screen.findByRole("alert")).toHaveTextContent("当前作品版本与登记结果不一致");
  expect(screen.queryByRole("button", { name: "打开截取文件" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "截取另一段" })).not.toBeInTheDocument();
  changed.unmount();

  const revokeObjectURL = vi.fn();
  vi.stubGlobal("URL", Object.assign(class extends URL {}, { createObjectURL: () => "blob:original-cut", revokeObjectURL }));
  const reopened = renderCut(version.id);
  const open = await screen.findByRole("button", { name: "打开截取文件" });
  expect(submissions).toBe(2);
  expect(media.mock.calls.some(([path]) => path.endsWith("/outputs/1"))).toBe(false);
  fireEvent.click(open);
  const video = await screen.findByLabelText("已保存的截取片段");
  expect(video).toHaveAttribute("src", "blob:original-cut");
  expect(video).toHaveAttribute("controls");
  expect(video).not.toHaveAttribute("autoplay");
  expect(screen.getByRole("link", { name: "下载 MP4" })).toHaveAttribute("href", "blob:original-cut");
  publishedHeadAdvanced = true;
  reopened.rerender(<SourceClipCut target={target} currentVersionId="advanced" onJob={vi.fn()} onPublished={onPublished} />);
  expect(video).not.toBeInTheDocument();
  expect(revokeObjectURL).toHaveBeenCalledWith("blob:original-cut");
  reopened.unmount();

  const advanced = { ...target, baseVersionId: "advanced", baseSequence: 14, clipNumber: 3,
    assetId: "next-source", sourceName: "next.mp4", start: 2, end: 3 };
  renderCut(advanced.baseVersionId, vi.fn().mockResolvedValue(null), advanced);
  expect(await screen.findByText(/原截取已登记于第 13 版，当前作品版本已变化/)).toBeInTheDocument();
  expect(screen.queryByLabelText("已保存的截取片段")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "打开截取文件" })).not.toBeInTheDocument();
  expect(media.mock.calls.filter(([path]) => path.endsWith("/outputs/1"))).toHaveLength(1);
  fireEvent.click(screen.getByRole("button", { name: "截取另一段" }));
  expect(await screen.findByText("镜头 3 · next.mp4 · 作品第 14 版")).toBeInTheDocument();
  expect(submissions).toBe(2);
  await verifyPreparation();
  await screen.findByText(/已核对这份准备回执/);
  fireEvent.click(screen.getByRole("button", { name: "截取并保存" }));
  await waitFor(() => expect(submissions).toBe(3));
  const nextPost = media.mock.calls.filter(([path]) => path === mediaPath).at(-1)![1];
  const nextInput = JSON.parse(nextPost?.body as string);
  expect(nextInput).toEqual({ request_id: expect.any(String), base_version_id: "advanced", asset_id: "next-source",
    start_seconds: 2, end_seconds: 3, preparation_request_id: preparationId });
  expect(nextInput.request_id).not.toBe(original?.request_id);
});

it("keeps an unverified original receipt when its job is stale", async () => {
  const media = vi.fn((path: string) => {
    if (path === mediaPath) return json(audioCutJob, 202);
    if (path === jobPath) return json({ ...audioCutJob, status: "stale" });
    throw new Error(`Unexpected request ${path}`);
  });
  mockRequests(media);
  const first = renderCut();
  await verifyPreparation();
  await screen.findByText(/已核对这份准备回执/);
  fireEvent.click(screen.getByRole("button", { name: "截取并保存" }));
  await screen.findByText("原版本、素材或准备回执已失效。");
  first.unmount();
  renderCut("advanced", vi.fn().mockResolvedValue(null), { ...target, baseVersionId: "advanced", baseSequence: 13 });
  await screen.findByText("原版本、素材或准备回执已失效。");
  expect(screen.queryByRole("button", { name: "截取另一段" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "打开截取文件" })).not.toBeInTheDocument();
  expect(media.mock.calls.filter(([path]) => path === mediaPath)).toHaveLength(1);
});

it("offers explicit recovery on a failed job with the original identity", async () => {
  const media = vi.fn((path: string, init?: RequestInit) => {
    if (path === mediaPath && init?.method === "POST") return json(audioCutJob, 202);
    if (path === jobPath) return json({ ...audioCutJob, status: "failed", failure_reason: "cut_failed", repair_action: "检查原素材后重试。" });
    if (path === `${jobPath}/retry` && init?.method === "POST") return json(audioCutJob, 202);
    throw new Error(`Unexpected request ${path}`);
  });
  mockRequests(media);
  renderCut();
  await verifyPreparation();
  await screen.findByText(/已核对这份准备回执/);
  fireEvent.click(screen.getByRole("button", { name: "截取并保存" }));
  const retry = await screen.findByRole("button", { name: "重试原作业" });
  expect(screen.getByText("检查原素材后重试。")).toBeInTheDocument();
  expect(media.mock.calls.some(([path]) => path.endsWith("/retry"))).toBe(false);
  fireEvent.click(retry);
  await waitFor(() => expect(media).toHaveBeenCalledWith(`${jobPath}/retry`, expect.objectContaining({ method: "POST" })));
  expect(media.mock.calls.filter(([path]) => path === mediaPath)).toHaveLength(1);
  expect(screen.queryByRole("button", { name: "打开截取文件" })).not.toBeInTheDocument();
});

it("associates a published media job only with its result version", () => {
  const saved: TaskMediaJob = { ...audioCutJob, status: "succeeded", result: {
    request_id: "request", base_version_id: "base", media_job_id: "inner", timeline_version_id: "published", output_refs: ["artifact:mp4"], log_path: null,
  } };
  expect(taskMediaJobMatchesContext(audioCutJob, "base", [])).toBe(true);
  expect(taskMediaJobMatchesContext(saved, "published", [])).toBe(true);
  expect(taskMediaJobMatchesContext(saved, "base", [])).toBe(false);
  expect(taskMediaJobMatchesContext(saved, "other", [])).toBe(false);
});
