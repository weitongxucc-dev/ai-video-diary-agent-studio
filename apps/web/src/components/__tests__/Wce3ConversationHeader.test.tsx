import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useMemo, useState, type AnchorHTMLAttributes, type ImgHTMLAttributes } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AssistantConversation } from "@/lib/types";
import { AppShell } from "../AppShell";
import { AssistantConversationPanel } from "../AssistantConversationPanel";

const fixtures = vi.hoisted(() => ({ pathname: "/", listTasks: vi.fn() }));

vi.mock("next/navigation", () => ({
  usePathname: () => fixtures.pathname,
  useRouter: () => ({ push: vi.fn(), back: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock("next/link", () => ({ default: ({ children, href, ...props }: AnchorHTMLAttributes<HTMLAnchorElement>) => <a href={href} {...props}>{children}</a> }));
vi.mock("next/image", () => ({ default: ({ alt, ...props }: ImgHTMLAttributes<HTMLImageElement>) => <img alt={alt} {...props} /> }));
vi.mock("@/components/KeyboardShortcuts", () => ({ KeyboardShortcuts: () => null }));
vi.mock("@/components/HelpPanel", () => ({ HelpPanel: () => null }));
vi.mock("@/lib/api", async importOriginal => ({
  ...await importOriginal<typeof import("@/lib/api")>(),
  listCreationTasks: fixtures.listTasks,
  listLocalMembers: vi.fn().mockResolvedValue({ members: [] }),
  getAssistantCapabilities: vi.fn().mockResolvedValue({
    application_commands: [], runtime: { owner: "test", version: "test", status: "ready", reason: "" },
    pi_tui_commands: { available: false, reason: "" }, extension_commands: { available: false, reason: "" },
    skills: { available: false, names: [], reason: "" }, prompt_templates: { available: false, reason: "" }, business_tools: {},
  }),
}));
vi.mock("@/hooks/useLocalMemberIdentity", () => ({ useLocalMemberIdentity: () => ({ ready: true, token: "test", role: "owner", error: null }) }));

function makeConversation(overrides: Partial<AssistantConversation> = {}): AssistantConversation {
  return {
    id: "conversation-one", workspace_id: "workspace-one", scope_kind: "task", scope_id: "task-one",
    title: "片头修订", draft_text: "", draft_version: 0, created_at: "2026-10-09T00:00:00Z", updated_at: "2026-10-09T00:00:00Z", turns: [],
    ...overrides,
  };
}

function HeaderFixture() {
  const [conversation, setConversation] = useState(makeConversation());
  const state = useMemo(() => ({
    conversation, conversations: [], loading: false, error: null, createNew: vi.fn(), resume: vi.fn(), rename: vi.fn(), stop: vi.fn(),
    isCurrentConversation: (id: string) => id === conversation.id,
  }) as never, [conversation]);
  return <>
    <button type="button" onClick={() => setConversation(makeConversation({ id: "conversation-two", title: "新片头", scope_kind: "home", scope_id: "home" }))}>切换会话测试</button>
    <button type="button" onClick={() => setConversation(makeConversation({ id: "conversation-material", title: "素材核对", scope_kind: "home", scope_id: "library" }))}>切换素材会话测试</button>
    <AppShell conversation={<AssistantConversationPanel state={state} input="" onInput={vi.fn()} onSend={vi.fn()} busy={false} configured scopeLabel={conversation.scope_id === "library" ? "素材库" : "工作区对话"} />}>
      <p>当前工作区</p>
    </AppShell>
  </>;
}

describe("WCE-3 会话头部", () => {
  beforeEach(() => {
    fixtures.pathname = "/";
    fixtures.listTasks.mockResolvedValue({ items: [], total_count: 0 });
    Object.defineProperty(window, "matchMedia", { configurable: true, value: () => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() }) });
    localStorage.clear();
  });

  afterEach(() => {
    localStorage.clear();
  });

  it("renders the persisted title and scope, then updates both when the session changes", async () => {
    render(<HeaderFixture />);
    fireEvent.click(screen.getByRole("button", { name: "打开曜核哈力斯" }));
    expect(await screen.findByLabelText("当前会话标题：片头修订")).toBeVisible();
    expect(screen.getByLabelText("当前会话范围：当前作品会话")).toHaveTextContent("当前作品会话");

    fireEvent.click(screen.getByRole("button", { name: "切换会话测试" }));
    await waitFor(() => expect(screen.getByLabelText("当前会话标题：新片头")).toBeVisible());
    expect(screen.getByLabelText("当前会话范围：工作区会话")).toHaveTextContent("工作区会话");

    fireEvent.click(screen.getByRole("button", { name: "切换素材会话测试" }));
    await waitFor(() => expect(screen.getByLabelText("当前会话标题：素材核对")).toBeVisible());
    expect(screen.getByLabelText("当前会话范围：素材会话")).toHaveTextContent("素材会话");
  });

  it("falls back to 新对话 when the persisted title is empty", async () => {
    function EmptyTitleFixture() {
      const state = useMemo(() => ({ conversation: makeConversation({ title: "" }), conversations: [], loading: false, error: null, createNew: vi.fn(), resume: vi.fn(), rename: vi.fn(), stop: vi.fn(), isCurrentConversation: vi.fn(() => true) }) as never, []);
      return <AppShell conversation={<AssistantConversationPanel state={state} input="" onInput={vi.fn()} onSend={vi.fn()} busy={false} configured scopeLabel="工作区对话" />}><p>当前工作区</p></AppShell>;
    }
    render(<EmptyTitleFixture />);
    fireEvent.click(screen.getByRole("button", { name: "打开曜核哈力斯" }));
    await waitFor(() => expect(screen.getByLabelText("当前会话标题：新对话")).toBeVisible());
  });
});
