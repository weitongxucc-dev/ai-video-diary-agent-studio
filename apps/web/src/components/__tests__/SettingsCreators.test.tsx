import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import type { AnchorHTMLAttributes } from "react";
const mocks=vi.hoisted(()=>({identity:vi.fn(),members:vi.fn(),refresh:vi.fn()}));
vi.mock("@/lib/auth/routing", () => ({ isLocalAuthMode: () => true }));
vi.mock("next/link",()=>({default:({href,children,...props}:AnchorHTMLAttributes<HTMLAnchorElement>)=><a href={href} {...props}>{children}</a>}));
vi.mock("@/lib/api",()=>({listLocalMembers:mocks.members,membersErrorMessage:(error:Error)=>error.message}));
vi.mock("@/lib/local-member-auth",()=>({fetchLocalMemberIdentity:mocks.identity}));
vi.mock("@/hooks/useLocalMemberIdentity",()=>({useLocalMemberIdentity:()=>({ready:true,role:"owner",token:"test-token",error:null,refresh:mocks.refresh})}));
import { SettingsCreators } from "../SettingsCreators";
beforeEach(()=>{vi.resetAllMocks();mocks.identity.mockResolvedValue({member_id:"own",name:"当前用户",role:"owner"});mocks.members.mockResolvedValue({members:[{member_id:"other",name:"其他成员",role:"member"}]});});
it("reads a selected member's directory metadata without changing the current identity",async()=>{
 render(<SettingsCreators selectedMemberId="other"/>);
 expect(await screen.findByText(/当前身份：/)).toHaveTextContent("当前用户");
 expect(screen.getByText(/所选名册资料/)).toHaveTextContent("其他成员 · 成员。当前身份仍为当前用户");
 expect(screen.getByRole("link",{name:"管理成员与共享空间"})).toHaveAttribute("href","/members");
});
it("does not offer owner management to the identity actually returned as member",async()=>{
 mocks.identity.mockResolvedValue({member_id:"current",name:"当前成员",role:"member"});render(<SettingsCreators/>);
 expect(await screen.findByRole("link",{name:"查看共享空间与加入方式"})).toHaveAttribute("href","/members");expect(screen.queryByRole("link",{name:"管理成员与共享空间"})).not.toBeInTheDocument();
});
it("keeps directory failures folded and retries the actual read",async()=>{
 mocks.members.mockRejectedValueOnce(new Error("raw local member diagnostic"));render(<SettingsCreators/>);
 expect(await screen.findByRole("alert")).toHaveTextContent("暂时无法读取创作者资料");expect(screen.getByText("raw local member diagnostic")).not.toBeVisible();
 fireEvent.click(screen.getByRole("button",{name:"重新确认身份"}));await waitFor(()=>expect(mocks.members).toHaveBeenCalledTimes(2));expect(await screen.findByText(/当前身份：/)).toBeVisible();
});
