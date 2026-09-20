import {fireEvent,render,screen} from "@testing-library/react";
import {expect,it,vi} from "vitest";
import type {CreationTaskMontageClip} from "@/lib/types";
import {VisualEffectsInspector} from "../VisualEffectsInspector";

it("previews explicit color values and an explicit multi-clip scope",()=>{
 const preview=vi.fn();
 const clip={id:"clip-a",locked:false} as CreationTaskMontageClip;
 render(<VisualEffectsInspector clip={clip} allClipIds={["clip-a","clip-b"]} versionId="version" busy={false} onPreview={preview} onDraftChange={vi.fn()}/>);
 fireEvent.change(screen.getByLabelText("曝光（EV）"),{target:{value:"0.5"}});
 fireEvent.click(screen.getByLabelText("应用同一组参数到全部镜头"));
 fireEvent.click(screen.getByRole("button",{name:"预览画面修改",hidden:true}));
 expect(preview).toHaveBeenCalledWith({base_version_id:"version",clip_ids:["clip-a","clip-b"],effects:{zoom:1,rotation:0,offset_x:0,offset_y:0,beauty_strength:0,skin_exposure:0,exposure:.5,contrast:1,saturation:1,temperature:0}});
 expect(clip.visual_effects).toBeUndefined();
});


it("previews geometry with existing color and marks draft changes",()=>{
 const preview=vi.fn(),draft=vi.fn();
 const clip={id:"clip-a",locked:false,visual_effects:{exposure:.5,contrast:1,saturation:1,temperature:0}} as CreationTaskMontageClip;
 render(<VisualEffectsInspector clip={clip} allClipIds={["clip-a"]} versionId="version" busy={false} onPreview={preview} onDraftChange={draft}/>);
 fireEvent.change(screen.getByLabelText("缩放倍数"),{target:{value:"0.8"}});
 fireEvent.change(screen.getByLabelText("旋转角度（顺时针）"),{target:{value:"30"}});
 fireEvent.change(screen.getByLabelText("水平位置（画幅宽度比例）"),{target:{value:"0.1"}});
 fireEvent.click(screen.getByRole("button",{name:"预览画面修改",hidden:true}));
 expect(preview).toHaveBeenCalledWith(expect.objectContaining({clip_ids:["clip-a"],effects:expect.objectContaining({zoom:.8,rotation:30,offset_x:.1,exposure:.5})}));
 expect(draft).toHaveBeenCalledTimes(3);
 expect(clip.visual_effects?.zoom).toBeUndefined();
});
