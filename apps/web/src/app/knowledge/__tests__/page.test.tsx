import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { StrictMode } from "react";
import type { AnchorHTMLAttributes, ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { KnowledgeItem } from "@/lib/source-api";
import type { ScriptLibraryEntry } from "@/lib/types";

const api = vi.hoisted(() => ({
  getKnowledgeGrants: vi.fn(),
  grantKnowledgeToWorkspace: vi.fn(),
  revokeKnowledgeGrant: vi.fn(),
  listKnowledgeItems: vi.fn(),
  getKnowledgeItem: vi.fn(),
  setKnowledgeItemVisibility: vi.fn(),
  sourceErrorMessage: vi.fn(),
  findCitedVersion: vi.fn(),
  parseKnowledgeReferenceId: vi.fn(),
  listScriptLibraryEntries: vi.fn(),
  getScriptLibrarySources: vi.fn(),
}));
vi.mock("@/lib/source-api", () => ({
  listKnowledgeItems: api.listKnowledgeItems,
  getKnowledgeItem: api.getKnowledgeItem,
  setKnowledgeItemVisibility: api.setKnowledgeItemVisibility,
  sourceErrorMessage: api.sourceErrorMessage,
  findCitedVersion: api.findCitedVersion,
  parseKnowledgeReferenceId: api.parseKnowledgeReferenceId,
}));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("@/lib/api", async (importOriginal) => ({
  ...await importOriginal<typeof import("@/lib/api")>(),
  listScriptLibraryEntries: api.listScriptLibraryEntries,
  getScriptLibrarySources: api.getScriptLibrarySources,
  getKnowledgeGrants: api.getKnowledgeGrants,
  grantKnowledgeToWorkspace: api.grantKnowledgeToWorkspace,
  revokeKnowledgeGrant: api.revokeKnowledgeGrant,
}));
vi.mock("next/link", () => ({ default: ({ children, href, ...props }: AnchorHTMLAttributes<HTMLAnchorElement>) => <a href={href} {...props}>{children}</a> }));
vi.mock("@/components/AppShell", () => ({ AppShell: ({ children, inspector }: { children: ReactNode; inspector?: ReactNode }) => <><div>{children}</div>{inspector ? <aside aria-label="来源详情">{inspector}</aside> : null}</> }));

import KnowledgePage from "@/app/knowledge/page";
import { KnowledgeWorkspace } from "@/app/knowledge/KnowledgeWorkspace";

const item: KnowledgeItem = {
  id: "knowledge-one", workspace_id: "workspace-one", content_type: "file",
  title: "设备说明书", body: "设备运行前请先检查电源。",
  file_reference: "/local/reference/manual.pdf", authorization: "内部使用",
  ownership: "当前工作区", source_item_id: "source-one", version: "v2", use_state: "available",
  visibility: "private",
  content_hash: null, sections: [], parameters: [],
  fetched_at: null, source_url: null,
  created_at: "2026-09-27T08:00:00Z",
};
const scriptEntry: ScriptLibraryEntry = {
  id: "script-entry-one", name: "已确认文案", category: "产品介绍", version: 2,
  supersedes_entry_id: null, source_script_id: "script-one", source_task_id: "task-one",
  source_run_id: "run-one", source_topic_id: "topic-one", title: "产品记录",
  language: "zh-CN", sections: [{ id: "section-one", title: "开场", content: "先讲产品用途。", visual_prompt: "", reference_ids: [] }],
  created_at: "2026-10-09T08:00:00Z", updated_at: "2026-10-09T08:00:00Z",
};
beforeEach(() => {
  Object.values(api).forEach((mock) => mock.mockReset());
  window.history.replaceState({}, "", "/knowledge");
  api.getKnowledgeGrants.mockResolvedValue({
    can_manage: true, source_workspace_id: "workspace-one", access_via_grant: false,
    target_resolution: "resolved", target_resolution_message: "隔离UI响应示例", cross_workspace_status: "AVAILABLE",
    targets: [{ workspace_id: "workspace-two", name: "乙的空间" }], grants: [],
  });
  api.listKnowledgeItems.mockResolvedValue({ items: [item], total_count: 1 });
  api.getKnowledgeItem.mockRejectedValue(new Error("detail read failed"));
  api.sourceErrorMessage.mockReturnValue("请求失败：502");
  api.parseKnowledgeReferenceId.mockReturnValue(null);
  api.listScriptLibraryEntries.mockResolvedValue({ items: [], total_count: 0 });
  api.getScriptLibrarySources.mockResolvedValue([]);
});
describe("KnowledgePage", () => {
  it("shows actual source content and leaves unsupported use categories unclassified", async () => {
    render(<KnowledgePage />);
    const listModes = screen.getByRole("group", { name:"资料列表显示方式" });
    fireEvent.click(within(listModes).getByRole("button", { name:"对象列表" }));
    expect(within(listModes).getByRole("button", { name:"对象列表" })).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(await screen.findByRole("button", { name: /设备说明书/ }));
    expect(screen.queryByRole("heading", { name: "主题关系" })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "从来源中找答案" })).not.toBeInTheDocument();
    expect(screen.queryByText(/主题关系、参数冲突核对、任务引用、问答和来源召回尚未接入/)).not.toBeInTheDocument();
    expect(screen.queryByText(/用途分类尚未接入/)).not.toBeInTheDocument();
    const detail = screen.getByRole("complementary", { name: "来源详情" });
    expect(detail).toHaveTextContent("设备运行前请先检查电源。");
    expect(detail).toHaveTextContent("尚无可核对的摘要来源");
    expect(detail).toHaveTextContent("没有显式参数格式就不生成参数");
    expect(detail).toHaveTextContent("v2");
    expect(detail).toHaveTextContent("内部使用");
    expect(screen.queryByRole("button", { name: /产品知识/ })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /设备说明书/ })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name:"文案库" }));
    const scriptModes = screen.getByRole("group", { name:"文案列表显示方式" });
    fireEvent.click(within(scriptModes).getByRole("button", { name:"对象列表" }));
    expect(within(scriptModes).getByRole("button", { name:"对象列表" })).toHaveAttribute("aria-pressed", "true");
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
    fireEvent.click(screen.getByRole("button", { name: "清除搜索" }));
    expect(screen.getByRole("textbox", { name: "查找来源" })).toHaveFocus();
    expect(screen.getByRole("button", { name: /设备说明书/ })).toBeInTheDocument();
    fireEvent.change(screen.getByRole("textbox", { name: "查找来源" }), { target: { value: "电源" } });
    expect(screen.getByRole("button", { name: /设备说明书/ })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "刷新知识库" }));
    await waitFor(() => expect(api.listKnowledgeItems).toHaveBeenCalledTimes(2));
  });

  it("does not describe a failed request as a zero result", async () => {
    api.listKnowledgeItems.mockRejectedValue(new Error("offline"));
    render(<KnowledgePage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("请求失败：502");
    expect(screen.getByText(/读取失败 · 选择资料查看正文/)).toBeInTheDocument();
    expect(screen.queryByText(/0 条当前工作区结果/)).not.toBeInTheDocument();
  });

  it("keeps the last successful list visible when a refresh fails", async () => {
    api.listKnowledgeItems.mockResolvedValueOnce({ items: [item], total_count: 1 }).mockRejectedValueOnce(new Error("offline"));
    render(<KnowledgePage />);
    expect(await screen.findByRole("button", { name: /设备说明书/ })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "刷新知识库" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("上次成功读取的资料");
    expect(screen.getByRole("button", { name: /设备说明书/ })).toBeInTheDocument();
  });

  it("ignores a late list response from the superseded mount read", async () => {
    let resolveFirst!: (value: { items: KnowledgeItem[]; total_count: number }) => void;
    let resolveSecond!: (value: { items: KnowledgeItem[]; total_count: number }) => void;
    api.listKnowledgeItems
      .mockImplementationOnce(() => new Promise(resolve => { resolveFirst = resolve; }))
      .mockImplementationOnce(() => new Promise(resolve => { resolveSecond = resolve; }));
    render(<StrictMode><KnowledgePage /></StrictMode>);
    await waitFor(() => expect(api.listKnowledgeItems).toHaveBeenCalledTimes(2));
    const newer = { ...item, id: "newer", title: "较新列表" };
    const older = { ...item, id: "older", title: "迟到旧列表" };
    await act(async () => { resolveSecond({ items: [newer], total_count: 1 }); });
    expect(await screen.findByRole("button", { name: /较新列表/ })).toBeInTheDocument();
    await act(async () => { resolveFirst({ items: [older], total_count: 1 }); });
    expect(screen.getByRole("button", { name: /较新列表/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /迟到旧列表/ })).not.toBeInTheDocument();
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

  it("shares a private item and re-reads the real value from the server", async () => {
    api.getKnowledgeItem.mockResolvedValue({ ...item, visibility: "private" });
    render(<KnowledgeWorkspace error={null} items={[item]} loading={false} onRefresh={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: /设备说明书/ }));
    const detail = screen.getByRole("complementary", { name: "来源详情" });
    await waitFor(() => expect(detail).toHaveTextContent("当前仅自己可见"));

    // 保存返回的条目就是服务端写入后的真实状态。
    api.setKnowledgeItemVisibility.mockResolvedValue({ ...item, visibility: "shared" });
    const readsBefore = api.getKnowledgeItem.mock.calls.length;
    fireEvent.click(await within(detail).findByRole("button", { name: "共享给团队" }));
    await waitFor(() => expect(api.setKnowledgeItemVisibility).toHaveBeenCalledWith("knowledge-one", "shared"));
    // 用 PUT 返回值回读，不再补一轮 GET。
    await waitFor(() => expect(screen.getByRole("complementary", { name: "来源详情" })).toHaveTextContent("当前与团队成员共享"));
    expect(api.getKnowledgeItem.mock.calls.length).toBe(readsBefore);
    expect(within(detail).getByRole("button", { name: "收回为仅自己可见" })).toBeInTheDocument();
  });

  it("reports a refused visibility change instead of pretending it saved", async () => {
    api.getKnowledgeItem.mockResolvedValue({ ...item, visibility: "private" });
    api.setKnowledgeItemVisibility.mockRejectedValue(new Error("forbidden"));
    api.sourceErrorMessage.mockReturnValue("仅所有者可执行此操作");
    render(<KnowledgeWorkspace error={null} items={[item]} loading={false} onRefresh={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: /设备说明书/ }));
    const detail = screen.getByRole("complementary", { name: "来源详情" });
    await waitFor(() => expect(detail).toHaveTextContent("当前仅自己可见"));
    fireEvent.click(await within(detail).findByRole("button", { name: "共享给团队" }));
    expect(await within(detail).findByRole("alert")).toHaveTextContent("仅所有者可执行此操作");
  });

  it("offers no real visibility change in example mode", () => {
    render(<KnowledgeWorkspace error={null} exampleMode items={[{ ...item, exampleCategory: "产品知识" }]} loading={false} onRefresh={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: /设备说明书/ }));
    const detail = screen.getByRole("complementary", { name: "来源详情" });
    expect(detail).toHaveTextContent("可见范围设置不会保存");
    expect(within(detail).queryByRole("button", { name: "共享给团队" })).not.toBeInTheDocument();
  });

  it("does not apply a visibility result to a different selected item", async () => {
    const other = { ...item, id: "knowledge-two", title: "另一份资料", visibility: "private" };
    api.listKnowledgeItems.mockResolvedValue({ items: [item, other], total_count: 2 });
    api.getKnowledgeItem.mockImplementation((id: string) =>
      Promise.resolve(id === item.id ? { ...item, visibility: "private" } : { ...other, versions: [] }));
    // 共享返回的条目 id 为 knowledge-one；若此时选中已切到另一条，不得写进去。
    let resolvePut: (value: KnowledgeItem) => void = () => {};
    api.setKnowledgeItemVisibility.mockReturnValue(new Promise<KnowledgeItem>((resolve) => { resolvePut = resolve; }));
    render(<KnowledgeWorkspace error={null} items={[item, other]} loading={false} onRefresh={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: /设备说明书/ }));
    fireEvent.click(await within(screen.getByRole("complementary", { name: "来源详情" })).findByRole("button", { name: "共享给团队" }));
    fireEvent.click(screen.getByRole("button", { name: /另一份资料/ }));
    resolvePut({ ...item, visibility: "shared" });
    await waitFor(() => expect(screen.getByRole("complementary", { name: "来源详情" })).toHaveTextContent("另一份资料"));
    expect(screen.getByRole("complementary", { name: "来源详情" })).toHaveTextContent("当前仅自己可见");
  });
});

describe("knowledge workspace grants", () => {
  it("keeps the chosen workspace after failure and displays only saved grant/revoke results", async () => {
    const grant = {
      id: "grant-one", knowledge_item_id: item.id,
      source_workspace_id: "workspace-one", target_workspace_id: "workspace-two",
      granted_by: "original-uploader", granted_at: "2026-10-04T10:00:00Z",
      revoked_by: null, revoked_at: null,
    };
    api.grantKnowledgeToWorkspace.mockRejectedValueOnce(new Error("failed")).mockResolvedValueOnce(grant);
    api.revokeKnowledgeGrant.mockResolvedValue({ ...grant, revoked_by: "original-uploader", revoked_at: "2026-10-04T11:00:00Z" });
    render(<KnowledgeWorkspace items={[item]} loading={false} error={null} onRefresh={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: /设备说明书/ }));
    const target = await screen.findByRole("combobox", { name: "目标工作空间" });
    fireEvent.change(target, { target: { value: "workspace-two" } });
    fireEvent.click(screen.getByRole("button", { name: "授予读取与引用" }));
    expect(api.grantKnowledgeToWorkspace).not.toHaveBeenCalled();
    const confirmation = screen.getByRole("dialog", { name: "跨空间授权确认" });
    expect(confirmation).toHaveAttribute("aria-modal", "true");
    expect(screen.getByRole("button", { name: "取消" })).toHaveFocus();
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(target).toHaveValue("workspace-two");
    fireEvent.click(screen.getByRole("button", { name: "授予读取与引用" }));
    fireEvent.click(screen.getByRole("button", { name: "确认授予读取与引用" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "授予读取与引用" })).toBeEnabled());
    expect(target).toHaveValue("workspace-two");
    expect(screen.queryByRole("button", { name: "撤销授权" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "授予读取与引用" }));
    fireEvent.click(screen.getByRole("button", { name: "确认授予读取与引用" }));
    const revoke = await screen.findByRole("button", { name: "撤销授权" });
    expect(api.grantKnowledgeToWorkspace).toHaveBeenLastCalledWith(item.id, "workspace-two");
    expect(target).toHaveValue("");
    fireEvent.click(revoke);
    expect(api.revokeKnowledgeGrant).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "确认撤销授权" }));
    await screen.findByText("乙的空间 · 已撤销");
    expect(api.revokeKnowledgeGrant).toHaveBeenCalledWith(item.id, "grant-one");
    expect(screen.queryByRole("button", { name: "撤销授权" })).not.toBeInTheDocument();
  });

  it("shows original workspace to a reader without offering regrant or visibility writes", async () => {
    api.getKnowledgeGrants.mockResolvedValue({ can_manage: false, source_workspace_id: "workspace-one", access_via_grant: true, target_resolution: "resolved", target_resolution_message: "隔离UI响应示例", cross_workspace_status: "AVAILABLE", targets: [], grants: [] });
    render(<KnowledgeWorkspace items={[item]} loading={false} error={null} onRefresh={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: /设备说明书/ }));
    await screen.findByText(/原工作空间：workspace-one/);
    expect(screen.getByText(/你通过显式授权读取和引用/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "授予读取与引用" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "共享给团队" })).not.toBeInTheDocument();
  });

  it("labels local members as same-workspace only without offering a cross-workspace grant", async () => {
    api.getKnowledgeGrants.mockResolvedValue({
      can_manage: true, source_workspace_id: "local", access_via_grant: false,
      target_resolution: "same_workspace", cross_workspace_status: "UNVERIFIED",
      target_resolution_message: "本地成员仅同空间；跨空间授权不成立，真实跨空间验收未验证。",
      targets: [], grants: [],
    });
    render(<KnowledgeWorkspace items={[{ ...item, workspace_id: "local" }]} loading={false} error={null} onRefresh={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: /设备说明书/ }));
    expect(await screen.findByText(/本地成员仅同空间/)).toHaveTextContent("UNVERIFIED");
    expect(screen.queryByRole("combobox", { name: "目标工作空间" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "授予读取与引用" })).not.toBeInTheDocument();
    expect(api.grantKnowledgeToWorkspace).not.toHaveBeenCalled();
  });

  it("opens the visible scripts collection and exact source-matched entry from a deep link", async () => {
    window.history.replaceState({}, "", "/knowledge?entry_id=script-entry-one&source_task_id=task-one");
    api.listScriptLibraryEntries.mockResolvedValue({ items: [scriptEntry], total_count: 1 });
    render(<KnowledgeWorkspace items={[item]} loading={false} error={null} onRefresh={vi.fn()} />);

    expect(await screen.findByRole("button", { name: "文案库" })).toHaveAttribute("aria-pressed", "true");
    expect(await screen.findByRole("heading", { name: "已确认文案 · 版本 2" })).toBeInTheDocument();
    expect(screen.getByText("先讲产品用途。")).toBeInTheDocument();
    expect(api.listScriptLibraryEntries).toHaveBeenCalledWith("", "");
  });

  it.each([
    ["source mismatch", "different-task", "文案版本与来源任务不匹配；没有打开其他版本。"],
    ["missing entry", "task-one", "链接中的文案版本当前不可用；你仍可从列表查找其他版本。"],
  ])("does not open another entry when a deep link has a %s", async (_case, sourceTaskId, message) => {
    window.history.replaceState({}, "", `/knowledge?entry_id=script-entry-one&source_task_id=${sourceTaskId}`);
    api.listScriptLibraryEntries.mockResolvedValue({
      items: sourceTaskId === "different-task" ? [scriptEntry] : [{ ...scriptEntry, id: "sibling-entry", name: "其他文案" }],
      total_count: 1,
    });
    render(<KnowledgeWorkspace items={[item]} loading={false} error={null} onRefresh={vi.fn()} />);

    expect(await screen.findByRole("button", { name: "文案库" })).toHaveAttribute("aria-pressed", "true");
    expect(await screen.findByRole("alert")).toHaveTextContent(message);
    expect(screen.queryByRole("heading", { name: /版本 2/ })).not.toBeInTheDocument();
    expect(screen.queryByText("先讲产品用途。")).not.toBeInTheDocument();
  });
});
