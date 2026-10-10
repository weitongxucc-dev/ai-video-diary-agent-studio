"use client";
import { useState } from "react";
import type { CreationTaskMontageClip, VisualEditInput, VisualEffects } from "@/lib/types";
import ui from "./VideoEditor.module.css";

export function VisualEffectsInspector({clip,allClipIds,versionId,busy,portraitAvailable=false,onPreview,onDraftChange}:{
  clip:CreationTaskMontageClip;allClipIds:string[];versionId:string|null;busy:boolean;portraitAvailable?:boolean;
  onPreview:(input:VisualEditInput)=>void;onDraftChange:()=>void;
}) {
  const original={fade_in_seconds:0,fade_out_seconds:0,zoom:1,rotation:0,offset_x:0,offset_y:0,beauty_strength:0,skin_exposure:0,...(clip.visual_effects ?? {exposure:0,contrast:1,saturation:1,temperature:0})};
  const [effects,setEffects]=useState<VisualEffects>(original);
  const [all,setAll]=useState(false);
  const save=(value=effects,allClips=all)=>onPreview({base_version_id:versionId,clip_ids:allClips?allClipIds:[clip.id],effects:value});
  const changed=Object.keys(effects).some(key=>effects[key as keyof VisualEffects]!==original[key as keyof VisualEffects]);
  const controls: {key:keyof VisualEffects;label:string;min:number;max:number;step:number}[]=[
    {key:"zoom",label:"缩放倍数",min:.1,max:4,step:.05},
    {key:"rotation",label:"旋转角度（顺时针）",min:-180,max:180,step:1},
    {key:"offset_x",label:"水平位置（画幅宽度比例）",min:-1,max:1,step:.01},
    {key:"offset_y",label:"垂直位置（画幅高度比例）",min:-1,max:1,step:.01},
    {key:"fade_in_seconds",label:"从黑场淡入（秒）",min:0,max:5,step:.1},
    {key:"fade_out_seconds",label:"淡出至黑场（秒）",min:0,max:5,step:.1},
    {key:"exposure",label:"曝光（EV）",min:-2,max:2,step:.1},
    {key:"contrast",label:"对比度",min:.5,max:1.5,step:.05},
    {key:"saturation",label:"饱和度",min:0,max:2,step:.1},
    {key:"temperature",label:"冷暖",min:-1,max:1,step:.05},
  ];
  if(portraitAvailable) controls.push({key:"beauty_strength",label:"自然磨皮（0–1）",min:0,max:1,step:.05},{key:"skin_exposure",label:"肤色曝光（EV）",min:-.5,max:.5,step:.05});
  return <details className={ui.propertySection}><summary>画面与人像</summary>
    <form onSubmit={event=>{event.preventDefault();save();}}>
      {controls.map(control=><label className={ui.field} key={control.key}>{control.label}<input aria-label={control.label} required type="number" min={control.min} max={control.max} step={control.step} disabled={busy||clip.locked} value={Number.isFinite(effects[control.key])?effects[control.key]:""} onBlur={event=>{if(changed&&event.currentTarget.form?.checkValidity())save();}} onChange={event=>{onDraftChange();setEffects({...effects,[control.key]:event.target.value===""?Number.NaN:Number(event.target.value)});}} /></label>)}
      <p>淡入淡出仅影响画面，0 表示关闭；声音与字幕保持原时轴。</p>
      {clip.fade_envelope_duration_frames != null ? <p>沿用原镜头的渐变进度；修改淡入或淡出数值后，按当前片段重新计算。</p>:null}
      <p>基于当前裁切/适配结果绕画幅中心变换，正数向右或向下；超出部分裁切，空白为黑色。字幕保持原位置。</p>
      {portraitAvailable ? <p>自然磨皮，不调整脸型；先对比眼口和皮肤细节。</p>:<p>本地人像组件未就绪，当前可调整整体颜色。</p>}
      <label className={ui.actionRow}>应用同一组参数到全部镜头<input type="checkbox" checked={all} disabled={busy||clip.locked} onChange={event=>{onDraftChange();setAll(event.target.checked);if(changed||event.target.checked)save(effects,event.target.checked);}} /></label>
      {all?<p>{allClipIds.length} 个镜头；不会自动逐镜头校色，锁定镜头会阻止执行。</p>:null}
      <div className={ui.actions}><button className={ui.primary} type="submit" disabled={busy||clip.locked||(!changed&&!all)}>保存画面草稿</button>
      <button className={ui.button} type="button" disabled={busy||clip.locked} onClick={()=>{onDraftChange();const reset={fade_in_seconds:0,fade_out_seconds:0,zoom:1,rotation:0,offset_x:0,offset_y:0,beauty_strength:0,skin_exposure:0,exposure:0,contrast:1,saturation:1,temperature:0};setEffects(reset);save(reset);}}>重置参数</button></div>
    </form>
  </details>;
}
