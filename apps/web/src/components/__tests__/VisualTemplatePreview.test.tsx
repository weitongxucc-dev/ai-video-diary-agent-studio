import {render,screen,fireEvent,waitFor} from "@testing-library/react";
import {afterEach,expect,it,vi} from "vitest";
import type {VisualTemplate} from "@/lib/types";
import {VisualTemplatePreview} from "../VisualTemplatePreview";
import {MediaVisibilityContext} from "../ui/media-visibility";

vi.mock("@/lib/video-template-api",()=>({visualTemplateDocument:vi.fn().mockResolvedValue({document:"<html><body>测试画面</body></html>"})}));
afterEach(()=>vi.unstubAllGlobals());
const definition:VisualTemplate={engine:"openfilm-html-v1",category:"title",width:1000,height:400,duration_seconds:4,
  source:"window.film={frame(){}}",bindings:[],values:{},dependencies:[],provenance:[]};
it("releases a hidden frame and fits the new viewport when its retained editor reopens",async()=>{
  let width=500;
  vi.stubGlobal("ResizeObserver",class {
    constructor(private callback:ResizeObserverCallback){}
    observe(target:Element){this.callback([{target,contentRect:{width}} as ResizeObserverEntry],this as unknown as ResizeObserver);}
    disconnect(){}
  });
  const content=(visible:boolean)=><MediaVisibilityContext.Provider value={visible}><VisualTemplatePreview definition={definition}/></MediaVisibilityContext.Provider>;
  const view=render(content(true));
  const frame=await screen.findByTitle("视觉模板画面");
  await waitFor(()=>expect(frame).toHaveStyle({transform:"scale(0.5)"}));
  fireEvent.click(screen.getByRole("button",{name:"播放动画"}));
  view.rerender(content(false));
  expect(screen.queryByTitle("视觉模板画面")).not.toBeInTheDocument();
  width=250;view.rerender(content(true));
  await waitFor(()=>expect(screen.getByTitle("视觉模板画面")).toHaveStyle({transform:"scale(0.25)"}));
  expect(screen.getByRole("button",{name:"播放动画"})).toBeInTheDocument();
});
