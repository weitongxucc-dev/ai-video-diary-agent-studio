import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useRef, useState } from "react";
import { describe, expect, it } from "vitest";
import { CardSelect, HomeCardDialog } from "../HomeCardDialog";
import { MediaVisibility } from "../ui/media-visibility";

function Example({kind="modal"}:{kind?:"modal"|"detail"|"confirmation"}) {
  const [open,setOpen]=useState(false);
  const trigger=useRef<HTMLButtonElement>(null);
  return <><button ref={trigger} onClick={()=>setOpen(true)}>打开</button><HomeCardDialog open={open} title="对象表单" kind={kind} anchor={trigger} onClose={()=>setOpen(false)}
    footer={kind === "confirmation" ? <><button data-card-cancel onClick={()=>setOpen(false)}>取消删除</button><button onClick={()=>setOpen(false)}>删除</button></> : undefined}>
    <input aria-label="草稿" defaultValue="原稿"/>
  </HomeCardDialog></>;
}

describe("standard overlay primitives",()=>{
  it("hides an inactive page's portal and restores its unsaved form when returning", async () => {
    const user = userEvent.setup();
    const view = render(<MediaVisibility visible><Example kind="detail"/></MediaVisibility>);
    await user.click(screen.getByRole("button", { name: "打开" }));
    const draft = screen.getByRole("textbox", { name: "草稿" });
    await user.clear(draft); await user.type(draft, "保留未提交内容");
    view.rerender(<MediaVisibility visible={false}><Example kind="detail"/></MediaVisibility>);
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    view.rerender(<MediaVisibility visible><Example kind="detail"/></MediaVisibility>);
    expect(screen.getByRole("textbox", { name: "草稿" })).toBe(draft);
    expect(draft).toHaveValue("保留未提交内容");
  });
  it("opens a modal dialog and restores trigger focus without losing its draft",async()=>{
    const user=userEvent.setup(); render(<Example/>);
    await user.click(screen.getByRole("button",{name:"打开"}));
    expect(screen.getByRole("dialog",{name:"对象表单"})).toHaveAttribute("aria-modal","true");
    await user.clear(screen.getByRole("textbox",{name:"草稿"}));
    await user.type(screen.getByRole("textbox",{name:"草稿"}),"保留内容");
    await user.keyboard("{Escape}");
    await waitFor(()=>expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    await waitFor(()=>expect(screen.getByRole("button",{name:"打开"})).toHaveFocus());
    await user.click(screen.getByRole("button",{name:"打开"}));
    expect(screen.getByRole("textbox",{name:"草稿"})).toHaveValue("保留内容");
  });
  it("uses a nonmodal sheet for details",async()=>{
    const user=userEvent.setup();render(<Example kind="detail"/>);
    await user.click(screen.getByRole("button",{name:"打开"}));
    expect(screen.getByRole("dialog")).not.toHaveAttribute("aria-modal","true");
    await user.click(screen.getByRole("button",{name:"关闭卡片"}));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
  it("requires the explicit AlertDialog decision and has only one cancel action",async()=>{
    const user=userEvent.setup();render(<Example kind="confirmation"/>);
    await user.click(screen.getByRole("button",{name:"打开"}));
    expect(screen.getByRole("alertdialog")).toHaveAttribute("aria-modal","true");
    expect(screen.getAllByRole("button",{name:/取消/})).toHaveLength(1);
    await waitFor(()=>expect(screen.getByRole("button",{name:"取消删除"})).toHaveFocus());
    await user.keyboard("{Escape}");expect(screen.getByRole("alertdialog")).toBeVisible();
    await user.click(screen.getByRole("button",{name:"取消删除"}));
    await waitFor(()=>expect(screen.getByRole("button",{name:"打开"})).toHaveFocus());
  });
  it("closes only the child dialog with Escape and returns to its parent trigger",async()=>{
    function Nested(){const[child,setChild]=useState(false);return <HomeCardDialog title="父表单" onClose={()=>{}}><button onClick={()=>setChild(true)}>子表单</button>{child?<HomeCardDialog title="子表单" onClose={()=>setChild(false)}><input aria-label="子字段"/></HomeCardDialog>:null}</HomeCardDialog>;}
    const user=userEvent.setup();render(<Nested/>);
    await user.click(screen.getByRole("button",{name:"子表单"}));
    expect(screen.getByRole("dialog",{name:"子表单"})).toBeVisible();
    await user.keyboard("{Escape}");
    await waitFor(()=>expect(screen.queryByRole("dialog",{name:"子表单"})).not.toBeInTheDocument());
    expect(screen.getByRole("dialog",{name:"父表单"})).toBeVisible();
    await waitFor(()=>expect(screen.getByRole("button",{name:"子表单"})).toHaveFocus());
  });
  it("uses a trigger-anchored selection popover and returns the selected value",async()=>{
    function Selection(){const[value,setValue]=useState("one");return <CardSelect label="版本" value={value} onChange={setValue} options={[{value:"one",label:"版本一"},{value:"two",label:"版本二"}]}/>;}
    const user=userEvent.setup();render(<Selection/>);
    await user.click(screen.getByRole("button",{name:"版本"}));
    await user.click(screen.getByRole("option",{name:"版本二"}));
    expect(screen.getByRole("button",{name:"版本"})).toHaveTextContent("版本二");
    await waitFor(()=>expect(screen.getByRole("button",{name:"版本"})).toHaveFocus());
  });
});
