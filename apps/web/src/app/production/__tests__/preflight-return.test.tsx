import { render, screen } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
const mocks = vi.hoisted(() => ({ form: vi.fn(), templates: vi.fn() }));
vi.mock("@/components/AppShell", () => ({ AppShell: ({ children }: { children: React.ReactNode }) => <main>{children}</main> }));
vi.mock("@/components/CreateProductionTaskForm", () => ({ CreateProductionTaskForm: (props: unknown) => { mocks.form(props); return <p>绑定表单</p>; } }));
vi.mock("@/lib/api.server", () => ({ getProductionFoundation: vi.fn().mockResolvedValue(null), listCreationTasks: vi.fn().mockResolvedValue({ items: [], total_count: 0 }), listVideoTemplates: mocks.templates }));
import ProductionPage from "@/app/production/page";
const draftId = "12345678-1234-4123-8123-123456789abc";
beforeEach(() => { vi.clearAllMocks(); });
it("binds only the draft UUID and the exact returned template version to the existing form", async () => {
  const template = { id: "fixed-template", version: 3, name: "固定版本" }; mocks.templates.mockResolvedValue({ items: [{ ...template, version: 4 }, template] });
  render(await ProductionPage({ searchParams: Promise.resolve({ new: "1", draft_id: draftId, template_id: template.id, template_version: "3" }) }));
  expect(mocks.form).toHaveBeenCalledWith({ autoFocus: true, initialGoal: "", draftId, template });
});
it("does not substitute the latest template when the pinned version cannot be read", async () => {
  mocks.templates.mockResolvedValue({ items: [{ id: "fixed-template", version: 4 }] });
  render(await ProductionPage({ searchParams: Promise.resolve({ new: "1", draft_id: draftId, template_id: "fixed-template", template_version: "3" }) }));
  expect(screen.getByRole("alert")).toHaveTextContent("无法读取所选模板版本"); expect(mocks.form).not.toHaveBeenCalled();
});
