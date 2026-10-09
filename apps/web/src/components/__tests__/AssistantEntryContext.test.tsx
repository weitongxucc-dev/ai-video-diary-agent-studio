import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { AnchorHTMLAttributes } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AssistantConversation, CreationTask } from "@/lib/types";
import { HomeCommandComposer } from "../HomeCommandComposer";

const mocks = vi.hoisted(() => ({
  useConversation: vi.fn(), setDraftText: vi.fn(), createNew: vi.fn(), beginTurn: vi.fn(),
  sendWorkspace: vi.fn(), sendTask: vi.fn(), sendMaterial: vi.fn(),
}));
vi.mock("next/link", () => ({ default: ({ children, href, ...props }: AnchorHTMLAttributes<HTMLAnchorElement>) => <a href={href} {...props}>{children}</a> }));
vi.mock("@/components/useAssistantConversation", () => ({ useAssistantConversation: mocks.useConversation }));
vi.mock("@/lib/api", () => ({
  getRuntimeSettings: vi.fn().mockResolvedValue({ status: { llm: { configured: true, provider: "fixture", model: "fixture" } } }),
  getAssistantCapabilities: vi.fn().mockRejectedValue(new Error("controlled fixture")),
  sendWorkspaceAssistantPlanTurn: mocks.sendWorkspace,
  sendCreationTaskAssistantTurn: mocks.sendTask,
  sendMaterialAssistantTurn: mocks.sendMaterial,
}));

function savedConversation(): AssistantConversation {
  return { id: "saved-conversation", workspace_id: "fixture-workspace", scope_kind: "home", scope_id: "home", title: "新对话",
    draft_text: "", created_at: "2026-10-09T00:00:00Z", updated_at: "2026-10-09T00:00:00Z", turns: [] };
}
let conversation: AssistantConversation;
let draftText: string;
let loading: boolean;
beforeEach(() => {
  vi.clearAllMocks(); conversation = savedConversation(); draftText = ""; loading = false;
  mocks.useConversation.mockImplementation(() => ({ conversation, conversations: [], loading, error: null, draftText,
    setDraftText: mocks.setDraftText, createNew: mocks.createNew, beginTurn: mocks.beginTurn }));
});
afterEach(cleanup);

describe("assistant empty conversation entry context", () => {
  it.each([
    ["/settings", "查看运行情况", "/health"], ["/profiles", "查看参考素材", "/library"],
    ["/members", "查看工作区首页", "/"], ["/knowledge", "管理资料来源", "/sources"],
    ["/sources", "查看知识库", "/knowledge"], ["/library", "进入新建视频", "/?new=1"],
    ["/templates", "进入新建视频", "/?new=1"], ["/history", "查看工作区任务", "/"],
    ["/health", "查看服务设置", "/settings"], ["/production", "进入新建视频", "/?new=1"],
  ])("offers a real navigation entry on %s without sending a request", (pathname, label, href) => {
    render(<HomeCommandComposer pageContext={{ pathname }} />);
    expect(screen.getByRole("link", { name: label })).toHaveAttribute("href", href);
    expect(screen.getByRole("navigation", { name: "当前页面建议入口" })).toHaveTextContent("只跳转页面");
    expect(screen.queryByRole("button", { name: "用我的素材，做一条产品介绍" })).not.toBeInTheDocument();
    expect(mocks.createNew).not.toHaveBeenCalled(); expect(mocks.beginTurn).not.toHaveBeenCalled();
    expect(mocks.sendWorkspace).not.toHaveBeenCalled();
  });

  it.each([
    ["topic", "帮我梳理选题方向"], ["script", "把文案写得更简洁"],
    ["narration", "先解释当前配音进度"], ["montage", "字幕放大一点"], ["review", "先解释当前成片检查结果"],
  ])("fills a draft appropriate to task stage %s without an operation", (stage, suggestion) => {
    render(<HomeCommandComposer task={{ id: "task-fixture", stage, name: "fixture" } as CreationTask} />);
    fireEvent.click(screen.getByRole("button", { name: suggestion }));
    expect(mocks.setDraftText).toHaveBeenCalledWith(suggestion);
    expect(mocks.beginTurn).not.toHaveBeenCalled(); expect(mocks.sendTask).not.toHaveBeenCalled();
    if (stage !== "montage") expect(screen.queryByRole("button", { name: "字幕放大一点" })).not.toBeInTheDocument();
    expect(screen.getByText("点击建议只填入草稿，发送后才处理。")).toBeInTheDocument();
  });

  it("keeps the home entry as draft suggestions and avoids inventing queries on unknown pages", () => {
    const view = render(<HomeCommandComposer pageContext={{ pathname: "/" }} />);
    fireEvent.click(screen.getByRole("button", { name: "用我的素材，做一条产品介绍" }));
    expect(mocks.setDraftText).toHaveBeenCalledWith("用我的素材，做一条产品介绍");
    view.rerender(<HomeCommandComposer pageContext={{ pathname: "/unknown" }} />);
    expect(screen.queryByRole("navigation", { name: "当前页面建议入口" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "用我的素材，做一条产品介绍" })).not.toBeInTheDocument();
    expect(mocks.sendWorkspace).not.toHaveBeenCalled();
  });

  it("shows material label drafts only for an actual selection, without changing the N06 submission route", () => {
    const onResult = vi.fn();
    const view = render(<HomeCommandComposer materialContext={{ selection: null, label: null, onResult }} />);
    expect(screen.queryByRole("button", { name: "把我的标签改成产品演示" })).not.toBeInTheDocument();
    view.rerender(<HomeCommandComposer materialContext={{ selection: { scope: "asset", asset_id: "fixture-asset" }, label: "整条素材", onResult }} />);
    fireEvent.click(screen.getByRole("button", { name: "把我的标签改成产品演示" }));
    expect(mocks.setDraftText).toHaveBeenCalledWith("把我的标签改成产品演示");
    expect(mocks.sendMaterial).not.toHaveBeenCalled(); expect(onResult).not.toHaveBeenCalled();
  });

  it("keeps the same home conversation binding and draft across page navigation", () => {
    draftText = "保留这条未发送的要求";
    const view = render(<HomeCommandComposer pageContext={{ pathname: "/settings" }} />);
    view.rerender(<HomeCommandComposer pageContext={{ pathname: "/profiles" }} />);
    expect(screen.getByRole("textbox", { name: "制作要求" })).toHaveValue(draftText);
    for (const [binding] of mocks.useConversation.mock.calls) expect(binding).toEqual({ scope_kind: "home", scope_id: "home" });
    expect(mocks.setDraftText).not.toHaveBeenCalled(); expect(mocks.createNew).not.toHaveBeenCalled();
    expect(screen.queryByRole("navigation", { name: "当前页面建议入口" })).not.toBeInTheDocument();
  });

  it("keeps existing Markdown history across navigation without repeating welcome entries", () => {
    conversation.turns = [{ id: "saved-turn", sequence: 1, user_message: "已发送", assistant_text: "已保存 **答复**", status: "completed",
      created_at: "2026-10-09T00:00:00Z", updated_at: "2026-10-09T00:00:00Z", events: [], result: null }];
    const view = render(<HomeCommandComposer pageContext={{ pathname: "/settings" }} />);
    view.rerender(<HomeCommandComposer pageContext={{ pathname: "/members" }} />);
    expect(screen.getByText("答复").tagName).toBe("STRONG");
    expect(screen.queryByRole("navigation", { name: "当前页面建议入口" })).not.toBeInTheDocument();
    expect(screen.queryByText("点击建议只填入草稿，发送后才处理。")).not.toBeInTheDocument();
    expect(mocks.createNew).not.toHaveBeenCalled(); expect(mocks.setDraftText).not.toHaveBeenCalled();
  });

  it("waits for conversation restoration before showing actionable suggestions", () => {
    loading = true; render(<HomeCommandComposer pageContext={{ pathname: "/" }} />);
    expect(screen.queryByRole("button", { name: "用我的素材，做一条产品介绍" })).not.toBeInTheDocument();
  });
});
