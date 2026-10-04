import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { VideoTemplate } from "@/lib/types";
const setVisibility = vi.fn();
vi.mock("@/lib/api", () => ({ setVideoTemplateVisibility: (...args: unknown[]) => setVisibility(...args) }));
vi.mock("@/components/AppShell", () => ({ AppShell: ({ children, inspector }: { children: React.ReactNode; inspector: React.ReactNode }) => <>{children}{inspector}</> }));
import { TemplateLibraryWorkspace } from "@/components/TemplateLibraryWorkspace";
const template: VideoTemplate = {
  visibility: "private",
  id: "template_1", version: 1, name: "视频日记", origin: "user", source_task_id: "task_1", source_timeline_version_id: "timeline_1", created_at: "2026-09-12",
  settings: { language: "zh-CN", aspect_ratio: "9:16", duration_target_seconds: 30, voice: null, style_instruction: "先讲结论", script_section_count: 3, fit_mode: "fit", subtitle_style: {} },
};
const colorTemplate: VideoTemplate = {
  ...template,
  settings: { ...template.settings, aspect_ratio: "16:9", color_style: { exposure: .5, contrast: 1.1, saturation: .8, temperature: .2 } },
};
describe("TemplateLibraryWorkspace", () => {
  it.each([
    { profile_id: "voice_1", version: "v1" },
    { profile_id: "voice_missing", version: "missing-version" },
  ])("reports that the original voice name is unreadable without exposing or replacing $profile_id", (voice) => {
    render(<TemplateLibraryWorkspace offline={false} templates={[{ ...template, settings: { ...template.settings, voice } }]} />);
    expect(screen.getByText("无法读取原声音名称")).toBeInTheDocument();
    expect(screen.queryByText(`${voice.profile_id} · ${voice.version}`)).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: /用这个做/ })).toHaveAttribute("href", "/production?new=1&template_id=template_1&template_version=1");
  });
  it("still requires a voice selection when the template has no voice configuration", () => {
    render(<TemplateLibraryWorkspace offline={false} templates={[template]} />);
    expect(screen.getByText("制作前选择可用声音")).toBeInTheDocument();
    expect(screen.queryByText("无法读取原声音名称")).not.toBeInTheDocument();
  });
  it("shows saved color parameters and distinguishes mixed and legacy templates", () => {
    const { rerender } = render(<TemplateLibraryWorkspace offline={false} templates={[colorTemplate]} />);
    expect(screen.getByText("曝光 0.5 · 对比度 1.1 · 饱和度 0.8 · 冷暖 0.2")).toBeInTheDocument();
    rerender(<TemplateLibraryWorkspace offline={false} templates={[{ ...template, settings: { ...template.settings, color_style: null } }]} />);
    expect(screen.getByText("原镜头调色不同，不统一复用")).toBeInTheDocument();
    rerender(<TemplateLibraryWorkspace offline={false} templates={[template]} />);
    expect(screen.getByText("未保存调色，使用新素材原色")).toBeInTheDocument();
  });
  it("selects immutable versions and routes with identity instead of copied goals", () => {
    render(<TemplateLibraryWorkspace offline={false} templates={[template, { ...template, version: 2 }]} />);
    expect(screen.getByRole("link", { name: /用这个做/ })).toHaveAttribute("href", "/production?new=1&template_id=template_1&template_version=2");
    fireEvent.click(screen.getByRole("button", { name: /版本记录/ }));
    fireEvent.click(screen.getByRole("button", { name: /视频日记 · 第1版/ }));
    expect(screen.getByRole("link", { name: /用这个做/ })).toHaveAttribute("href", "/production?new=1&template_id=template_1&template_version=1");
    expect(screen.getByRole("link", { name: "查看原片" })).toHaveAttribute("href", "/tasks/task_1");
  });
  it("keeps built-in seeds separate from saved versions", () => {
    render(<TemplateLibraryWorkspace offline={false} templates={[{ ...template, origin: "built_in" }]} />);
    expect(screen.queryByRole("link", { name: /用这个做/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /内置模板/ }));
    expect(screen.getByRole("link", { name: /用这个做/ })).toBeInTheDocument();
  });
  it("suppresses detail and create action while offline even with leftover templates, then recovers", () => {
    const { rerender } = render(<TemplateLibraryWorkspace offline={true} templates={[colorTemplate]} />);
    expect(screen.getByText("暂时无法读取模板")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /用这个做/ })).not.toBeInTheDocument();
    expect(screen.queryByText("当前未复用")).not.toBeInTheDocument();
    expect(screen.queryByText("曝光 0.5 · 对比度 1.1 · 饱和度 0.8 · 冷暖 0.2")).not.toBeInTheDocument();
    expect(document.querySelectorAll("[data-offline-inert]")).toHaveLength(1);
    expect(screen.queryByRole("button", { name: /视频日记 · 第1版/ })).not.toBeInTheDocument();
    rerender(<TemplateLibraryWorkspace offline={false} templates={[colorTemplate]} />);
    expect(screen.getByRole("link", { name: /用这个做/ })).toBeInTheDocument();
    expect(screen.getByText("曝光 0.5 · 对比度 1.1 · 饱和度 0.8 · 冷暖 0.2")).toBeInTheDocument();
    expect(document.querySelectorAll("[data-offline-inert]")).toHaveLength(0);
  });
  it("searches by name and clears back to the full list", () => {
    const product = { ...template, id: "template_2", name: "产品讲解" };
    render(<TemplateLibraryWorkspace offline={false} templates={[template, product]} />);
    const input = screen.getByRole("searchbox", { name: "搜索模板名称" });
    fireEvent.change(input, { target: { value: "日记" } });
    expect(screen.getByRole("button", { name: /视频日记/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /产品讲解/ })).not.toBeInTheDocument();
    fireEvent.change(input, { target: { value: "" } });
    expect(screen.getByRole("button", { name: /视频日记/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /产品讲解/ })).toBeInTheDocument();
  });
  it("shows a distinct no-results empty state and clears it", () => {
    render(<TemplateLibraryWorkspace offline={false} templates={[template]} />);
    const input = screen.getByRole("searchbox", { name: "搜索模板名称" });
    fireEvent.change(input, { target: { value: "不存在" } });
    expect(screen.getByText("没有找到名称匹配“不存在”的模板。")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "清除搜索" }));
    expect(screen.getByRole("button", { name: /视频日记 · 第1版/ })).toBeInTheDocument();
  });
  it("shows the mine empty state when nothing is saved", () => {
    render(<TemplateLibraryWorkspace offline={false} templates={[]} />);
    expect(screen.getByText(/还没有保存的模板。/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "查看创作任务" })).toHaveAttribute("href", "/production");
  });
  it("shows the built-in empty state separately", () => {
    render(<TemplateLibraryWorkspace offline={false} templates={[template]} />);
    fireEvent.click(screen.getByRole("button", { name: /内置模板/ }));
    expect(screen.getByText(/暂无可用的内置模板。/)).toBeInTheDocument();
  });
  it("shows the versions empty state separately", () => {
    render(<TemplateLibraryWorkspace offline={false} templates={[]} />);
    fireEvent.click(screen.getByRole("button", { name: /版本记录/ }));
    expect(screen.getByText(/还没有模板版本记录。/)).toBeInTheDocument();
  });
  it("shares a user template with the team and shows the visibility the API returned", async () => {
    setVisibility.mockResolvedValue({ item: { ...template, visibility: "shared" } });
    render(<TemplateLibraryWorkspace offline={false} templates={[template]} />);

    expect(screen.getByText("仅自己可见")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "共享给团队" }));

    expect(await screen.findByText("团队共享")).toBeInTheDocument();
    expect(setVisibility).toHaveBeenCalledWith("template_1", "shared");
  });

  it("does not show a template as shared when the save fails", async () => {
    setVisibility.mockRejectedValue(new Error("本地服务没有保存成功"));
    render(<TemplateLibraryWorkspace offline={false} templates={[template]} />);

    fireEvent.click(screen.getByRole("button", { name: "共享给团队" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("本地服务没有保存成功");
    expect(screen.getByText("仅自己可见")).toBeInTheDocument();
  });

  it("offers revocation only for user templates", () => {
    render(<TemplateLibraryWorkspace offline={false} templates={[{ ...template, origin: "built_in" }]} />);
    fireEvent.click(screen.getByRole("button", { name: /内置模板/ }));

    expect(screen.queryByRole("button", { name: "共享给团队" })).not.toBeInTheDocument();
    expect(screen.getByText("内置模板由系统提供，不单独设置可见范围。")).toBeInTheDocument();
  });
});
