import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { AnchorHTMLAttributes, ImgHTMLAttributes } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const identity = vi.hoisted(() => ({ role: null as "owner" | "member" | null, ready: true, error: null as string | null }));
const membersApi = vi.hoisted(() => vi.fn());

vi.mock("next/link", () => ({
  default: ({ children, href, ...props }: AnchorHTMLAttributes<HTMLAnchorElement>) => <a href={href} {...props}>{children}</a>,
}));
vi.mock("next/image", () => ({
  default: ({ alt, ...props }: ImgHTMLAttributes<HTMLImageElement>) => <img alt={alt} {...props} />,
}));
vi.mock("next/navigation", () => ({
  usePathname: () => "/",
  useRouter: () => ({ back: vi.fn(), push: vi.fn() }),
  useSearchParams: () => new URLSearchParams(""),
}));
vi.mock("@/components/HelpPanel", () => ({ HelpPanel: () => null }));
vi.mock("@/components/KeyboardShortcuts", () => ({ KeyboardShortcuts: () => null }));
vi.mock("@/lib/helpPanel", () => ({ toggleHelpPanel: vi.fn() }));
vi.mock("@/lib/api", () => ({ listLocalMembers: membersApi,
  listCreationTasks: vi.fn().mockResolvedValue({ items: [], total_count: 0 }), getRuntimeSettings: vi.fn().mockResolvedValue({ status: { llm: { configured: false } } }),
  getCurrentAssistantConversation: vi.fn(async (scope: import("@/lib/types").AssistantConversationScope) => ({
    id: `conversation-${scope.scope_kind}-${scope.scope_id}`, workspace_id: "workspace-1", ...scope,
    title: "新对话", draft_text: "", draft_version: 0, created_at: "2026-10-09T00:00:00Z", updated_at: "2026-10-09T00:00:00Z", turns: [],
  } satisfies import("@/lib/types").AssistantConversation)),
  listAssistantConversations: vi.fn().mockResolvedValue({ items: [] }),
  getAssistantCapabilities: vi.fn().mockResolvedValue({
    application_commands: ["/new", "/name <名称>", "/resume <会话ID>", "/help"],
    runtime: { owner: "pi-coding-agent-sdk", version: "1.3.0", status: "ready", reason: "已就绪" },
    pi_tui_commands: { available: false, reason: "终端命令不在此运行", commands: [] },
    extension_commands: { available: false, reason: "未接通" }, skills: { available: true, reason: "按需加载", names: ["local-edit"] }, prompt_templates: { available: true, reason: "按需加载", names: ["review-result"] }, business_tools: {},
  } satisfies import("@/lib/types").AssistantCapabilities),
}));
vi.mock("@/hooks/useLocalMemberIdentity", () => ({
  useLocalMemberIdentity: () => ({ token: "tok", role: identity.role, ready: identity.ready, error: identity.error }),
}));


import { AppShell } from "@/components/AppShell";

function openPalette() {
  fireEvent.click(screen.getAllByRole("button", { name: "搜索" })[0]);
  return screen.getByRole("dialog", { name: "搜索页面和创作任务" });
}

describe("real avatar group and existing theme controls", () => {
  beforeEach(() => {
    identity.role = null; identity.ready = true; identity.error = null;
    localStorage.clear();
    localStorage.setItem("video-editor-left-collapsed", "false");
    membersApi.mockReset();membersApi.mockResolvedValue({members:[{member_id:"member-a",name:"测试创作者",role:"owner",created_at:"2026-10-10"}]});
    vi.stubEnv("NEXT_PUBLIC_AUTH_MODE", "local");
    Object.defineProperty(window, "matchMedia", { configurable: true, value: () => ({ addEventListener: vi.fn(), matches: false, removeEventListener: vi.fn() }) });
  });
  it("shows one actual member without adding empty slots, photographs or status dots", async () => {
    identity.role="owner";render(<AppShell creationTasks={[]}><p>页面内容</p></AppShell>);
    const nav=screen.getByRole("navigation", {name:"创作者与设备设置"});
    const group=await within(nav).findByRole("link",{name:"1位创作者，打开成员设置"});expect(group).toHaveAttribute("href","/settings?section=creators");
    expect(group.querySelectorAll("[data-avatar]")).toHaveLength(1);expect(group.querySelector("[data-avatar-count]")).toBeNull();expect(group).toHaveAttribute("title", "测试创作者；不会切换当前身份");
    expect(within(nav).queryByRole("img")).not.toBeInTheDocument();expect(within(nav).queryByText(/成员空位/)).not.toBeInTheDocument();expect(within(nav).getAllByRole("link")).toHaveLength(2);
    expect(within(nav).getByRole("button",{name:"切换明暗主题"})).toBeInTheDocument();
    openPalette();expect(screen.getByRole("option", {name:/成员与空间/})).toBeInTheDocument();
  });
  it("shows at most three overlapping actual members and the real remaining count for five",async()=>{
    membersApi.mockResolvedValue({members:Array.from({length:5},(_,index)=>({member_id:`member-${index}`,name:`创作者${index+1}`,role:index===0?"owner":"member",created_at:"2026-10-10"}))});
    render(<AppShell creationTasks={[]}><p>页面内容</p></AppShell>);
    const group=await screen.findByRole("link",{name:"5位创作者，打开成员设置"});expect(group.querySelectorAll("[data-avatar]")).toHaveLength(3);expect(group.querySelector("[data-avatar-count]")).toHaveTextContent("+2");
    expect(group).toHaveAttribute("title","创作者1、创作者2、创作者3、创作者4、创作者5；不会切换当前身份");expect(membersApi).toHaveBeenCalledTimes(1);
  });
  it("renders no avatars when the actual roster is empty",async()=>{
    membersApi.mockResolvedValue({members:[]});render(<AppShell creationTasks={[]}><p>页面内容</p></AppShell>);
    const nav=screen.getByRole("navigation", {name:"创作者与设备设置"});await waitFor(()=>expect(within(nav).getByRole("link",{name:"成员设置"})).toHaveAttribute("title","尚无成员，打开成员设置"));
    expect(nav.querySelector("[data-avatar-group]")).toBeNull();expect(nav.querySelector("[data-avatar]")).toBeNull();expect(within(nav).getByRole("link",{name:"设备设置"})).toBeInTheDocument();
  });
  it("does not offer owner management to a member",()=>{
    identity.role="member";render(<AppShell creationTasks={[]}><p>页面内容</p></AppShell>);openPalette();expect(screen.queryByRole("option",{name:/成员与空间/})).not.toBeInTheDocument();
  });
  it("does not invent avatars while the identity or directory is unavailable",()=>{
    identity.error="身份读取失败";render(<AppShell creationTasks={[]}><p>页面内容</p></AppShell>);
    const nav=screen.getByRole("navigation", {name:"创作者与设备设置"});expect(within(nav).getByRole("link",{name:"名册暂不可读"})).toHaveAttribute("href","/settings?section=creators");expect(nav.querySelector("[data-avatar]")).toBeNull();
  });
  it("keeps theme and settings discoverable with navigation collapsed and uses the same theme data",()=>{
    render(<AppShell creationTasks={[]}><p>页面内容</p></AppShell>);fireEvent.click(screen.getByRole("button",{name:"收起导航栏"}));
    expect(screen.queryByRole("navigation", {name:"创作者与设备设置"})).not.toBeInTheDocument();expect(screen.getByRole("link",{name:"设备设置"})).toHaveAttribute("href","/settings");
    fireEvent.click(screen.getByRole("button",{name:"切换明暗主题"}));expect(localStorage.getItem("ai-video-diary-theme")).toBe("dark");expect(document.documentElement.dataset.theme).toBe("dark");expect(document.documentElement.dataset.themeMode).toBe("dark");
    fireEvent.click(screen.getByRole("button",{name:"切换明暗主题"}));expect(localStorage.getItem("ai-video-diary-theme")).toBe("light");expect(document.documentElement.dataset.theme).toBe("light");
  });
});
