import {fireEvent,render,screen,waitFor} from "@testing-library/react";
import {expect,it,vi} from "vitest";
import {GraphicOverlayEditor} from "../GraphicOverlayEditor";
const imports=vi.hoisted(()=>({upload:vi.fn(),list:vi.fn().mockResolvedValue([])}));
vi.mock("@/lib/api",()=>({importCreationTaskGraphic:imports.upload,getCreationTaskGraphicSources:imports.list}));
it("previews a real title draft, then selects existing metadata for editing or deletion",()=>{
 const preview=vi.fn();
 render(<GraphicOverlayEditor taskId="task" items={[{id:"title",text:"旧标题",start_seconds:0,end_seconds:2,x:.5,y:.15,width:.8,font_size:64,font_family:"PingFang SC",color:"#FFFFFF"}]} duration={6} busy={false} onPreview={preview} onDraftChange={vi.fn()}/>);
 fireEvent.change(screen.getByLabelText("标题文字"),{target:{value:"新标题"}});
 fireEvent.click(screen.getByRole("button",{name:"预览标题修改",hidden:true}));
 expect(preview).toHaveBeenLastCalledWith(expect.objectContaining({operation_type:"EditGraphicOverlay",overlay_action:"add",graphic_overlay:expect.objectContaining({text:"新标题",start_seconds:0,end_seconds:3})}));
 fireEvent.click(screen.getByRole("button",{name:"标题1 · 旧标题",hidden:true}));
 fireEvent.change(screen.getByLabelText("标题字号"),{target:{value:"96"}});
 fireEvent.click(screen.getByRole("button",{name:"预览标题修改",hidden:true}));
 expect(preview).toHaveBeenLastCalledWith(expect.objectContaining({overlay_action:"update",overlay_id:"title",graphic_overlay:expect.objectContaining({font_size:96})}));
 fireEvent.click(screen.getByRole("button",{name:"预览删除标题1",hidden:true}));
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
 fireEvent.click(screen.getByRole("button",{name:"预览图片修改",hidden:true}));
 expect(preview).toHaveBeenCalledWith(expect.objectContaining({operation_type:"EditGraphicOverlay",overlay_action:"add",
  graphic_overlay:expect.objectContaining({kind:"image",source_ref:"artifacts/task/source.png",opacity:.5,start_seconds:0,end_seconds:3})}));
});
