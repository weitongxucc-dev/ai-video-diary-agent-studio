import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
const api = vi.hoisted(() => ({ listVideoTemplates: vi.fn(), saveVideoTemplate: vi.fn() }));
vi.mock("@/lib/api", () => api);
import { SaveVideoTemplateForm } from "@/components/SaveVideoTemplateForm";

it("saves a new version with its expected predecessor and leaves conflicts editable", async () => {
  api.listVideoTemplates.mockResolvedValue({ items: [{ id: "template-1", version: 2, name: "讲解", origin: "user", settings: { style_instruction: "短句" } }] });
  api.saveVideoTemplate.mockRejectedValueOnce(new Error("模板版本已变化，请刷新后重新保存。"));
  api.saveVideoTemplate.mockResolvedValueOnce({ id: "template-1", version: 3, name: "讲解" });
  const editing = vi.fn();
  render(<SaveVideoTemplateForm taskId="source-task" onEditingChange={editing} />);
  fireEvent.click(screen.getByRole("button", { name: "保存为视频模板" }));
  await screen.findByRole("option", { name: "讲解 · 保存为 v3" });
  fireEvent.change(screen.getByLabelText("保存方式"), { target: { value: "template-1" } });
  fireEvent.click(screen.getByRole("button", { name: "保存模板设置" }));
  await screen.findByRole("alert");
  expect(api.saveVideoTemplate).toHaveBeenCalledWith("source-task", { template_id: "template-1", expected_version: 2, name: "讲解", style_instruction: "短句" });
  expect(screen.getByRole("textbox", { name: "模板名称" })).toHaveValue("讲解");
  fireEvent.click(screen.getByRole("button", { name: "取消" }));
  expect(editing).toHaveBeenLastCalledWith(false);
  expect(screen.getByRole("button", { name: "保存为视频模板" })).toBeInTheDocument();
});
