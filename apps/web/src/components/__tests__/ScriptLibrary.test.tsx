import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { ScriptLibraryPanel } from "@/app/knowledge/KnowledgeWorkspace";

const mocks = vi.hoisted(() => ({ list: vi.fn(), sources: vi.fn(), reuse: vi.fn(), push: vi.fn() }));
vi.mock("@/lib/api", () => ({
  listScriptLibraryEntries: mocks.list, getScriptLibrarySources: mocks.sources,
  reuseScriptLibraryEntry: mocks.reuse,
}));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: mocks.push }) }));
vi.mock("@/components/AppShell", () => ({ AppShell: () => null }));

const entry = {
  id: "entry-1", name: "维护介绍", category: "产品", version: 1, title: "模块化介绍",
  source_task_id: "original-task", source_script_id: "original-script", source_run_id: "",
  sections: [{ id: "section-1", title: "特点", content: "本人已确认的历史正文", reference_ids: [] }],
};

beforeEach(() => {
  vi.clearAllMocks();
  mocks.list.mockResolvedValue({ items: [entry], total_count: 1 });
  mocks.sources.mockResolvedValue([]);
  mocks.reuse.mockResolvedValue({ task_id: "new-task", script: { selection_status: "draft" } });
});

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(done => { resolve = done; });
  return { promise, resolve };
}

it("allows an unreferenced owned script only after explicit new requirements confirmation", async () => {
  render(<ScriptLibraryPanel />);
  fireEvent.click(await screen.findByRole("button", { name: /维护介绍/ }));
  await screen.findByText("本人文案没有外部事实引用。");
  const button = screen.getByRole("button", { name: "创建待确认新稿" });
  expect(button).toBeDisabled();
  fireEvent.click(screen.getByRole("checkbox"));
  fireEvent.click(button);
  await waitFor(() => expect(mocks.push).toHaveBeenCalledWith("/tasks/new-task"));
  expect(mocks.reuse).toHaveBeenCalledWith("entry-1", {
    name: "维护介绍 · 新任务", aspect_ratio: "9:16", duration_target_seconds: 60,
    requirements_confirmed: true,
  });
});

it("keeps owned historical text visible when a source changes, but refuses reuse", async () => {
  mocks.sources.mockResolvedValue([{ reference_id: "knowledge:item:1", status: "version_changed" }]);
  render(<ScriptLibraryPanel />);
  fireEvent.click(await screen.findByRole("button", { name: /维护介绍/ }));
  await screen.findByText(/版本或授权已变化/);
  expect(screen.getByText("本人已确认的历史正文")).toBeVisible();
  fireEvent.click(screen.getByRole("checkbox"));
  expect(screen.getByRole("button", { name: "创建待确认新稿" })).toBeDisabled();
  expect(mocks.reuse).not.toHaveBeenCalled();
  fireEvent.change(screen.getByRole("textbox", { name: "搜索文案" }), { target: { value: "维护" } });
  await waitFor(() => expect(mocks.list).toHaveBeenLastCalledWith("维护", ""));
  fireEvent.change(screen.getByRole("textbox", { name: "筛选文案分类" }), { target: { value: "产品" } });
  await waitFor(() => expect(mocks.list).toHaveBeenLastCalledWith("维护", "产品"));
});

it("retries a failed filter read and drops a selection outside the successful result", async () => {
  mocks.list.mockReset()
    .mockResolvedValueOnce({ items: [entry], total_count: 1 })
    .mockRejectedValueOnce(new Error("暂时不可用"))
    .mockResolvedValueOnce({ items: [], total_count: 0 });
  render(<ScriptLibraryPanel />);
  fireEvent.click(await screen.findByRole("button", { name: /维护介绍/ }));
  await screen.findByText("本人文案没有外部事实引用。");
  fireEvent.change(screen.getByRole("textbox", { name: "搜索文案" }), { target: { value: "其他" } });
  expect(await screen.findByRole("alert")).toHaveTextContent("搜索“全部”，分类“全部”");
  expect(screen.getByRole("button", { name: "重试读取文案" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "重试读取文案" }));
  await screen.findByText(/没有匹配的可用文案/);
  expect(screen.queryByRole("region", { name: "文案复用详情" })).not.toBeInTheDocument();
});

it("offers retry when source verification fails", async () => {
  mocks.sources.mockRejectedValueOnce(new Error("来源暂不可读")).mockResolvedValueOnce([]);
  render(<ScriptLibraryPanel />);
  fireEvent.click(await screen.findByRole("button", { name: /维护介绍/ }));
  expect(await screen.findByRole("alert")).toHaveTextContent("来源暂不可读");
  fireEvent.click(screen.getByRole("button", { name: "重试核对来源" }));
  await screen.findByText("本人文案没有外部事实引用。");
  expect(mocks.sources).toHaveBeenCalledTimes(2);
});

it("keeps source and run identifiers inside on-demand diagnostics", async () => {
  const withDiagnostics = { ...entry, source_run_id: "run-123", sections: [{ ...entry.sections[0], reference_ids: ["knowledge:item:version"] }] };
  mocks.list.mockResolvedValue({ items: [withDiagnostics], total_count: 1 });
  render(<ScriptLibraryPanel />);
  fireEvent.click(await screen.findByRole("button", { name: /维护介绍/ }));
  await screen.findByText("本人文案没有外部事实引用。");
  const diagnostics = screen.getAllByText("技术诊断信息");
  expect(screen.getByText(/原稿记录：original-script · 运行记录：run-123/)).not.toBeVisible();
  fireEvent.click(diagnostics.at(-1)!);
  expect(screen.getByText(/原稿记录：original-script · 运行记录：run-123/)).toBeVisible();
  expect(screen.getByText("引用记录：knowledge:item:version")).not.toBeVisible();
});

it("keeps verified sources and the current new-task confirmation when reselecting the same version", async () => {
  render(<ScriptLibraryPanel />);
  const card = await screen.findByRole("button", { name: /维护介绍/ });
  fireEvent.click(card);
  await screen.findByText("本人文案没有外部事实引用。");
  fireEvent.click(screen.getByRole("checkbox"));
  expect(screen.getByRole("checkbox")).toBeChecked();
  fireEvent.click(card);
  expect(screen.getByRole("checkbox")).toBeChecked();
  expect(mocks.sources).toHaveBeenCalledTimes(1);
});

it("rechecks a new version of the same entry and ignores the old version response", async () => {
  const olderSources = deferred<Array<{ reference_id: string; status: string }>>();
  const newerSources = deferred<Array<{ reference_id: string; status: string }>>();
  const newerEntry = { ...entry, version: 2 };
  mocks.list.mockReset()
    .mockResolvedValueOnce({ items: [entry], total_count: 1 })
    .mockResolvedValueOnce({ items: [newerEntry], total_count: 1 });
  mocks.sources.mockReset().mockReturnValueOnce(olderSources.promise).mockReturnValueOnce(newerSources.promise);
  render(<ScriptLibraryPanel />);
  fireEvent.click(await screen.findByRole("button", { name: /维护介绍/ }));
  fireEvent.click(screen.getByRole("checkbox"));
  expect(screen.getByRole("checkbox")).toBeChecked();
  fireEvent.change(screen.getByRole("textbox", { name: "搜索文案" }), { target: { value: "维护" } });
  expect(await screen.findByRole("heading", { name: "维护介绍 · 版本 2" })).toBeInTheDocument();
  expect(screen.getByRole("checkbox")).not.toBeChecked();
  expect(mocks.sources).toHaveBeenCalledTimes(2);
  await act(async () => { olderSources.resolve([{ reference_id: "old-version-reference", status: "readable" }]); });
  expect(screen.queryByText("old-version-reference")).not.toBeInTheDocument();
  await act(async () => { newerSources.resolve([]); });
  expect(await screen.findByText("本人文案没有外部事实引用。")).toBeInTheDocument();
});
