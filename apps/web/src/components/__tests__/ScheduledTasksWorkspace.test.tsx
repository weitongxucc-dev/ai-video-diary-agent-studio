import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ScheduledTasksWorkspace } from "../ScheduledTasksWorkspace";
import type { ScheduledTask } from "@/lib/api";

const imports = vi.hoisted(() => ({ list: vi.fn(), create: vi.fn(), enable: vi.fn(), remove: vi.fn() }));
vi.mock("@/lib/api", () => ({
  listScheduledTasks: imports.list,
  createScheduledTask: imports.create,
  setScheduledTaskEnabled: imports.enable,
  deleteScheduledTask: imports.remove,
}));
afterEach(() => { vi.clearAllMocks(); });

function task(overrides: Partial<ScheduledTask> = {}): ScheduledTask {
  return { id: "st-1", workspace_id: "ws", task_type: "material_analysis", trigger_mode: "scheduled",
    target_ref: "asset:abc123", not_before_utc: "2026-03-01T00:30:00Z", window: { hours: [2, 3] }, enabled: true,
    created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z", ...overrides };
}

function expectReadOnly() {
  expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  expect(document.querySelector("form, input, textarea, select")).toBeNull();
  for (const action of ["创建任务定义", "启动", "暂停", "删除"]) {
    expect(screen.queryByRole("button", { name: action })).not.toBeInTheDocument();
  }
  expect(imports.create).not.toHaveBeenCalled();
  expect(imports.enable).not.toHaveBeenCalled();
  expect(imports.remove).not.toHaveBeenCalled();
}

it("previews saved definitions without treating their enabled flags as execution states", async () => {
  const definitions = [task(), task({ id: "st-2", task_type: "creation_render", target_ref: "creation-task:render-9", trigger_mode: "idle", not_before_utc: null, enabled: false })];
  imports.list.mockResolvedValue({ tasks: definitions, total_count: 2 });
  render(<ScheduledTasksWorkspace />);
  expect(await screen.findByText("2 项已保存定义")).toBeInTheDocument();
  expect(screen.getByRole("region", { name: "定时任务只读预览" })).toBeInTheDocument();
  expect(screen.getByText("只读预览 · 尚未接通执行")).toBeInTheDocument();
  expect(screen.getByText("这里只查看已保存的任务定义，不会在指定时间或设备空闲时自动运行。")).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "素材解析" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "成片制作" })).toBeInTheDocument();
  expect(screen.getByText("2026-03-01 00:30 UTC")).toBeInTheDocument();
  expect(screen.getByText("设备空闲")).toBeInTheDocument();
  expect(screen.getByText("未设置")).toBeInTheDocument();
  expect(screen.queryByText("已启用")).not.toBeInTheDocument();
  expect(screen.queryByText("暂停中")).not.toBeInTheDocument();
  expect(screen.queryByText(/上次[：:]|下次[：:]/)).not.toBeInTheDocument();
  expectReadOnly();
  expect(definitions).toEqual([task(), task({ id: "st-2", task_type: "creation_render", target_ref: "creation-task:render-9", trigger_mode: "idle", not_before_utc: null, enabled: false })]);
});

it("keeps raw target references and JSON windows inside optional read-only diagnostics", async () => {
  const definition = task();
  imports.list.mockResolvedValue({ tasks: [definition], total_count: 1 });
  render(<ScheduledTasksWorkspace />);
  await screen.findByText("1 项已保存定义");
  const details = document.querySelector("details")!;
  const raw = details.querySelector("pre")!;
  expect(JSON.parse(raw.textContent!)).toEqual(definition);
  expect(raw).not.toBeVisible();
  expect(details).not.toHaveAttribute("open");
  // jsdom does not implement a browser's native summary toggle.
  details.open = true;
  expect(raw).toBeVisible();
  expect(details).toHaveTextContent("asset:abc123");
  expect(details).toHaveTextContent('"hours"');
  expect(details).toHaveTextContent('"enabled": true');
  expect(details).toHaveTextContent("不代表已调度或已执行");
  expectReadOnly();
});

it("distinguishes an empty saved collection without offering definition creation", async () => {
  imports.list.mockResolvedValue({ tasks: [], total_count: 0 });
  render(<ScheduledTasksWorkspace />);
  expect(await screen.findByText("还没有已保存的定时任务定义")).toBeInTheDocument();
  expect(screen.queryByText(/项已保存定义/)).not.toBeInTheDocument();
  expectReadOnly();
});

it("keeps loading distinct from empty and failure", async () => {
  let finish!: (value: { tasks: ScheduledTask[]; total_count: number }) => void;
  imports.list.mockReturnValueOnce(new Promise(resolve => { finish = resolve; }));
  render(<ScheduledTasksWorkspace />);
  expect(screen.getByRole("status")).toHaveTextContent("正在读取已保存定义…");
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  expect(screen.queryByText("还没有已保存的定时任务定义")).not.toBeInTheDocument();
  expectReadOnly();
  await act(async () => finish({ tasks: [task()], total_count: 1 }));
  expect(screen.queryByRole("status")).not.toBeInTheDocument();
  expect(screen.getByText("1 项已保存定义")).toBeInTheDocument();
});

it("shows a read failure without an invented empty count, and retries only the read API", async () => {
  imports.list.mockRejectedValueOnce(new Error("本地服务未接通。"));
  render(<ScheduledTasksWorkspace />);
  expect(await screen.findByRole("alert")).toHaveTextContent("定时任务定义读取失败：本地服务未接通。");
  expect(screen.queryByText("还没有已保存的定时任务定义")).not.toBeInTheDocument();
  expect(screen.queryByText(/项已保存定义/)).not.toBeInTheDocument();
  imports.list.mockResolvedValue({ tasks: [task()], total_count: 1 });
  fireEvent.click(screen.getByRole("button", { name: "重试" }));
  expect(await screen.findByText("1 项已保存定义")).toBeInTheDocument();
  expect(imports.list).toHaveBeenCalledTimes(2);
  expectReadOnly();
});

it("preserves unknown stored fields in diagnostics without inventing a supported schedule", async () => {
  const definition = task({ task_type: "unknown_type", trigger_mode: "unknown_trigger", not_before_utc: "invalid-time" });
  imports.list.mockResolvedValue({ tasks: [definition], total_count: 1 });
  render(<ScheduledTasksWorkspace />);
  await screen.findByText("1 项已保存定义");
  expect(screen.getByRole("heading", { name: "任务定义" })).toBeInTheDocument();
  expect(screen.getByText("无法识别，详见诊断")).toBeInTheDocument();
  expect(screen.getByText("时间格式无法识别")).toBeInTheDocument();
  expect(document.querySelector("pre")).toHaveTextContent('"task_type": "unknown_type"');
  expectReadOnly();
});
