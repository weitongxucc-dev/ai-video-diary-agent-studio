import { fireEvent, render, screen, waitFor } from "@testing-library/react";
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
