import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import type { CreationTaskQualityCheck } from "@/lib/types";
import { CreationPlanApiError } from "@/lib/api";
import { FinalVideoQualityCheck } from "../creation-task/FinalVideoQualityCheck";

const api = vi.hoisted(() => ({ read: vi.fn(), fresh: vi.fn(), resume: vi.fn() }));
vi.mock("@/lib/api", async original => ({ ...await original<typeof import("@/lib/api")>(), getCreationTaskQualityCheck: api.read, checkCreationTaskQuality: api.fresh }));
vi.mock("@/lib/api/creation-tasks", async original => ({ ...await original<typeof import("@/lib/api/creation-tasks")>(), continueCreationTaskQuality: api.resume }));
function failed(): CreationTaskQualityCheck {
    return { check_state: "failed", result_status: null, stale: false, check_id: "check-original", timeline_version_id: "version",
        render_ref: "render", checked_at: null, error: null, findings: [], unavailable: [], clipping_summary: null, black_frame_summary: null,
        validation_scope: "deterministic_media_errors_only", continuation: { job_id: "job-original", failed_request_id: "failed-request",
            phase: "black_frames", completed_phases: ["metadata"], can_request: true, reason_code: null, release_check: "on_submit" } };
}
const props = { taskId: "task", versionId: "version", outputUrl: "/video", onLocate: vi.fn() };
beforeEach(() => { api.read.mockReset().mockResolvedValue(failed()); api.fresh.mockReset(); api.resume.mockReset(); });

it("continues only the original failed phase, preserves its completed prefix and reads actual progress rather than claiming admission", async () => {
    api.read.mockResolvedValueOnce(failed()).mockResolvedValue({ ...failed(), check_state: "checking", continuation: { ...failed().continuation!, can_request: false, reason_code: "request_in_flight" } });
    api.resume.mockResolvedValue({ check_id: "check-original", job_id: "job-original", request_id: "successor-request" });
    render(<FinalVideoQualityCheck {...props}/>);
    const button = await screen.findByRole("button", { name: "续检原失败阶段" });
    expect(screen.getByText(/失败阶段：黑帧检查。已完成阶段：媒体信息/)).toBeInTheDocument();
    expect(screen.getByText(/可请求续检，不代表已获设备准入/)).toBeInTheDocument();
    fireEvent.click(button);
    await screen.findByText(/续检回执已读回：successor-request/);
    expect(api.resume).toHaveBeenCalledWith("task", { job_id: "job-original", failed_request_id: "failed-request" });
    expect(await screen.findByRole("status", { name: "成片质量检查状态" })).toHaveTextContent("成片检查中");
    expect(api.fresh).not.toHaveBeenCalled();
});

it("shows a denied continuation reason without offering a grant or a fresh-check substitution", async () => {
    api.read.mockResolvedValue({ ...failed(), continuation: { ...failed().continuation!, can_request: false, reason_code: "original_actor_required" } });
    render(<FinalVideoQualityCheck {...props}/>);
    expect(await screen.findByText("仅原执行身份可以续检")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "续检原失败阶段" })).toBeDisabled();
    expect(api.resume).not.toHaveBeenCalled();
    expect(api.fresh).not.toHaveBeenCalled();
});

it("does not treat can_request as release approval when submission is rejected by the original unit", async () => {
    api.resume.mockRejectedValue(new CreationPlanApiError(409, null, "原阶段资源尚未释放"));
    render(<FinalVideoQualityCheck {...props}/>);
    fireEvent.click(await screen.findByRole("button", { name: "续检原失败阶段" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("原阶段资源尚未释放");
    expect(screen.getByText(/已完成阶段：媒体信息/)).toBeInTheDocument();
    expect(screen.queryByText(/续检回执已读回/)).not.toBeInTheDocument();
    expect(api.fresh).not.toHaveBeenCalled();
});

it("keeps an unknown continuation on its original identity and uses a read to resolve progress without reposting", async () => {
    api.resume.mockRejectedValue(new Error("续检回包中断"));
    api.read.mockResolvedValueOnce(failed()).mockResolvedValue({ ...failed(), check_state: "checking", continuation: { ...failed().continuation!, can_request: false, reason_code: "request_in_flight" } });
    render(<FinalVideoQualityCheck {...props}/>);
    fireEvent.click(await screen.findByRole("button", { name: "续检原失败阶段" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("续检回包中断");
    expect(screen.getByRole("button", { name: "续检原失败阶段" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "重新检查成片" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "查询原检查状态" }));
    await screen.findByText("原检查最新状态已读回，进度以该检查记录为准。");
    expect(api.resume).toHaveBeenCalledTimes(1);
    expect(api.fresh).not.toHaveBeenCalled();
});

it("ignores a late continuation receipt after the timeline identity changes", async () => {
    let finish!: (value: { check_id: string; job_id: string; request_id: string }) => void;
    api.resume.mockReturnValue(new Promise(resolve => { finish = resolve; }));
    const view = render(<FinalVideoQualityCheck {...props}/>);
    fireEvent.click(await screen.findByRole("button", { name: "续检原失败阶段" }));
    await waitFor(() => expect(api.resume).toHaveBeenCalled());
    api.read.mockResolvedValue({ ...failed(), check_id: "new-check", timeline_version_id: "new-version", continuation: null });
    view.rerender(<FinalVideoQualityCheck {...props} versionId="new-version" outputUrl="/new-video"/>);
    await act(async () => { finish({ check_id: "check-original", job_id: "job-original", request_id: "old-successor" }); });
    expect(screen.queryByText(/续检回执已读回：old-successor/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "续检原失败阶段" })).not.toBeInTheDocument();
    expect(api.fresh).not.toHaveBeenCalled();
});

it('clears the old continuation receipt only when the user deliberately starts a separate fresh check',async()=>{
    api.resume.mockResolvedValue({check_id:'check-original',job_id:'job-original',request_id:'known-successor'});
    api.fresh.mockResolvedValue({...failed(),check_id:'new-check',check_state:'checking',continuation:null});
    render(<FinalVideoQualityCheck {...props}/>);
    fireEvent.click(await screen.findByRole('button',{name:'续检原失败阶段'}));
    await screen.findByText(/续检回执已读回：known-successor/);
    await waitFor(()=>expect(screen.getByRole('button',{name:'重新检查成片'})).toBeEnabled());
    fireEvent.click(screen.getByRole('button',{name:'重新检查成片'}));
    await waitFor(()=>expect(api.fresh).toHaveBeenCalledWith('task'));
    expect(screen.queryByText(/续检回执已读回：known-successor/)).not.toBeInTheDocument();
    expect(screen.queryByRole('button',{name:'续检原失败阶段'})).not.toBeInTheDocument();
});

it('shows the backend failed_request_unavailable reason instead of a blank continuation refusal',async()=>{
    api.read.mockResolvedValue({...failed(),continuation:{...failed().continuation!,failed_request_id:null,can_request:false,reason_code:'failed_request_unavailable'}});
    render(<FinalVideoQualityCheck {...props}/>);
    expect(await screen.findByText('原失败执行请求不可用，当前不能续检')).toBeInTheDocument();
    expect(screen.getByRole('button',{name:'续检原失败阶段'})).toBeDisabled();
    expect(api.resume).not.toHaveBeenCalled();
    expect(api.fresh).not.toHaveBeenCalled();
});
