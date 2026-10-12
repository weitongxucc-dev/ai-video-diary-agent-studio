import { createRoot } from "react-dom/client";
import { useState } from "react";
import { HomeWorkspace } from "@/components/HomeWorkspace";
import { home100LayoutTasks } from "./home-100-layout-data";
import type { HomeAggregation, MaterialLibraryInsights } from "@/lib/types";
import "@/app/globals.css";
import "@/app/modern-overrides.css";

// This isolated fixture cannot fetch production facts or navigate to business routes.
window.fetch=async()=>{throw new Error("布局fixture禁止API请求");};
document.addEventListener("click",event=>{if((event.target as Element).closest("a"))event.preventDefault();},true);
const meta={scope:"caller_visible" as const,source:"fixture-only-not-business",as_of:"2026-10-10T00:00:00Z"};
const insights:MaterialLibraryInsights={asset_count:100,segment_count:0,recommended_gaps:[],tags:[100,48,20,12,8,4,2,1].map((asset_count,index)=>({tag:`词云样例${index+1}`,asset_count,segment_count:0,duration_seconds:0,usage_count:0}))};
function Fixture(){
  const [days,setDays]=useState<7|30>(7);
  const [narrow,setNarrow]=useState(false);
  const data:HomeAggregation={days,counts:[],trend:{days,points:Array.from({length:days},(_,index)=>({date:new Date(Date.UTC(2026,9,10-days+index+1)).toISOString().slice(0,10),count:0})),meta},device_queue:{counts:{},records_total:0,running:0,queued:0,meta},storage:{directory_state:"ready",bound_to_current_workspace:false,bound_to_another_workspace:false,configured_path_changed:false,capacity:null,meta},entries:{todos:[],queue:[],works:[],meta},unknown:[]};
  return <><header style={{height:40,display:"flex",alignItems:"center",gap:12,padding:"0 16px",fontSize:13}}><strong>100项隔离布局 fixture · 非业务记录</strong><button onClick={()=>document.documentElement.setAttribute("data-theme",document.documentElement.getAttribute("data-theme")==="dark"?"light":"dark")} type="button">切换主题</button><button onClick={()=>setNarrow(value=>!value)} type="button">{narrow ? "恢复宽工作区" : "检查540像素工作区"}</button></header><main className="desktop-main-scroll" style={{display:"block",height:"calc(100dvh - 40px)",maxWidth:narrow ? 540 : 840,margin:"auto"}}><HomeWorkspace tasks={home100LayoutTasks} foundation={null} insights={insights} recentAssets={[]} live={{data,days,loading:false,error:null,onDays:setDays,onRetry:()=>{}}}/></main></>;
}
createRoot(document.getElementById("fixture-root")!).render(<Fixture/>);
