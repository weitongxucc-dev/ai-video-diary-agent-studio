import {fireEvent,render,screen,waitFor} from "@testing-library/react";
import {expect,it,vi} from "vitest";
import {GraphicOverlayEditor} from "../GraphicOverlayEditor";
const imports=vi.hoisted(()=>({upload:vi.fn(),list:vi.fn().mockResolvedValue([])}));
vi.mock("@/lib/api",()=>({importCreationTaskGraphic:imports.upload,getCreationTaskGraphicSources:imports.list}));
it("applies a real title draft, then selects existing metadata for editing or deletion",()=>{
 const preview=vi.fn();
 render(<GraphicOverlayEditor taskId="task" items={[{id:"title",text:"旧标题",start_seconds:0,end_seconds:2,x:.5,y:.15,width:.8,font_size:64,font_family:"PingFang SC",color:"#FFFFFF"}]} duration={6} busy={false} onPreview={preview} onDraftChange={vi.fn()}/>);
 fireEvent.change(screen.getByLabelText("标题文字"),{target:{value:"新标题"}});
 fireEvent.click(screen.getByRole("button",{name:"保存标题草稿",hidden:true}));
 expect(preview).toHaveBeenLastCalledWith(expect.objectContaining({operation_type:"EditGraphicOverlay",overlay_action:"add",graphic_overlay:expect.objectContaining({text:"新标题",start_seconds:0,end_seconds:3})}));
 fireEvent.click(screen.getByRole("button",{name:"标题1 · 旧标题",hidden:true}));
 fireEvent.change(screen.getByLabelText("标题字号"),{target:{value:"96"}});
 fireEvent.click(screen.getByRole("button",{name:"保存标题草稿",hidden:true}));
 expect(preview).toHaveBeenLastCalledWith(expect.objectContaining({overlay_action:"update",overlay_id:"title",graphic_overlay:expect.objectContaining({font_size:96})}));
 fireEvent.click(screen.getByRole("button",{name:"删除标题1",hidden:true}));
 expect(preview).toHaveBeenLastCalledWith({operation_type:"EditGraphicOverlay",overlay_action:"delete",overlay_id:"title"});
});

it("imports an image before proposing its opacity and timing",async()=>{
 const preview=vi.fn();
 imports.upload.mockResolvedValue({artifact_id:"image",source_ref:"artifacts/task/source.png",name:"logo.png",width:300,height:160});
 render(<GraphicOverlayEditor taskId="task" items={[]} duration={6} busy={false} onPreview={preview} onDraftChange={vi.fn()}/>);
 const file=new File(["mocked-upload"],"logo.png",{type:"image/png"});
 fireEvent.change(screen.getByLabelText("导入图片或标识"),{target:{files:[file]}});
 await waitFor(()=>expect(screen.getByLabelText("图片不透明度")).toBeInTheDocument());
 expect(imports.upload).toHaveBeenCalledWith("task",file);
 expect(preview).not.toHaveBeenCalled();
 fireEvent.change(screen.getByLabelText("图片不透明度"),{target:{value:"0.5"}});
 fireEvent.click(screen.getByRole("button",{name:"保存图片草稿",hidden:true}));
 expect(preview).toHaveBeenCalledWith(expect.objectContaining({operation_type:"EditGraphicOverlay",overlay_action:"add",
  graphic_overlay:expect.objectContaining({kind:"image",source_ref:"artifacts/task/source.png",opacity:.5,start_seconds:0,end_seconds:3})}));
});

it("starts an arrow draft with contract defaults and submits kind arrow",()=>{
 const preview=vi.fn();
 render(<GraphicOverlayEditor taskId="task" items={[]} duration={6} busy={false} onPreview={preview} onDraftChange={vi.fn()}/>);
 fireEvent.click(screen.getByRole("button",{name:"新建箭头标注",hidden:true}));
 expect(screen.getByLabelText("箭头名称")).toHaveValue("");
 expect(screen.getByLabelText("箭头作用说明")).toHaveValue("");
 expect(screen.getByLabelText("标注角")).toHaveAttribute("data-selected-value", "right-top");
 expect(screen.getByLabelText("箭头颜色（#RRGGBB）")).toHaveValue("#FFFFFF");
 expect((screen.getByLabelText("指向 X（0–1）") as HTMLInputElement).value).toBe("");
 fireEvent.change(screen.getByLabelText("箭头名称"),{target:{value:"电源按钮"}});
 fireEvent.change(screen.getByLabelText("指向 X（0–1）"),{target:{value:"0.3"}});
 fireEvent.change(screen.getByLabelText("指向 Y（0–1）"),{target:{value:"0.4"}});
 fireEvent.click(screen.getByRole("button",{name:"保存箭头草稿",hidden:true}));
 expect(preview).toHaveBeenLastCalledWith(expect.objectContaining({operation_type:"EditGraphicOverlay",overlay_action:"add",
  graphic_overlay:expect.objectContaining({kind:"arrow",name:"电源按钮",purpose:"",x:.3,y:.4,position:"right-top",color:"#FFFFFF",start_seconds:0,end_seconds:3})}));
});

it("keeps arrow submission invalid while coordinates are missing",()=>{
 const preview=vi.fn();
 render(<GraphicOverlayEditor taskId="task" items={[]} duration={6} busy={false} onPreview={preview} onDraftChange={vi.fn()}/>);
 fireEvent.click(screen.getByRole("button",{name:"新建箭头标注",hidden:true}));
 fireEvent.change(screen.getByLabelText("箭头名称"),{target:{value:"电源按钮"}});
 fireEvent.click(screen.getByRole("button",{name:"保存箭头草稿",hidden:true}));
 expect((screen.getByLabelText("指向 X（0–1）") as HTMLInputElement).validity.valid).toBe(false);
 expect(preview).not.toHaveBeenCalled();
});

it("blocks arrow submission when the name is too long or the interval is under 0.04s",()=>{
 const preview=vi.fn();
 render(<GraphicOverlayEditor taskId="task" items={[]} duration={6} busy={false} onPreview={preview} onDraftChange={vi.fn()}/>);
 fireEvent.click(screen.getByRole("button",{name:"新建箭头标注",hidden:true}));
 fireEvent.change(screen.getByLabelText("箭头名称"),{target:{value:"超".repeat(25)}});
 fireEvent.change(screen.getByLabelText("指向 X（0–1）"),{target:{value:"0.3"}});
 fireEvent.change(screen.getByLabelText("指向 Y（0–1）"),{target:{value:"0.4"}});
 fireEvent.click(screen.getByRole("button",{name:"保存箭头草稿",hidden:true}));
 expect(screen.getByRole("alert")).toHaveTextContent("1–24");
 expect(preview).not.toHaveBeenCalled();
 fireEvent.change(screen.getByLabelText("箭头名称"),{target:{value:"电源按钮"}});
 fireEvent.change(screen.getByLabelText("开始（秒）"),{target:{value:"1"}});
 fireEvent.change(screen.getByLabelText("结束（秒）"),{target:{value:"1.02"}});
 fireEvent.click(screen.getByRole("button",{name:"保存箭头草稿",hidden:true}));
 expect(screen.getByRole("alert")).toHaveTextContent("0.04");
 expect(preview).not.toHaveBeenCalled();
 fireEvent.change(screen.getByLabelText("结束（秒）"),{target:{value:"1.2"}});
 fireEvent.click(screen.getByRole("button",{name:"保存箭头草稿",hidden:true}));
 expect(preview).toHaveBeenCalledTimes(1);
});

it("selects an existing arrow overlay for editing and updates it",()=>{
 const preview=vi.fn();
 render(<GraphicOverlayEditor taskId="task" items={[{kind:"arrow",id:"arrow-1",x:.6,y:.7,name:"电源按钮",purpose:"指示开机键",start_seconds:0,end_seconds:2,position:"left-top",color:"#00FF00"}]} duration={6} busy={false} onPreview={preview} onDraftChange={vi.fn()}/>);
 fireEvent.click(screen.getByRole("button",{name:"箭头1 · 电源按钮",hidden:true}));
 expect(screen.getByLabelText("标注角")).toHaveAttribute("data-selected-value", "left-top");
 expect(screen.getByLabelText("箭头颜色（#RRGGBB）")).toHaveValue("#00FF00");
 fireEvent.click(screen.getByRole("button",{name:"保存箭头草稿",hidden:true}));
 expect(preview).toHaveBeenLastCalledWith(expect.objectContaining({operation_type:"EditGraphicOverlay",overlay_action:"update",overlay_id:"arrow-1",
  graphic_overlay:expect.objectContaining({kind:"arrow",x:.6,y:.7,name:"电源按钮",purpose:"指示开机键",position:"left-top",color:"#00FF00"})}));
});
