import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { AnchorHTMLAttributes, ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { KnowledgeItem } from "@/lib/source-api";

const api = vi.hoisted(() => ({ listKnowledgeItems: vi.fn(), sourceErrorMessage: vi.fn() }));
vi.mock("@/lib/source-api", () => ({ listKnowledgeItems: api.listKnowledgeItems, sourceErrorMessage: api.sourceErrorMessage }));
vi.mock("next/link", () => ({ default: ({ children, href, ...props }: AnchorHTMLAttributes<HTMLAnchorElement>) => <a href={href} {...props}>{children}</a> }));
vi.mock("@/components/AppShell", () => ({ AppShell: ({ children, inspector }: { children: ReactNode; inspector?: ReactNode }) => <><div>{children}</div>{inspector ? <aside aria-label="来源详情">{inspector}</aside> : null}</> }));

import KnowledgePage from "@/app/knowledge/page";
import { KnowledgeWorkspace } from "@/app/knowledge/KnowledgeWorkspace";

const item: KnowledgeItem = {
  id: "knowledge-one", workspace_id: "workspace-one", content_type: "file",
  title: "设备说明书", body: "设备运行前请先检查电源。",
  file_reference: "/local/reference/manual.pdf", authorization: "内部使用",
  ownership: "当前工作区", source_item_id: "source-one", version: "v2",
  created_at: "2026-09-27T08:00:00Z",
};
beforeEach(() => {
  api.listKnowledgeItems.mockReset();
  api.sourceErrorMessage.mockReset();
  api.listKnowledgeItems.mockResolvedValue({ items: [item], total_count: 1 });
  api.sourceErrorMessage.mockReturnValue("请求失败：502");
});
describe("KnowledgePage", () => {
  it("shows actual source content and leaves unsupported use categories unclassified", async () => {
    render(<KnowledgePage />);
    fireEvent.click(await screen.findByRole("button", { name: /设备说明书/ }));
    expect(screen.getAllByText(/用途未分类/)).not.toHaveLength(0);
    const detail = screen.getByRole("complementary", { name: "来源详情" });
    expect(detail).toHaveTextContent("设备运行前请先检查电源。");
    expect(detail).toHaveTextContent("尚无可核对的摘要来源");
    expect(detail).toHaveTextContent("结构化参数尚未接入");
    expect(detail).toHaveTextContent("v2");
    expect(detail).toHaveTextContent("内部使用");
    fireEvent.click(within(screen.getByRole("navigation", { name: "知识用途分类" })).getByRole("button", { name: /产品知识/ }));
    expect(screen.getByText(/不能按文件格式推断/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "查看全部来源" }));
    expect(screen.getByRole("button", { name: /设备说明书/ })).toBeInTheDocument();
  });

  it("keeps search and refresh connected to the real list endpoint", async () => {
    render(<KnowledgePage />);
    await screen.findByRole("button", { name: /设备说明书/ });
    fireEvent.change(screen.getByRole("textbox", { name: "查找来源" }), { target: { value: "找不到" } });
    expect(screen.getByText("没有匹配的来源")).toBeInTheDocument();
    fireEvent.change(screen.getByRole("textbox", { name: "查找来源" }), { target: { value: "电源" } });
    expect(screen.getByRole("button", { name: /设备说明书/ })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "刷新知识库" }));
    await waitFor(() => expect(api.listKnowledgeItems).toHaveBeenCalledTimes(2));
  });

  it("does not describe a failed request as a zero result", async () => {
    api.listKnowledgeItems.mockRejectedValue(new Error("offline"));
    render(<KnowledgePage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("请求失败：502");
    expect(screen.getByText(/读取失败 · 选来源查看内容/)).toBeInTheDocument();
    expect(screen.queryByText(/0 条当前工作区结果/)).not.toBeInTheDocument();
  });

  it("keeps category and summary examples isolated from production data", () => {
    render(<KnowledgeWorkspace error={null} exampleLabel="成员隔离视觉示例" exampleMode items={[{ ...item, exampleCategory: "产品知识", exampleSummary: "先检查电源。", exampleParameters: "额定功率示例", exampleIssue: "待核对" }]} loading={false} onRefresh={vi.fn()} />);
    expect(screen.getByText(/成员隔离视觉示例/)).toBeInTheDocument();
    fireEvent.click(within(screen.getByRole("navigation", { name: "知识用途分类" })).getByRole("button", { name: /产品知识/ }));
    fireEvent.click(screen.getByRole("button", { name: /设备说明书/ }));
    expect(screen.getByRole("complementary", { name: "来源详情" })).toHaveTextContent("额定功率示例");
  });
});
