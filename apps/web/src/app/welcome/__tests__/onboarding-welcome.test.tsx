import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import WelcomePage from "../page";
const mock = vi.hoisted(() => ({ complete:vi.fn(), replace:vi.fn(), refresh:vi.fn() }));
vi.mock("next/navigation", () => ({useRouter: () => ({replace:mock.replace,refresh:mock.refresh})}));
vi.mock("@/lib/api/runtime", () => ({completeOnboarding:mock.complete}));
vi.mock("@/components/voice-sampling", () => ({VoiceSamplingSetup:()=>null}));
vi.mock("@/components/material-library/MaterialDiscoveryPanel", () => ({MaterialDiscoveryPanel:()=>null}));
beforeEach(() => { vi.clearAllMocks(); localStorage.clear(); });
it("all-skip initializes the trusted workspace before navigating", async () => {
  mock.complete.mockResolvedValue({workspace_id:"workspace",completed:true});
  render(<WelcomePage />);
  fireEvent.click(screen.getByRole("checkbox"));
  fireEvent.click(screen.getByRole("button",{name:"全部跳过，开始使用"}));
  await waitFor(() => expect(mock.replace).toHaveBeenCalledWith("/"));
  expect(mock.complete).toHaveBeenCalledOnce();
});
it("a missing data disk cannot be bypassed by skipping optional setup", async () => {
  mock.complete.mockRejectedValue(new Error("请连接原磁盘"));
  render(<WelcomePage />);
  fireEvent.click(screen.getByRole("checkbox"));
  fireEvent.click(screen.getByRole("button",{name:"全部跳过，开始使用"}));
  await screen.findByText("请连接原磁盘");
  expect(mock.replace).not.toHaveBeenCalled();
});
