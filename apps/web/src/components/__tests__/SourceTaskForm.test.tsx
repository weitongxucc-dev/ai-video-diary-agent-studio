import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { SourceTaskForm } from "../material-library/SourceTaskForm";
import { CreationPlanApiError } from "@/lib/api/creation-transport";
import type { MaterialAsset } from "@/lib/types";
import type { SourceTaskCreateInput, SourceTaskReceipt } from "@/lib/types/creation-tasks";
import { chooseSelectValue } from "./select-interaction";

const api = vi.hoisted(() => ({ create: vi.fn(), read: vi.fn(), member: "member-a" }));
vi.mock("@/lib/api/creation-tasks", () => ({ createSourceTask: api.create, getSourceTaskRequest: api.read }));
vi.mock("@/lib/auth/routing", () => ({ isLocalAuthMode: () => true }));
vi.mock("@/hooks/useLocalMemberIdentity", () => ({ useLocalMemberIdentity: () => ({ token: api.member, ready: true }) }));
vi.mock("@/lib/local-member-auth", () => ({ ensureLocalMemberIdentity: async () => ({ member_id: api.member }), readLocalMemberToken: () => api.member }));

const asset: MaterialAsset = { id: "original", workspace_id: "local", file_name: "原片.mp4", path: "/declared/source.mp4",
  visibility: "private", media_type: "video", source_url: null, duration_seconds: 9.25, size_bytes: 100,
  modified_at: "2026-11-09T00:00:00Z", created_at: "2026-11-09T00:00:00Z", updated_at: "2026-11-09T00:00:00Z",
  usage_role: "unassigned", suggested_usage_role: "unassigned", usage_role_confidence: 0, usage_role_reason: "",
  review_status: "raw", use_state: "available", user_tags: [], user_description: "", upload_context: "",
  analysis_status: "ready", analysis_message: "", segments: [] };
function receipt(input: SourceTaskCreateInput): SourceTaskReceipt {
  return { request_id: input.request_id, status: "created", task_id: "source-task", plan_id: "source-plan",
    montage_plan_id: "source-montage", timeline_version_id: "source-version", source_media: {
      asset_id: input.asset_id, asset_updated_at: input.expected_asset_updated_at, source_ref: "workspace://original.mp4",
      source_start_seconds: input.source_start_seconds, source_end_seconds: input.source_end_seconds,
      preserve_audio: input.preserve_audio, aspect_ratio: input.aspect_ratio,
      file_signature: { size_bytes: "100", mtime_ns: "1", ctime_ns: "1", inode: "1" },
    } };
}
async function open() {
  fireEvent.click(screen.getByRole("button", { name: "以原片创作" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "创建剪辑任务" })).toBeEnabled());
}
beforeEach(() => { vi.clearAllMocks(); localStorage.clear(); api.member = "member-a"; });

it("submits the explicitly selected original range and opens the created task", async () => {
  const created = vi.fn();
  api.create.mockImplementation(async (input: SourceTaskCreateInput) => receipt(input));
  render(<SourceTaskForm asset={asset} onCreated={created} />);
  await open();
  fireEvent.change(screen.getByLabelText("起点（秒）"), { target: { value: "1.25" } });
  fireEvent.change(screen.getByLabelText("终点（秒）"), { target: { value: "7.75" } });
  await chooseSelectValue(screen.getByRole("combobox", { name: "原片创作画幅" }), "1:1");
  fireEvent.click(screen.getByLabelText("保留原声"));
  fireEvent.click(screen.getByRole("button", { name: "创建剪辑任务" }));
  await waitFor(() => expect(created).toHaveBeenCalledOnce());
  const input = api.create.mock.calls[0][0];
  expect(input).toMatchObject({ asset_id: asset.id, expected_asset_updated_at: asset.updated_at,
    source_start_seconds: 1.25, source_end_seconds: 7.75, preserve_audio: false, aspect_ratio: "1:1" });
  expect(input.request_id).toMatch(/^[0-9a-f-]{36}$/);
  expect(created).toHaveBeenCalledWith(receipt(input));
  expect(localStorage.length).toBe(0);
});

it("restores an unknown request and reuses its UUID and frozen payload after GET 404", async () => {
  const created = vi.fn();
  api.create.mockRejectedValueOnce(new CreationPlanApiError(0, null, "连接中断"));
  api.read.mockRejectedValueOnce(new CreationPlanApiError(404, null, "未查到原请求"));
  const first = render(<SourceTaskForm asset={asset} onCreated={created} />);
  await open();
  fireEvent.click(screen.getByRole("button", { name: "创建剪辑任务" }));
  await screen.findByText("连接中断");
  const input = api.create.mock.calls[0][0];
  expect(screen.queryByRole("button", { name: "创建剪辑任务" })).not.toBeInTheDocument();
  first.unmount();
  sessionStorage.clear(); // A new app session must retain the original request.
  render(<SourceTaskForm asset={{ ...asset, updated_at: "new-current-revision" }} onCreated={created} />);
  fireEvent.click(screen.getByRole("button", { name: "以原片创作" }));
  const query = await screen.findByRole("button", { name: "查询原创建结果" });
  fireEvent.click(query);
  const resume = await screen.findByRole("button", { name: "继续原片创作请求" });
  expect(api.read).toHaveBeenCalledWith(input.request_id);
  expect(api.create).toHaveBeenCalledTimes(1);
  expect(screen.getByLabelText("起点（秒）")).toBeDisabled();
  api.create.mockImplementationOnce(async (original: SourceTaskCreateInput) => receipt(original));
  fireEvent.click(resume);
  await waitFor(() => expect(created).toHaveBeenCalledOnce());
  expect(api.create.mock.calls[1][0]).toEqual(input);
});

it("suppresses a late result after switching members and preserves the original member's pending request", async () => {
  let resolve!: (value: SourceTaskReceipt) => void;
  api.create.mockImplementationOnce(() => new Promise<SourceTaskReceipt>(done => { resolve = done; }));
  const created = vi.fn();
  const view = render(<SourceTaskForm asset={asset} onCreated={created} />);
  await open();
  fireEvent.click(screen.getByRole("button", { name: "创建剪辑任务" }));
  await waitFor(() => expect(api.create).toHaveBeenCalledOnce());
  const input = api.create.mock.calls[0][0];
  api.member = "member-b";
  view.rerender(<SourceTaskForm asset={asset} onCreated={created} />);
  await waitFor(() => expect(screen.getByRole("button", { name: "创建剪辑任务" })).toBeEnabled());
  await act(async () => resolve(receipt(input)));
  expect(created).not.toHaveBeenCalled();
  expect(JSON.parse(localStorage.getItem("source-task:local:original:member-a")!)).toEqual(input);
});

it("keeps the original unknown outcome queryable when a later replay loses permission", async () => {
  api.create.mockRejectedValueOnce(new CreationPlanApiError(0, null, "连接中断"))
    .mockRejectedValueOnce(new CreationPlanApiError(403, null, "当前成员不可用"));
  api.read.mockRejectedValueOnce(new CreationPlanApiError(404, null, "未查到原请求"));
  render(<SourceTaskForm asset={asset} onCreated={vi.fn()} />);
  await open();
  fireEvent.click(screen.getByRole("button", { name: "创建剪辑任务" }));
  await screen.findByText("连接中断");
  const input = api.create.mock.calls[0][0];
  fireEvent.click(screen.getByRole("button", { name: "查询原创建结果" }));
  fireEvent.click(await screen.findByRole("button", { name: "继续原片创作请求" }));
  await screen.findByText("当前成员不可用");
  expect(screen.getByRole("button", { name: "查询原创建结果" })).toBeEnabled();
  expect(screen.queryByRole("button", { name: "创建剪辑任务" })).not.toBeInTheDocument();
  expect(JSON.parse(localStorage.getItem("source-task:local:original:member-a")!)).toEqual(input);
  expect(api.create.mock.calls[1][0]).toEqual(input);
});

it("does not navigate when the recovered receipt belongs to different original-media inputs", async () => {
  const created = vi.fn();
  api.create.mockRejectedValueOnce(new CreationPlanApiError(0, null, "连接中断"));
  render(<SourceTaskForm asset={asset} onCreated={created} />);
  await open();
  fireEvent.click(screen.getByRole("button", { name: "创建剪辑任务" }));
  await screen.findByText("连接中断");
  const input = api.create.mock.calls[0][0];
  const wrong = receipt(input);
  wrong.source_media.preserve_audio = !input.preserve_audio;
  api.read.mockResolvedValueOnce(wrong);
  fireEvent.click(screen.getByRole("button", { name: "查询原创建结果" }));
  await screen.findByText(/返回结果与原片范围或制作要求不一致/);
  expect(created).not.toHaveBeenCalled();
  expect(JSON.parse(localStorage.getItem("source-task:local:original:member-a")!)).toEqual(input);
  expect(api.create).toHaveBeenCalledOnce();
});

it("keeps unknown duration visible without submitting an invented range", async () => {
  render(<SourceTaskForm asset={{ ...asset, duration_seconds: null }} onCreated={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: "以原片创作" }));
  expect(screen.getByText(/原视频时长尚未就绪/)).toBeVisible();
  expect(screen.getByRole("button", { name: "创建剪辑任务" })).toBeDisabled();
  expect(api.create).not.toHaveBeenCalled();
});
