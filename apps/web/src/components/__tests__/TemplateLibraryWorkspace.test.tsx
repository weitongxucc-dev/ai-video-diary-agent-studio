import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { VideoTemplate } from "@/lib/types";
const setVisibility = vi.fn();
const createVersion = vi.fn();
vi.mock("@/lib/video-template-api", () => ({ createVideoTemplateVersion: (...args: unknown[]) => createVersion(...args) }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }), useSearchParams: () => new URLSearchParams() }));
vi.mock("@/lib/preflight-draft", async importOriginal => ({ ...await importOriginal<typeof import("@/lib/preflight-draft")>(), readPreflightDraftScope: vi.fn().mockResolvedValue({ memberId: "local:member", workspaceId: "space" }) }));
beforeEach(() => { sessionStorage.clear(); createVersion.mockReset(); });
vi.mock("@/lib/api", () => ({ setVideoTemplateVisibility: (...args: unknown[]) => setVisibility(...args) }));
vi.mock("@/components/AppShell", () => ({ AppShell: ({ children, inspector }: { children: React.ReactNode; inspector: React.ReactNode }) => <>{children}{inspector}</> }));
import { WorkspacePageContext } from "@/components/workbench/context";
import { TemplateLibraryWorkspace } from "@/components/TemplateLibraryWorkspace";
const template: VideoTemplate = {
  visibility: "private", can_edit: true,
  id: "template_1", version: 1, name: "视频日记", origin: "user", source_task_id: "task_1", source_timeline_version_id: "timeline_1", created_at: "2026-09-12",
  settings: { language: "zh-CN", aspect_ratio: "9:16", duration_target_seconds: 30, voice: null, style_instruction: "先讲结论", script_section_count: 3, fit_mode: "fit", subtitle_style: {} },
};
const colorTemplate: VideoTemplate = {
  ...template,
  settings: { ...template.settings, aspect_ratio: "16:9", color_style: { exposure: .5, contrast: 1.1, saturation: .8, temperature: .2 } },
};
function renderWorkspace(node: React.ReactElement) {
  const result = render(node);
  const open = () => { const button = screen.queryByRole("button", { name: "查看详情" }); if (button) fireEvent.click(button); };
  open();
  return { ...result, rerender: (next: React.ReactElement) => { result.rerender(next); open(); } };
}

describe("TemplateLibraryWorkspace", () => {
  it.each([
    { profile_id: "voice_1", version: "v1" },
    { profile_id: "voice_missing", version: "missing-version" },
  ])("reports that the original voice name is unreadable without exposing or replacing $profile_id", async (voice) => {
    renderWorkspace(<TemplateLibraryWorkspace offline={false} templates={[{ ...template, settings: { ...template.settings, voice } }]} />);
    expect(screen.getByText("无法读取原声音名称")).toBeInTheDocument();
    expect(screen.queryByText(`${voice.profile_id} · ${voice.version}`)).not.toBeInTheDocument();
    expect(await screen.findByRole("link", { name: /用这个做/ })).toHaveAttribute("href", "/production?new=1&template_id=template_1&template_version=1");
  });
  it("still requires a voice selection when the template has no voice configuration", () => {
    renderWorkspace(<TemplateLibraryWorkspace offline={false} templates={[template]} />);
    expect(screen.getByText("制作前选择可用声音")).toBeInTheDocument();
    expect(screen.queryByText("无法读取原声音名称")).not.toBeInTheDocument();
  });
  it("shows saved color parameters and distinguishes mixed and legacy templates", () => {
    const { rerender } = renderWorkspace(<TemplateLibraryWorkspace offline={false} templates={[colorTemplate]} />);
    expect(screen.getByText("曝光 0.5 · 对比度 1.1 · 饱和度 0.8 · 冷暖 0.2")).toBeInTheDocument();
    rerender(<TemplateLibraryWorkspace offline={false} templates={[{ ...template, settings: { ...template.settings, color_style: null } }]} />);
    expect(screen.getByText("原镜头调色不同，不统一复用")).toBeInTheDocument();
    rerender(<TemplateLibraryWorkspace offline={false} templates={[template]} />);
    expect(screen.getByText("未保存调色，使用新素材原色")).toBeInTheDocument();
  });
  it("selects immutable versions and routes with identity instead of copied goals", async () => {
    renderWorkspace(<TemplateLibraryWorkspace offline={false} templates={[template, { ...template, version: 2 }]} />);
    expect(await screen.findByRole("link", { name: /用这个做/ })).toHaveAttribute("href", "/production?new=1&template_id=template_1&template_version=2");
    fireEvent.click(screen.getByRole("button", { name: /版本记录/ }));
    fireEvent.click(screen.getByRole("button", { name: /视频日记 · 第1版/ }));
    expect(await screen.findByRole("link", { name: /用这个做/ })).toHaveAttribute("href", "/production?new=1&template_id=template_1&template_version=1");
    expect(screen.getByRole("link", { name: "查看原片" })).toHaveAttribute("href", "/tasks/task_1");
  });
  it("keeps built-in seeds separate from saved versions", async () => {
    renderWorkspace(<TemplateLibraryWorkspace offline={false} templates={[{ ...template, origin: "built_in" }]} />);
    expect(screen.queryByRole("link", { name: /用这个做/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /内置模板/ }));
    const details = screen.queryByRole("button", { name: "查看详情" }); if (details) fireEvent.click(details);
    expect(await screen.findByRole("link", { name: /用这个做/ })).toBeInTheDocument();
  });
  it("suppresses detail and create action while offline even with leftover templates, then recovers", async () => {
    const { rerender } = renderWorkspace(<TemplateLibraryWorkspace offline={true} templates={[colorTemplate]} />);
    expect(screen.getByText("暂时无法读取模板")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /用这个做/ })).not.toBeInTheDocument();
    expect(screen.queryByText("当前未复用")).not.toBeInTheDocument();
    expect(screen.queryByText("曝光 0.5 · 对比度 1.1 · 饱和度 0.8 · 冷暖 0.2")).not.toBeInTheDocument();
    expect(document.querySelectorAll("[data-offline-inert]")).toHaveLength(1);
    expect(screen.queryByRole("button", { name: /视频日记 · 第1版/ })).not.toBeInTheDocument();
    rerender(<TemplateLibraryWorkspace offline={false} templates={[colorTemplate]} />);
    expect(await screen.findByRole("link", { name: /用这个做/ })).toBeInTheDocument();
    expect(screen.getByText("曝光 0.5 · 对比度 1.1 · 饱和度 0.8 · 冷暖 0.2")).toBeInTheDocument();
    expect(document.querySelectorAll("[data-offline-inert]")).toHaveLength(0);
  });
  it("searches by name and clears back to the full list", () => {
    const product = { ...template, id: "template_2", name: "产品讲解" };
    renderWorkspace(<TemplateLibraryWorkspace offline={false} templates={[template, product]} />);
    const input = screen.getByRole("searchbox", { name: "搜索模板名称" });
    fireEvent.change(input, { target: { value: "日记" } });
    expect(screen.getByRole("button", { name: /视频日记/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /产品讲解/ })).not.toBeInTheDocument();
    fireEvent.change(input, { target: { value: "" } });
    expect(screen.getByRole("button", { name: /视频日记/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /产品讲解/ })).toBeInTheDocument();
  });
  it("shows a distinct no-results empty state and clears it", () => {
    renderWorkspace(<TemplateLibraryWorkspace offline={false} templates={[template]} />);
    const input = screen.getByRole("searchbox", { name: "搜索模板名称" });
    fireEvent.change(input, { target: { value: "不存在" } });
    expect(screen.getByText("没有找到名称匹配“不存在”的模板。")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "清除搜索" }));
    expect(screen.getByRole("button", { name: /视频日记 · 第1版/ })).toBeInTheDocument();
  });
  it("shows the mine empty state when nothing is saved", () => {
    renderWorkspace(<TemplateLibraryWorkspace offline={false} templates={[]} />);
    expect(screen.getByText(/还没有保存的模板。/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "查看创作任务" })).toHaveAttribute("href", "/production");
  });
  it("shows the built-in empty state separately", () => {
    renderWorkspace(<TemplateLibraryWorkspace offline={false} templates={[template]} />);
    fireEvent.click(screen.getByRole("button", { name: /内置模板/ }));
    const details = screen.queryByRole("button", { name: "查看详情" }); if (details) fireEvent.click(details);
    expect(screen.getByText(/暂无可用的内置模板。/)).toBeInTheDocument();
  });
  it("shows the versions empty state separately", () => {
    renderWorkspace(<TemplateLibraryWorkspace offline={false} templates={[]} />);
    fireEvent.click(screen.getByRole("button", { name: /版本记录/ }));
    expect(screen.getByText(/还没有模板版本记录。/)).toBeInTheDocument();
  });
  it("shares a user template with the team and shows the visibility the API returned", async () => {
    setVisibility.mockResolvedValue({ item: { ...template, visibility: "shared" } });
    renderWorkspace(<TemplateLibraryWorkspace offline={false} templates={[template]} />);

    expect(screen.getByText("仅自己可见")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "共享给团队" }));

    expect(await screen.findByText("团队共享")).toBeInTheDocument();
    expect(setVisibility).toHaveBeenCalledWith("template_1", "shared");
  });

  it("does not show a template as shared when the save fails", async () => {
    setVisibility.mockRejectedValue(new Error("本地服务没有保存成功"));
    renderWorkspace(<TemplateLibraryWorkspace offline={false} templates={[template]} />);

    fireEvent.click(screen.getByRole("button", { name: "共享给团队" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("本地服务没有保存成功");
    expect(screen.getByText("仅自己可见")).toBeInTheDocument();
  });

  it("offers revocation only for user templates", () => {
    renderWorkspace(<TemplateLibraryWorkspace offline={false} templates={[{ ...template, origin: "built_in" }]} />);
    fireEvent.click(screen.getByRole("button", { name: /内置模板/ }));
    const details = screen.queryByRole("button", { name: "查看详情" }); if (details) fireEvent.click(details);

    expect(screen.queryByRole("button", { name: "共享给团队" })).not.toBeInTheDocument();
    expect(screen.getByText("内置模板由系统提供，不单独设置可见范围。")).toBeInTheDocument();
  });
});


describe("template detail action and structure", () => {
  it("puts the immutable version action in the fixed dialog footer without a duplicate body action", async () => {
    renderWorkspace(<TemplateLibraryWorkspace offline={false} templates={[template, { ...template, version: 2 }]} />);
    const details = screen.getByRole("dialog", { name: "模板详情" });
    const links = await within(details).findAllByRole("link", { name: "使用此模板" });
    expect(links).toHaveLength(1);
    expect(links[0].closest("footer")).not.toBeNull();
    links.forEach(link => expect(link).toHaveAttribute("href", "/production?new=1&template_id=template_1&template_version=2"));
    fireEvent.click(within(details).getByRole("button", { name: "关闭卡片" }));
    fireEvent.click(screen.getByRole("button", { name: /版本记录/ }));
    fireEvent.click(screen.getByRole("button", { name: /视频日记 · 第1版/ }));
    (await within(screen.getByRole("dialog", { name: "模板详情" })).findAllByRole("link", { name: "使用此模板" })).forEach(link => expect(link).toHaveAttribute("href", "/production?new=1&template_id=template_1&template_version=1"));
  });

  it("previews saved built-in structure as labelled example content without claiming a finished video", async () => {
    const builtIn = { ...template, id: "builtin_tool_review", origin: "built_in" as const, name: "AI 工具测评", settings: { ...template.settings, script_structure: ["结论先行", "工具介绍", "实测场景", "坑点", "推荐人群"] } };
    renderWorkspace(<TemplateLibraryWorkspace offline={false} templates={[builtIn]} />);
    fireEvent.click(screen.getByRole("button", { name: /内置模板/ }));
    fireEvent.click(screen.getByRole("button", { name: /AI 工具测评 · 第1版/ }));
    const preview = within(screen.getByRole("dialog", { name: "模板详情" })).getByRole("region", { name: "制作结构预览" });
    expect(within(preview).getAllByRole("listitem")).toHaveLength(5);
    expect(preview).toHaveTextContent("示例文案 · 展示结构，不是成片");
    expect(preview).toHaveTextContent("这款工具适合快速整理初稿");
    expect(document.querySelector("video")).toBeNull();
    (await within(screen.getByRole("dialog", { name: "模板详情" })).findAllByRole("link", { name: "使用此制作结构" })).forEach(link => expect(link).toHaveAttribute("href", "/production?new=1&template_id=builtin_tool_review&template_version=1"));
  });
});


it.each(["product_intro", "customer_faq", "customer_case"])("uses real catalog input requirements and the selected immutable identity for %s", async (id) => {
  const item = { ...template, id: `builtin_${id}`, name: id, origin: "built_in" as const, settings: { ...template.settings, script_structure: ["问题", "依据", "下一步"] } };
  const catalog = [{ template_id: id, name: id, scenario: "已确认场景", recommended_duration: "30-90 秒", topic_structure: [], script_structure: item.settings.script_structure, material_requirements: ["已核实资料与出处"], jianying_style: "tutorial_demo", subtitle_style: "", title_rules: [], cover_copy_rules: [], publish_copy_rules: [], default_feedback_dimensions: [] }];
  renderWorkspace(<TemplateLibraryWorkspace offline={false} templates={[item]} catalog={catalog} />);
  fireEvent.click(screen.getByRole("button", { name: /内置模板/ }));
  fireEvent.click(screen.getByRole("button", { name: new RegExp(`${id} · 第1版`) }));
  const details = screen.getByRole("dialog", { name: "模板详情" });
  expect(within(details).getByRole("region", { name: "必要输入" })).toHaveTextContent("已核实资料与出处");
  (await within(details).findAllByRole("link", { name: "使用此制作结构" })).forEach(link => expect(link).toHaveAttribute("href", `/production?new=1&template_id=builtin_${id}&template_version=1`));
});

describe("template version editing", () => {
  it("saves the exact old version, retains a failed draft, then selects the returned new version", async () => {
    createVersion.mockRejectedValueOnce(new Error("本地服务没有保存成功。"));
    createVersion.mockResolvedValueOnce({ ...template, can_edit: true, version: 2, name: "新名称", settings: { ...template.settings, style_instruction: "短句" } });
    renderWorkspace(<TemplateLibraryWorkspace offline={false} templates={[{ ...template, can_edit: true }]} />);
    fireEvent.click(screen.getByRole("button", { name: /版本记录/ }));
    fireEvent.click(screen.getByRole("button", { name: /视频日记 · 第1版/ }));
    const parent = screen.getByRole("dialog", { name: "模板详情" });
    fireEvent.click(screen.getByRole("button", { name: "编辑名称与文案风格" }));
    const child = screen.getByRole("dialog", { name: "编辑模板" });
    expect(child).toHaveAttribute("aria-modal", "true");
    expect(parent).toBeInTheDocument();
    expect(screen.queryByRole("dialog", { name: "模板详情" })).not.toBeInTheDocument();
    fireEvent.change(within(child).getByLabelText("模板名称"), { target: { value: " 新名称 " } });
    fireEvent.change(within(child).getByLabelText("新文案的风格要求"), { target: { value: " 短句 " } });
    fireEvent.click(within(child).getByRole("button", { name: "保存新版本" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("本地服务没有保存成功");
    expect(within(child).getByLabelText("模板名称")).toHaveValue(" 新名称 ");
    expect(createVersion).toHaveBeenLastCalledWith("template_1", { expected_version: 1, name: "新名称", style_instruction: "短句" });
    fireEvent.click(within(child).getByRole("button", { name: "保存新版本" }));
    expect(await screen.findByRole("status")).toHaveTextContent("已保存「新名称」第2版");
    expect(screen.queryByRole("dialog", { name: "编辑模板" })).toBeNull();
    expect(within(screen.getByRole("dialog", { name: "模板详情" })).getByRole("link", { name: "使用此模板" })).toHaveAttribute("href", "/production?new=1&template_id=template_1&template_version=2");
    expect(screen.getByRole("button", { name: /视频日记 · 第1版/ })).toBeInTheDocument();
  });
  it("pins open details through search and returns from cancel with the original version intact", () => {
    renderWorkspace(<TemplateLibraryWorkspace offline={false} templates={[{ ...template, can_edit: true }, { ...template, id: "other", name: "其他模板" }]} />);
    fireEvent.change(screen.getByRole("searchbox", { name: "搜索模板名称" }), { target: { value: "其他" } });
    expect(within(screen.getByRole("dialog", { name: "模板详情" })).getByRole("heading", { name: "视频日记 · 第1版" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "编辑名称与文案风格" }));
    fireEvent.change(screen.getByLabelText("模板名称"), { target: { value: "未保存" } });
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(createVersion).not.toHaveBeenCalled();
    expect(screen.queryByRole("dialog", { name: "编辑模板" })).toBeNull();
    expect(screen.getByRole("searchbox")).toHaveValue("其他");
    expect(within(screen.getByRole("dialog", { name: "模板详情" })).getByRole("heading", { name: "视频日记 · 第1版" })).toBeInTheDocument();
  });
  it("does not retarget another detail after cancelling an in-flight save", async () => {
    let finish!: (saved: VideoTemplate) => void;
    createVersion.mockReturnValue(new Promise(resolve => { finish = resolve; }));
    renderWorkspace(<TemplateLibraryWorkspace offline={false} templates={[{ ...template, can_edit: true }, { ...template, id: "other", name: "其他模板" }]} />);
    fireEvent.click(screen.getByRole("button", { name: "编辑名称与文案风格" }));
    fireEvent.click(screen.getByRole("button", { name: "保存新版本" }));
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    fireEvent.click(screen.getByRole("button", { name: /其他模板 · 第1版/ }));
    await act(async () => finish({ ...template, version: 2, can_edit: true, name: "已保存版本" }));
    expect(screen.queryByRole("dialog", { name: "编辑模板" })).toBeNull();
    expect(within(screen.getByRole("dialog", { name: "模板详情" })).getByRole("heading", { name: "其他模板 · 第1版" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /已保存版本 · 第2版/ })).toBeInTheDocument();
  });
  it("offers no version editor for a shared reader or builtin", () => {
    renderWorkspace(<TemplateLibraryWorkspace offline={false} templates={[{ ...template, visibility: "shared", can_edit: false }]} />);
    expect(screen.queryByRole("button", { name: "编辑名称与文案风格" })).toBeNull();
    expect(screen.getByText("此模板只读；可用固定版本创作。")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "收回为仅自己可见" })).toBeNull();
  });
});


it("hides both template dialogs without discarding the unsaved version draft", async () => {
  const slot = { id: "/templates", href: "/templates", updateShell: vi.fn(), viewState: new Map<string, unknown>() };
  const content = (active: boolean) => <WorkspacePageContext.Provider value={{ ...slot, active }}><TemplateLibraryWorkspace offline={false} templates={[template]} /></WorkspacePageContext.Provider>;
  const view = render(content(true));
  fireEvent.click(screen.getByRole("button", { name: "查看详情" }));
  fireEvent.click(screen.getByRole("button", { name: "编辑名称与文案风格" }));
  fireEvent.change(screen.getByLabelText("模板名称"), { target: { value: "保留未保存名称" } });
  fireEvent.change(screen.getByLabelText("新文案的风格要求"), { target: { value: "保留未保存风格" } });
  view.rerender(content(false));
  await vi.waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  view.rerender(content(true));
  const dialog = await screen.findByRole("dialog", { name: "编辑模板" });
  expect(within(dialog).getByLabelText("模板名称")).toHaveValue("保留未保存名称");
  expect(within(dialog).getByLabelText("新文案的风格要求")).toHaveValue("保留未保存风格");
  expect(createVersion).not.toHaveBeenCalled();
});
