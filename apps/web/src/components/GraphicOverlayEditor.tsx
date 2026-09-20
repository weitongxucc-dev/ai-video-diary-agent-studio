"use client";
import {useState} from "react";
import type {CreationTaskExactEditInput, TextOverlay} from "@/lib/types";
import ui from "./VideoEditor.module.css";

type Draft=Omit<TextOverlay,"id"> & {id?:string};
export function GraphicOverlayEditor({items,duration,busy,onPreview,onDraftChange}:{items:TextOverlay[];duration:number;busy:boolean;
 onPreview:(input:CreationTaskExactEditInput)=>void;onDraftChange:()=>void}) {
 const blank=():Draft=>({text:"",start_seconds:0,end_seconds:Math.min(3,duration),x:.5,y:.15,width:.8,font_size:64,font_family:"PingFang SC",color:"#FFFFFF"});
 const [draft,setDraft]=useState<Draft>(blank);
 const change=(value:Partial<Draft>)=>{onDraftChange();setDraft({...draft,...value});};
 const numbers:{key:Exclude<keyof Draft,"id"|"text"|"font_family"|"color">;label:string;min:number;max:number;step:number}[]=[
  {key:"start_seconds",label:"开始（秒）",min:0,max:duration,step:.1},{key:"end_seconds",label:"结束（秒）",min:.04,max:duration,step:.01},
  {key:"x",label:"水平中心（0–1）",min:0,max:1,step:.01},{key:"y",label:"垂直中心（0–1）",min:0,max:1,step:.01},
  {key:"width",label:"最大宽度（画幅比例）",min:.05,max:1,step:.05},{key:"font_size",label:"标题字号",min:12,max:256,step:1}];
 return <details className={ui.propertySection}><summary>文字标题</summary>
  {items.map((item,index)=><div key={item.id} className={ui.actionRow}><button className={ui.button} type="button" disabled={busy} onClick={()=>{onDraftChange();setDraft(item);}}>标题{index+1} · {item.text}</button>
   <button className={ui.button} type="button" disabled={busy} onClick={()=>onPreview({operation_type:"EditGraphicOverlay",overlay_action:"delete",overlay_id:item.id})}>预览删除标题{index+1}</button></div>)}
  <form onSubmit={event=>{event.preventDefault();onPreview({operation_type:"EditGraphicOverlay",overlay_action:draft.id?"update":"add",graphic_overlay:draft,...(draft.id?{overlay_id:draft.id}:{})});}}>
   <label className={ui.field}>标题文字<textarea aria-label="标题文字" maxLength={200} value={draft.text} disabled={busy} onChange={event=>change({text:event.target.value})}/></label>
   {numbers.map(control=><label className={ui.field} key={control.key}>{control.label}<input aria-label={control.label} type="number" min={control.min} max={control.max} step={control.step} value={draft[control.key]} disabled={busy} onChange={event=>change({[control.key]:Number(event.target.value)})}/></label>)}
   <label className={ui.field}>标题字体<input aria-label="标题字体" value={draft.font_family} disabled={busy} onChange={event=>change({font_family:event.target.value})}/></label>
   <label className={ui.field}>标题颜色<input aria-label="标题颜色" type="color" value={draft.color} disabled={busy} onChange={event=>change({color:event.target.value})}/></label>
   <div className={ui.actions}><button className={ui.primary} type="submit" disabled={busy||!draft.text.trim()}>预览标题修改</button>
    {draft.id?<button className={ui.button} type="button" disabled={busy} onClick={()=>{onDraftChange();setDraft(blank());}}>新建标题</button>:null}</div>
  </form>
 </details>;
}
