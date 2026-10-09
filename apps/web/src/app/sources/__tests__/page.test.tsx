import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { AnchorHTMLAttributes, ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  listSourceItems: vi.fn(),
  listSourceConnectors: vi.fn(),
  createManualSourceItem: vi.fn(),
  createSourceConnector: vi.fn(),
  syncSourceConnector: vi.fn(),
  saveSourceItemToKnowledge: vi.fn(),
  importKnowledgeFile: vi.fn(),
  captureWebSnapshot: vi.fn(),
  sourceErrorMessage: vi.fn(),
}));
vi.mock("@/lib/source-api", () => ({
  listSourceItems: api.listSourceItems,
  listSourceConnectors: api.listSourceConnectors,
  createManualSourceItem: api.createManualSourceItem,
  createSourceConnector: api.createSourceConnector,
  syncSourceConnector: api.syncSourceConnector,
  saveSourceItemToKnowledge: api.saveSourceItemToKnowledge,
  importKnowledgeFile: api.importKnowledgeFile,
  captureWebSnapshot: api.captureWebSnapshot,
  sourceErrorMessage: api.sourceErrorMessage,
}));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("next/link", () => ({ default: ({ children, href, ...props }: AnchorHTMLAttributes<HTMLAnchorElement>) => <a href={href} {...props}>{children}</a> }));
vi.mock("@/components/AppShell", () => ({ AppShell: ({ children, inspector, inspectorTitle }: { children: ReactNode; inspector?: ReactNode; inspectorTitle?: string }) => <><div>{children}</div>{inspector ? <aside aria-label={inspectorTitle}>{inspector}</aside> : null}</> }));

import SourcesPage from "@/app/sources/page";

const parsed = {
  id: "source-file", workspace_id: "workspace-one", connector_id: "compat:knowledge_files:workspace-one",
  source_label: "file_import", source_url: null, content_type: "text/markdown", author: null,
  published_at: null, body: "# 说明书\n\n全文正文", subtitles: null, media_references: [],
  license: "user_provided", title: "说明书", file_reference: "说明书.md",
  content_hash: "hash-file", sections: [{ locator: "第1段", title: "说明书", text: "# 说明书" }],
  parameters: [{ key: "净重", value: "500", unit: "g", locator: "第1段" }],
  created_at: "2026-10-04T09:00:00Z",
};

beforeEach(() => {
  Object.values(api).forEach((mock) => mock.mockReset());
  api.listSourceItems.mockResolvedValue({ items: [], total_count: 0 });
  api.listSourceConnectors.mockResolvedValue({ items: [] });
  api.sourceErrorMessage.mockReturnValue("请求失败：500");
  // 初始没有这份文件；实际提交成功后，列表刷新才读到新条目。
  api.importKnowledgeFile.mockImplementation(async () => {
    api.listSourceItems.mockResolvedValue({ items: [parsed], total_count: 1 });
    return parsed;
  });
  api.saveSourceItemToKnowledge.mockResolvedValue({
    id: "ki-one", version: "1", content_hash: "hash-file", source_item_id: parsed.id,
  });
});

function selectFile(name = "说明书.md") {
  const input = document.querySelector("input[type=file]") as HTMLInputElement;
  const file = new File(["# 说明书"], name, { type: "text/markdown" });
  Object.defineProperty(input, "files", { value: [file], configurable: true });
  fireEvent.change(input);
  fireEvent.submit(input.closest("form") as HTMLFormElement);
}

describe("sources file import", () => {
  it("imports the selected file, shows the parse result and saves it to knowledge", async () => {
    render(<SourcesPage />);
    fireEvent.click(screen.getByRole("button", { name: "导入文件" }));
    await screen.findByRole("heading", { name: "上传知识文件" });
    selectFile();
    await waitFor(() => expect(api.importKnowledgeFile).toHaveBeenCalledTimes(1));
    expect(api.importKnowledgeFile.mock.calls[0][0].filename).toBe("说明书.md");
    expect(await screen.findByText(/已解析《说明书》/)).toHaveTextContent("出处 1 处");

    const inspector = await screen.findByRole("complementary", { name: "信息条目详情" });
    fireEvent.change(within(inspector).getByLabelText(/归属/) as HTMLInputElement, { target: { value: "我的内容" } });
    fireEvent.change(within(inspector).getByLabelText(/授权\/使用范围/) as HTMLInputElement, { target: { value: "用户提供" } });
    fireEvent.click(within(inspector).getByRole("button", { name: /保存到知识库/ }));
    await waitFor(() => expect(api.saveSourceItemToKnowledge).toHaveBeenCalledTimes(1));
    expect(api.saveSourceItemToKnowledge.mock.calls[0][0]).toBe("source-file");
    expect(await screen.findByRole("link", { name: "查看我的资料" })).toHaveAttribute("href", "/knowledge");
  });

  it("reports a parse failure instead of inventing body text", async () => {
    api.importKnowledgeFile.mockRejectedValue(new Error("扫描件没有文本层"));
    api.sourceErrorMessage.mockReturnValue("扫描件没有文本层");
    render(<SourcesPage />);
    fireEvent.click(screen.getByRole("button", { name: "导入文件" }));
    await screen.findByRole("heading", { name: "上传知识文件" });
    selectFile();
    await waitFor(() => expect(api.importKnowledgeFile).toHaveBeenCalledTimes(1));
    expect(await screen.findByRole("alert")).toHaveTextContent("扫描件没有文本层");
    expect(screen.queryByText(/已解析/)).not.toBeInTheDocument();
  });
});
describe("sources web snapshot", () => {
  it("captures the page body with the original url and the fetched time", async () => {
    const snapshot = {
      ...parsed,
      id: "source-web", source_label: "web_snapshot", content_type: "article",
      source_url: "https://example.com/v1", file_reference: "https://example.com/v1",
      fetched_at: "2026-10-04T10:00:00Z", title: "曜核一体机发布",
      body: "第一段：设备支持本地成片。",
      sections: [{ locator: "「曜核一体机发布」第 1 段", title: "曜核一体机发布", text: "第一段：设备支持本地成片。" }],
      parameters: [],
    };
    api.captureWebSnapshot.mockImplementation(async () => {
      // 快照落库后才由刷新读取，不提前把尚未抓取的内容放进初始列表。
      api.listSourceItems.mockResolvedValue({ items: [snapshot], total_count: 1 });
      return snapshot;
    });
    render(<SourcesPage />);
    fireEvent.click(screen.getByRole("button", { name: "保存网页" }));
    await screen.findByRole("heading", { name: "网页快照" });
    const urlInput = screen.getByPlaceholderText("https://example.com/article") as HTMLInputElement;
    fireEvent.change(urlInput, { target: { value: "https://example.com/v1" } });
    fireEvent.submit(urlInput.closest("form") as HTMLFormElement);
    await waitFor(() => expect(api.captureWebSnapshot).toHaveBeenCalledWith("https://example.com/v1"));
    expect(await screen.findByText(/网页正文已存为快照/)).toBeInTheDocument();
    const inspector = await screen.findByRole("complementary", { name: "信息条目详情" });
    expect(within(inspector).getByRole("link", { name: /打开原文/ })).toHaveAttribute("href", "https://example.com/v1");
    // 抓取时刻精确到秒；同一天的两次抓取要能分辨。
    expect(within(inspector).getByText(/2026\/10\/04/)).toHaveTextContent(/\d{2}:\d{2}:\d{2}/);
    // 快照列表项不会被刷新掉：选中的是快照自己的连接器。
    expect((await screen.findAllByText("曜核一体机发布")).length).toBeGreaterThan(0);
  });
});


describe("content entry intent and preparation", () => {
  it("starts with text only and preserves each draft when switching methods", async () => {
    render(<SourcesPage />);
    await waitFor(() => expect(screen.getByRole("button", { name: "刷新" })).toBeEnabled());
    expect(screen.getByRole("heading", { name: "粘贴文字" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "上传知识文件" })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "网页快照" })).not.toBeInTheDocument();
    const body = screen.getByLabelText("正文");
    fireEvent.change(body, { target: { value: "已经整理的产品资料" } });
    fireEvent.click(screen.getByRole("button", { name: "保存网页" }));
    expect(screen.queryByRole("textbox", { name: "正文" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "粘贴文字" }));
    expect(screen.getByLabelText("正文")).toHaveValue("已经整理的产品资料");
  });

  it("keeps subscriptions in an advanced disclosure and unavailable platforms noninteractive", async () => {
    render(<SourcesPage />);
    await waitFor(() => expect(screen.getByRole("button", { name: "刷新" })).toBeEnabled());
    const disclosure = screen.getByText("持续订阅与信息源 · 高级").closest("details")!;
    expect(disclosure).not.toHaveAttribute("open");
    fireEvent.click(screen.getByText("持续订阅与信息源 · 高级"));
    expect(disclosure).toHaveAttribute("open");
    expect(screen.queryByRole("button", { name: /小红书/ })).not.toBeInTheDocument();
    expect(screen.getByText("小红书").closest("div")).not.toHaveAttribute("tabindex");
    const platform = screen.getByLabelText("平台");
    expect(within(platform).queryByRole("option", { name: /尚未支持/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /产品社区/ }));
    expect(platform).toHaveValue("product_hunt");
  });

  it("shows inline preparation and rejects missing, empty or unsupported files without API calls", async () => {
    render(<SourcesPage />);
    await waitFor(() => expect(screen.getByRole("button", { name: "刷新" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "导入文件" }));
    const submit = screen.getByRole("button", { name: "解析并导入" });
    const input = screen.getByLabelText("选择文件") as HTMLInputElement;
    expect(submit).toBeDisabled();
    expect(screen.getByText("请先选择文件，再解析并导入。")).toHaveAttribute("role", "status");
    fireEvent.submit(input.closest("form")!);
    expect(screen.getByRole("alert")).toHaveTextContent("请先选择");
    fireEvent.change(input, { target: { files: [new File([], "空资料.txt")] } });
    expect(screen.getByRole("alert")).toHaveTextContent("文件为空");
    expect(input).toHaveAttribute("aria-invalid", "true");
    fireEvent.change(input, { target: { files: [new File(["data"], "资料.exe")] } });
    expect(screen.getByRole("alert")).toHaveTextContent("请选择 Markdown");
    fireEvent.submit(input.closest("form")!);
    expect(api.importKnowledgeFile).not.toHaveBeenCalled();
    fireEvent.change(input, { target: { files: [new File(["正文"], "资料.txt")] } });
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(submit).toBeEnabled();
    fireEvent.change(input, { target: { files: [] } });
    expect(submit).toBeDisabled();
    expect(api.importKnowledgeFile).not.toHaveBeenCalled();
  });

  it("submits pasted content through the existing source method and resets only after success", async () => {
    api.createManualSourceItem.mockResolvedValue({ ...parsed, connector_id: "manual-connector" });
    render(<SourcesPage />);
    fireEvent.change(screen.getByLabelText("标题"), { target: { value: "说明书" } });
    fireEvent.change(screen.getByLabelText("正文"), { target: { value: "全文正文" } });
    fireEvent.submit(screen.getByLabelText("正文").closest("form")!);
    await waitFor(() => expect(api.createManualSourceItem).toHaveBeenCalledWith(expect.objectContaining({ title: "说明书", body: "全文正文", content_type: "text" })));
    expect(await screen.findByText("信息条目已添加，可查看正文并保存到知识库。")).toBeInTheDocument();
    expect(screen.getByLabelText("正文")).toHaveValue("");
    expect(api.listSourceItems).toHaveBeenCalledWith("manual-connector");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});


it("keeps a pasted draft when the real source submission fails", async () => {
  api.createManualSourceItem.mockRejectedValue(new Error("保存失败"));
  api.sourceErrorMessage.mockReturnValue("保存失败");
  render(<SourcesPage />);
  fireEvent.change(screen.getByLabelText("标题"), { target: { value: "保留标题" } });
  fireEvent.change(screen.getByLabelText("正文"), { target: { value: "保留正文" } });
  fireEvent.submit(screen.getByLabelText("正文").closest("form")!);
  expect(await screen.findByRole("alert")).toHaveTextContent("保存失败");
  expect(screen.getByLabelText("正文")).toHaveValue("保留正文");
  expect(screen.queryByText("信息条目已添加，可查看正文并保存到知识库。")).not.toBeInTheDocument();
});

it("saves the supported subscription selected by its platform control without a post-await reset error", async () => {
  api.createSourceConnector.mockResolvedValue({ id: "connector-ph", platform: "product_hunt", connection_status: "ready", account_or_rule: "https://example.com/feed", permissions: [], frequency: "每天", latest_run_id: null });
  render(<SourcesPage />);
  fireEvent.click(screen.getByText("持续订阅与信息源 · 高级"));
  fireEvent.click(screen.getByRole("button", { name: /产品社区/ }));
  const rule = screen.getByLabelText("地址或规则");
  fireEvent.change(rule, { target: { value: "https://example.com/feed" } });
  fireEvent.submit(rule.closest("form")!);
  await waitFor(() => expect(api.createSourceConnector).toHaveBeenCalledWith(expect.objectContaining({ platform: "product_hunt", account_or_rule: "https://example.com/feed" })));
  expect(await screen.findByText("信息源已保存，可执行同步。")).toBeInTheDocument();
  expect(rule).toHaveValue("");
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});
