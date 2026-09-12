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
 fireEvent.click(screen.getByRole("button",{name:"预览调色",hidden:true}));
 expect(preview).toHaveBeenCalledWith({base_version_id:"version",clip_ids:["clip-a","clip-b"],effects:{exposure:.5,contrast:1,saturation:1,temperature:0}});
 expect(clip.visual_effects).toBeUndefined();
});
