import { chooseSelectValue, inspectSelectOptions } from "./select-interaction";
import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
const api = vi.hoisted(() => ({ listVideoTemplates: vi.fn(), saveVideoTemplate: vi.fn() }));
vi.mock("@/lib/api", () => api);
import { SaveVideoTemplateForm } from "@/components/SaveVideoTemplateForm";
beforeEach(() => vi.resetAllMocks());

it("saves a new version with its expected predecessor and leaves conflicts editable", async () => {
  api.listVideoTemplates.mockResolvedValue({ items: [{ id: "template-1", version: 2, name: "讲解", origin: "user", can_edit: true, settings: { style_instruction: "短句" } }] });
  api.saveVideoTemplate.mockRejectedValueOnce(new Error("模板版本已变化，请刷新后重新保存。"));
  api.saveVideoTemplate.mockResolvedValueOnce({ id: "template-1", version: 3, name: "讲解" });
  const editing = vi.fn();
  render(<SaveVideoTemplateForm taskId="source-task" onEditingChange={editing} />);
  fireEvent.click(screen.getByRole("button", { name: "保存为视频模板" }));
  await inspectSelectOptions(screen.getByLabelText("保存方式"),()=>screen.findByRole("option", { name: "讲解 · 保存为 v3" }));
  await chooseSelectValue(screen.getByLabelText("保存方式"), "template-1");
  expect(screen.getByRole("dialog", { name: "保存视频模板" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "保存模板设置" }).closest("footer")).not.toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "保存模板设置" }));
  await screen.findByRole("alert");
  expect(api.saveVideoTemplate).toHaveBeenCalledWith("source-task", { template_id: "template-1", expected_version: 2, name: "讲解", style_instruction: "短句" });
  expect(screen.getByRole("textbox", { name: "模板名称" })).toHaveValue("讲解");
  fireEvent.click(screen.getByRole("button", { name: "取消" }));
  expect(editing).toHaveBeenLastCalledWith(false);
  expect(screen.getByRole("button", { name: "保存为视频模板" })).toBeInTheDocument();
});

it("uses the actual latest editable version from an unsorted list and reports the returned save", async () => {
  api.listVideoTemplates.mockResolvedValue({ items: [
    { id: "template-1", version: 1, name: "旧版本", origin: "user", can_edit: true, settings: { style_instruction: "旧风格" } },
    { id: "shared", version: 8, name: "他人共享", origin: "user", can_edit: false, settings: { style_instruction: "共享风格" } },
    { id: "template-1", version: 3, name: "当前版本", origin: "user", can_edit: true, settings: { style_instruction: "当前风格" } },
  ] });
  api.saveVideoTemplate.mockResolvedValueOnce({ id: "template-1", version: 4, name: "保存后名称" });
  render(<SaveVideoTemplateForm taskId="source-task" onEditingChange={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: "保存为视频模板" }));
  await inspectSelectOptions(screen.getByLabelText("保存方式"), async()=>{ await screen.findByRole("option", { name: "当前版本 · 保存为 v4" }); expect(screen.queryByRole("option", { name: /他人共享/ })).toBeNull(); });
  await chooseSelectValue(screen.getByLabelText("保存方式"), "template-1");
  fireEvent.click(screen.getByRole("button", { name: "保存模板设置" }));
  expect(await screen.findByRole("status")).toHaveTextContent("保存后名称”v4");
  expect(api.saveVideoTemplate).toHaveBeenLastCalledWith("source-task", { template_id: "template-1", expected_version: 3, name: "当前版本", style_instruction: "当前风格" });
});
