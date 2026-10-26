import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ScriptLibraryPanel } from "@/app/knowledge/KnowledgeWorkspace";
import type { ScriptLibraryEntryHeader } from "@/lib/types/script-library";

const mocks = vi.hoisted(() => ({ list: vi.fn(), sources: vi.fn(), reuse: vi.fn(), push: vi.fn(), header: vi.fn(), sections: vi.fn() }));
vi.mock("@/lib/api", () => ({
  listScriptLibraryEntries: mocks.list, getScriptLibrarySources: mocks.sources,
  reuseScriptLibraryEntry: mocks.reuse,
}));
vi.mock("@/lib/api/script-library", async original => ({ ...await original<typeof import("@/lib/api/script-library")>(), getScriptLibraryEntry: mocks.header, getScriptLibrarySections: mocks.sections }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: mocks.push }) }));
vi.mock("@/components/AppShell", () => ({ AppShell: () => null }));

const body = { id: "section-1", title: "特点", content: "本人已确认的历史正文", visual_prompt: "原画面", reference_ids: [] as string[] };
const entry: ScriptLibraryEntryHeader = {
  id: "entry-1", name: "维护介绍", category: "产品", version: 1, title: "模块化介绍", language: "zh-CN",
  source_task_id: "original-task", source_script_id: "original-script", source_run_id: "", source_topic_id: "topic",
  supersedes_entry_id: null, created_at: "2026-10-25T00:00:00Z", updated_at: "2026-10-25T00:00:00Z",
  sections: null, sections_loaded: false, sections_total_count: 1,
};
function sectionPage(header:ScriptLibraryEntryHeader=entry,after=0) {
  const items=Array.from({length:Math.min(20,header.sections_total_count-after)},(_,index)=>{
    const ordinal=after+index+1;
    return {ordinal,digest:`digest-${ordinal}`,section:{...body,id:`${header.id}-section-${ordinal}`,content:header.sections_total_count===1?body.content:`库稿正文 ${header.id} ${ordinal}`}};
  });
  const next=after+items.length<header.sections_total_count?after+items.length:null;
  return {entry_id:header.id,version:header.version,updated_at:header.updated_at,total_count:header.sections_total_count,items,next_after_ordinal:next,has_more:next!==null};
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.list.mockReset().mockResolvedValue({ items: [entry], next: null, total_count: null });
  mocks.header.mockReset().mockResolvedValue(entry);
  mocks.sections.mockReset().mockImplementation(async(id:string,input:{after_ordinal?:number;expected_updated_at:string})=>sectionPage({...entry,id,updated_at:input.expected_updated_at,version:input.expected_updated_at===entry.updated_at?1:2},input.after_ordinal??0));
  mocks.sources.mockResolvedValue([]);
  mocks.reuse.mockResolvedValue({ task_id: "new-task", script: { selection_status: "draft" } });
});

afterEach(() => {
  vi.unstubAllGlobals();
  window.history.replaceState({}, "", "/knowledge");
});

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(done => { resolve = done; });
  return { promise, resolve };
}

it("allows an unreferenced owned script only after explicit new requirements confirmation", async () => {
  render(<ScriptLibraryPanel />);
  fireEvent.click(await screen.findByRole("button", { name: /维护介绍/ }));
  await screen.findByText("本人文案没有外部事实引用。");
  const button = screen.getByRole("button", { name: "创建待确认新稿" });
  expect(button).toBeDisabled();
  fireEvent.click(screen.getByRole("checkbox"));
  fireEvent.click(button);
  await waitFor(() => expect(mocks.push).toHaveBeenCalledWith("/tasks/new-task"));
  expect(mocks.reuse).toHaveBeenCalledWith("entry-1", {
    name: "维护介绍 · 新任务", aspect_ratio: "9:16", duration_target_seconds: 60,
    requirements_confirmed: true,
  });
});

it("opens the exact saved entry from its task deep link and retains the source-task return", async () => {
  window.history.replaceState({}, "", "/knowledge?entry_id=entry-1&source_task_id=original-task");
  render(<ScriptLibraryPanel />);
  expect(await screen.findByRole("heading", { name: "维护介绍 · 版本 1" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "查看原任务" })).toHaveAttribute("href", "/tasks/original-task");
});

it("keeps owned historical text visible when a source changes, but refuses reuse", async () => {
  mocks.sources.mockResolvedValue([{ reference_id: "knowledge:item:1", status: "version_changed" }]);
  render(<ScriptLibraryPanel />);
  fireEvent.click(await screen.findByRole("button", { name: /维护介绍/ }));
  await screen.findByText(/版本或授权已变化/);
  expect(screen.getByText("本人已确认的历史正文")).toBeVisible();
  fireEvent.click(screen.getByRole("checkbox"));
  expect(screen.getByRole("button", { name: "创建待确认新稿" })).toBeDisabled();
  expect(mocks.reuse).not.toHaveBeenCalled();
  fireEvent.change(screen.getByRole("textbox", { name: "搜索文案" }), { target: { value: "维护" } });
  await waitFor(() => expect(mocks.list).toHaveBeenLastCalledWith("维护", ""));
  fireEvent.change(screen.getByRole("textbox", { name: "筛选文案分类" }), { target: { value: "产品" } });
  await waitFor(() => expect(mocks.list).toHaveBeenLastCalledWith("维护", "产品"));
});

it("retries a failed filter read and drops a selection outside the successful result", async () => {
  mocks.list.mockReset()
    .mockResolvedValueOnce({ items: [entry], next: null, total_count: null })
    .mockRejectedValueOnce(new Error("暂时不可用"))
    .mockResolvedValueOnce({ items: [], next: null, total_count: null });
  render(<ScriptLibraryPanel />);
  fireEvent.click(await screen.findByRole("button", { name: /维护介绍/ }));
  await screen.findByText("本人文案没有外部事实引用。");
  fireEvent.change(screen.getByRole("textbox", { name: "搜索文案" }), { target: { value: "其他" } });
  expect(await screen.findByRole("alert")).toHaveTextContent("搜索“全部”，分类“全部”");
  expect(screen.getByRole("button", { name: "重试读取文案" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "重试读取文案" }));
  await screen.findByText(/没有匹配的可用文案/);
  expect(screen.queryByRole("region", { name: "文案复用详情" })).not.toBeInTheDocument();
});

it("offers retry when source verification fails", async () => {
  mocks.sources.mockRejectedValueOnce(new Error("来源暂不可读")).mockResolvedValueOnce([]);
  render(<ScriptLibraryPanel />);
  fireEvent.click(await screen.findByRole("button", { name: /维护介绍/ }));
  expect(await screen.findByRole("alert")).toHaveTextContent("来源暂不可读");
  fireEvent.click(screen.getByRole("button", { name: "重试核对来源" }));
  await screen.findByText("本人文案没有外部事实引用。");
  expect(mocks.sources).toHaveBeenCalledTimes(2);
});

it("keeps source and run identifiers inside on-demand diagnostics", async () => {
  const withDiagnostics = { ...entry, source_run_id: "run-123" };
  mocks.list.mockResolvedValue({ items: [withDiagnostics], next: null, total_count: null });
  mocks.sections.mockResolvedValue({...sectionPage(),items:[{...sectionPage().items[0],section:{...body,reference_ids:["knowledge:item:version"]}}]});
  render(<ScriptLibraryPanel />);
  fireEvent.click(await screen.findByRole("button", { name: /维护介绍/ }));
  await screen.findByText("本人文案没有外部事实引用。");
  const diagnostics = screen.getAllByText("技术诊断信息");
  expect(screen.getByText(/原稿记录：original-script · 运行记录：run-123/)).not.toBeVisible();
  fireEvent.click(diagnostics.at(-1)!);
  expect(screen.getByText(/原稿记录：original-script · 运行记录：run-123/)).toBeVisible();
  expect(screen.getByText("引用记录：knowledge:item:version")).not.toBeVisible();
});

it("keeps verified sources and the current new-task confirmation when reselecting the same version", async () => {
  render(<ScriptLibraryPanel />);
  const card = await screen.findByRole("button", { name: /维护介绍/ });
  fireEvent.click(card);
  await screen.findByText("本人文案没有外部事实引用。");
  fireEvent.click(screen.getByRole("checkbox"));
  expect(screen.getByRole("checkbox")).toBeChecked();
  fireEvent.click(card);
  expect(screen.getByRole("checkbox")).toBeChecked();
  expect(mocks.sources).toHaveBeenCalledTimes(1);
});

it("rechecks a new version of the same entry and ignores the old version response", async () => {
  const olderSources = deferred<Array<{ reference_id: string; status: string }>>();
  const newerSources = deferred<Array<{ reference_id: string; status: string }>>();
  const newerEntry = { ...entry, version: 2, updated_at: "2026-10-25T00:01:00Z" };
  mocks.list.mockReset()
    .mockResolvedValueOnce({ items: [entry], next: null, total_count: null })
    .mockResolvedValueOnce({ items: [newerEntry], next: null, total_count: null });
  mocks.sources.mockReset().mockReturnValueOnce(olderSources.promise).mockReturnValueOnce(newerSources.promise);
  render(<ScriptLibraryPanel />);
  fireEvent.click(await screen.findByRole("button", { name: /维护介绍/ }));
  fireEvent.click(screen.getByRole("checkbox"));
  expect(screen.getByRole("checkbox")).toBeChecked();
  fireEvent.change(screen.getByRole("textbox", { name: "搜索文案" }), { target: { value: "维护" } });
  expect(await screen.findByRole("heading", { name: "维护介绍 · 版本 2" })).toBeInTheDocument();
  expect(screen.getByRole("checkbox")).not.toBeChecked();
  expect(mocks.sources).toHaveBeenCalledTimes(2);
  await act(async () => { olderSources.resolve([{ reference_id: "old-version-reference", status: "readable" }]); });
  expect(screen.queryByText("old-version-reference")).not.toBeInTheDocument();
  await act(async () => { newerSources.resolve([]); });
  expect(await screen.findByText("本人文案没有外部事实引用。")).toBeInTheDocument();
});

it('uses the server metadata cursor without a fabricated total and preserves a selected header when listing another page',async()=>{
  const first=Array.from({length:20},(_,index)=>({...entry,id:`entry-${index+1}`,name:`库条目 ${index+1}`}));
  const cursor={after_created_at:'2026-10-24T00:00:00Z',after_entry_id:'entry-20'};
  mocks.list.mockImplementation(async(_query:string,_category:string,after?:typeof cursor)=>after?{items:[{...entry,id:'entry-21',name:'库条目 21'}],next:null,total_count:null}:{items:first,next:cursor,total_count:null});
  render(<ScriptLibraryPanel/>);
  fireEvent.click(await screen.findByRole('button',{name:/^库条目 1(?!\d)/}));
  await screen.findByRole('heading',{name:'库条目 1 · 版本 1'});
  fireEvent.click(screen.getByRole('button',{name:'下一页文案条目'}));
  await screen.findByRole('button',{name:/^库条目 21(?!\d)/});
  expect(mocks.list).toHaveBeenLastCalledWith('','',cursor);
  expect(screen.getByRole('heading',{name:'库条目 1 · 版本 1'})).toBeInTheDocument();
  expect(screen.getByRole('button',{name:'下一页文案条目'})).toBeDisabled();
  fireEvent.click(screen.getByRole('button',{name:'上一页文案条目'}));
  await screen.findByRole('button',{name:/^库条目 20(?!\d)/});
  expect(mocks.list).toHaveBeenLastCalledWith('','');
  expect(screen.queryByText(/全库共/)).not.toBeInTheDocument();
});

it('opens a deep-linked header outside the first metadata page and reads all 45 sections through version-bound windows',async()=>{
  const target={...entry,id:'entry-45',name:'深链库稿',sections_total_count:45};
  window.history.replaceState({},'','/knowledge?entry_id=entry-45&source_task_id=original-task');
  mocks.list.mockResolvedValue({items:[],next:{after_created_at:entry.created_at,after_entry_id:'entry-20'},total_count:null});
  mocks.header.mockResolvedValue(target);
  mocks.sections.mockImplementation(async(id:string,input:{after_ordinal:number;expected_updated_at:string})=>{
    expect(id).toBe(target.id);expect(input.expected_updated_at).toBe(target.updated_at);
    return sectionPage(target,input.after_ordinal);
  });
  render(<ScriptLibraryPanel/>);
  await screen.findByText('库稿正文 entry-45 1');
  expect(mocks.header).toHaveBeenCalledWith(target.id);
  expect(screen.getAllByRole('article')).toHaveLength(20);
  fireEvent.click(screen.getByRole('button',{name:'下一页文案正文'}));
  await screen.findByText('库稿正文 entry-45 21');
  expect(screen.queryByText('库稿正文 entry-45 1')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button',{name:'下一页文案正文'}));
  await screen.findByText('库稿正文 entry-45 45');
  expect(screen.getAllByRole('article')).toHaveLength(5);
  expect(screen.getByRole('button',{name:'下一页文案正文'})).toBeDisabled();
  expect(mocks.list).toHaveBeenCalledTimes(1);
});

it('clears a stale body window, reloads its exact header and invalidates confirmation before showing the new version',async()=>{
  const old={...entry,sections_total_count:45},next={...entry,version:2,updated_at:'2026-10-25T00:01:00Z',sections_total_count:45};
  mocks.list.mockResolvedValue({items:[old],next:null,total_count:null});
  mocks.header.mockResolvedValue(next);
  mocks.sections.mockResolvedValueOnce(sectionPage(old)).mockRejectedValueOnce(new Error('文案版本已变化，请重新读取当前稿件')).mockResolvedValueOnce(sectionPage(next));
  render(<ScriptLibraryPanel/>);
  fireEvent.click(await screen.findByRole('button',{name:/维护介绍/}));
  await screen.findByText('库稿正文 entry-1 1');
  fireEvent.click(screen.getByRole('checkbox'));
  fireEvent.click(screen.getByRole('button',{name:'下一页文案正文'}));
  await screen.findByText('文案版本已变化，请重新读取当前稿件');
  expect(screen.queryByText('库稿正文 entry-1 1')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button',{name:'重新读取文案版本'}));
  await screen.findByRole('heading',{name:'维护介绍 · 版本 2'});
  await screen.findByText('库稿正文 entry-1 1');
  expect(screen.getByRole('checkbox')).not.toBeChecked();
  expect(mocks.sections).toHaveBeenLastCalledWith(entry.id,{after_ordinal:0,expected_updated_at:next.updated_at});
});

it('does not let a late deep-link header overwrite a manually selected different entry',async()=>{
  window.history.replaceState({},'','/knowledge?entry_id=entry-1&source_task_id=original-task');
  const delayed=deferred<typeof entry>(),other={...entry,id:'other-entry',name:'用户当前选择'};
  mocks.header.mockReturnValue(delayed.promise);
  mocks.list.mockResolvedValue({items:[other],next:null,total_count:null});
  render(<ScriptLibraryPanel/>);
  fireEvent.click(await screen.findByRole('button',{name:/用户当前选择/}));
  await screen.findByRole('heading',{name:'用户当前选择 · 版本 1'});
  await act(async()=>{delayed.resolve(entry);});
  expect(screen.getByRole('heading',{name:'用户当前选择 · 版本 1'})).toBeInTheDocument();
  expect(screen.queryByRole('heading',{name:'维护介绍 · 版本 1'})).not.toBeInTheDocument();
});

it('does not substitute another source task for a deep-linked library entry',async()=>{
  window.history.replaceState({},'','/knowledge?entry_id=entry-1&source_task_id=wrong-task');
  render(<ScriptLibraryPanel/>);
  await screen.findByText('文案版本与来源任务不匹配；没有打开其他版本。');
  expect(screen.queryByRole('heading',{name:'维护介绍 · 版本 1'})).not.toBeInTheDocument();
  expect(mocks.sections).not.toHaveBeenCalled();
});

it('encodes the formal list cursor, exact header and versioned section GET without a mutation body',async()=>{
  const api=await vi.importActual<typeof import('@/lib/api/script-library')>('@/lib/api/script-library');
  const fetch=vi.fn(async()=>new Response(JSON.stringify({})));
  vi.stubGlobal('fetch',fetch);
  const cursor={after_created_at:'2026-10-25T00:00:00+00:00',after_entry_id:'entry/20'};
  await api.listScriptLibraryEntries('库稿','产品',cursor);
  await api.getScriptLibraryEntry('entry/45');
  await api.getScriptLibrarySections('entry/45',{after_ordinal:20,expected_updated_at:entry.updated_at});
  const [list,header,sections]=fetch.mock.calls as unknown as [string,RequestInit?][];
  const query=new URL(list[0],'http://local').searchParams;
  expect(query.get('after_created_at')).toBe(cursor.after_created_at);
  expect(query.get('after_entry_id')).toBe(cursor.after_entry_id);
  expect(header[0]).toBe('/api/production/script-library/entry%2F45');
  expect(new URL(sections[0],'http://local').searchParams.get('expected_updated_at')).toBe(entry.updated_at);
  expect(new URL(sections[0],'http://local').searchParams.get('after_ordinal')).toBe('20');
  for(const [,init] of [list,header,sections]) expect(init?.body).toBeUndefined();
});
