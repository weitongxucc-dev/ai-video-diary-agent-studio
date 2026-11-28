"use client";
import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { Plus, Search, X } from "lucide-react";
import { getProductionMaterials } from "@/lib/api/materials";
import type { MaterialAsset } from "@/lib/types";
import { useAssistantReferenceStatus, useAssistantSelection } from "../AssistantConversationProvider";
import { workspaceNavigationHref } from "../workbench/context";
import { useWorkbenchTarget } from "../workbench/WorkbenchTarget";
import { Button } from "./button";
import { Popover, PopoverContent, PopoverTrigger, PopoverTitle, PopoverClose } from "./popover";
import { ReferencePreview } from "./reference-preview";

export function ReferencePicker({open, onOpenChange, onAdded}: {
  open:boolean; onOpenChange:(open:boolean)=>void; onAdded:()=>void;
}) {
  const selection=useAssistantSelection();
  const referenceStatus = useAssistantReferenceStatus();
  const pdfInput = useRef<HTMLInputElement>(null);
  const [pdfAttempt, setPdfAttempt] = useState<{file: File; error: string | null} | null>(null);
  const uploadingPdf = useRef(false);
  const pdfAttemptEpoch = useRef(0);
  useEffect(() => () => { pdfAttemptEpoch.current++; }, []);
  const { targetId } = useWorkbenchTarget();
  const [query,setQuery]=useState("");
  const queryRef=useRef(query);queryRef.current=query;
  const [assets,setAssets]=useState<MaterialAsset[]>([]);
  const [error,setError]=useState<string|null>(null);
  const [loading,setLoading]=useState(false);
  const [revision,setRevision]=useState(0);
  useEffect(()=>{
    if(!open)return;
    let active=true;setLoading(true);setError(null);
    void getProductionMaterials({limit:20,query:queryRef.current}).then(result=>{if(active)setAssets(result.assets);})
      .catch(cause=>{if(active)setError(cause instanceof Error?cause.message:"素材读取失败。");})
      .finally(()=>{if(active)setLoading(false);});
    return()=>{active=false;};
  },[open,revision]);
  async function addPdf(file: File) {
    if (!selection?.attachPdf || uploadingPdf.current) return;
    uploadingPdf.current = true;
    const epoch = ++pdfAttemptEpoch.current;
    setPdfAttempt({file, error: null});
    try {
      await selection.attachPdf(file);
      if (epoch !== pdfAttemptEpoch.current) return;
      setPdfAttempt(null);
      onOpenChange(false);
    } catch (cause) {
      if (epoch === pdfAttemptEpoch.current) {
        setPdfAttempt({file, error: cause instanceof Error ? cause.message : "附件未保存，请重试。"});
      }
    } finally {
      uploadingPdf.current = false;
    }
  }
  if(!selection)return null;
  return <Popover open={open} onOpenChange={onOpenChange}>
    <PopoverTrigger render={<Button variant="ghost" size="icon"/>} aria-label="添加素材引用" title="添加素材引用，也可以输入 @"><Plus size={16}/></PopoverTrigger>
    <PopoverContent className="assistant-reference-popover">
      <div className="assistant-reference-heading"><PopoverTitle>添加素材引用</PopoverTitle><PopoverClose render={<Button variant="ghost" size="icon"/>} aria-label="关闭素材选择"><X size={16}/></PopoverClose></div>
      {selection.attachPdf ? <div>
        <input ref={pdfInput} type="file" accept="application/pdf,.pdf" hidden onChange={event => {
          const file = event.currentTarget.files?.[0];
          event.currentTarget.value = "";
          if (file) void addPdf(file);
        }}/>
        <Button type="button" variant="ghost" disabled={referenceStatus.pending} onClick={() => pdfInput.current?.click()}>
          {selection.items.some(item => item.kind === "turn_pdf") ? "替换" : "添加"}本地 PDF · 仅本轮使用
        </Button>
        {pdfAttempt?.error ? <div><p role="alert">{pdfAttempt.error}</p>
          <Button type="button" variant="ghost" disabled={referenceStatus.pending} onClick={() => void addPdf(pdfAttempt.file)}>重试 PDF 上传</Button>
        </div> : pdfAttempt ? <p role="status">正在保存 PDF…</p> : null}
      </div> : null}
      <div className="assistant-reference-search"><Search size={16}/><input aria-label="搜索引用素材" placeholder="搜索文件、画面或标签" value={query} onChange={event=>setQuery(event.target.value)} onKeyDown={event=>{if(event.key==="Enter"&&!event.nativeEvent.isComposing){event.preventDefault();setRevision(value=>value+1);}}}/><Button variant="ghost" onClick={()=>setRevision(value=>value+1)}>搜索</Button></div>
      {loading?<p role="status">正在读取素材…</p>:error?<p role="alert">{error}<Button variant="ghost" onClick={()=>setRevision(value=>value+1)}>重试</Button></p>:<ul className="assistant-reference-list">{assets.map(asset=><li key={asset.id}><Button variant="ghost" className="assistant-reference-locate" disabled={Boolean(pdfAttempt && !pdfAttempt.error)} onClick={()=>{
        selection.select([{kind:"material",label:asset.file_name,selection:{scope:"asset",asset_id:asset.id,expected_updated_at:asset.updated_at}}],true);
        onAdded();onOpenChange(false);
      }}><ReferencePreview assetId={asset.id} mediaType={asset.media_type}/><span>{asset.file_name}<small>{asset.media_type === "audio" ? "音频" : asset.media_type === "image" ? "图片" : "视频"} · 整条素材</small></span></Button></li>)}</ul>}
      {!loading&&!error&&!assets.length?<p>没有找到素材。</p>:null}
      <nav className="assistant-reference-links"><Link href={workspaceNavigationHref("/library", Boolean(targetId))} onClick={()=>onOpenChange(false)}>完整素材库与片段</Link><Link href={workspaceNavigationHref("/knowledge", Boolean(targetId))} onClick={()=>onOpenChange(false)}>引用资料</Link><Link href="/profiles" onClick={()=>onOpenChange(false)}>人物与声音</Link></nav>
    </PopoverContent>
  </Popover>;
}
