import { render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import type { AnchorHTMLAttributes } from "react";
vi.mock("next/link", () => ({ default: ({ href, children, ...props }: AnchorHTMLAttributes<HTMLAnchorElement>) => <a href={href} {...props}>{children}</a> }));
vi.mock("@/lib/api", () => ({ listLocalMembers: async () => ({ members: [{ member_id: "member-a", name: "成员甲", role: "member" }] }), membersErrorMessage: (error: Error) => error.message }));
vi.mock("@/lib/local-member-auth", () => ({ fetchLocalMemberIdentity: async () => ({ member_id: "member-a", name: "成员甲", role: "member" }) }));
vi.mock("@/hooks/useLocalMemberIdentity", () => ({ useLocalMemberIdentity: () => ({ ready: true, role: "member", token: "test-token", error: null, refresh: vi.fn() }) }));
import { SettingsCreators } from "../SettingsCreators";
it("exposes the current member's creator preferences in settings", async () => {
  render(<SettingsCreators/>);
  expect(await screen.findByRole("link", { name: "创作偏好" })).toHaveAttribute("href", "/profile");
});
