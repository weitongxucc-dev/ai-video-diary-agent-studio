import { beforeEach, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({ listVideoTemplates: vi.fn(), getContentTemplates: vi.fn() }));
vi.mock("@/lib/api.server", () => api);
vi.mock("@/components/TemplateLibraryWorkspace", () => ({ TemplateLibraryWorkspace: () => null }));
import TemplatesPage from "@/app/templates/page";

beforeEach(() => { api.listVideoTemplates.mockReset(); api.getContentTemplates.mockReset(); });

it("passes both real versioned templates and input requirements to the workspace", async () => {
  const template = { id: "builtin_product_intro", version: 1 };
  const catalog = { template_id: "product_intro", material_requirements: ["已核实的功能资料"] };
  api.listVideoTemplates.mockResolvedValue({ items: [template] });
  api.getContentTemplates.mockResolvedValue({ templates: [catalog] });
  const page = await TemplatesPage();
  expect(page.props.label).toBe("视频模板");
  expect(page.props.children.props).toEqual({ templates: [template], catalog: [catalog], offline: false });
});

it("reports service failure instead of manufacturing a selectable catalog", async () => {
  api.listVideoTemplates.mockResolvedValue({ items: [] });
  api.getContentTemplates.mockRejectedValue(new Error("不可访问"));
  const page = await TemplatesPage();
  expect(page.props.label).toBe("视频模板");
  expect(page.props.children.props).toEqual({ templates: [], catalog: [], offline: true });
});
