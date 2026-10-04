import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { KnowledgeItemDetail } from "@/lib/source-api";
import type { ScriptLibraryEntry, ScriptLibrarySource } from "@/lib/types";

const api = vi.hoisted(() => ({
  listScriptLibraryEntries: vi.fn(),
  getScriptLibrarySources: vi.fn(),
  reuseScriptLibraryEntry: vi.fn(),
  getKnowledgeItem: vi.fn(),
}));
vi.mock("@/lib/api", () => api);
vi.mock("@/lib/source-api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/source-api")>();
  return { ...actual, getKnowledgeItem: api.getKnowledgeItem };
});
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("next/link", () => ({ default: ({ children, ...props }: { children: ReactNode }) => <a {...props}>{children}</a> }));
vi.mock("@/components/AppShell", () => ({ AppShell: ({ children }: { children: ReactNode }) => <div>{children}</div> }));

import { ScriptLibraryPanel } from "@/app/knowledge/KnowledgeWorkspace";

const entry: ScriptLibraryEntry = {
  id: "entry-1", name: "夏季文案", category: "促销", version: 2, supersedes_entry_id: null,
  source_script_id: "script-1", source_task_id: "task-1", source_run_id: "run-1", source_topic_id: "topic-1",
  title: "夏季新品", language: "zh-CN",
  sections: [{ id: "s1", title: "开场", content: "夏季新品上市。", visual_prompt: "", reference_ids: ["knowledge:ki-one:2"] }],
  created_at: "2026-10-01T00:00:00Z", updated_at: "2026-10-01T00:00:00Z",
};

function source(overrides: Partial<ScriptLibrarySource>): ScriptLibrarySource {
  return {
    reference_id: "knowledge:ki-one:2", status: "readable", title: "设备说明书",
    version_or_identity: "2", source_url: null,
    cited_version: "2", cited_source_url: null, cited_fetched_at: null,
    cited_content_hash: "hash-v2", locator: null,
    ...overrides,
  };
}

function detail(overrides: Partial<KnowledgeItemDetail> = {}): KnowledgeItemDetail {
  return {
    id: "ki-one", workspace_id: "workspace-one", content_type: "file", title: "设备说明书",
    body: "当前版正文：先通电。", file_reference: "/manual.pdf", authorization: "内部使用",
    ownership: "我的内容", source_item_id: "source-one", version: "3", content_hash: "hash-v3",
    sections: [{ locator: "第3段", title: "新版", text: "当前版第3段" }],
    parameters: [{ key: "净重", value: "700", unit: "g", locator: "第3段" }],
    fetched_at: null, source_url: null, created_at: "2026-10-03T00:00:00Z",
    versions: [
      { version: "2", content_hash: "hash-v2", source_item_id: "source-one", title: "设备说明书",
        body: "旧版正文：先检查电源。", file_reference: "/manual.pdf",
        sections: [{ locator: "第2页", title: "基本参数", text: "第2页 净重 600g" }],
        parameters: [{ key: "净重", value: "600", unit: "g", locator: "第2页" }],
        fetched_at: null, source_url: null, saved_at: "2026-10-02T00:00:00Z" },
    ],
    ...overrides,
  };
}

async function openEntry(sources: ScriptLibrarySource[]) {
  api.listScriptLibraryEntries.mockResolvedValue({ items: [entry], total_count: 1 });
  api.getScriptLibrarySources.mockResolvedValue(sources);
  render(<ScriptLibraryPanel />);
  fireEvent.click(await screen.findByRole("button", { name: /夏季文案/ }));
  await screen.findByText("事实出处（实时核对）");
}

beforeEach(() => {
  Object.values(api).forEach((mock) => mock.mockReset());
});

describe("文案引用回查", () => {
  it("reads the exact cited version with its page and parameter locators", async () => {
    api.getKnowledgeItem.mockResolvedValue(detail());
    await openEntry([source({})]);
    fireEvent.click(screen.getByRole("button", { name: "回查固定版本原文" }));
    const trace = await screen.findByText(/引用版本 2 · 指纹 hash-v2/);
    expect(within(trace.parentElement!).getByText(/第2页 基本参数/)).toBeInTheDocument();
    // 页码出处与参数都来自被引用的那一版，不是当前版。
    expect(within(trace.parentElement!).getByText("第2页")).toBeInTheDocument();
    expect(within(trace.parentElement!).getByText("600")).toBeInTheDocument();
    expect(within(trace.parentElement!).getByText(/资料内的指令不会被执行/)).toBeInTheDocument();
    expect(screen.queryByText(/当前版第3段/)).not.toBeInTheDocument();
  });

  it("does not substitute the current version when the cited one is gone", async () => {
    api.getKnowledgeItem.mockResolvedValue(detail({ versions: [] }));
    await openEntry([source({})]);
    fireEvent.click(screen.getByRole("button", { name: "回查固定版本原文" }));
    await screen.findByText(/该引用绑定的版本已不在可回查范围/);
    expect(screen.queryByText(/当前版正文/)).not.toBeInTheDocument();
  });

  it("still traces the cited old version after the current one advanced", async () => {
    // version_changed 只是当前版本不同或授权变化，不等于引用不可读：
    // 旧版快照还在时就应该能回查当时原文，不能拿授权失效当理由阻断。
    api.getKnowledgeItem.mockResolvedValue(detail());
    // 该场景只看回查本身，摘要行不重复断言。
    await openEntry([source({ status: "version_changed", version_or_identity: "3", cited_version: null, cited_content_hash: null })]);
    fireEvent.click(screen.getByRole("button", { name: "回查固定版本原文" }));
    await screen.findByText(/以下仍是引用当时那一版的原文/);
    const trace = screen.getByText(/引用版本 2 · 指纹 hash-v2/).parentElement!;
    expect(within(trace).getByText(/第2页 基本参数/)).toBeInTheDocument();
    // 当前版正文不会被拿来冒充引用当时的内容。
    expect(screen.queryByText(/当前版第3段/)).not.toBeInTheDocument();
  });

  it("shows the fixed cited version facts and the unbound locator state on the row", async () => {
    // 摘要行直接显示服务端按引用版本回填的事实；没有绑定定位时明说未绑定。
    await openEntry([source({ cited_source_url: "https://example.test/v2", cited_fetched_at: "2026-10-02T08:00:00Z" })]);
    const row = screen.getByText(/knowledge:ki-one:2 · 设备说明书/).parentElement!;
    expect(within(row).getByText(/引用版本 2 · 指纹 hash-v2/)).toBeInTheDocument();
    expect(within(row).getByText(/抓取于/)).toBeInTheDocument();
    expect(within(row).getByRole("link", { name: "该版原始来源" })).toHaveAttribute("href", "https://example.test/v2");
    expect(within(row).getByText(/未绑定具体段落定位/)).toBeInTheDocument();
  });

  it("locates the cited paragraph and parameter by the locator the source provides", async () => {
    api.getKnowledgeItem.mockResolvedValue(detail());
    await openEntry([{ ...source({}), locator: "第2页" }]);
    fireEvent.click(screen.getByRole("button", { name: "回查固定版本原文" }));
    const hit = (await screen.findByText("命中定位 第2页")).parentElement!;
    // 命中定位给出该段原文与该定位下的参数，不拿整版代替。
    expect(within(hit).getByText(/第2页 净重 600g/)).toBeInTheDocument();
    expect(within(hit).getByText("600")).toBeInTheDocument();
  });

  it("reports a locator that is not in the cited version instead of guessing a page", async () => {
    api.getKnowledgeItem.mockResolvedValue(detail());
    await openEntry([{ ...source({}), locator: "第9页" }]);
    fireEvent.click(screen.getByRole("button", { name: "回查固定版本原文" }));
    await screen.findByText(/引用绑定的定位 第9页 不在这一版里/);
  });

  it("shows the failure state and allows retrying", async () => {
    api.getKnowledgeItem.mockRejectedValueOnce(new Error("boom")).mockResolvedValueOnce(detail());
    await openEntry([source({})]);
    fireEvent.click(screen.getByRole("button", { name: "回查固定版本原文" }));
    await screen.findByText("回查失败，请稍后重试。");
  });

  it("leaves non-knowledge citations to their own source link", async () => {
    await openEntry([source({ reference_id: "source:source-one", source_url: "https://example.test/doc" })]);
    expect(screen.getByRole("link", { name: "原始来源" })).toHaveAttribute("href", "https://example.test/doc");
    expect(screen.queryByRole("button", { name: "回查固定版本原文" })).not.toBeInTheDocument();
  });
});