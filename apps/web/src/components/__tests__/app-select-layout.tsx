import { createRoot } from "react-dom/client";
import { useState } from "react";
import { AppSelect, AppSelectOption } from "@/components/AppSelect";
import { HomeCardDialog } from "@/components/HomeCardDialog";
import "@/app/globals.css";
import "@/app/modern-overrides.css";

window.fetch=async()=>{throw new Error("隔离布局检查不请求业务API");};
function Fixture() {
  const [open,setOpen]=useState(false);
  const [value,setValue]=useState("1");
  const choices=Array.from({length:50},(_,i)=><AppSelectOption key={i} value={String(i)} disabled={i===0}>{i===0?"不可选择的布局样例":`布局样例 ${i}：用于检查长列表和内部滚动`}</AppSelectOption>);
  return <><header style={{padding:16,fontSize:13}}>隔离布局检查 · 非业务数据 <button onClick={()=>document.documentElement.dataset.theme=document.documentElement.dataset.theme==="dark"?"light":"dark"}>切换主题</button></header><div className="desktop-shell" style={{display:"block",height:"calc(100dvh - 60px)"}}><main className="desktop-workspace" style={{height:"100%",display:"block"}}><div className="desktop-main-scroll" style={{height:"100%",display:"block"}}><button onClick={()=>setOpen(true)}>打开卡内选择器</button></div></main></div><div style={{position:"fixed",right:12,bottom:12,width:210}}><AppSelect aria-label="屏边长列表" value={value} onValueChange={setValue}>{choices}</AppSelect></div>{open?<HomeCardDialog title="卡内选择器布局样例" onClose={()=>setOpen(false)}><label>布局选项<AppSelect aria-label="卡内长列表" value={value} onValueChange={setValue}>{choices}</AppSelect></label><p>当前选择：{value}（仅本页面状态）</p></HomeCardDialog>:null}</>;
}
createRoot(document.getElementById("fixture-root")!).render(<Fixture/>);
