"use client";
import {useEffect, useRef, useState} from 'react';
import type {VideoTemplate, VisualTemplate, CreationTaskExactEditInput} from '@/lib/types';
import type {VisualTemplateOverlay, GraphicOverlay} from '@/lib/types/timeline';
import {readVisualTemplates, readVideoTemplateVersion, saveVisualTemplateCopy, createVideoTemplateVersion, VideoTemplateRequestError} from '@/lib/video-template-api';
import {VisualTemplatePreview} from './VisualTemplatePreview';
import {AppSelect, AppSelectOption} from './AppSelect';
import styles from './VisualTemplateEditor.module.css';
import ui from './VideoEditor.module.css';

export function VisualTemplateFields({value, onChange, disabled=false}: {value:VisualTemplate;
  onChange:(value:VisualTemplate)=>void; disabled?:boolean}) {
  return <div className={styles.editor}>
    <div className={styles.fields}>{value.bindings.map(binding => <label key={binding.key}>{binding.label}
      <textarea rows={2} maxLength={4000} disabled={disabled} value={value.values[binding.key] ?? binding.default}
        onChange={event => onChange({...value, values:{...value.values, [binding.key]:event.target.value}})}/></label>)}</div>
    <VisualTemplatePreview definition={value}/>
    <details><summary>修改动画源码与参数结构</summary>
      <div className={styles.fields}>{(['width','height','duration_seconds'] as const).map(key=><label key={key}>
        {{width:'组件宽度（像素）',height:'组件高度（像素）',duration_seconds:'动画长度（秒）'}[key]}
        <input type="number" disabled={disabled} value={value[key]} onChange={event=>onChange({...value,[key]:Number(event.target.value)})}/></label>)}</div>
      <label>HTML / CSS / JavaScript<textarea aria-label="模板动画源码" className={styles.source} spellCheck={false}
        maxLength={262144} disabled={disabled} value={value.source} onChange={event => onChange({...value,source:event.target.value})}/></label>
      <p>源码实现 window.film.frame(t)，t 为秒；可修改结构、样式和动画。外部依赖需改为自包含文件。</p>
      <BindingEditor key={JSON.stringify(value.bindings)} value={value.bindings} disabled={disabled}
        onChange={bindings=>onChange({...value,bindings})}/>
    </details>
    <p>{value.provenance.map(item => `${item.license} · ${item.repository} @ ${item.revision.slice(0,8)}`).join('；')}</p>
  </div>;
}

function BindingEditor({value,disabled,onChange}:{value:VisualTemplate['bindings'];disabled:boolean;onChange:(value:VisualTemplate['bindings'])=>void}){
  const [text,setText]=useState(JSON.stringify(value,null,2));
  const [error,setError]=useState<string|null>(null);
  return <><label>参数绑定定义<textarea aria-label="模板参数绑定" className={styles.source} value={text} disabled={disabled} onChange={e=>setText(e.target.value)}/></label>
    <button className={ui.button} type="button" disabled={disabled} onClick={()=>{try{const bindings=JSON.parse(text);if(!Array.isArray(bindings))throw Error('请输入参数数组');onChange(bindings);setError(null);}catch(cause){setError((cause as Error).message);}}}>采用参数结构草稿</button>
    {error?<p role="alert">{error}</p>:null}</>;
}

export function VisualTemplateLibraryEditor({template,onSaved}: {template:VideoTemplate;onSaved:(value:VideoTemplate)=>void}) {
  const [visual,setVisual]=useState(template.settings.visual!);
  const [name,setName]=useState(template.name);
  const [busy,setBusy]=useState(false),[error,setError]=useState<string|null>(null);
  const requestId=useRef<string|null>(null);
  const [pending,setPending]=useState<{id:string;version:number;name:string;visual:VisualTemplate}|null>(null);
  async function save(copy:boolean) {
    setBusy(true);setError(null);
    try {
      requestId.current ??= crypto.randomUUID();
      setPending({id:copy?`template_${requestId.current.replaceAll('-','')}`:template.id,
        version:copy?1:template.version+1,name:name.trim(),visual});
      const saved=copy ? await saveVisualTemplateCopy({request_id:requestId.current,
        source_template_id:template.id,source_template_version:template.version,name,visual})
        : await createVideoTemplateVersion(template.id,{expected_version:template.version,name,
          style_instruction:template.settings.style_instruction,visual});
      onSaved(saved);requestId.current=null;setPending(null);
    } catch(cause){setError((cause as Error).message);if(cause instanceof VideoTemplateRequestError&&cause.status<500){setPending(null);requestId.current=null;}}
    finally{setBusy(false);}
  }
  async function reconcile(){
    if(!pending)return;
    setBusy(true);setError(null);
    try{
      const saved=await readVideoTemplateVersion(pending.id,pending.version);
      if(saved.name!==pending.name||JSON.stringify(saved.settings.visual)!==JSON.stringify(pending.visual))
        throw Error('此版本与原提交内容不同，请刷新版本记录核对；未将其他修改当成本次成功。');
      onSaved(saved);setPending(null);requestId.current=null;
    }catch(cause){setError((cause as Error).message);if(cause instanceof VideoTemplateRequestError&&cause.status===404)setPending(null);}
    finally{setBusy(false);}
  }
  return <section className={styles.editor} aria-label="编辑视觉模板">
    <label>模板名称<input maxLength={80} value={name} disabled={busy} onChange={e=>setName(e.target.value)}/></label>
    <VisualTemplateFields value={visual} onChange={setVisual} disabled={busy}/>
    <div className={styles.actions}>
      <button className={ui.button} type="button" disabled={busy||!!pending||!name.trim()} onClick={()=>void save(true)}>另存我的视觉模板</button>
      {template.can_edit ? <button className={ui.button} type="button" disabled={busy||!!pending||!name.trim()} onClick={()=>void save(false)}>保存下一版本</button>:null}
      {pending?<button className={ui.button} type="button" disabled={busy} onClick={()=>void reconcile()}>查询原保存结果（第{pending.version}版）</button>:null}
    </div>
    {error?<p role="alert">{error} 草稿已保留；结果未知时请先读取版本记录。</p>:null}
    <p>在作品的“图文与动画”中选择此模板。制作结构与视觉组件分别使用。</p>
  </section>;
}

export function VisualTemplateLayerEditor({items,duration,busy,selectedId,onSelect,onPreview,onMediaPreview,onDraftChange}: {
  items:GraphicOverlay[];duration:number;busy:boolean;selectedId?:string|null;onSelect?:(id:string|null)=>void;
  onPreview:(input:CreationTaskExactEditInput)=>void;onMediaPreview?:(input:CreationTaskExactEditInput)=>void;onDraftChange:()=>void}) {
  const [templates,setTemplates]=useState<VideoTemplate[]>([]);
  const [error,setError]=useState<string|null>(null);
  const [draft,setDraft]=useState<VisualTemplateOverlay|null>(null);
  const [loaded,setLoaded]=useState(false);
  const selected=items.find((item):item is VisualTemplateOverlay=>item.kind==='template'&&item.id===selectedId);
  useEffect(()=>setDraft(null),[selectedId]);
  const layer=draft ?? selected;
  function change(value:VisualTemplateOverlay){onDraftChange();setDraft(value);}
  function choose(template:VideoTemplate){change({kind:'template',id:crypto.randomUUID(),name:template.name,
    template_id:template.id,template_version:template.version,definition:{...structuredClone(template.settings.visual!),
      values:Object.fromEntries(template.settings.visual!.bindings.map(binding=>[binding.key,template.settings.visual!.values[binding.key]??binding.default]))},
    start_seconds:0,end_seconds:Math.min(duration,template.settings.visual!.duration_seconds),x:.5,y:.5,width:.8,opacity:1,keyframes:[],z_index:0,animation_offset_seconds:0});}
  return <details open={Boolean(selected)} className={styles.editor}><summary>可编辑动画模板</summary>
    <button className={ui.button} type="button" disabled={busy} onClick={()=>void readVisualTemplates().then(value=>{setTemplates(value);setLoaded(true);setError(null);}).catch(cause=>setError(cause.message))}>读取模板库</button>
    {loaded?<AppSelect value="" aria-label="选择动画模板" onValueChange={key=>{const value=templates.find(item=>`${item.id}:${item.version}`===key);if(value)choose(value);}}>
      <AppSelectOption value="">选择模板版本…</AppSelectOption>{templates.map(item=><AppSelectOption key={`${item.id}:${item.version}`} value={`${item.id}:${item.version}`}>{item.name} · 第{item.version}版</AppSelectOption>)}
    </AppSelect>:null}
    <div className={styles.actions}>{items.filter(item=>item.kind==='template').map(item=><button key={item.id} type="button" disabled={busy}
      aria-pressed={selectedId===item.id} onClick={()=>{setDraft(null);onSelect?.(item.id);}}>{item.kind==='template'?item.name:''}</button>)}</div>
    {layer?<section className={styles.editor}>
      <strong>{layer.name} · 来源第{layer.template_version}版</strong>
      <div className={styles.fields}>{(['start_seconds','end_seconds','x','y','width','opacity','z_index'] as const).map(key=><label key={key}>
        {{start_seconds:'开始（秒）',end_seconds:'结束（秒）',x:'水平位置（0–1）',y:'垂直位置（0–1）',width:'宽度占比（0–1）',opacity:'不透明度（0–1）',z_index:'叠放层级（越大越靠前）'}[key]}
        <input type="number" step={key.includes('seconds')?.1:.01} disabled={busy} value={layer[key]} onChange={e=>change({...layer,[key]:Number(e.target.value)})}/></label>)}</div>
      <VisualTemplateFields value={layer.definition} onChange={definition=>change({...layer,definition})} disabled={busy}/>
      <TemplateKeyframes value={layer.keyframes} disabled={busy} onChange={keyframes=>change({...layer,keyframes})}/>
      <div className={styles.actions}><button className={ui.button} type="button" disabled={busy} onClick={()=>onPreview({operation_type:'EditGraphicOverlay',
        overlay_action:items.some(item=>item.id===layer.id)?'update':'add',graphic_overlay:layer,
        ...(items.some(item=>item.id===layer.id)?{overlay_id:layer.id}:{})})}>保存动画草稿</button>
        {onMediaPreview?<button className={ui.button} type="button" disabled={busy} onClick={()=>onMediaPreview({operation_type:'EditGraphicOverlay',
          overlay_action:items.some(item=>item.id===layer.id)?'update':'add',graphic_overlay:layer,
          ...(items.some(item=>item.id===layer.id)?{overlay_id:layer.id}:{})})}>运行动画预览</button>:null}
        {items.some(item=>item.id===layer.id)?<button className={ui.button} type="button" disabled={busy} onClick={()=>onPreview({operation_type:'EditGraphicOverlay',overlay_action:'delete',overlay_id:layer.id})}>移除动画图层</button>:null}
      </div>
      <p>层级越大越靠前；相同层级按添加顺序叠放。保存走作品版本，可用撤销恢复。</p>
    </section>:null}
    {error?<p role="alert">{error}</p>:null}
  </details>;
}

function TemplateKeyframes({value,disabled,onChange}:{value:VisualTemplateOverlay['keyframes'];disabled:boolean;
  onChange:(value:VisualTemplateOverlay['keyframes'])=>void}) {
  const [text,setText]=useState(JSON.stringify(value,null,2));
  const [error,setError]=useState<string|null>(null);
  return <details><summary>位置、缩放与不透明度关键帧</summary>
    <textarea aria-label="动画关键帧" className={styles.source} disabled={disabled} value={text} onChange={e=>setText(e.target.value)}/>
    <p>相对图层起点的秒数，按时间递增；每项包含 seconds、x、y、scale、opacity。关键帧间线性插值。</p>
    <button className={ui.button} type="button" disabled={disabled} onClick={()=>{try{const parsed=JSON.parse(text);if(!Array.isArray(parsed))throw Error('请输入关键帧数组');onChange(parsed);setError(null);}catch(cause){setError((cause as Error).message);}}}>采用关键帧草稿</button>
    {error?<p role="alert">{error}</p>:null}
  </details>;
}
