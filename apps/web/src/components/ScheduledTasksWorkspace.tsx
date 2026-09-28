"use client";
import {useEffect,useState,type FormEvent} from "react";
import {createScheduledTask,deleteScheduledTask,listScheduledTasks,setScheduledTaskEnabled} from "@/lib/api";
import type {ScheduledTask,ScheduledTaskTriggerMode,ScheduledTaskType} from "@/lib/api";
import styles from "./ScheduledTasksWorkspace.module.css";

const taskTypeLabels:Record<string,string>={material_analysis:"素材解析",creation_render:"成片制作"};
const modeLabels:Record<string,string>={scheduled:"到点",idle:"闲时"};
const HONEST_NOTE="执行未接线：本页只管理任务定义，到点不会自动运行，也没有上次/下次运行时间。";

function formatUtc(value:string):string{
  const date=new Date(value);
  if(Number.isNaN(date.getTime()))return value;
  return `${date.toISOString().slice(0,16).replace("T"," ")} UTC`;
}
function toUtcIso(localValue:string):string|null{
  if(!localValue)return null;
  const date=new Date(localValue);
  if(Number.isNaN(date.getTime()))return null;
  return date.toISOString();
}
type Draft={taskType:ScheduledTaskType;triggerMode:ScheduledTaskTriggerMode;targetRef:string;notBeforeLocal:string;windowText:string};
const blankDraft=():Draft=>({taskType:"material_analysis",triggerMode:"scheduled",targetRef:"",notBeforeLocal:"",windowText:""});

export function ScheduledTasksWorkspace() {
  const [tasks,setTasks]=useState<ScheduledTask[]|null>(null);
  const [loadError,setLoadError]=useState<string|null>(null);
  const [actionError,setActionError]=useState<string|null>(null);
  const [formError,setFormError]=useState<string|null>(null);
  const [busy,setBusy]=useState<string|null>(null);
  const [draft,setDraft]=useState<Draft>(blankDraft);

  async function refresh(){
    setTasks(null);setLoadError(null);
    try{const response=await listScheduledTasks();setTasks(response.tasks);}
    catch(cause){setTasks([]);setLoadError(cause instanceof Error?cause.message:"定时任务读取失败");}
  }
  useEffect(()=>{void refresh();},[]);

  async function toggleEnabled(task:ScheduledTask){
    setBusy(task.id);setActionError(null);
    try{const updated=await setScheduledTaskEnabled(task.id,!task.enabled);
      setTasks(current=>(current??[]).map(item=>item.id===updated.id?updated:item));}
    catch(cause){setActionError(cause instanceof Error?cause.message:"启停未保存");}
    finally{setBusy(null);}
  }
  async function remove(task:ScheduledTask){
    setBusy(task.id);setActionError(null);
    try{await deleteScheduledTask(task.id);setTasks(current=>(current??[]).filter(item=>item.id!==task.id));}
    catch(cause){setActionError(cause instanceof Error?cause.message:"删除未完成");}
    finally{setBusy(null);}
  }
  async function submit(event:FormEvent){
    event.preventDefault();
    const targetRef=draft.targetRef.trim();
    if(!targetRef){setFormError("目标对象不能为空");return;}
    let notBefore:string|null=null;
    if(draft.triggerMode==="scheduled"){
      notBefore=toUtcIso(draft.notBeforeLocal);
      if(!notBefore){setFormError("到点模式必须给出最早开始时间");return;}
    }else if(draft.notBeforeLocal){
      notBefore=toUtcIso(draft.notBeforeLocal);
      if(!notBefore){setFormError("最早开始时间无法解析，请重新选择");return;}
    }
    let windowValue:Record<string,unknown>|null=null;
    if(draft.windowText.trim()){
      try{const parsed:unknown=JSON.parse(draft.windowText);
        if(!parsed||typeof parsed!=="object"||Array.isArray(parsed)){setFormError("时间窗需要是一个 JSON 对象，例如 {\"hours\":[2,3]}");return;}
        windowValue=parsed as Record<string,unknown>;
      }catch{setFormError("时间窗需要是合法 JSON");return;}
    }
    setBusy("create");setFormError(null);
    try{
      const task=await createScheduledTask({task_type:draft.taskType,trigger_mode:draft.triggerMode,target_ref:targetRef,not_before_utc:notBefore,window:windowValue});
      setTasks(current=>[task,...(current??[])]);
      setDraft({...blankDraft(),taskType:draft.taskType,triggerMode:draft.triggerMode});
    }catch(cause){setFormError(cause instanceof Error?cause.message:"定时任务未创建");}
    finally{setBusy(null);}
  }

  const creating=busy==="create";
  return <div className={styles.page}>
    <header className={styles.header}>
      <h1>定时任务</h1>
      <p>按到点或闲时管理自动化任务定义；常驻素材解析不依赖定时任务。</p>
      <span className={styles.count}>{tasks===null?"正在读取定时任务…":`${tasks.length} 项`}</span>
    </header>
    <p className={styles.honestNote}>{HONEST_NOTE}</p>
    <div className={styles.columns}>
      <section className={styles.card} aria-label="定时任务定义">
        <div className={styles.cardHeading}><h2>任务定义</h2><small>列表来自已保存的定时任务；不含运行历史。</small></div>
        {loadError?<div role="alert" className={styles.errorBox}><p>定时任务读取失败：{loadError}</p><button className={styles.button} type="button" onClick={()=>void refresh()}>重试</button></div>
         :tasks===null?<p>正在读取定时任务…</p>
         :tasks.length===0?<div className={styles.empty}><strong>还没有定时任务</strong><p>可以在右侧创建一条任务定义；执行接线完成前，任务不会在到点时自动运行。</p></div>
         :<div className={styles.list}>{tasks.map(task=>{
           const timeSuffix=task.not_before_utc?` · ${formatUtc(task.not_before_utc)}`:task.trigger_mode==="scheduled"?" · 未设时间":"";
           return <div key={task.id} className={styles.row}>
            <div className={styles.rowMain}><strong>{task.target_ref}</strong>
             <small>{taskTypeLabels[task.task_type]??task.task_type} · {modeLabels[task.trigger_mode]??task.trigger_mode}{timeSuffix}</small></div>
            <span className={`${styles.state} ${task.enabled?styles.stateOn:styles.stateOff}`}>{task.enabled?"已启用":"暂停中"}</span>
            <div className={styles.rowActions}>
             <button className={styles.button} type="button" disabled={busy!==null} onClick={()=>void toggleEnabled(task)}>{task.enabled?"暂停":"启动"}</button>
             <button className={`${styles.button} ${styles.danger}`} type="button" disabled={busy!==null} onClick={()=>void remove(task)}>删除</button>
            </div>
           </div>;})}</div>}
        {actionError?<p role="alert" className={styles.formError}>{actionError}</p>:null}
      </section>
      <section className={styles.card} aria-label="新建定时任务">
        <div className={styles.cardHeading}><h2>新建任务定义</h2><small>创建后出现在左侧列表；启停与删除即时生效。</small></div>
        <form onSubmit={event=>void submit(event)} className={styles.field}>
          <label className={styles.field}><span>任务类型</span>
           <select aria-label="任务类型" value={draft.taskType} disabled={creating}
            onChange={event=>setDraft({...draft,taskType:event.target.value as ScheduledTaskType})}>
            <option value="material_analysis">素材解析</option>
            <option value="creation_render">成片制作</option>
           </select></label>
          <div className={styles.fieldRow}>
           <div className={styles.field} role="group" aria-label="触发模式"><span>触发模式</span>
            <div className={styles.modeRow}>
             <button className={styles.modeButton} type="button" aria-pressed={draft.triggerMode==="scheduled"} disabled={creating}
              onClick={()=>setDraft({...draft,triggerMode:"scheduled" as ScheduledTaskTriggerMode})}>到点</button>
             <button className={styles.modeButton} type="button" aria-pressed={draft.triggerMode==="idle"} disabled={creating}
              onClick={()=>setDraft({...draft,triggerMode:"idle" as ScheduledTaskTriggerMode})}>闲时</button>
            </div></div>
           <label className={styles.field}><span>最早开始时间{draft.triggerMode==="scheduled"?"（到点必填）":"（可省）"}</span>
            <input aria-label="最早开始时间" type="datetime-local" value={draft.notBeforeLocal} disabled={creating}
             onChange={event=>setDraft({...draft,notBeforeLocal:event.target.value})}/></label>
          </div>
          <label className={styles.field}><span>目标对象</span>
           <input aria-label="目标对象" value={draft.targetRef} placeholder="素材或任务引用，例如 asset:abc123" disabled={creating}
            onChange={event=>setDraft({...draft,targetRef:event.target.value})}/></label>
          <label className={styles.field}><span>时间窗 JSON（可省）</span>
           <textarea aria-label="时间窗 JSON" className={styles.windowInput} rows={2} value={draft.windowText} placeholder='{"hours":[2,3]}' disabled={creating}
            onChange={event=>setDraft({...draft,windowText:event.target.value})}/></label>
          {formError?<p role="alert" className={styles.formError}>{formError}</p>:null}
          <div className={styles.actions}>
           <button className={styles.primaryButton} type="submit" disabled={creating}>创建任务定义</button>
          </div>
          <p className={styles.honestNote}>创建的只是任务定义：{HONEST_NOTE}</p>
        </form>
      </section>
    </div>
  </div>;
}
