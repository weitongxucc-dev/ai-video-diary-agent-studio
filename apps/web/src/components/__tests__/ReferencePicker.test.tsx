import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { useState } from "react";
const api=vi.hoisted(()=>({read:vi.fn(),select:vi.fn()}));
vi.mock("@/lib/api/materials",()=>({getProductionMaterials:api.read}));
vi.mock("../AssistantConversationProvider",()=>({useAssistantSelection:()=>({items:[],select:api.select})}));
import { ReferencePicker } from "../ui/reference-picker";
const asset={id:"visible-material",file_name:"现场分享.mp4",media_type:"video",updated_at:"fixed-source-version"};
function Picker(){const[open,setOpen]=useState(false);return <ReferencePicker open={open} onOpenChange={setOpen} onAdded={()=>{}}/>;}
beforeEach(()=>{vi.clearAllMocks();api.read.mockResolvedValue({assets:[asset]});});
it("browses and searches without changing references, then explicitly appends the fixed source",async()=>{
  render(<Picker/>);
  expect(api.read).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button",{name:"添加素材引用"}));
  await screen.findByRole("button",{name:/现场分享.mp4/});
  expect(api.select).not.toHaveBeenCalled();
  fireEvent.change(screen.getByRole("textbox",{name:"搜索引用素材"}),{target:{value:"会议"}});
  fireEvent.keyDown(screen.getByRole("textbox",{name:"搜索引用素材"}),{key:"Enter",isComposing:true});
  expect(api.read).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole("button",{name:/^搜索$/}));
  await waitFor(()=>expect(api.read).toHaveBeenLastCalledWith({limit:20,query:"会议"}));
  fireEvent.click(await screen.findByRole("button",{name:/现场分享.mp4/}));
  expect(api.select).toHaveBeenCalledWith([{kind:"material",label:asset.file_name,selection:{scope:"asset",asset_id:asset.id,expected_updated_at:asset.updated_at}}],true);
});
it("keeps a failed library read distinct from an empty search",async()=>{
  api.read.mockRejectedValue(new Error("素材服务不可读取"));render(<Picker/>);
  fireEvent.click(screen.getByRole("button",{name:"添加素材引用"}));
  expect(await screen.findByRole("alert")).toHaveTextContent("素材服务不可读取");
  expect(screen.queryByText("没有找到素材。")).not.toBeInTheDocument();
  expect(api.select).not.toHaveBeenCalled();
});
