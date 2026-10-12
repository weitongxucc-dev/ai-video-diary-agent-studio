import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { AnchorHTMLAttributes, ReactNode } from "react";

const mocks = vi.hoisted(() => ({ get: vi.fn(), update: vi.fn(), export: vi.fn() }));
vi.mock("next/link", () => ({ default: ({ href, children, ...props }: AnchorHTMLAttributes<HTMLAnchorElement>) => <a href={href} {...props}>{children}</a> }));
vi.mock("@/components/AppShell", () => ({ AppShell: ({ children, toolbar, toolbarActions }: { children: ReactNode; toolbar: ReactNode; toolbarActions: ReactNode }) => <><header>{toolbar}{toolbarActions}</header><main>{children}</main></> }));
vi.mock("@/lib/profile-api", () => ({
  getCreatorProfile: mocks.get, updateCreatorProfile: mocks.update, exportCreatorPreferences: mocks.export,
  CreatorProfileApiError: class extends Error { constructor(public status: number, message: string) { super(message); } },
}));
import ProfilePage from "../page";
import { CreatorProfileApiError } from "@/lib/profile-api";

const profile = { id: "private-profile-a", revision: 7, common_expressions: ["说白了"], banned_words: ["众所周知"], style_preferences: { tone: "casual", "视频时长": "通常两分钟", "目标受众": "创业者" }, created_at: "2026-10-11T00:00:00Z", updated_at: "2026-10-11T00:00:00Z" };
beforeEach(() => { vi.resetAllMocks(); mocks.get.mockResolvedValue(profile); mocks.update.mockImplementation(async input => ({ ...profile, ...input, revision: input.expected_revision + 1 })); });
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it("shows custom preferences, edits names and values, and sends the read revision", async () => {
  render(<ProfilePage/>);
  expect(await screen.findByDisplayValue("通常两分钟")).toBeVisible();
  expect(screen.getByDisplayValue("创业者")).toBeVisible();
  expect(screen.queryByText("private-profile-a")).not.toBeInTheDocument();
  expect(screen.queryByText(/revision|JSON|从历史学习/)).not.toBeInTheDocument();
  fireEvent.change(screen.getByRole("textbox", { name: "偏好名称 1" }), { target: { value: "常用视频时长" } });
  fireEvent.change(screen.getByRole("textbox", { name: "偏好内容 常用视频时长" }), { target: { value: "三分钟" } });
  fireEvent.change(screen.getByRole("textbox", { name: "新增偏好名称" }), { target: { value: "画幅" } });
  fireEvent.change(screen.getByRole("textbox", { name: "新增偏好内容" }), { target: { value: "竖屏" } });
  fireEvent.click(screen.getByRole("button", { name: "添加创作偏好" }));
  fireEvent.click(screen.getByRole("button", { name: "保存修改" }));
  await waitFor(() => expect(mocks.update).toHaveBeenCalledWith({ expected_revision: 7, common_expressions: ["说白了"], banned_words: ["众所周知"], style_preferences: { tone: "casual", "常用视频时长": "三分钟", "目标受众": "创业者", "画幅": "竖屏" } }));
  expect(await screen.findByText("创作偏好已保存。")).toBeVisible();
});

it("forgets a custom preference only by saving its removal", async () => {
  render(<ProfilePage/>); await screen.findByDisplayValue("通常两分钟");
  fireEvent.click(screen.getByRole("button", { name: "删除偏好 视频时长" }));
  expect(mocks.update).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "保存修改" }));
  await waitFor(() => expect(mocks.update).toHaveBeenCalledWith(expect.objectContaining({ expected_revision: 7, style_preferences: { tone: "casual", "目标受众": "创业者" } })));
  expect(await screen.findByText("创作偏好已保存。")).toBeVisible();
  expect(screen.queryByDisplayValue("通常两分钟")).not.toBeInTheDocument();
});

it("keeps the draft on conflict and never silently binds a new revision or resaves", async () => {
  mocks.update.mockRejectedValueOnce(new CreatorProfileApiError(409, "偏好已变化"));
  mocks.get.mockResolvedValueOnce(profile).mockResolvedValueOnce({ ...profile, revision: 9, style_preferences: { ...profile.style_preferences, "视频时长": "一分钟" } });
  render(<ProfilePage/>); await screen.findByDisplayValue("通常两分钟");
  fireEvent.change(screen.getByRole("textbox", { name: "偏好内容 视频时长" }), { target: { value: "三分钟" } });
  fireEvent.click(screen.getByRole("button", { name: "保存修改" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("草稿已保留");
  expect(screen.getByDisplayValue("三分钟")).toBeVisible();
  expect(screen.getByRole("button", { name: "保存修改" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "重新读取已保存偏好" }));
  const conflict = await screen.findByRole("region", { name: "偏好更新冲突" });
  expect(await within(conflict).findByText("一分钟")).toBeVisible();
  expect(screen.getByDisplayValue("三分钟")).toBeVisible();
  expect(mocks.update).toHaveBeenCalledTimes(1);
  expect(screen.getByRole("button", { name: "保存修改" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "核对后保留我的修改" }));
  expect(mocks.update).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole("button", { name: "保存修改" }));
  await waitFor(() => expect(mocks.update).toHaveBeenLastCalledWith(expect.objectContaining({ expected_revision: 9, style_preferences: expect.objectContaining({ "视频时长": "三分钟" }) })));
});

it("keeps unsaved edits and reports no success when the save fails", async () => {
  mocks.update.mockRejectedValueOnce(new CreatorProfileApiError(503, "暂时无法保存"));
  render(<ProfilePage/>); await screen.findByDisplayValue("通常两分钟");
  fireEvent.change(screen.getByRole("textbox", { name: "偏好内容 视频时长" }), { target: { value: "三分钟" } });
  fireEvent.click(screen.getByRole("button", { name: "保存修改" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("暂时无法保存");
  expect(screen.getByDisplayValue("三分钟")).toBeVisible();
  expect(screen.queryByText("创作偏好已保存。")).not.toBeInTheDocument();
});

it("keeps nonstandard explicitly saved style visible in the three existing selectors", async () => {
  mocks.get.mockResolvedValueOnce({ ...profile, style_preferences: { tone: "亲切自然" } });
  render(<ProfilePage/>);
  expect(await screen.findByRole("combobox", { name: "语气" })).toHaveTextContent("亲切自然");
  expect(screen.getByRole("combobox", { name: "句长" })).toBeVisible();
  expect(screen.getByRole("combobox", { name: "开篇方式" })).toBeVisible();
});

it("downloads the saved projection as user.md while keeping unsaved edits distinct", async () => {
  const blob = new Blob(["# 我的创作偏好\n- 视频时长：通常两分钟\n"], { type: "text/markdown" });
  mocks.export.mockResolvedValueOnce(blob);
  vi.stubGlobal("URL", Object.assign(class extends URL {}, { createObjectURL: vi.fn().mockReturnValue("blob:memory-download"), revokeObjectURL: vi.fn() }));
  const downloads: Array<{ name: string; href: string }> = [];
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function(this: HTMLAnchorElement) { downloads.push({ name: this.download, href: this.href }); });
  render(<ProfilePage/>); await screen.findByDisplayValue("通常两分钟");
  expect(screen.getByRole("heading", { level: 1, name: "创作偏好" })).toHaveClass(/srOnly/);
  expect(screen.getByRole("link", { name: "返回设置" })).toHaveAttribute("href", "/settings?section=creators");
  fireEvent.change(screen.getByRole("textbox", { name: "偏好内容 视频时长" }), { target: { value: "三分钟" } });
  expect(screen.getByText("导出只包含已保存内容。")).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "导出偏好" }));
  expect(await screen.findByText("已开始下载已保存的偏好。")).toBeVisible();
  expect(mocks.export).toHaveBeenCalledWith();
  expect(mocks.update).not.toHaveBeenCalled();
  expect(URL.createObjectURL).toHaveBeenCalledWith(blob);
  expect(downloads).toEqual([{ name: "user.md", href: "blob:memory-download" }]);
  expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:memory-download");
  expect(screen.getByDisplayValue("三分钟")).toBeVisible();
});

it("does not start a download or report success when projection export fails", async () => {
  mocks.export.mockRejectedValueOnce(new CreatorProfileApiError(503, "暂时无法导出偏好"));
  vi.stubGlobal("URL", Object.assign(class extends URL {}, { createObjectURL: vi.fn(), revokeObjectURL: vi.fn() }));
  const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  render(<ProfilePage/>); await screen.findByDisplayValue("通常两分钟");
  fireEvent.change(screen.getByRole("textbox", { name: "偏好内容 视频时长" }), { target: { value: "三分钟" } });
  fireEvent.click(screen.getByRole("button", { name: "导出偏好" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("暂时无法导出偏好");
  expect(URL.createObjectURL).not.toHaveBeenCalled();
  expect(click).not.toHaveBeenCalled();
  expect(screen.queryByText("已开始下载已保存的偏好。")).not.toBeInTheDocument();
  expect(screen.getByDisplayValue("三分钟")).toBeVisible();
});
