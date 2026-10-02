import {fireEvent,render,screen,waitFor,within} from "@testing-library/react";
import {expect,it,vi} from "vitest";
import {ScheduledTasksWorkspace} from "../ScheduledTasksWorkspace";
import type {ScheduledTask} from "@/lib/api";

const imports=vi.hoisted(()=>({list:vi.fn(),create:vi.fn(),enable:vi.fn(),remove:vi.fn()}));
vi.mock("@/lib/api",()=>({
  listScheduledTasks:imports.list,
  createScheduledTask:imports.create,
  setScheduledTaskEnabled:imports.enable,
  deleteScheduledTask:imports.remove,
}));

function task(overrides:Partial<ScheduledTask>={}):ScheduledTask{
  return {id:"st-1",workspace_id:"ws",task_type:"material_analysis",trigger_mode:"scheduled",
    target_ref:"asset:abc123",not_before_utc:"2026-03-01T00:30:00Z",window:null,enabled:true,
    created_at:"2026-01-01T00:00:00Z",updated_at:"2026-01-01T00:00:00Z",...overrides};
}

it("renders saved task definitions with honest no-run-history boundaries",async()=>{
  imports.list.mockResolvedValue({tasks:[task(),task({id:"st-2",target_ref:"creation-task:render-9",trigger_mode:"idle",not_before_utc:null,enabled:false})],total_count:2});
  render(<ScheduledTasksWorkspace/>);
  expect(await screen.findByText("asset:abc123")).toBeInTheDocument();
  expect(screen.getByText("creation-task:render-9")).toBeInTheDocument();
  expect(screen.getByText("2 项")).toBeInTheDocument();
  expect(screen.queryByRole("heading",{name:"定时任务"})).not.toBeInTheDocument();
  expect(screen.getByText(/素材解析 · 到点 · 2026-03-01 00:30 UTC/)).toBeInTheDocument();
  expect(screen.getByText(/素材解析 · 闲时/)).toBeInTheDocument();
  expect(screen.getByText("已启用")).toBeInTheDocument();
  expect(screen.getByText("暂停中")).toBeInTheDocument();
  expect(screen.getAllByText(/执行未接线：本页只管理任务定义/).length).toBeGreaterThanOrEqual(1);
  expect(screen.queryByText(/上次[：:]/)).toBeNull();
  expect(screen.queryByText(/下次[：:]/)).toBeNull();
});

it("shows an honest empty state instead of a run schedule",async()=>{
  imports.list.mockResolvedValue({tasks:[],total_count:0});
  render(<ScheduledTasksWorkspace/>);
  expect(await screen.findByText("还没有定时任务")).toBeInTheDocument();
  expect(screen.getByText(/执行接线完成前，任务不会在到点时自动运行/)).toBeInTheDocument();
});

it("surfaces load failure with a retry that reloads real data",async()=>{
  imports.list.mockRejectedValueOnce(new Error("本地服务未接通。"));
  render(<ScheduledTasksWorkspace/>);
  expect(await screen.findByRole("alert")).toHaveTextContent("定时任务读取失败：本地服务未接通。");
  imports.list.mockResolvedValue({tasks:[task()],total_count:1});
  fireEvent.click(screen.getByRole("button",{name:"重试"}));
  expect(await screen.findByText("asset:abc123")).toBeInTheDocument();
});

it("validates the create form, edits the time field, and posts a real definition",async()=>{
  imports.list.mockResolvedValue({tasks:[],total_count:0});
  render(<ScheduledTasksWorkspace/>);
  await screen.findByText("还没有定时任务");
  fireEvent.click(screen.getByRole("button",{name:"创建任务定义"}));
  expect(screen.getByRole("alert")).toHaveTextContent("目标对象不能为空");
  expect(imports.create).not.toHaveBeenCalled();
  const types=within(screen.getByRole("group",{name:"任务类型"}));
  expect(screen.queryByRole("combobox",{name:"任务类型"})).not.toBeInTheDocument();
  expect(types.getByRole("button",{name:"素材解析"})).toHaveAttribute("aria-pressed","true");
  fireEvent.click(types.getByRole("button",{name:"成片制作"}));
  expect(types.getByRole("button",{name:"成片制作"})).toHaveAttribute("aria-pressed","true");
  expect(types.getByRole("button",{name:"素材解析"})).toHaveAttribute("aria-pressed","false");
  expect(imports.create).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText("目标对象"),{target:{value:"creation-task:render-9"}});
  fireEvent.click(screen.getByRole("button",{name:"创建任务定义"}));
  expect(screen.getByRole("alert")).toHaveTextContent("到点模式必须给出最早开始时间");
  expect(imports.create).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText("最早开始时间"),{target:{value:"2026-03-01T08:30"}});
  fireEvent.change(screen.getByLabelText("时间窗 JSON"),{target:{value:'{"hours":[2]}'}});
  imports.create.mockResolvedValue(task({id:"st-new",task_type:"creation_render",target_ref:"creation-task:render-9",not_before_utc:new Date("2026-03-01T08:30").toISOString(),window:{hours:[2]}}));
  fireEvent.click(screen.getByRole("button",{name:"创建任务定义"}));
  expect(types.getByRole("button",{name:"素材解析"})).toBeDisabled();
  expect(types.getByRole("button",{name:"成片制作"})).toBeDisabled();
  await waitFor(()=>expect(imports.create).toHaveBeenCalledTimes(1));
  expect(imports.create).toHaveBeenCalledWith({task_type:"creation_render",trigger_mode:"scheduled",
    target_ref:"creation-task:render-9",not_before_utc:new Date("2026-03-01T08:30").toISOString(),window:{hours:[2]}});
  expect(await screen.findByText("creation-task:render-9")).toBeInTheDocument();
  expect(types.getByRole("button",{name:"素材解析"})).toBeEnabled();
  expect((screen.getByLabelText("目标对象") as HTMLInputElement).value).toBe("");
});

it("toggles a definition through the enable endpoint",async()=>{
  imports.list.mockResolvedValue({tasks:[task()],total_count:1});
  render(<ScheduledTasksWorkspace/>);
  await screen.findByText("asset:abc123");
  imports.enable.mockResolvedValue(task({enabled:false}));
  fireEvent.click(screen.getByRole("button",{name:"暂停"}));
  await waitFor(()=>expect(imports.enable).toHaveBeenCalledWith("st-1",false));
  expect(await screen.findByText("暂停中")).toBeInTheDocument();
  expect(screen.getByRole("button",{name:"启动"})).toBeInTheDocument();
});

it("deletes a definition and returns to the honest empty state",async()=>{
  imports.list.mockResolvedValue({tasks:[task()],total_count:1});
  render(<ScheduledTasksWorkspace/>);
  await screen.findByText("asset:abc123");
  imports.remove.mockResolvedValue(undefined);
  fireEvent.click(screen.getByRole("button",{name:"删除"}));
  await waitFor(()=>expect(imports.remove).toHaveBeenCalledWith("st-1"));
  expect(await screen.findByText("还没有定时任务")).toBeInTheDocument();
  expect(screen.queryByText("asset:abc123")).toBeNull();
});
