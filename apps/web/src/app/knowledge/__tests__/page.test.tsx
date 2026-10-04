import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { AnchorHTMLAttributes, ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { KnowledgeItem } from "@/lib/source-api";

const api = vi.hoisted(() => ({ listKnowledgeItems: vi.fn(), getKnowledgeItem: vi.fn(), sourceErrorMessage: vi.fn(),
  listParameterComparisons: vi.fn(), adjudicateParameterComparison: vi.fn(), createParameterComparison: vi.fn() }));
vi.mock("@/lib/source-api", () => ({ listKnowledgeItems: api.listKnowledgeItems, getKnowledgeItem: api.getKnowledgeItem, sourceErrorMessage: api.sourceErrorMessage,
  listParameterComparisons: api.listParameterComparisons, adjudicateParameterComparison: api.adjudicateParameterComparison, createParameterComparison: api.createParameterComparison }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("@/lib/api", () => ({ listScriptLibraryEntries: vi.fn().mockResolvedValue({ items: [], total_count: 0 }) }));
vi.mock("next/link", () => ({ default: ({ children, href, ...props }: AnchorHTMLAttributes<HTMLAnchorElement>) => <a href={href} {...props}>{children}</a> }));
vi.mock("@/components/AppShell", () => ({ AppShell: ({ children, inspector }: { children: ReactNode; inspector?: ReactNode }) => <><div>{children}</div>{inspector ? <aside aria-label="来源详情">{inspector}</aside> : null}</> }));

import KnowledgePage from "@/app/knowledge/page";
import { KnowledgeWorkspace } from "@/app/knowledge/KnowledgeWorkspace";

const item: KnowledgeItem = {
  id: "knowledge-one", workspace_id: "workspace-one", content_type: "file",
  title: "设备说明书", body: "设备运行前请先检查电源。",
  file_reference: "/local/reference/manual.pdf", authorization: "内部使用",
  ownership: "当前工作区", source_item_id: "source-one", version: "v2",
  content_hash: null, sections: [], parameters: [],
  fetched_at: null, source_url: null,
  created_at: "2026-09-27T08:00:00Z",
};
beforeEach(() => {
  api.listKnowledgeItems.mockReset();
  api.getKnowledgeItem.mockReset();
  api.sourceErrorMessage.mockReset();
  api.listParameterComparisons.mockResolvedValue({ items: [], total_count: 0 });
  api.listKnowledgeItems.mockResolvedValue({ items: [item], total_count: 1 });
  api.getKnowledgeItem.mockRejectedValue(new Error("detail read failed"));
  api.sourceErrorMessage.mockReturnValue("请求失败：502");
});
describe("KnowledgePage", () => {
  it("shows actual source content and leaves unsupported use categories unclassified", async () => {
    render(<KnowledgePage />);
    fireEvent.click(await screen.findByRole("button", { name: /设备说明书/ }));
    expect(screen.queryByRole("heading", { name: "主题关系" })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "从来源中找答案" })).not.toBeInTheDocument();
    expect(screen.getByText(/主题关系、参数冲突核对、任务引用、问答和来源召回尚未接入/)).toBeInTheDocument();
    expect(screen.getAllByText(/未分类/)).not.toHaveLength(0);
    const detail = screen.getByRole("complementary", { name: "来源详情" });
    expect(detail).toHaveTextContent("设备运行前请先检查电源。");
    expect(detail).toHaveTextContent("尚无可核对的摘要来源");
    expect(detail).toHaveTextContent("没有显式参数格式就不生成参数");
    expect(detail).toHaveTextContent("v2");
    expect(detail).toHaveTextContent("内部使用");
    fireEvent.click(within(screen.getByRole("navigation", { name: "知识用途分类" })).getByRole("button", { name: /产品知识/ }));
    expect(screen.getByText(/不能按文件格式推断/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "查看全部来源" }));
    expect(screen.getByRole("button", { name: /设备说明书/ })).toBeInTheDocument();
  });

  it("shows parsed parameters with their locators and the saved versions", async () => {
    api.getKnowledgeItem.mockResolvedValue({
      ...item,
      content_hash: "hash-v2",
      parameters: [{ key: "净重", value: "600", unit: "g", locator: "第2段" }],
      sections: [{ locator: "第2段", title: "基本参数", text: "## 基本参数\n净重 600g" }],
      versions: [
        { version: "1", content_hash: "hash-v1", source_item_id: "source-one", title: item.title, body: "旧正文", file_reference: item.file_reference, sections: [], parameters: [], saved_at: "2026-09-26T08:00:00Z" },
        { version: "2", content_hash: "hash-v2", source_item_id: "source-two", title: item.title, body: item.body, file_reference: item.file_reference, sections: [], parameters: [], saved_at: "2026-09-27T08:00:00Z" },
      ],
    });
    render(<KnowledgePage />);
    fireEvent.click(await screen.findByRole("button", { name: /设备说明书/ }));
    const detail = screen.getByRole("complementary", { name: "来源详情" });
    await waitFor(() => expect(detail).toHaveTextContent("净重"));
    expect(within(detail).getByText("第2段")).toBeInTheDocument();
    expect(detail).toHaveTextContent("600");
    expect(detail).toHaveTextContent("hash-v2");
    expect(detail).toHaveTextContent("版本 1 ·");
    expect(detail).toHaveTextContent("版本 2 ·");
  });

  it("keeps the add-source action without an empty relation or question panel", async () => {
    api.listKnowledgeItems.mockResolvedValue({ items: [], total_count: 0 });
    render(<KnowledgePage />);
    await screen.findByText("知识库还没有内容");
    expect(screen.getByRole("link", { name: "去信息源添加内容" })).toHaveAttribute("href", "/sources");
    expect(screen.queryByRole("heading", { name: "主题关系" })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "从来源中找答案" })).not.toBeInTheDocument();
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

  it("renders correction and task-reference actions honestly while interfaces are absent", async () => {
    render(<KnowledgePage />);
    fireEvent.click(await screen.findByRole("button", { name: /设备说明书/ }));
    const detail = screen.getByRole("complementary", { name: "来源详情" });
    expect(detail).toHaveTextContent("纠正保存与任务引用接口尚未接入");
    expect(screen.getByRole("button", { name: "保存纠正" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "用于当前任务" })).toBeDisabled();
    expect(screen.getByRole("textbox", { name: "摘要纠正" })).toBeDisabled();
    expect(screen.getByRole("textbox", { name: "参数纠正" })).toBeDisabled();
  });

  it("shows example correction and task-reference actions in example mode", () => {
    render(<KnowledgeWorkspace error={null} exampleMode items={[{ ...item, exampleCategory: "产品知识", exampleSummary: "先检查电源。", exampleParameters: "额定功率示例", exampleIssue: "待核对" }]} loading={false} onRefresh={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: /设备说明书/ }));
    expect(screen.getByRole("button", { name: /保存纠正 · 示例/ })).toBeEnabled();
    expect(screen.getByRole("button", { name: /用于当前任务 · 示例/ })).toBeEnabled();
    expect(screen.getByRole("textbox", { name: "摘要纠正" })).toHaveValue("先检查电源。");
    expect(screen.getByRole("textbox", { name: "参数纠正" })).toHaveValue("额定功率示例");
  });

  it("keeps category and summary examples isolated from production data", () => {
    render(<KnowledgeWorkspace error={null} exampleLabel="成员隔离视觉示例" exampleMode items={[{ ...item, exampleCategory: "产品知识", exampleSummary: "先检查电源。", exampleParameters: "额定功率示例", exampleIssue: "待核对" }]} loading={false} onRefresh={vi.fn()} />);
    expect(screen.getByText(/成员隔离视觉示例/)).toBeInTheDocument();
    fireEvent.click(within(screen.getByRole("navigation", { name: "知识用途分类" })).getByRole("button", { name: /产品知识/ }));
    fireEvent.click(screen.getByRole("button", { name: /设备说明书/ }));
    expect(screen.getByRole("complementary", { name: "来源详情" })).toHaveTextContent("额定功率示例");
  });
});

describe("参数比较与人工裁定", () => {
  const comparison = {
    id: "conflict-1", workspace_id: "workspace-one",
    object_label: "曜核一体机", parameter_key: "净重", unit: "g", condition: "标准版",
    left: { knowledge_item_id: "knowledge-one", version: "1", value: "500", locator: "「规格」第 2 段" },
    right: { knowledge_item_id: "knowledge-two", version: "1", value: "800", locator: "「规格」第 2 段" },
    created_at: "2026-10-04T08:00:00Z", status: "pending", is_conflict: true, adjudication: null,
  };
  const decided = { ...comparison, status: "adjudicated", adjudication: {
    id: "adj-1", operator: "本地未认证", reason: "以右资料为准", decided_at: "2026-10-04T09:00:00Z",
    left_version_ref: "knowledge-one@1", right_version_ref: "knowledge-two@1",
    retained_side: "right", retained_value: "800", corrected_value: null } };

  it("keeps both sides visible while pending and records the adjudication", async () => {
    api.listParameterComparisons.mockResolvedValue({ items: [comparison], total_count: 1 });
    api.getKnowledgeItem.mockRejectedValue(new Error("detail read failed"));
    render(<KnowledgePage />);
    await screen.findByText("设备说明书");
    fireEvent.click(screen.getByText("设备说明书"));
    const panel = await screen.findByRole("region", { name: "参数比较与裁定" });
    // 待裁定：两侧证据和各自出处都在，没有单一下结论。
    expect(within(panel).getByText(/左：500 · 版本 1 · 出处/)).toBeInTheDocument();
    expect(within(panel).getByText(/右：800 · 版本 1 · 出处/)).toBeInTheDocument();
    expect(within(panel).getAllByText(/待裁定/).length).toBeGreaterThan(0);

    api.adjudicateParameterComparison.mockResolvedValue(decided);
    api.listParameterComparisons.mockResolvedValue({ items: [decided], total_count: 1 });
    fireEvent.change(within(panel).getByLabelText("裁定理由 conflict-1"), { target: { value: "以右资料为准" } });
    fireEvent.click(within(panel).getByRole("button", { name: "保留右侧 800" }));

    await waitFor(() => expect(api.adjudicateParameterComparison).toHaveBeenCalledWith("conflict-1", { reason: "以右资料为准", retained_side: "right" }));
    // 裁定留痕：操作者、理由、双方版本；未选的一侧仍在。
    await within(panel).findByText(/裁定：保留右侧 800 · 本地未认证/);
    expect(within(panel).getByText(/左：500/)).toBeInTheDocument();
  });
});
