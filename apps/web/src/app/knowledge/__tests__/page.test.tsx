import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { AnchorHTMLAttributes, ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { KnowledgeItem } from "@/lib/source-api";

const api = vi.hoisted(() => ({ listKnowledgeItems: vi.fn(), getKnowledgeItem: vi.fn(), sourceErrorMessage: vi.fn(),
  listParameterComparisons: vi.fn(), adjudicateParameterComparison: vi.fn(), createParameterComparison: vi.fn(),
  updateKnowledgeItemClassification: vi.fn() }));
vi.mock("@/lib/source-api", () => ({ listKnowledgeItems: api.listKnowledgeItems, getKnowledgeItem: api.getKnowledgeItem, sourceErrorMessage: api.sourceErrorMessage,
  listParameterComparisons: api.listParameterComparisons, adjudicateParameterComparison: api.adjudicateParameterComparison, createParameterComparison: api.createParameterComparison,
  updateKnowledgeItemClassification: api.updateKnowledgeItemClassification }));
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
  // WIL-105：真实分类/主题/解析状态；新保存的条目默认未分类且无主题。
  use_category: null, topics: [], parse_status: "text_only",
};
beforeEach(() => {
  api.listKnowledgeItems.mockReset();
  api.getKnowledgeItem.mockReset();
  api.sourceErrorMessage.mockReset();
  api.updateKnowledgeItemClassification.mockReset();
  api.listParameterComparisons.mockResolvedValue({ items: [], total_count: 0 });
  api.listKnowledgeItems.mockResolvedValue({ items: [item], total_count: 1, category_counts: { 产品知识: 0, 公司文档: 0, 菜单条目: 0, 销售话术: 0, 未分类: 1 }, topics: [] });
  api.getKnowledgeItem.mockRejectedValue(new Error("detail read failed"));
  api.sourceErrorMessage.mockReturnValue("请求失败：502");
});
describe("KnowledgePage", () => {
  it("shows actual source content and the real unclassified state", async () => {
    render(<KnowledgePage />);
    fireEvent.click(await screen.findByRole("button", { name: /设备说明书/ }));
    expect(screen.queryByRole("heading", { name: "从来源中找答案" })).not.toBeInTheDocument();
    expect(screen.getByText(/参数冲突核对可在上方入口使用/)).toBeInTheDocument();
    // 新保存的条目显示未分类与真实解析状态，不显示示例占位。
    expect(screen.getAllByText(/未分类/)).not.toHaveLength(0);
    const detail = screen.getByRole("complementary", { name: "来源详情" });
    expect(detail).toHaveTextContent("设备运行前请先检查电源。");
    expect(detail).toHaveTextContent("尚无可核对的摘要来源");
    expect(detail).toHaveTextContent("没有显式参数格式就不生成参数");
    expect(detail).toHaveTextContent("仅纯文本");
    expect(detail).toHaveTextContent("v2");
    expect(detail).toHaveTextContent("内部使用");
    fireEvent.click(within(screen.getByRole("navigation", { name: "知识用途分类" })).getByRole("button", { name: /产品知识/ }));
    expect(screen.getByText("这个分类下没有匹配的来源")).toBeInTheDocument();
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

  it("shows the real saved category, topics and parse status instead of example guesses", async () => {
    api.listKnowledgeItems.mockResolvedValue({ items: [{ ...item, use_category: "产品知识", topics: ["曜核一体机"], parse_status: "parsed" }], total_count: 1,
      category_counts: { 产品知识: 1, 公司文档: 0, 菜单条目: 0, 销售话术: 0, 未分类: 0 }, topics: ["曜核一体机"] });
    api.getKnowledgeItem.mockResolvedValue({ ...item, use_category: "产品知识", topics: ["曜核一体机"], parse_status: "parsed",
      parameters: [{ key: "净重", value: "600", unit: "g", locator: "第2段" }], versions: [] });
    render(<KnowledgePage />);
    const nav = screen.getByRole("navigation", { name: "知识用途分类" });
    // 概览数量读真实字段，未分类独立一格。
    await waitFor(() => expect(within(nav).getByRole("button", { name: /产品知识/ })).toHaveTextContent("1 条"));
    expect(within(nav).getByRole("button", { name: /未分类/ })).toHaveTextContent("0 条");
    fireEvent.click(await screen.findByRole("button", { name: /设备说明书/ }));
    const detail = screen.getByRole("complementary", { name: "来源详情" });
    expect(detail).toHaveTextContent("已解析结构");
    expect((screen.getByLabelText("用途分类") as HTMLSelectElement).value).toBe("产品知识");
    expect(within(detail).getByText("曜核一体机")).toBeInTheDocument();
    // 按分类筛选后只显示该分类下的真实条目。
    fireEvent.click(within(nav).getByRole("button", { name: /公司文档/ }));
    expect(screen.getByText("这个分类下没有匹配的来源")).toBeInTheDocument();
  });

  it("saves the user chosen category and topic through the real endpoint", async () => {
    api.listKnowledgeItems.mockResolvedValue({ items: [item], total_count: 1,
      category_counts: { 产品知识: 0, 公司文档: 0, 菜单条目: 0, 销售话术: 0, 未分类: 1 }, topics: [] });
    // 详情跟着保存结果变，避免第二次写入把刚存的分类当成未分类。
    let saved: KnowledgeItem = item;
    api.getKnowledgeItem.mockImplementation(async () => ({ ...saved, versions: [] }));
    api.updateKnowledgeItemClassification.mockImplementation(async (_id, input) => {
      saved = { ...saved, use_category: input.use_category, topics: input.topics };
      return saved;
    });
    render(<KnowledgePage />);
    fireEvent.click(await screen.findByRole("button", { name: /设备说明书/ }));
    fireEvent.change(screen.getByLabelText("用途分类"), { target: { value: "销售话术" } });
    await waitFor(() => expect(api.updateKnowledgeItemClassification).toHaveBeenCalledWith("knowledge-one", { use_category: "销售话术", topics: [] }));
    // 等上一次写入结束再发下一次：保存期间分类控件禁用。
    await waitFor(() => expect(screen.getByLabelText("用途分类")).not.toBeDisabled());
    fireEvent.change(screen.getByRole("textbox", { name: "新增主题或对象" }), { target: { value: "曜核一体机" } });
    fireEvent.click(screen.getByRole("button", { name: "添加" }));
    await waitFor(() => expect(api.updateKnowledgeItemClassification).toHaveBeenCalledWith("knowledge-one", { use_category: "销售话术", topics: ["曜核一体机"] }));
  });

  it("keeps the topic draft and shows the error when the save fails, then succeeds on retry", async () => {
    api.listKnowledgeItems.mockResolvedValue({ items: [item], total_count: 1,
      category_counts: { 产品知识: 0, 公司文档: 0, 菜单条目: 0, 销售话术: 0, 未分类: 1 }, topics: [] });
    api.getKnowledgeItem.mockResolvedValue({ ...item, versions: [] });
    api.updateKnowledgeItemClassification
      .mockRejectedValueOnce(new Error("保存失败：后端拒绝"))
      .mockResolvedValueOnce({ ...item, topics: ["曜核一体机"] });
    render(<KnowledgePage />);
    fireEvent.click(await screen.findByRole("button", { name: /设备说明书/ }));
    fireEvent.change(screen.getByRole("textbox", { name: "新增主题或对象" }), { target: { value: "曜核一体机" } });
    fireEvent.click(screen.getByRole("button", { name: "添加" }));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("请求失败：502"));
    // 失败时草稿保留，用户可以直接重试。
    expect((screen.getByRole("textbox", { name: "新增主题或对象" }) as HTMLInputElement).value).toBe("曜核一体机");
    fireEvent.click(screen.getByRole("button", { name: "添加" }));
    await waitFor(() => expect(api.updateKnowledgeItemClassification).toHaveBeenCalledTimes(2));
  });

  it("filters the real items by an exact registered topic", async () => {
    api.listKnowledgeItems.mockResolvedValue({
      items: [
        { ...item, id: "knowledge-one", use_category: "产品知识", topics: ["曜核一体机"] },
        { ...item, id: "knowledge-two", title: "另一份资料", use_category: "公司文档", topics: [] },
      ],
      total_count: 2,
      category_counts: { 产品知识: 1, 公司文档: 1, 菜单条目: 0, 销售话术: 0, 未分类: 0 },
      topics: ["曜核一体机"],
    });
    api.getKnowledgeItem.mockResolvedValue({ ...item, use_category: "产品知识", topics: ["曜核一体机"], versions: [] });
    render(<KnowledgePage />);
    await screen.findByRole("button", { name: /设备说明书/ });
    expect(screen.getByRole("button", { name: /另一份资料/ })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("按主题筛选"), { target: { value: "曜核一体机" } });
    expect(screen.queryByRole("button", { name: /另一份资料/ })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /设备说明书/ })).toBeInTheDocument();
    // 没有登记主题时给出添加主题的指引，而不是空白。
    api.listKnowledgeItems.mockResolvedValue({ items: [item], total_count: 1,
      category_counts: { 产品知识: 0, 公司文档: 0, 菜单条目: 0, 销售话术: 0, 未分类: 1 }, topics: [] });
    fireEvent.click(screen.getByRole("button", { name: "刷新知识库" }));
    await waitFor(() => expect(screen.getByText(/还没有登记任何主题/)).toBeInTheDocument());
  });

  it("does not report a real zero count before the counts load", async () => {
    let release: () => void = () => {};
    api.listKnowledgeItems.mockImplementation(() => new Promise((resolve) => { release = () => resolve({ items: [item], total_count: 1,
      category_counts: { 产品知识: 1, 公司文档: 0, 菜单条目: 0, 销售话术: 0, 未分类: 0 }, topics: [] }); }));
    render(<KnowledgePage />);
    expect(screen.getByRole("navigation", { name: "知识用途分类" })).toHaveTextContent("读取中");
    release();
    await waitFor(() => expect(screen.getByRole("navigation", { name: "知识用途分类" })).toHaveTextContent("1 条"));
  });

  it("describes the historical version from that version's own content", async () => {
    api.listKnowledgeItems.mockResolvedValue({ items: [{ ...item, parse_status: "parsed" }], total_count: 1,
      category_counts: { 产品知识: 0, 公司文档: 0, 菜单条目: 0, 销售话术: 0, 未分类: 1 }, topics: [] });
    api.getKnowledgeItem.mockResolvedValue({ ...item, parse_status: "parsed",
      parameters: [{ key: "净重", value: "600", unit: "g", locator: "第2段" }],
      versions: [{ version: "1", content_hash: "hash-v1", source_item_id: "source-one", title: item.title,
        body: "旧正文", file_reference: item.file_reference, sections: [], parameters: [],
        fetched_at: null, source_url: null, saved_at: "2026-09-26T08:00:00Z" }] });
    render(<KnowledgePage />);
    fireEvent.click(await screen.findByRole("button", { name: /设备说明书/ }));
    const detail = screen.getByRole("complementary", { name: "来源详情" });
    await waitFor(() => expect(detail).toHaveTextContent("已解析结构"));
    fireEvent.change(screen.getByLabelText("查看版本"), { target: { value: "1" } });
    // 旧版只有整段正文：不拿当前版的已解析状态冒充。
    expect(detail).toHaveTextContent("该版只有整段正文");
  });

  it("keeps the empty classification state actionable", async () => {
    api.listKnowledgeItems.mockResolvedValue({ items: [], total_count: 0,
      category_counts: { 产品知识: 0, 公司文档: 0, 菜单条目: 0, 销售话术: 0, 未分类: 0 }, topics: [] });
    render(<KnowledgePage />);
    fireEvent.click(await screen.findByRole("button", { name: /产品知识/ }));
    expect(screen.getByText(/这个分类下还没有来源/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "去信息源添加内容" })).toHaveAttribute("href", "/sources");
  });

  it("keeps search and refresh connected to the real list endpoint", async () => {
    render(<KnowledgePage />);
    await screen.findByRole("button", { name: /设备说明书/ });
    fireEvent.change(screen.getByRole("textbox", { name: "查找来源" }), { target: { value: "找不到" } });
    expect(screen.getByText("这个分类下没有匹配的来源")).toBeInTheDocument();
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

  it("keeps only the real conflict entry and drops the disabled correction shell", async () => {
    render(<KnowledgePage />);
    fireEvent.click(await screen.findByRole("button", { name: /设备说明书/ }));
    const detail = screen.getByRole("complementary", { name: "来源详情" });
    // 135 的冲突核对入口是真实可用的，不再配一个禁用的示例壳。
    expect(detail).toHaveTextContent("参数冲突核对");
    expect(screen.queryByRole("textbox", { name: "摘要纠正" })).not.toBeInTheDocument();
    expect(screen.queryByRole("textbox", { name: "参数纠正" })).not.toBeInTheDocument();
    expect(detail).toHaveTextContent("知识条目本身的摘要纠正、任务引用和问答尚未接入");
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
    await within(panel).findByText(/裁定值：800 · 保留右侧来源 · 本地未认证/);
    expect(within(panel).getByText(/左：500/)).toBeInTheDocument();
  });
});

describe("参数比较闭环操作", () => {
  const counterpart = { ...item, id: "knowledge-two", title: "右资料", version: "v2" };
  const counterpartDetail = {
    ...item, id: "knowledge-two", title: "右资料", version: "2",
    versions: [
      { version: "1", content_hash: "h1", source_item_id: "s2", title: "右资料", body: "旧版正文 800", file_reference: null,
        sections: [{ locator: "「规格」第 2 段", title: "规格", text: "旧版净重 800 g" }],
        parameters: [{ key: "净重", value: "800", unit: "g", locator: "「规格」第 2 段" }],
        fetched_at: "2026-10-04T08:00:00Z", source_url: null, saved_at: "2026-10-04T08:00:00Z" },
      { version: "2", content_hash: "h2", source_item_id: "s2", title: "右资料", body: "新版正文 820", file_reference: null,
        sections: [{ locator: "「规格」第 2 段", title: "规格", text: "新版净重 820 g" }],
        parameters: [{ key: "净重", value: "820", unit: "g", locator: "「规格」第 2 段" }],
        fetched_at: "2026-10-04T09:00:00Z", source_url: null, saved_at: "2026-10-04T09:00:00Z" },
    ],
  };
  const currentDetail = {
    ...item, parameters: [{ key: "净重", value: "500", unit: "g", locator: "「规格」第 2 段" }],
    versions: [{ version: "v2", content_hash: "h0", source_item_id: "s1", title: "设备说明书", body: "当前正文 500", file_reference: null,
      sections: [{ locator: "「规格」第 2 段", title: "规格", text: "当前净重 500 g" }],
      parameters: [{ key: "净重", value: "500", unit: "g", locator: "「规格」第 2 段" }],
      fetched_at: null, source_url: null, saved_at: "2026-09-27T08:00:00Z" }],
  };

  it("selects a counterpart, creates the comparison from real options, shows the fixed-version source and applies a correction", async () => {
    api.listKnowledgeItems.mockResolvedValue({ items: [item, counterpart], total_count: 2 });
    api.getKnowledgeItem.mockImplementation((id: string) =>
      Promise.resolve(id === "knowledge-two" ? counterpartDetail : currentDetail));
    const pending = {
      id: "conflict-1", workspace_id: "workspace-one",
      object_label: "曜核一体机", parameter_key: "净重", unit: "g", condition: "标准版",
      left: { knowledge_item_id: "knowledge-one", version: "v2", value: "500", locator: "「规格」第 2 段" },
      right: { knowledge_item_id: "knowledge-two", version: "1", value: "800", locator: "「规格」第 2 段" },
      created_at: "2026-10-04T08:00:00Z", status: "pending", is_conflict: true, adjudication: null,
    };
    api.listParameterComparisons.mockResolvedValue({ items: [pending], total_count: 1 });
    api.createParameterComparison.mockResolvedValue(pending);
    const adjudicatedItem = { ...pending, status: "adjudicated", adjudication: {
      id: "adj-2", operator: "本地未认证", reason: "以右资料为准", decided_at: "2026-10-04T10:00:00Z",
      left_version_ref: "knowledge-one@v2", right_version_ref: "knowledge-two@1",
      retained_side: "right", retained_value: "800", corrected_value: "810" } };
    api.adjudicateParameterComparison.mockImplementation(() => {
      api.listParameterComparisons.mockResolvedValue({ items: [adjudicatedItem], total_count: 1 });
      return Promise.resolve(adjudicatedItem);
    });

    render(<KnowledgePage />);
    await screen.findByText("设备说明书");
    fireEvent.click(screen.getByText("设备说明书"));
    const panel = await screen.findByRole("region", { name: "参数比较与裁定" });

    // 未选对比资料时明确占位，不会显示出一个看似已选的条目。
    const counterpartSelect = within(panel).getByLabelText("对比条目") as HTMLSelectElement;
    expect(counterpartSelect.value).toBe("");
    fireEvent.change(counterpartSelect, { target: { value: "knowledge-two" } });
    // 版本和参数都来自该条目的真实详情，不要求用户手填出处串。
    await waitFor(() => expect((within(panel).getByLabelText("对比条目版本") as HTMLSelectElement).options.length).toBe(2));
    expect([...(within(panel).getByLabelText("对比条目参数") as HTMLSelectElement).options].map((option) => option.textContent)).toEqual(["净重 · 820（g） · 「规格」第 2 段"]);
    fireEvent.change(within(panel).getByLabelText("对比条目版本"), { target: { value: "1" } });
    fireEvent.change(within(panel).getByLabelText("对象"), { target: { value: "曜核一体机" } });
    fireEvent.change(within(panel).getByLabelText("参数名"), { target: { value: "净重" } });
    fireEvent.change(within(panel).getByLabelText("单位"), { target: { value: "g" } });
    fireEvent.change(within(panel).getByLabelText("适用条件"), { target: { value: "标准版" } });
    fireEvent.click(within(panel).getByRole("button", { name: "建立比较" }));
    await waitFor(() => expect(api.createParameterComparison).toHaveBeenCalledWith({
      object_label: "曜核一体机", parameter_key: "净重", unit: "g", condition: "标准版",
      left_knowledge_item_id: "knowledge-one", left_version: "v2", left_locator: "「规格」第 2 段",
      right_knowledge_item_id: "knowledge-two", right_version: "1", right_locator: "「规格」第 2 段",
    }));

    // 查看右侧来源读的是那一版的原文，不是当前版。
    fireEvent.click(within(panel).getByRole("button", { name: "查看右侧来源" }));
    const source = await within(panel).findByRole("group", { name: "来源原文" });
    expect(within(source).getByText(/版本 1 ·/)).toBeInTheDocument();
    expect(within(source).getByText("旧版净重 800 g")).toBeInTheDocument();

    // 纠正值优先于保留侧取值。
    fireEvent.change(within(panel).getByLabelText("裁定理由 conflict-1"), { target: { value: "以右资料为准" } });
    fireEvent.change(within(panel).getByLabelText("纠正值 conflict-1"), { target: { value: "810" } });
    fireEvent.click(within(panel).getByRole("button", { name: "保留右侧 800" }));
    await waitFor(() => expect(api.adjudicateParameterComparison).toHaveBeenCalledWith("conflict-1", { reason: "以右资料为准", retained_side: "right", corrected_value: "810" }));
    await within(panel).findByText(/裁定值：810（人工纠正）/);
  });
});
