"use client";
import {useState} from "react";
import {getCreationTaskGraphicSources,importCreationTaskGraphic} from "@/lib/api";
import type {CreationTaskExactEditInput,TextOverlay,ImageOverlay,GraphicOverlay,GraphicSource} from "@/lib/types";
import ui from "./VideoEditor.module.css";

type Draft=(Omit<TextOverlay,"id">|Omit<ImageOverlay,"id">)&{id?:string};
export function GraphicOverlayEditor({taskId,items,duration,busy,onPreview,onDraftChange}:{taskId:string;items:GraphicOverlay[];duration:number;busy:boolean;
 onPreview:(input:CreationTaskExactEditInput)=>void;onDraftChange:()=>void}) {
 const blank=():Draft=>({text:"",start_seconds:0,end_seconds:Math.min(3,duration),x:.5,y:.15,width:.8,font_size:64,font_family:"PingFang SC",color:"#FFFFFF"});
 const [draft,setDraft]=useState<Draft>(blank);
 const [sources,setSources]=useState<GraphicSource[]>([]);
 const [working,setWorking]=useState(false),[error,setError]=useState<string|null>(null);
 const disabled=busy||working;
 const isImage=draft.kind==="image";
 const change=(value:Partial<Draft>)=>{onDraftChange();setDraft({...draft,...value} as Draft);};
 const choose=(source:GraphicSource)=>{onDraftChange();setDraft({kind:"image",name:source.name,source_ref:source.source_ref,
  start_seconds:0,end_seconds:Math.min(3,duration),x:.5,y:.5,width:.3,opacity:1});};
 async function load(){try{setSources(await getCreationTaskGraphicSources(taskId));}catch(cause){setError(cause instanceof Error?cause.message:"读取图片失败");}}
 async function upload(file:File){setWorking(true);setError(null);try{const source=await importCreationTaskGraphic(taskId,file);setSources(current=>[...current,source]);choose(source);}catch(cause){setError(cause instanceof Error?cause.message:"图片导入失败");}finally{setWorking(false);}}
 const numbers:{key:"start_seconds"|"end_seconds"|"x"|"y"|"width";label:string;min:number;max:number;step:number}[]=[
  {key:"start_seconds",label:"开始（秒）",min:0,max:duration,step:.1},{key:"end_seconds",label:"结束（秒）",min:.04,max:duration,step:.01},
  {key:"x",label:"水平中心（0–1）",min:0,max:1,step:.01},{key:"y",label:"垂直中心（0–1）",min:0,max:1,step:.01},
  {key:"width",label:"最大宽度（画幅比例）",min:.05,max:1,step:.05}];
 return <details className={ui.propertySection} onToggle={event=>{if(event.currentTarget.open)void load();}}><summary>图文叠加</summary>
  {items.map((item,index)=><div key={item.id} className={ui.actionRow}><button className={ui.button} type="button" disabled={disabled} onClick={()=>{onDraftChange();setDraft(item);}}>{item.kind==="image"?"图片":"标题"}{index+1} · {item.kind==="image"?item.name:item.text}</button>
   <button className={ui.button} type="button" disabled={disabled} onClick={()=>onPreview({operation_type:"EditGraphicOverlay",overlay_action:"delete",overlay_id:item.id})}>预览删除{item.kind==="image"?"图片":"标题"}{index+1}</button></div>)}
  <label className={ui.field}>导入图片或标识<input aria-label="导入图片或标识" type="file" accept="image/png,image/jpeg,image/webp" disabled={disabled} onChange={event=>{const file=event.target.files?.[0];if(file)void upload(file);event.target.value="";}}/></label>
  {sources.length?<label className={ui.field}>已导入图片<select aria-label="已导入图片" value={isImage?draft.source_ref:""} disabled={disabled} onChange={event=>{const source=sources.find(item=>item.source_ref===event.target.value);if(source)choose(source);}}><option value="" disabled>选择图片</option>{sources.map(source=><option key={source.artifact_id} value={source.source_ref}>{source.name} · {source.width}×{source.height}</option>)}</select></label>:null}
  {working?<p role="status">正在导入图片…</p>:null}{error?<p role="alert">{error}</p>:null}
  <form onSubmit={event=>{event.preventDefault();onPreview({operation_type:"EditGraphicOverlay",overlay_action:draft.id?"update":"add",graphic_overlay:draft,...(draft.id?{overlay_id:draft.id}:{})});}}>
   {isImage?<p>{draft.name} · 等比缩放，保留透明背景</p>:<label className={ui.field}>标题文字<textarea aria-label="标题文字" maxLength={200} value={draft.text} disabled={disabled} onChange={event=>change({text:event.target.value})}/></label>}
   {numbers.map(control=><label className={ui.field} key={control.key}>{control.label}<input aria-label={control.label} type="number" min={control.min} max={control.max} step={control.step} value={draft[control.key]} disabled={disabled} onChange={event=>change({[control.key]:Number(event.target.value)})}/></label>)}
   {isImage?<label className={ui.field}>不透明度（0–1）<input aria-label="图片不透明度" type="number" min={0} max={1} step={.05} value={draft.opacity} disabled={disabled} onChange={event=>change({opacity:Number(event.target.value)})}/></label>:<>
    <label className={ui.field}>标题字号<input aria-label="标题字号" type="number" min={12} max={256} step={1} value={draft.font_size} disabled={disabled} onChange={event=>change({font_size:Number(event.target.value)})}/></label>
    <label className={ui.field}>标题字体<input aria-label="标题字体" value={draft.font_family} disabled={disabled} onChange={event=>change({font_family:event.target.value})}/></label>
    <label className={ui.field}>标题颜色<input aria-label="标题颜色" type="color" value={draft.color} disabled={disabled} onChange={event=>change({color:event.target.value})}/></label></>}
   <div className={ui.actions}><button className={ui.primary} type="submit" disabled={disabled||(!isImage&&!draft.text.trim())}>{isImage?"预览图片修改":"预览标题修改"}</button>
    {draft.id||isImage?<button className={ui.button} type="button" disabled={disabled} onClick={()=>{onDraftChange();setDraft(blank());}}>新建标题</button>:null}</div>
  </form>
 </details>;
}
