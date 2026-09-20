import {fireEvent,render,screen} from "@testing-library/react";
import {expect,it,vi} from "vitest";
import {GraphicOverlayEditor} from "../GraphicOverlayEditor";
it("previews a real title draft, then selects existing metadata for editing or deletion",()=>{
 const preview=vi.fn();
 render(<GraphicOverlayEditor items={[{id:"title",text:"旧标题",start_seconds:0,end_seconds:2,x:.5,y:.15,width:.8,font_size:64,font_family:"PingFang SC",color:"#FFFFFF"}]} duration={6} busy={false} onPreview={preview} onDraftChange={vi.fn()}/>);
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
