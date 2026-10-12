"use client";
import { AppSelect, AppSelectOption } from "@/components/AppSelect";
import {useEffect,useState} from "react";
import {getCreationTaskGraphicSources,importCreationTaskGraphic} from "@/lib/api";
import type {CreationTaskExactEditInput,TextOverlay,ImageOverlay,ArrowOverlay,GraphicOverlay,GraphicSource} from "@/lib/types";
import ui from "./VideoEditor.module.css";

type Draft=(Omit<TextOverlay,"id">|Omit<ImageOverlay,"id">|Omit<ArrowOverlay,"id">)&{id?:string};
type ArrowDraft=Omit<ArrowOverlay,"id">&{id?:string};
const arrowPositions:{value:ArrowOverlay["position"];label:string}[]=[
 {value:"left-top",label:"左上"},{value:"right-top",label:"右上"},{value:"right-bottom",label:"右下"},{value:"left-bottom",label:"左下"}];
function arrowProblem(draft:ArrowDraft):string|null{
 if(!Number.isFinite(draft.x)||draft.x<0||draft.x>1||!Number.isFinite(draft.y)||draft.y<0||draft.y>1)return"请填写 0–1 之间的箭头指向坐标";
 if(!draft.name.trim()||draft.name.length>24)return"箭头名称需为 1–24 个字符";
 if(draft.purpose.length>64)return"箭头作用说明最长 64 个字符";
 if(!(draft.end_seconds>0)||draft.end_seconds-draft.start_seconds<0.04)return"箭头显示时间至少0.04秒";
 if(!/^#[0-9a-fA-F]{6}$/.test(draft.color))return"箭头颜色需为 #RRGGBB 六位十六进制";
 return null;
}
export function GraphicOverlayEditor({taskId,items,duration,busy,onPreview,onDraftChange,selectedId,onSelect}:{taskId:string;items:GraphicOverlay[];duration:number;busy:boolean;
 onPreview:(input:CreationTaskExactEditInput)=>void;onDraftChange:()=>void;selectedId?:string|null;onSelect?:(id:string|null)=>void}) {
 const blank=():Draft=>({text:"",start_seconds:0,end_seconds:Math.min(3,duration),x:.5,y:.15,width:.8,font_size:64,font_family:"PingFang SC",color:"#FFFFFF"});
 const blankArrow=():ArrowDraft=>({kind:"arrow",x:Number.NaN,y:Number.NaN,name:"",purpose:"",start_seconds:0,end_seconds:Math.min(3,duration),position:"right-top",color:"#FFFFFF"});
 const selectedItem=items.find(item=>item.id===selectedId);
 const [draft,setDraft]=useState<Draft>(()=>selectedItem??blank());
 useEffect(()=>{setDraft(selectedItem??blank());},[selectedId,selectedItem,duration]);
 const [sources,setSources]=useState<GraphicSource[]>([]);
 const [working,setWorking]=useState(false),[error,setError]=useState<string|null>(null);
 const disabled=busy||working;
 const isImage=draft.kind==="image";
 const isArrow=draft.kind==="arrow";
 const save=(value:Draft=draft)=>{
  if(value.kind==="arrow"){const problem=arrowProblem(value);if(problem){setError(problem);return;}}
  if(value.kind!=="image"&&value.kind!=="arrow"&&!value.text.trim())return;
  setError(null);
  onPreview({operation_type:"EditGraphicOverlay",overlay_action:value.id?"update":"add",graphic_overlay:value,...(value.id?{overlay_id:value.id}:{})});
 };
 const change=(value:Partial<Draft>,apply=false)=>{onDraftChange();const next={...draft,...value} as Draft;setDraft(next);if(apply&&next.id)save(next);};
 const choose=(source:GraphicSource)=>{onSelect?.(null);onDraftChange();setDraft({kind:"image",name:source.name,source_ref:source.source_ref,
  start_seconds:0,end_seconds:Math.min(3,duration),x:.5,y:.5,width:.3,opacity:1});};
 async function load(){try{setSources(await getCreationTaskGraphicSources(taskId));}catch(cause){setError(cause instanceof Error?cause.message:"读取图片失败");}}
 async function upload(file:File){setWorking(true);setError(null);try{const source=await importCreationTaskGraphic(taskId,file);setSources(current=>[...current,source]);choose(source);}catch(cause){setError(cause instanceof Error?cause.message:"图片导入失败");}finally{setWorking(false);}}
 const numbers:{key:"start_seconds"|"end_seconds"|"x"|"y"|"width";label:string;min:number;max:number;step:number}[]=[
  {key:"start_seconds",label:"开始（秒）",min:0,max:duration,step:.1},{key:"end_seconds",label:"结束（秒）",min:.04,max:duration,step:.01},
  {key:"x",label:"水平中心（0–1）",min:0,max:1,step:.01},{key:"y",label:"垂直中心（0–1）",min:0,max:1,step:.01},
  {key:"width",label:"最大宽度（画幅比例）",min:.05,max:1,step:.05}];
 return <details open={selectedId?true:undefined} className={ui.propertySection} onToggle={event=>{if(event.currentTarget.open)void load();}}><summary>图文叠加</summary>
  {items.map((item,index)=><div key={item.id} className={ui.actionRow}><button className={ui.button} type="button" disabled={disabled} aria-pressed={selectedId===item.id} onClick={()=>{if(onSelect)onSelect(item.id);else onDraftChange();setDraft(item);}}>{item.kind==="image"?"图片":item.kind==="arrow"?"箭头":"标题"}{index+1} · {item.kind==="image"||item.kind==="arrow"?item.name:item.text}</button>
   <button className={ui.button} type="button" disabled={disabled} onClick={()=>onPreview({operation_type:"EditGraphicOverlay",overlay_action:"delete",overlay_id:item.id})}>删除{item.kind==="image"?"图片":item.kind==="arrow"?"箭头":"标题"}{index+1}</button></div>)}
  {!selectedId?<label className={ui.field}>导入图片或标识<input aria-label="导入图片或标识" type="file" accept="image/png,image/jpeg,image/webp" disabled={disabled} onChange={event=>{const file=event.target.files?.[0];if(file)void upload(file);event.target.value="";}}/></label>:null}
  {!selectedId&&sources.length?<label className={ui.field}>已导入图片<AppSelect aria-label="已导入图片" value={isImage?draft.source_ref:""} disabled={disabled} onValueChange={newValue =>{const source=sources.find(item=>item.source_ref===newValue);if(source)choose(source);}}><AppSelectOption value="" disabled>选择图片</AppSelectOption>{sources.map(source=><AppSelectOption key={source.artifact_id} value={source.source_ref}>{source.name} · {source.width}×{source.height}</AppSelectOption>)}</AppSelect></label>:null}
  {!selectedId?<div className={ui.actions}><button className={ui.button} type="button" disabled={disabled} onClick={()=>{onSelect?.(null);onDraftChange();setDraft(blankArrow());}}>新建箭头标注</button></div>:null}
  {working?<p role="status">正在导入图片…</p>:null}{error?<p role="alert">{error}</p>:null}
  <form onBlur={event=>{if(draft.id&&!disabled&&event.target instanceof HTMLInputElement||draft.id&&!disabled&&event.target instanceof HTMLTextAreaElement){
   const stored=items.find(item=>item.id===draft.id);
   if(JSON.stringify(stored)!==JSON.stringify(draft)&&event.currentTarget.checkValidity())save();
  }}} onSubmit={event=>{event.preventDefault();save();}}>
   {isImage?<p>{draft.name} · 等比缩放，保留透明背景</p>:isArrow?<>
    <label className={ui.field}>箭头名称（指向的画面部件）<input aria-label="箭头名称" maxLength={24} value={draft.name} disabled={disabled} onChange={event=>change({name:event.target.value})}/></label>
    <label className={ui.field}>作用说明（可省）<input aria-label="箭头作用说明" maxLength={64} value={draft.purpose} disabled={disabled} onChange={event=>change({purpose:event.target.value})}/></label>
    <label className={ui.field}>指向 X（0–1）<input aria-label="指向 X（0–1）" required type="number" min={0} max={1} step={.01} value={Number.isFinite(draft.x)?draft.x:""} disabled={disabled} onChange={event=>change({x:event.target.value===""?Number.NaN:event.target.value===""?Number.NaN:Number(event.target.value)})}/></label>
    <label className={ui.field}>指向 Y（0–1）<input aria-label="指向 Y（0–1）" required type="number" min={0} max={1} step={.01} value={Number.isFinite(draft.y)?draft.y:""} disabled={disabled} onChange={event=>change({y:event.target.value===""?Number.NaN:event.target.value===""?Number.NaN:Number(event.target.value)})}/></label>
    <label className={ui.field}>开始（秒）<input aria-label="开始（秒）" required type="number" min={0} max={duration} step={.1} value={draft.start_seconds} disabled={disabled} onChange={event=>change({start_seconds:event.target.value===""?Number.NaN:Number(event.target.value)})}/></label>
    <label className={ui.field}>结束（秒）<input aria-label="结束（秒）" required type="number" min={.04} max={duration} step={.01} value={draft.end_seconds} disabled={disabled} onChange={event=>change({end_seconds:event.target.value===""?Number.NaN:Number(event.target.value)})}/></label>
    <label className={ui.field}>标注角<AppSelect aria-label="标注角" value={draft.position} disabled={disabled} onValueChange={newValue =>change({position:newValue as ArrowOverlay["position"]},true)}>{arrowPositions.map(option=><AppSelectOption key={option.value} value={option.value}>{option.label}</AppSelectOption>)}</AppSelect></label>
    <label className={ui.field}>颜色（#RRGGBB）<input aria-label="箭头颜色（#RRGGBB）" maxLength={7} placeholder="#FFFFFF" value={draft.color} disabled={disabled} onChange={event=>change({color:event.target.value},true)}/></label>
   </>:<label className={ui.field}>标题文字<textarea aria-label="标题文字" maxLength={200} value={draft.text} disabled={disabled} onChange={event=>change({text:event.target.value})}/></label>}
   {!isArrow?numbers.map(control=><label className={ui.field} key={control.key}>{control.label}<input aria-label={control.label} required type="number" min={control.min} max={control.max} step={control.step} value={Number.isFinite(draft[control.key])?draft[control.key]:""} disabled={disabled} onChange={event=>change({[control.key]:event.target.value===""?Number.NaN:Number(event.target.value)})}/></label>):null}
   {isImage?<label className={ui.field}>不透明度（0–1）<input aria-label="图片不透明度" required type="number" min={0} max={1} step={.05} value={draft.opacity} disabled={disabled} onChange={event=>change({opacity:event.target.value===""?Number.NaN:Number(event.target.value)})}/></label>:!isArrow?<>
    <label className={ui.field}>标题字号<input aria-label="标题字号" required type="number" min={12} max={256} step={1} value={draft.font_size} disabled={disabled} onChange={event=>change({font_size:event.target.value===""?Number.NaN:Number(event.target.value)})}/></label>
    <label className={ui.field}>标题字体<input aria-label="标题字体" value={draft.font_family} disabled={disabled} onChange={event=>change({font_family:event.target.value})}/></label>
    <label className={ui.field}>标题颜色<input aria-label="标题颜色" type="color" value={draft.color} disabled={disabled} onChange={event=>change({color:event.target.value},true)}/></label></>:null}
   <div className={ui.actions}><button className={ui.primary} type="submit" disabled={disabled||(isArrow?!draft.name.trim():!isImage&&!draft.text.trim())}>{isImage?"保存图片草稿":isArrow?"保存箭头草稿":"保存标题草稿"}</button>
    {!selectedId&&(draft.id||isImage||isArrow)?<button className={ui.button} type="button" disabled={disabled} onClick={()=>{onSelect?.(null);onDraftChange();setDraft(blank());}}>新建标题</button>:null}</div>
  </form>
 </details>;
}
