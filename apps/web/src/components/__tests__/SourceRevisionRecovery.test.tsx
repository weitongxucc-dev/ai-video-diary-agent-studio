import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { SourceRevisionRecovery } from "../creation-task/SourceRevisionRecovery";
import { getProductionMaterial } from "@/lib/api/materials";
import type { SourceRevisionRecoveryInput } from "@/lib/api/source-revision-recovery";

vi.mock("@/lib/api/materials", () => ({ getProductionMaterial: vi.fn() }));

const boundRevision = "2026-11-26T13:00:00.123455+00:00";
const currentRevision = "2026-11-26T13:00:00.123456Z";
const source = {
    asset_id: "asset", asset_updated_at: boundRevision, source_ref: "library://asset",
    source_start_seconds: 0, source_end_seconds: 30, aspect_ratio: "9:16", preserve_audio: true,
    file_signature: { size_bytes: "42", mtime_ns: "42", ctime_ns: "42", inode: "42" },
} as const;
const props = {
    taskId: "work", planId: "plan", versionId: "v9", sequence: 9,
    source, memberScope: "local:work:member", active: true, disabled: false,
    onRecovered: vi.fn(async (): Promise<"updated" | "past" | "unverified"> => "updated"),
};
const fetcher = vi.fn<typeof fetch>();

function material(updated_at = currentRevision) {
    return { id: "asset", file_name: "原片.mp4", updated_at } as Awaited<ReturnType<typeof getProductionMaterial>>;
}

function receipt(input: SourceRevisionRecoveryInput) {
    return {
        request_id: input.request_id, task_id: "work", plan_id: "plan", status: "recovered",
        timeline_version_id: "v9", timeline_sequence: 9, asset_id: "asset",
        previous_asset_updated_at: boundRevision.replace("+00:00", "Z"),
        asset_updated_at: currentRevision.replace("Z", "+00:00"),
    };
}

const check = () => fireEvent.click(screen.getByRole("button", { name: "核对原片来源" }));
const confirm = async () => fireEvent.click(await screen.findByRole("button", { name: "确认复核同一原片" }));
const query = () => fireEvent.click(screen.getByRole("button", { name: "查询原复核结果" }));
const submittedInput = () => JSON.parse(String(fetcher.mock.calls[0][1]?.body)) as SourceRevisionRecoveryInput;

beforeEach(() => {
    vi.resetAllMocks();
    sessionStorage.clear();
    vi.stubGlobal("fetch", fetcher);
    vi.mocked(getProductionMaterial).mockResolvedValue(material());
    props.onRecovered.mockResolvedValue("updated");
});

it("reads the actual material only on demand and treats equivalent UTC revisions as matched", async () => {
    vi.mocked(getProductionMaterial).mockResolvedValue(material(boundRevision.replace("+00:00", "Z")));
    render(<SourceRevisionRecovery {...props} />);
    expect(getProductionMaterial).not.toHaveBeenCalled();
    expect(fetcher).not.toHaveBeenCalled();
    check();
    expect(await screen.findByText("当前素材登记与作品绑定的修订已匹配。")).toBeVisible();
    expect(screen.getByText("原片.mp4")).toBeVisible();
    expect(screen.getByText("0–30 秒")).toBeVisible();
    expect(screen.getByText("第 9 版")).toBeVisible();
    expect(screen.queryByRole("button", { name: "确认复核同一原片" })).not.toBeInTheDocument();
    expect(fetcher).not.toHaveBeenCalled();
});

it("submits the two exact microsecond revisions only after confirmation and waits for workspace readback", async () => {
    fetcher.mockImplementation(async (_url, init) => Response.json(receipt(JSON.parse(String(init?.body)))));
    props.onRecovered.mockResolvedValueOnce("unverified");
    render(<SourceRevisionRecovery {...props} />);
    check();
    expect(await screen.findByText("素材登记已变化。复核会核对是否仍为同一原片及原区间。")).toBeVisible();
    expect(fetcher).not.toHaveBeenCalled();
    await confirm();
    expect(await screen.findByRole("alert")).toHaveTextContent("当前作品绑定尚未核实");
    expect(submittedInput()).toEqual({
        request_id: expect.any(String), asset_id: "asset", expected_timeline_version_id: "v9",
        expected_timeline_sequence: 9, expected_bound_asset_updated_at: boundRevision,
        expected_asset_updated_at: currentRevision,
    });
    expect(props.onRecovered).toHaveBeenCalledWith(receipt(submittedInput()), expect.any(Function));
    expect(screen.queryByText("已复核同一原片。可继续编辑当前版本。")).not.toBeInTheDocument();
});

it("retains a lost response after remount and only retries the same request when its receipt is absent", async () => {
    fetcher.mockRejectedValueOnce(new TypeError("response interrupted"));
    const view = render(<SourceRevisionRecovery {...props} />);
    check();
    await confirm();
    expect(await screen.findByRole("alert")).toHaveTextContent("原复核结果待核对");
    const input = submittedInput();
    view.unmount();
    render(<SourceRevisionRecovery {...props} />);
    fetcher.mockResolvedValueOnce(Response.json({ detail: "原复核请求尚无已提交回执" }, { status: 404 }));
    query();
    const replay = await screen.findByRole("button", { name: "按原请求重试" });
    expect(String(fetcher.mock.calls[1][0])).toMatch(new RegExp(`/work/source-revision-recoveries/${input.request_id}$`));
    expect(fetcher.mock.calls[1][1]?.method).toBeUndefined();
    fetcher.mockResolvedValueOnce(Response.json(receipt(input)));
    fireEvent.click(replay);
    expect(await screen.findByText("已复核同一原片。可继续编辑当前版本。")).toBeVisible();
    expect(JSON.parse(String(fetcher.mock.calls[2][1]?.body))).toEqual(input);
    expect(sessionStorage.length).toBe(0);
});

it("keeps a returned receipt with different facts out of the active workspace and can query the original", async () => {
    fetcher.mockImplementationOnce(async (_url, init) => Response.json({
        ...receipt(JSON.parse(String(init?.body))), task_id: "different-work",
    }));
    render(<SourceRevisionRecovery {...props} />);
    check();
    await confirm();
    expect(await screen.findByRole("alert")).toHaveTextContent("回执与原复核请求不一致");
    expect(props.onRecovered).not.toHaveBeenCalled();
    fetcher.mockResolvedValueOnce(Response.json(receipt(submittedInput())));
    query();
    expect(await screen.findByText("已复核同一原片。可继续编辑当前版本。")).toBeVisible();
    expect(fetcher.mock.calls[1][1]?.method).toBeUndefined();
});

it("leaves a confirmed submission with its original work when the user switches before the response", async () => {
    let respond!: (value: Response) => void;
    fetcher.mockImplementationOnce(() => new Promise(resolve => { respond = resolve; }));
    const view = render(<SourceRevisionRecovery {...props} />);
    check();
    await confirm();
    await waitFor(() => expect(fetcher).toHaveBeenCalledOnce());
    const input = submittedInput();
    view.rerender(<SourceRevisionRecovery {...props} taskId="other" memberScope="local:other:member" />);
    await act(async () => respond(Response.json(receipt(input))));
    expect(props.onRecovered).not.toHaveBeenCalled();
    expect(screen.queryByRole("button", { name: "查询原复核结果" })).not.toBeInTheDocument();
    view.rerender(<SourceRevisionRecovery {...props} />);
    fetcher.mockResolvedValueOnce(Response.json(receipt(input)));
    query();
    expect(await screen.findByText("已复核同一原片。可继续编辑当前版本。")).toBeVisible();
    expect(String(fetcher.mock.calls[1][0])).toContain(input.request_id);
});

it("shows a definite conflict and requires a fresh check before another submission", async () => {
    fetcher.mockResolvedValueOnce(Response.json({ detail: "当前原片登记已变化" }, { status: 409 }));
    render(<SourceRevisionRecovery {...props} />);
    check();
    await confirm();
    expect(await screen.findByRole("alert")).toHaveTextContent("当前原片登记已变化");
    expect(screen.queryByRole("button", { name: "确认复核同一原片" })).not.toBeInTheDocument();
    expect(fetcher).toHaveBeenCalledOnce();
    expect(getProductionMaterial).toHaveBeenCalledOnce();
    expect(props.onRecovered).not.toHaveBeenCalled();
    check();
    expect(await screen.findByRole("button", { name: "确认复核同一原片" })).toBeVisible();
    expect(fetcher).toHaveBeenCalledOnce();
    expect(getProductionMaterial).toHaveBeenCalledTimes(2);
});
