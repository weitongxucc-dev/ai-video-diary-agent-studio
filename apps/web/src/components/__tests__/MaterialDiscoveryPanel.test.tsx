import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MaterialDiscoveryPanel } from "../material-library/MaterialDiscoveryPanel";
import type { MaterialDiscovery } from "@/lib/types/materials";

const api = vi.hoisted(() => ({
  getDiscoveryRoots: vi.fn(), getLatestDiscovery: vi.fn(), startMaterialDiscovery: vi.fn(),
  advanceMaterialDiscovery: vi.fn(), controlMaterialDiscovery: vi.fn(), receiveDiscoveryCandidates: vi.fn(),
  getMaterialDiscovery: vi.fn(), scanMaterialIntake: vi.fn(), discoveryPreviewUrl: () => "/preview",
}));
vi.mock("@/lib/api/materials", () => api);
vi.mock("../material-library/useMaterialIntake", () => ({ useMaterialIntake: () => ({
  intake: {bound_to_current_workspace:true, directory_state:"ready"}, intakeError:"", intakeBusy:null,
}) }));
vi.mock("../material-library/useLocalMaterialImport", () => ({ useLocalMaterialImport: () => ({ importBusy:false, importResult:null, runImport:vi.fn() }) }));
const stored: MaterialDiscovery = {
  id:"session", state:"scanning", exclusions:[], visited_count:2, skipped_count:0, issues:[], updated_at:"now",
  candidates:[{id:"a",file_name:"片段.mp4",location:"/local",media_type:"video",size_bytes:20,duration_seconds:null,state:"candidate",asset_id:null,error:null,excluded:false}],
  total_count:1, offset:0, has_more:false,
};
beforeEach(() => {
  vi.clearAllMocks();
  api.getDiscoveryRoots.mockResolvedValue({roots:[{id:"root",label:"本机",display_path:"/",available:true}],exclusions:[]});
  api.getLatestDiscovery.mockResolvedValue(structuredClone(stored));
});
describe("discovery user boundaries", () => {
  it("only restores metadata until the user resumes, and only receives selected ids", async () => {
    api.receiveDiscoveryCandidates.mockResolvedValue({...stored,state:"completed",candidates:[{...stored.candidates[0],state:"received",asset_id:"asset"}]});
    render(<MaterialDiscoveryPanel />);
    await screen.findByText("继续本次扫描");
    expect(api.advanceMaterialDiscovery).not.toHaveBeenCalled();
    expect(api.receiveDiscoveryCandidates).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("checkbox",{name:"选择 片段.mp4"}));
    fireEvent.click(screen.getByRole("button",{name:"加入素材库（1）"}));
    await screen.findByText("已接收");
    expect(api.receiveDiscoveryCandidates).toHaveBeenCalledExactlyOnceWith("session",["a"]);
  });
  it("does not replace a new scan with the late receipt of a cancelled scan", async () => {
    let resolveReceive!: (value: MaterialDiscovery) => void;
    api.receiveDiscoveryCandidates.mockImplementation(() => new Promise(resolve => { resolveReceive = resolve; }));
    api.controlMaterialDiscovery.mockResolvedValue({...stored,state:"cancelled"});
    api.startMaterialDiscovery.mockResolvedValue({...stored,id:"new-session",state:"completed",visited_count:99});
    render(<MaterialDiscoveryPanel />);
    await screen.findByText("继续本次扫描");
    fireEvent.click(screen.getByRole("checkbox",{name:"选择 片段.mp4"}));
    fireEvent.click(screen.getByRole("button",{name:"加入素材库（1）"}));
    fireEvent.click(screen.getByRole("button",{name:"取消本次扫描"}));
    await screen.findByText(/扫描已取消 ·/);
    fireEvent.click(screen.getByRole("button",{name:"重新扫描"}));
    await screen.findByText(/已检查 99 项/);
    await act(async () => resolveReceive({...stored,state:"cancelled"}));
    expect(screen.getByText(/已检查 99 项/)).toBeInTheDocument();
  });
  it("shows a failed copy without claiming that an asset was received", async () => {
    api.receiveDiscoveryCandidates.mockResolvedValue({...stored,candidates:[{...stored.candidates[0],state:"failed",error:"磁盘断开"}]});
    render(<MaterialDiscoveryPanel />);
    await screen.findByText("继续本次扫描");
    fireEvent.click(screen.getByRole("checkbox",{name:"选择 片段.mp4"}));
    fireEvent.click(screen.getByRole("button",{name:"加入素材库（1）"}));
    await waitFor(() => expect(screen.getByText("磁盘断开")).toBeInTheDocument());
    expect(screen.queryByText("已接收")).not.toBeInTheDocument();
  });
});
