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
  api.importKnowledgeFile.mockResolvedValue(parsed);
  // 导入后列表会重新读取同一个来源条目（服务端事实），这里用同一份对象。
  api.listSourceItems.mockResolvedValue({ items: [parsed], total_count: 1 });
  api.saveSourceItemToKnowledge.mockResolvedValue({
    id: "ki-one", version: "1", content_hash: "hash-file", source_item_id: parsed.id,
  });
});

function selectFile(name = "说明书.md") {
  const input = document.querySelector("input[type=file]") as HTMLInputElement;
  const file = new File(["# 说明书"], name, { type: "text/markdown" });
  Object.defineProperty(input, "files", { value: [file], configurable: true });
  fireEvent.submit(input.closest("form") as HTMLFormElement);
}

describe("sources file import", () => {
  it("imports the selected file, shows the parse result and saves it to knowledge", async () => {
    render(<SourcesPage />);
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
  });

  it("reports a parse failure instead of inventing body text", async () => {
    api.importKnowledgeFile.mockRejectedValue(new Error("扫描件没有文本层"));
    api.sourceErrorMessage.mockReturnValue("扫描件没有文本层");
    render(<SourcesPage />);
    await screen.findByRole("heading", { name: "上传知识文件" });
    selectFile();
    await waitFor(() => expect(api.importKnowledgeFile).toHaveBeenCalledTimes(1));
    expect(await screen.findByRole("alert")).toHaveTextContent("扫描件没有文本层");
    expect(screen.queryByText(/已解析/)).not.toBeInTheDocument();
  });
});