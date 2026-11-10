"use client";
import { useEffect, useState } from "react";
import { Plus, X } from "lucide-react";
import type { VisualTemplateOverlay } from "@/lib/types/timeline";
import { Button } from "./ui/button";
import styles from "./VisualTemplateEditor.module.css";

type Frames = VisualTemplateOverlay["keyframes"];
const fields = {seconds:"时间（秒）",x:"水平",y:"垂直",scale:"缩放",opacity:"不透明度"} as const;
export function TemplateKeyframes({value, duration, disabled, onChange}: {
  value:Frames; duration:number; disabled:boolean; onChange:(value:Frames)=>void;
}) {
  const [text,setText]=useState(JSON.stringify(value,null,2));
  const [error,setError]=useState<string|null>(null);
  useEffect(()=>{setText(JSON.stringify(value,null,2));},[value]);
  return <details><summary>位置、缩放与不透明度关键帧 · {value.length}</summary>
    <div className={styles.keyframes}>{value.map((frame,index)=><fieldset key={index}><legend>第 {index+1} 帧</legend><div className={styles.keyframeFields}>
      {Object.entries(fields).map(([key,label])=><label key={key}>{label}<input aria-label={`第${index+1}帧${label}`} type="number" min={key==="scale"?undefined:0} max={key==="seconds"?duration:key==="scale"?4:1} step="any" disabled={disabled} value={frame[key as keyof typeof fields]} onChange={event=>{if(Number.isFinite(event.target.valueAsNumber))onChange(value.map((item,i)=>i===index?{...item,[key]:event.target.valueAsNumber}:item));}}/></label>)}
      <Button variant="ghost" size="icon" aria-label={`移除第${index+1}帧`} title="移除关键帧" disabled={disabled} onClick={()=>onChange(value.filter((_,i)=>i!==index))}><X size={14}/></Button>
    </div></fieldset>)}</div>
    <Button variant="ghost" type="button" disabled={disabled || (value.at(-1)?.seconds ?? -1)>=duration} onClick={()=>onChange([...value,{seconds:Math.min(duration,(value.at(-1)?.seconds ?? -.5)+.5),x:.5,y:.5,scale:1,opacity:1}])}><Plus size={14}/>添加关键帧</Button>
    <p>时间相对于图层起点，帧间线性变化。保存动画后可撤销。</p>
    <details><summary>高级关键帧 JSON</summary><textarea aria-label="动画关键帧" className={styles.source} disabled={disabled} value={text} onChange={event=>setText(event.target.value)}/><Button variant="outline" disabled={disabled} onClick={()=>{try{const parsed=JSON.parse(text);if(!Array.isArray(parsed) || parsed.some(frame=>!frame || typeof frame!=="object" || Array.isArray(frame) || Object.keys(frame).some(key=>!(key in fields)) || typeof frame.seconds!=="number" || !Number.isFinite(frame.seconds) || ["x","y","scale","opacity"].some(key=>frame[key]!==undefined && (typeof frame[key]!=="number" || !Number.isFinite(frame[key])))))throw Error("每个关键帧需要数值 seconds；可选 x、y、scale 和 opacity 也必须是有限数值");onChange(parsed.map(frame=>({x:.5,y:.5,scale:1,opacity:1,...frame})));setError(null);}catch(cause){setError((cause as Error).message);}}}>采用关键帧草稿</Button>{error?<p role="alert">{error}</p>:null}</details>
  </details>;
}
