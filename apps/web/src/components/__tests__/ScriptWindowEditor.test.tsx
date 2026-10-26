import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ScriptWindowEditor, ScriptWindowStart } from "../creation-task/ScriptWindowEditor";
import type { CreationTaskScript, CreationTaskScriptSection, CreationTaskScriptWindowSaved, CreationTaskScriptWindowResult } from "@/lib/types/creation-tasks";

import { readPreflightDraftScope } from "@/lib/preflight-draft";
vi.mock("@/lib/preflight-draft", async importOriginal => ({
    ...await importOriginal<typeof import("@/lib/preflight-draft")>(),
    readPreflightDraftScope: vi.fn(),
}));
const originalScope = { memberId: "local:member-a", workspaceId: "workspace-a" };
beforeEach(() => {
    sessionStorage.clear();
    vi.mocked(readPreflightDraftScope).mockReset().mockResolvedValue(originalScope);
});
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });
function header(id = "script", count = 0): CreationTaskScript {
    return { id, task_id: "task", run_id: "", topic_id: "topic", title: "窗口长稿", language: "zh-CN",
        sections_loaded: false, sections: null, sections_total_count: count, draft_complete: false,
        selection_status: "draft", created_at: "2026-10-24T00:00:00Z", updated_at: "2026-10-24T00:00:00Z", confirmed_at: null };
}
function harness(initial: CreationTaskScriptSection[] = []) {
    let sections = structuredClone(initial);
    let version = header().updated_at;
    let tick = 0;
    type Operation = "start" | "patch" | "confirm";
    const writes: { method: string; body: Record<string, unknown>; operation: Operation }[] = [];
    const queries: URL[] = [];
    const outcomes = new Map<string, CreationTaskScriptWindowResult>();
    let nextFailure: "conflict" | "unknown" | "invalid" | "claimed" | null = null;
    let readFailure = false, missingReceipt = false;
    function unsettled(status: "unknown" | "failed" = "unknown"): CreationTaskScriptWindowResult {
        const write = writes[writes.length - 1];
        return { task_id: "task", ...(write.operation === "start" ? {} : { script_id: "script" }),
            request_id: write.body.request_id as string, operation: write.operation, status, submission_effect: "unknown",
            submitted_input_available: true, message: "原请求尚无最终成功回执，不能自动重提。",
            ...(status === "failed" ? { error_detail: { reason: "核查未能完成" } } : {}) };
    }
    function finish(write: typeof writes[number]): CreationTaskScriptWindowResult {
        const body = write.body;
        if (write.operation !== "confirm") {
            const updates = body.sections as CreationTaskScriptSection[];
            expect(updates.length).toBeLessThanOrEqual(20);
            expect(body).not.toHaveProperty("edit_mode");
            if (write.operation === "start") sections = structuredClone(updates);
            else {
                expect(body.expected_updated_at).toBe(version);
                const oldIds = new Set(sections.map(section => section.id));
                sections = sections.map(section => structuredClone(updates.find(update => update.id === section.id) ?? section));
                const added = updates.filter(update => !oldIds.has(update.id));
                const position = body.insert_after_section_id ? sections.findIndex(section => section.id === body.insert_after_section_id) + 1 : sections.length;
                sections.splice(position, 0, ...structuredClone(added));
            }
        } else expect(body.expected_updated_at).toBe(version);
        version = `2026-10-24T00:00:${String(++tick).padStart(2, "0")}Z`;
        const common = { task_id: "task", script_id: "script", request_id: body.request_id as string, updated_at: version, total_count: sections.length };
        const result: CreationTaskScriptWindowResult = write.operation === "confirm" ? { ...common, status: "confirmed" }
            : { ...common, status: "saved", affected_section_ids: (body.sections as CreationTaskScriptSection[]).map(section => section.id) };
        outcomes.set(common.request_id, result);
        return result;
    }
    const fetch = vi.fn(async (url: string, init?: RequestInit) => {
        const request = new URL(url, "http://local");
        if (request.pathname.endsWith("/assistant/script-context")) return new Response(JSON.stringify({ sources: [], draft: null, reply_origin: null }));
        if (request.pathname.includes("/script-window-requests/")) {
            queries.push(request);
            expect(init?.method ?? "GET").toBe("GET");
            expect(init?.body).toBeUndefined();
            const result = outcomes.get(request.pathname.split("/").at(-1)!);
            if (missingReceipt || !result) { missingReceipt = false; return new Response(JSON.stringify({ detail: "未找到请求回执" }), { status: 404 }); }
            return new Response(JSON.stringify(request.searchParams.get("include_input") === "true" && result.status !== "saved" && result.status !== "confirmed"
                ? { ...result, submitted_input: writes.find(write => write.body.request_id === result.request_id)!.body } : result));
        }
        if (!init?.method || init.method === "GET") {
            if (readFailure) { readFailure = false; throw new Error("窗口读取失败"); }
            const expected = request.searchParams.get("expected_updated_at");
            if (expected && expected !== version) return new Response(JSON.stringify({ detail: "文案已变化，请读取当前版本。" }), { status: 409 });
            const after = Number(request.searchParams.get("after_ordinal") ?? 0);
            const items = sections.slice(after, after + 20).map((section, index) => ({ ordinal: after + index + 1, section, digest: section.id }));
            return new Response(JSON.stringify({ task_id: "task", script_id: "script", updated_at: version, title: "窗口长稿",
                language: "zh-CN", selection_status: "draft", total_count: sections.length, items,
                next_after_ordinal: after + items.length < sections.length ? after + items.length : null, truncated: after + items.length < sections.length }));
        }
        const body = JSON.parse(init.body as string);
        const operation: Operation = request.pathname.endsWith("/confirm-saved") ? "confirm" : init.method === "POST" ? "start" : "patch";
        const write = { method: init.method, body, operation };
        writes.push(write);
        const failure = nextFailure;
        nextFailure = null;
        if (failure === "unknown" || failure === "claimed") {
            const result = unsettled(); outcomes.set(body.request_id, result);
            if (failure === "unknown") throw new Error("回包中断");
            return new Response(JSON.stringify(result));
        }
        if (failure === "conflict") return new Response(JSON.stringify({ detail: "文案版本冲突；当前修改未保存。" }), { status: 409 });
        if (failure === "invalid") return new Response(JSON.stringify({ detail: "该段核查未通过，草稿未保存。" }), { status: 422 });
        return new Response(JSON.stringify(finish(write)));
    });
    vi.stubGlobal("fetch", fetch);
    return { fetch, writes, queries, fail: (value: typeof nextFailure) => { nextFailure = value; }, failRead: () => { readFailure = true; },
        missingOnce: () => { missingReceipt = true; }, outcome: (status: "unknown" | "failed") => { const result = unsettled(status); outcomes.set(result.request_id, result); },
        completeLast: () => finish(writes[writes.length - 1]), sections: () => sections, version: () => version };
}
function fill(count: number, offset = 0, append = false) {
    for (let index = 1; index < count; index++) fireEvent.click(screen.getByRole("button", { name: "再加一段" }));
    for (let index = 1; index <= count; index++) {
        const ordinal = append ? `新增 ${index}` : String(index);
        fireEvent.change(screen.getByLabelText(`第 ${ordinal} 段标题`), { target: { value: `标题 ${offset + index}` } });
        fireEvent.change(screen.getByLabelText(`第 ${ordinal} 段正文`), { target: { value: `正文 ${offset + index}` } });
        fireEvent.change(screen.getByLabelText(`第 ${ordinal} 段画面建议`), { target: { value: `画面 ${offset + index}` } });
    }
}
function section(id = "first"): CreationTaskScriptSection {
    return { id, title: "第一段", content: "原正文", visual_prompt: "原画面", evidence_reference_ids: [] };
}

it("creates 45 sections through three real-client windows, edits section 45 and inserts by stable anchor without replacing the rest", async () => {
    const server = harness();
    const onSaved = vi.fn(async () => undefined);
    const view = render(<ScriptWindowStart taskId="task" onSaved={onSaved}/>);
    await waitFor(() => expect(screen.getByLabelText("新文案标题")).toBeEnabled());
    fireEvent.change(screen.getByLabelText("新文案标题"), { target: { value: "窗口长稿" } });
    fill(20);
    fireEvent.click(screen.getByRole("button", { name: "保存首窗" }));
    await screen.findByText("首窗已保存，共 20 段；尚未核查并采用为完整文案。");
    expect(server.writes[0].method).toBe("POST");
    expect(server.writes[0].body).not.toHaveProperty("expected_updated_at");
    view.rerender(<ScriptWindowEditor taskId="task" script={{ ...header("script", 20), updated_at: server.version() }} onSaved={onSaved}/>);
    await screen.findByLabelText("第 20 段正文");
    for (const [count, offset] of [[20, 20], [5, 40]]) {
        fireEvent.click(screen.getByRole("button", { name: "追加文案" }));
        fill(count, offset, true);
        fireEvent.click(screen.getByRole("button", { name: "保存新增段落" }));
        await screen.findByText(`已保存窗口，当前共 ${offset + count} 段；尚未采用为完整文案。`);
        await waitFor(() => expect(screen.getByRole("button", { name: "追加文案" })).toBeEnabled());
    }
    expect(server.sections()).toHaveLength(45);
    expect(server.writes.map(write => (write.body.sections as unknown[]).length)).toEqual([20, 20, 5]);
    const before = structuredClone(server.sections());
    fireEvent.click(screen.getByRole("button", { name: "下一页文案" }));
    await screen.findByLabelText("第 21 段正文");
    fireEvent.click(screen.getByRole("button", { name: "下一页文案" }));
    await screen.findByLabelText("第 45 段正文");
    fireEvent.change(screen.getByLabelText("第 45 段画面建议"), { target: { value: "末段新画面" } });
    expect(screen.getByRole("button", { name: "上一页文案" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "保存本页修改" }));
    await waitFor(() => expect(server.writes).toHaveLength(4));
    await waitFor(() => expect(screen.getByRole("button", { name: "追加文案" })).toBeEnabled());
    expect(server.writes[3].body.sections).toEqual([{ ...before[44], visual_prompt: "末段新画面" }]);
    expect(server.sections().slice(0, 44)).toEqual(before.slice(0, 44));
    expect(screen.getByLabelText("第 45 段画面建议")).toHaveValue("末段新画面");
    fireEvent.click(screen.getByRole("button", { name: "在第 45 段后插入" }));
    fill(1, 45, true);
    fireEvent.click(screen.getByRole("button", { name: "保存新增段落" }));
    await waitFor(() => expect(server.writes).toHaveLength(5));
    expect(server.writes[4].body.insert_after_section_id).toBe(before[44].id);
    expect(server.sections()).toHaveLength(46);
    expect(screen.queryByRole("button", { name: "确认文案" })).not.toBeInTheDocument();
});

it.each(["conflict", "invalid"] as const)("preserves editable window inputs after a definite %s rejection without reusing a changed request", async failure => {
    const server = harness([section()]);
    server.fail(failure);
    render(<ScriptWindowEditor taskId="task" script={header("script", 1)} onSaved={vi.fn(async () => undefined)}/>);
    await screen.findByLabelText("第 1 段正文");
    fireEvent.change(screen.getByLabelText("第 1 段正文"), { target: { value: "本页未保存修改" } });
    fireEvent.click(screen.getByRole("button", { name: "保存本页修改" }));
    await screen.findByRole("alert");
    expect(screen.getByLabelText("第 1 段正文")).toHaveValue("本页未保存修改");
    expect(screen.getByLabelText("第 1 段正文")).toBeEnabled();
    expect(server.sections()[0].content).toBe("原正文");
    if (failure === "conflict") {
        expect(screen.getByRole("button", { name: "保存本页修改" })).toBeDisabled();
        fireEvent.click(screen.getByRole("button", { name: "放弃本页修改并读取最新版本" }));
        await waitFor(() => expect(screen.getByLabelText("第 1 段正文")).toHaveValue("原正文"));
    } else {
        fireEvent.change(screen.getByLabelText("第 1 段正文"), { target: { value: "修订后正文" } });
        fireEvent.click(screen.getByRole("button", { name: "保存本页修改" }));
        await screen.findByText("已保存窗口，当前共 1 段；尚未采用为完整文案。");
        expect(server.writes[0].body.request_id).not.toBe(server.writes[1].body.request_id);
    }
});

it("keeps an unknown patch request and draft without reposting or claiming saved", async () => {
    const server = harness([section()]);
    server.fail("unknown");
    render(<ScriptWindowEditor taskId="task" script={header("script", 1)} onSaved={vi.fn(async () => undefined)}/>);
    await screen.findByLabelText("第 1 段正文");
    fireEvent.change(screen.getByLabelText("第 1 段正文"), { target: { value: "结果未知的正文" } });
    fireEvent.click(screen.getByRole("button", { name: "保存本页修改" }));
    await screen.findByRole("alert");
    expect(screen.getByText(/窗口保存结果尚待核对，原请求定位已保留/)).toBeInTheDocument();
    expect(screen.getByLabelText("第 1 段正文")).toHaveValue("结果未知的正文");
    expect(screen.getByLabelText("第 1 段正文")).toBeDisabled();
    expect(server.writes).toHaveLength(1);
    expect(screen.queryByRole("button", { name: "重试原窗口保存" })).not.toBeInTheDocument();
    expect(screen.queryByText(/已保存窗口，当前共/)).not.toBeInTheDocument();
});

it("ignores an old window read after changing script identity", async () => {
    let finish!: (response: Response) => void;
    const server = harness([section("new")]);
    const real = server.fetch.getMockImplementation()!;
    server.fetch.mockImplementationOnce(async (url, init) => real(url, init));
    server.fetch.mockImplementationOnce(() => new Promise<Response>(resolve => { finish = resolve; }));
    const view = render(<ScriptWindowEditor taskId="task" script={header("old", 1)} onSaved={vi.fn(async () => undefined)}/>);
    await waitFor(() => expect(finish).toBeDefined());
    view.rerender(<ScriptWindowEditor taskId="task" script={header("new", 1)} onSaved={vi.fn(async () => undefined)}/>);
    await screen.findByLabelText("第 1 段正文");
    await act(async () => { finish(new Response(JSON.stringify({ title: "旧稿", items: [{ ordinal: 1, section: { ...section(), content: "过期旧稿" } }], total_count: 1, updated_at: header().updated_at, next_after_ordinal: null }))); });
    expect(screen.getByLabelText("第 1 段正文")).toHaveValue("原正文");
    expect(screen.queryByDisplayValue("过期旧稿")).not.toBeInTheDocument();
});

it('does not present the previous window as the current page when a page read fails',async()=>{
  const server=harness(Array.from({length:21},(_,index)=>({...section(`section-${index+1}`),content:`正文 ${index+1}`})));
  render(<ScriptWindowEditor taskId="task" script={header('script',21)} onSaved={vi.fn(async()=>undefined)}/>);
  await screen.findByLabelText('第 1 段正文');
  server.failRead();
  fireEvent.click(screen.getByRole('button',{name:'下一页文案'}));
  await screen.findByRole('alert');
  expect(screen.queryByLabelText('第 1 段正文')).not.toBeInTheDocument();
  expect(screen.queryByRole('button',{name:'保存本页修改'})).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button',{name:'重试读取窗口'}));
  expect(await screen.findByLabelText('第 21 段正文')).toHaveValue('正文 21');
});

it('keeps an unknown first-window request without a second POST or pretending creation succeeded',async()=>{
  const server=harness();
  server.fail('unknown');
  const onSaved=vi.fn(async()=>undefined);
  render(<ScriptWindowStart taskId="task" onSaved={onSaved}/>);
  fireEvent.change(screen.getByLabelText('新文案标题'),{target:{value:'首窗文案'}});
  fill(1);
  fireEvent.click(screen.getByRole('button',{name:'保存首窗'}));
  await screen.findByRole('alert');
  expect(screen.getByRole('button',{name:'首窗保存结果待核对'})).toBeDisabled();
  expect(screen.getByLabelText('第 1 段正文')).toHaveValue('正文 1');
  expect(server.writes).toHaveLength(1);
  expect(onSaved).not.toHaveBeenCalled();
  expect(screen.queryByText(/首窗已保存，共/)).not.toBeInTheDocument();
});

it('adopts a saved 45-section draft by request and version without submitting or loading all sections',async()=>{
  const server=harness(Array.from({length:45},(_,index)=>section(`section-${index+1}`)));
  const onSaved=vi.fn(async(_receipt:CreationTaskScriptWindowResult)=>undefined);
  render(<ScriptWindowEditor taskId="task" script={header('script',45)} onSaved={onSaved}/>);
  await screen.findByLabelText('第 20 段正文');
  expect(screen.queryByLabelText('第 21 段正文')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button',{name:'核查并采用完整文案'}));
  await screen.findByText('已核查并采用完整文案，共 45 段。');
  expect(server.writes[0].operation).toBe('confirm');
  expect(server.writes[0].body).toEqual({request_id:expect.any(String),expected_updated_at:header().updated_at});
  expect(onSaved).toHaveBeenCalledWith(expect.objectContaining({status:'confirmed',total_count:45,updated_at:server.version()}));
  expect(screen.queryByText(/当前为部分稿/)).not.toBeInTheDocument();
  expect(screen.getByLabelText('第 1 段正文')).toBeDisabled();
});

it.each(['start','patch','confirm'] as const)('recovers unknown %s only by the original request GET and never treats 404 or failed as permission to repost',async(operation)=>{
  const server=harness(operation==='start'?[]:[section()]);
  server.fail(operation==='patch'?'claimed':'unknown');
  const onSaved=vi.fn(async(_receipt:CreationTaskScriptWindowResult)=>undefined);
  if(operation==='start') {
    render(<ScriptWindowStart taskId="task" onSaved={onSaved}/>);
    await waitFor(() => expect(screen.getByLabelText('新文案标题')).toBeEnabled());
    fireEvent.change(screen.getByLabelText('新文案标题'),{target:{value:'首窗文案'}});
    fill(1);
    fireEvent.click(screen.getByRole('button',{name:'保存首窗'}));
  } else {
    render(<ScriptWindowEditor taskId="task" script={header('script',1)} onSaved={onSaved}/>);
    await screen.findByLabelText('第 1 段正文');
    if(operation==='patch') {
      fireEvent.change(screen.getByLabelText('第 1 段正文'),{target:{value:'待恢复正文'}});
      fireEvent.click(screen.getByRole('button',{name:'保存本页修改'}));
    } else fireEvent.click(screen.getByRole('button',{name:'核查并采用完整文案'}));
  }
  await waitFor(()=>expect(screen.getByRole('button',{name:'查询原请求结果'})).toBeEnabled());
  expect(onSaved).not.toHaveBeenCalled();
  expect(screen.getByLabelText('第 1 段正文')).toBeDisabled();
  fireEvent.click(screen.getByRole('button',{name:'查询原请求结果'}));
  await screen.findByText('原请求结果仍未知。原请求继续保留。');
  server.missingOnce();
  fireEvent.click(screen.getByRole('button',{name:'查询原请求结果'}));
  await screen.findByText(/不代表请求未提交/);
  server.outcome('failed');
  fireEvent.click(screen.getByRole('button',{name:'查询原请求结果'}));
  await screen.findByText('原请求记录为失败，但提交结果仍未确认。原请求继续保留。');
  expect(onSaved).not.toHaveBeenCalled();
  const receipt=server.completeLast();
  fireEvent.click(screen.getByRole('button',{name:'查询原请求结果'}));
  await waitFor(()=>expect(onSaved).toHaveBeenCalledWith(receipt));
  expect(server.writes).toHaveLength(1);
  for(const query of server.queries) {
    expect(query.pathname.endsWith(String(server.writes[0].body.request_id))).toBe(true);
    expect(query.searchParams.get('operation')).toBe(operation);
    expect(query.searchParams.get('script_id')).toBe(operation==='start'?null:'script');
  }
  if(operation==='patch') {
    await waitFor(()=>expect(screen.getByLabelText('第 1 段正文')).toBeEnabled());
    expect(screen.getByLabelText('第 1 段正文')).toHaveValue('待恢复正文');
  }
});

it('keeps a definite confirm rejection editable so the same draft can be expanded instead of being permanently blocked',async()=>{
  const server=harness([section()]);server.fail('conflict');
  render(<ScriptWindowEditor taskId="task" script={header('script',1)} onSaved={vi.fn(async()=>undefined)}/>);
  await screen.findByLabelText('第 1 段正文');
  fireEvent.click(screen.getByRole('button',{name:'核查并采用完整文案'}));
  await screen.findByRole('alert');
  expect(screen.getByLabelText('第 1 段正文')).toBeEnabled();
  fireEvent.click(screen.getByRole('button',{name:'追加文案'}));
  expect(screen.getByLabelText('第 新增 1 段正文')).toBeEnabled();
  expect(screen.queryByRole('region',{name:'原文案请求对账'})).not.toBeInTheDocument();
});

it('ignores an old request receipt after changing script identity',async()=>{
  const server=harness([section()]);server.fail('unknown');
  const onSaved=vi.fn(async(_receipt:CreationTaskScriptWindowResult)=>undefined);
  const view=render(<ScriptWindowEditor taskId="task" script={header('old-script',1)} onSaved={onSaved}/>);
  await screen.findByLabelText('第 1 段正文');
  fireEvent.change(screen.getByLabelText('第 1 段正文'),{target:{value:'旧稿待保存正文'}});
  fireEvent.click(screen.getByRole('button',{name:'保存本页修改'}));
  await waitFor(()=>expect(screen.getByRole('button',{name:'查询原请求结果'})).toBeEnabled());
  const original=server.fetch.getMockImplementation()!;
  let finish!:(response:Response)=>void;
  server.fetch.mockImplementation((url,init)=>url.includes('/script-window-requests/')?new Promise<Response>(resolve=>{finish=resolve;}):original(url,init));
  fireEvent.click(screen.getByRole('button',{name:'查询原请求结果'}));
  await waitFor(()=>expect(finish).toBeDefined());
  view.rerender(<ScriptWindowEditor taskId="task" script={header('new-script',1)} onSaved={onSaved}/>);
  await screen.findByLabelText('第 1 段正文');
  await act(async()=>{finish(new Response(JSON.stringify(server.completeLast())));});
  expect(onSaved).not.toHaveBeenCalled();
  expect(screen.getByLabelText('第 1 段正文')).toHaveValue('原正文');
  expect(screen.queryByText(/已保存窗口，当前共/)).not.toBeInTheDocument();
});

it('fetches original request input only after explicit recovery and never overwrites the current draft or reposts',async()=>{
  const server=harness([section()]);server.fail('claimed');
  render(<ScriptWindowEditor taskId="task" script={header('script',1)} onSaved={vi.fn(async()=>undefined)}/>);
  await screen.findByLabelText('第 1 段正文');
  fireEvent.change(screen.getByLabelText('第 1 段正文'),{target:{value:'待恢复正文'}});
  fireEvent.click(screen.getByRole('button',{name:'保存本页修改'}));
  await waitFor(()=>expect(screen.getByRole('button',{name:'查询原请求结果'})).toBeEnabled());
  fireEvent.click(screen.getByRole('button',{name:'查询原请求结果'}));
  await screen.findByRole('button',{name:'恢复原请求输入'});
  expect(server.queries[0].searchParams.has('include_input')).toBe(false);
  expect(screen.queryByRole('region',{name:'原请求输入'})).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button',{name:'恢复原请求输入'}));
  const original=await screen.findByRole('region',{name:'原请求输入'});
  expect(original).toHaveTextContent('"content": "待恢复正文"');
  expect(server.queries[1].searchParams.get('include_input')).toBe('true');
  expect(screen.getByLabelText('第 1 段正文')).toHaveValue('待恢复正文');
  expect(server.writes).toHaveLength(1);
});

async function pendingWindow(operation: "start" | "patch" | "confirm" = "patch") {
    const server = harness(operation === "start" ? [] : [section()]);
    server.fail("unknown");
    const onSaved = vi.fn(async () => undefined);
    const element = operation === "start" ? <ScriptWindowStart taskId="task" onSaved={onSaved}/>
        : <ScriptWindowEditor taskId="task" script={header("script", 1)} onSaved={onSaved}/>;
    const view = render(element);
    await waitFor(() => expect(screen.getByLabelText("第 1 段正文")).toBeEnabled());
    if (operation === "start") {
        fireEvent.change(screen.getByLabelText("新文案标题"), { target: { value: "首窗文案" } });
        fill(1);
    }
    if (operation !== "confirm") fireEvent.change(screen.getByLabelText("第 1 段正文"), { target: { value: "原请求私有正文" } });
    fireEvent.click(screen.getByRole("button", { name: operation === "start" ? "保存首窗" : operation === "patch" ? "保存本页修改" : "核查并采用完整文案" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "查询原请求结果" })).toBeEnabled());
    return { server, onSaved, element, view, key: sessionStorage.key(0)!, raw: sessionStorage.getItem(sessionStorage.key(0)!)! };
}

it.each(["start", "patch", "confirm"] as const)("refresh locator restores %s with no body cache or repost through unknown, 404 and failed receipts", async operation => {
    const { server, onSaved, element, view, key, raw } = await pendingWindow(operation);
    const locator = JSON.parse(raw);
    expect(locator).toEqual({ ...originalScope, taskId: "task", scriptId: operation === "start" ? null : "script", operation, requestId: server.writes[0].body.request_id });
    expect(raw).not.toContain("原请求私有正文");
    view.unmount();
    render(element); // fresh component state, with only sessionStorage retained
    expect(screen.getByRole("button", { name: operation === "start" ? "保存首窗" : "核查并采用完整文案" })).toBeDisabled();
    await screen.findByRole("region", { name: "原文案请求对账" });
    expect(screen.getByText(/刷新仅保留原请求定位/)).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "原文案请求对账" })).toHaveTextContent(locator.requestId);
    expect(screen.queryByDisplayValue("原请求私有正文")).not.toBeInTheDocument();
    expect(server.queries).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: "查询原请求结果" }));
    await screen.findByText("原请求结果仍未知。原请求继续保留。");
    server.missingOnce();
    fireEvent.click(screen.getByRole("button", { name: "查询原请求结果" }));
    await screen.findByText(/不代表请求未提交/);
    server.outcome("failed");
    fireEvent.click(screen.getByRole("button", { name: "查询原请求结果" }));
    await screen.findByText("原请求记录为失败，但提交结果仍未确认。原请求继续保留。");
    fireEvent.click(screen.getByRole("button", { name: "恢复原请求输入" }));
    const input = await screen.findByRole("region", { name: "原请求输入" });
    expect(input).toHaveTextContent(locator.requestId);
    if (operation !== "confirm") expect(input).toHaveTextContent("原请求私有正文");
    expect(server.queries.at(-1)!.searchParams.get("include_input")).toBe("true");
    expect(sessionStorage.getItem(key)).toBe(raw);
    expect(onSaved).not.toHaveBeenCalled();
    server.completeLast();
    fireEvent.click(screen.getByRole("button", { name: "查询原请求结果" }));
    await waitFor(() => expect(onSaved).toHaveBeenCalledOnce());
    expect(sessionStorage.getItem(key)).toBeNull();
    expect(server.writes).toHaveLength(1);
    for (const query of server.queries) {
        expect(query.pathname.endsWith(locator.requestId)).toBe(true);
        expect(query.searchParams.get("operation")).toBe(operation);
        expect(query.searchParams.get("script_id")).toBe(locator.scriptId);
    }
});

it.each(["getItem", "setItem"] as const)("refresh locator blocks submission when sessionStorage %s fails", async method => {
    const server = harness();
    if (method === "getItem") vi.spyOn(Storage.prototype, method).mockImplementation(() => { throw new Error("storage unavailable"); });
    render(<ScriptWindowStart taskId="task" onSaved={vi.fn(async () => undefined)}/>);
    if (method === "setItem") {
        await waitFor(() => expect(screen.getByLabelText("新文案标题")).toBeEnabled());
        fireEvent.change(screen.getByLabelText("新文案标题"), { target: { value: "私有标题" } });
        fill(1);
        vi.spyOn(Storage.prototype, method).mockImplementation(() => { throw new Error("storage unavailable"); });
        fireEvent.click(screen.getByRole("button", { name: "保存首窗" }));
    }
    await waitFor(() => expect(screen.getAllByRole("alert").some(node => node.textContent?.includes("storage unavailable"))).toBe(true));
    expect(screen.getByRole("button", { name: "保存首窗" })).toBeDisabled();
    expect(server.writes).toHaveLength(0);
});

it("refresh locator keeps a successful receipt locked if storage removal fails", async () => {
    const { server, onSaved, key, raw } = await pendingWindow();
    server.completeLast();
    vi.spyOn(Storage.prototype, "removeItem").mockImplementation(() => { throw new Error("cannot remove locator"); });
    fireEvent.click(screen.getByRole("button", { name: "查询原请求结果" }));
    await screen.findByText(/原请求恢复不可用：cannot remove locator/);
    expect(sessionStorage.getItem(key)).toBe(raw);
    expect(screen.getByRole("button", { name: "核查并采用完整文案" })).toBeDisabled();
    expect(onSaved).not.toHaveBeenCalled();
    expect(server.writes).toHaveLength(1);
});

it("refresh locator isolates a different identity and preserves the original scoped reference", async () => {
    const { server, element, view, key, raw } = await pendingWindow();
    view.unmount();
    vi.mocked(readPreflightDraftScope).mockResolvedValue({ ...originalScope, memberId: "local:member-b" });
    const other = render(element);
    await waitFor(() => expect(screen.getByLabelText("第 1 段正文")).toBeEnabled());
    expect(screen.queryByRole("region", { name: "原文案请求对账" })).not.toBeInTheDocument();
    expect(sessionStorage.getItem(key)).toBe(raw);
    expect(server.queries).toHaveLength(0);
    other.unmount();
    vi.mocked(readPreflightDraftScope).mockResolvedValue(originalScope);
    render(element);
    await screen.findByRole("region", { name: "原文案请求对账" });
    expect(server.writes).toHaveLength(1);
});

it("refresh locator discards a late input receipt after the current identity changes", async () => {
    const { server, onSaved, key, raw } = await pendingWindow();
    fireEvent.click(screen.getByRole("button", { name: "查询原请求结果" }));
    await screen.findByRole("button", { name: "恢复原请求输入" });
    const original = server.fetch.getMockImplementation()!;
    let finish!: () => void;
    server.fetch.mockImplementation(async (url, init) => {
        const response = await original(url, init);
        if (url.includes("include_input=true")) await new Promise<void>(resolve => { finish = resolve; });
        return response;
    });
    fireEvent.click(screen.getByRole("button", { name: "恢复原请求输入" }));
    await waitFor(() => expect(finish).toBeDefined());
    vi.mocked(readPreflightDraftScope).mockResolvedValue({ ...originalScope, workspaceId: "other-workspace" });
    await act(async () => { finish(); });
    await screen.findByText(/当前身份或工作区已变化/);
    expect(screen.queryByRole("region", { name: "原请求输入" })).not.toBeInTheDocument();
    expect(sessionStorage.getItem(key)).toBe(raw);
    expect(onSaved).not.toHaveBeenCalled();
    expect(server.writes).toHaveLength(1);
});

it("refresh locator rejects a receipt for a different operation without releasing the original request", async () => {
    const { server, onSaved, key, raw } = await pendingWindow();
    const original = server.fetch.getMockImplementation()!;
    server.fetch.mockImplementation(async (url, init) => url.includes("/script-window-requests/")
        ? new Response(JSON.stringify({ ...server.completeLast(), status: "confirmed" })) : original(url, init));
    fireEvent.click(screen.getByRole("button", { name: "查询原请求结果" }));
    await screen.findByText(/回执与原任务、稿件或请求操作不符/);
    expect(sessionStorage.getItem(key)).toBe(raw);
    expect(onSaved).not.toHaveBeenCalled();
    expect(server.writes).toHaveLength(1);
});
