"use client";
import NextImage from "next/image";
import { useEffect, useMemo, useRef, useState } from "react";
import { Languages, Play, Save, Undo2, Redo2, Settings2 } from "lucide-react";
import { apiUrl } from "@/lib/api";
import type { CreationTaskExactEditInput, CreationTaskSubtitleCue, CreationTaskSubtitleTrack, CreationTaskMontage, CreationTaskCoverAsset, CreationTaskCapability } from "@/lib/types";
import { AppSelect, AppSelectOption } from "./AppSelect";
import { Tabs } from "@base-ui/react/tabs";
import { Popover } from "@base-ui/react/popover";
import styles from "./SubtitleWorkspace.module.css";

function cueEmphasisTerms(cue: CreationTaskSubtitleCue) {
  return cue.emphasis.join("、");
}

function emphasisTerms(text: string, rawTerms: string) {
  return rawTerms
    .split(/[、,]/)
    .map((term) => term.trim())
    .filter(Boolean)
    .filter((term) => text.includes(term));
}

function lineBreakPositions(text: string) {
  const positions: number[] = [];
  let compactOffset = 0;
  for (const character of text) {
    if (character === "\n") {
      positions.push(compactOffset);
    } else {
      compactOffset += 1;
    }
  }
  return positions;
}

function cueTextWithLineBreaks(cue: CreationTaskSubtitleCue) {
  if (!cue.line_breaks.length || cue.text.includes("\n")) return cue.text;
  const positions = new Set(cue.line_breaks);
  return Array.from(cue.text).reduce((result, character, index) => {
    const separator = positions.has(index) ? "\n" : "";
    return `${result}${separator}${character}`;
  }, "") + (positions.has(Array.from(cue.text).length) ? "\n" : "");
}

function SubtitleCueEditor({
  busy,
  cue,
  index,
  onDraftChange,
  onPreview,
  onSelect,
  selected,
  track,
}: {
  busy: boolean;
  cue: CreationTaskSubtitleCue;
  index: number;
  onDraftChange: () => void;
  onPreview: (input: CreationTaskExactEditInput) => void;
  onSelect: () => void;
  selected: boolean;
  track: CreationTaskSubtitleTrack;
}) {
  const storedText = cueTextWithLineBreaks(cue);
  const storedTerms = cueEmphasisTerms(cue);
  const [text, setText] = useState(storedText);
  const [terms, setTerms] = useState(cueEmphasisTerms(cue));
  const [start, setStart] = useState(String(cue.start_seconds));
  const [end, setEnd] = useState(String(cue.end_seconds));
  useEffect(() => {
    setText(storedText); setTerms(storedTerms);
    setStart(String(cue.start_seconds)); setEnd(String(cue.end_seconds));
    setSplitOffset(Math.max(1, Math.floor(Array.from(cue.text).length / 2)));
    setSplitTime(Number(((cue.start_seconds + cue.end_seconds) / 2).toFixed(3)));
  }, [cue.id, cue.text, storedText, cue.start_seconds, cue.end_seconds, storedTerms]);
  const [splitOffset, setSplitOffset] = useState(Math.max(1, Math.floor(Array.from(cue.text).length / 2)));
  const [splitTime, setSplitTime] = useState(Number(((cue.start_seconds + cue.end_seconds) / 2).toFixed(3)));
  const validTime = start.trim() !== "" && end.trim() !== "" && Number(start) >= 0 && Number(end) > Number(start);
  const changed = Number(start) !== cue.start_seconds || Number(end) !== cue.end_seconds || text !== storedText || terms !== cueEmphasisTerms(cue);
  // UpdateSubtitleCue 的 text 上限 96 字（CreationTaskExactEditRequest，schemas.py）。按 Unicode 字符计数，
  // 与后端一致；不用 text.length，那会把 emoji 等星芒平面字符算成两个字。
  const textMaxLength = 96;
  const textLength = Array.from(text).length;
  const overTextLimit = textLength > textMaxLength;

  const save=()=>{if(busy||!changed||!text.trim()||!validTime||overTextLimit)return;
    onPreview({operation_type:"UpdateSubtitleCue",track_id:track.id,cue_id:cue.id,
      start_seconds:Number(start),end_seconds:Number(end),text,
      line_breaks:lineBreakPositions(text),emphasis:emphasisTerms(text,terms)});
  };
  return (
    <article className={[styles.subtitleCue, selected ? styles.selectedSubtitleCue : ""].filter(Boolean).join(" ")}>
      <button aria-label={`选择${track.language === "zh-CN" ? "中文" : "英文"}字幕 ${index + 1}`} aria-pressed={selected} className={styles.cueTime} onClick={onSelect} type="button">
        <strong>{cue.start_seconds.toFixed(1)}–{cue.end_seconds.toFixed(1)}</strong>
      </button>
      <div className={styles.cueFields} onFocus={onSelect} onBlur={save}>
        <div className={styles.timeFields}><label><span>出现时间（秒）</span><input aria-label={`字幕 ${index + 1} 出现时间`} type="number" min="0" step="0.01" disabled={busy} value={start} onChange={(event) => { onDraftChange(); setStart(event.target.value); }} /></label>
        <label><span>结束时间（秒）</span><input aria-label={`字幕 ${index + 1} 结束时间`} type="number" min="0" step="0.01" disabled={busy} value={end} onChange={(event) => { onDraftChange(); setEnd(event.target.value); }} /></label></div>
        <label>
          <span>字幕文本与断行</span>
          <textarea aria-label={`字幕 ${index + 1} 文本`} disabled={busy} onChange={(event) => { onDraftChange(); setText(event.target.value); }} rows={2} value={text} />
          <small aria-label={`字幕 ${index + 1} 字数`}>{textLength} / {textMaxLength} 字</small>
          {overTextLimit ? <small role="alert">字幕文本超出 {textMaxLength} 字上限（当前 {textLength} 字），无法保存。请缩短到 {textMaxLength} 字以内，或还原草稿后用下方「拆分字幕」把它拆成两条。</small> : null}
        </label>
        <label>
          <span>强调词（用顿号分隔）</span>
          <input aria-label={`字幕 ${index + 1} 强调词`} disabled={busy} onChange={(event) => { onDraftChange(); setTerms(event.target.value); }} value={terms} />
        </label>
      </div>
      <details className={styles.propertySection}>
        <summary>拆分、合并与删除</summary>
        <label className={styles.field}>第几个字后拆分<input aria-label="字幕拆分文字位置" type="number" min="1" max={Array.from(cue.text).length - 1} disabled={busy || changed} value={splitOffset} onChange={event => { onDraftChange(); setSplitOffset(Number(event.target.value)); }} /></label>
        <label className={styles.field}>拆分时间（秒）<input aria-label="字幕拆分时间" type="number" step="0.001" min={cue.start_seconds + .04} max={cue.end_seconds - .04} disabled={busy || changed} value={splitTime} onChange={event => { onDraftChange(); setSplitTime(Number(event.target.value)); }} /></label>
        <div className={styles.cueAction}>
          <button type="button" className={styles.secondaryButton} disabled={busy || changed || splitOffset < 1 || splitOffset >= Array.from(cue.text).length || splitTime < cue.start_seconds + .04 || splitTime > cue.end_seconds - .04} onClick={() => onPreview({operation_type:"SplitSubtitleCue", track_id:track.id, cue_id:cue.id, split_offset:splitOffset, split_time_seconds:splitTime})}>拆分字幕</button>
          <button type="button" className={styles.secondaryButton} disabled={busy || changed || index >= track.cues.length - 1} onClick={() => onPreview({operation_type:"MergeSubtitleCue", track_id:track.id, cue_id:cue.id})}>与下一条合并</button>
          <button type="button" className={styles.secondaryButton} disabled={busy || changed} onClick={() => onPreview({operation_type:"DeleteSubtitleCue", track_id:track.id, cue_id:cue.id})}>删除字幕</button>
        </div>
        {changed ? <small>先保存或还原上方文字与时间修改，再调整结构。</small> : null}
      </details>
      <div className={styles.cueAction}>
        {changed ? <small>待保存</small> : <small>已保存</small>}
        <button className={styles.secondaryButton} disabled={busy || !changed || !text.trim() || !validTime || overTextLimit} onClick={save} type="button">保存字幕草稿</button>
      </div>
    </article>
  );
}

function SubtitleStyleEditor({ style, busy, onDraftChange, onPreview }: {
  style: CreationTaskSubtitleTrack["style"]; busy: boolean; onDraftChange: () => void;
  onPreview: (input: CreationTaskExactEditInput) => void;
}) {
  const initial = useMemo(() => ({
    name: style.name, font_family: style.font_family, font_size: style.font_size,
    primary_color: style.primary_color, emphasis_color: style.emphasis_color,
    position: style.position, bilingual_order: style.bilingual_order, enabled: style.enabled,
    stroke_width: style.stroke_width ?? 3, stroke_color: style.stroke_color ?? "#000000",
    background_color: style.background_color ?? "#000000", background_opacity: style.background_opacity ?? 158 / 255,
  }), [style.name, style.font_family, style.font_size, style.primary_color, style.emphasis_color,
    style.position, style.bilingual_order, style.enabled, style.stroke_width, style.stroke_color,
    style.background_color, style.background_opacity]);
  const [draft, setDraft] = useState(initial);
  useEffect(() => { setDraft(initial); }, [initial]);
  const changed = JSON.stringify(draft) !== JSON.stringify(initial);
  const save=(value=draft)=>{
    if(busy||!value.font_family.trim())return;
    const patch=Object.fromEntries(Object.entries(value).filter(([key,v])=>key!=="enabled"&&initial[key as keyof typeof initial]!==v));
    if(Object.keys(patch).length)onPreview({operation_type:"ConfigureSubtitleTrack",style:patch});
  };
  const change=(patch:Partial<typeof draft>,apply=false)=>{onDraftChange();const next={...draft,...patch};setDraft(next);if(apply)save(next);};
  return <section className={styles.styleEditor} aria-label="全部字幕样式">
    <div className={styles.cueFields} onBlur={event=>{if(event.target instanceof HTMLInputElement&&event.target.validity.valid)save();}}>
      <label><span>本机字体</span><input aria-label="字幕字体" disabled={busy} value={draft.font_family} onChange={event => change({font_family:event.target.value})} /></label>
      <label><span>字号</span><input aria-label="字幕字号" required type="number" min="1" max="1920" disabled={busy} value={draft.font_size} onChange={event => change({font_size:Number(event.target.value)})} /></label>
      <label><span>位置</span><AppSelect aria-label="字幕位置" disabled={busy} value={draft.position} onValueChange={newValue => change({position:newValue as typeof draft.position},true)}><AppSelectOption value="top">顶部</AppSelectOption><AppSelectOption value="middle">居中</AppSelectOption><AppSelectOption value="bottom">底部</AppSelectOption></AppSelect></label>
      <label><span>双语顺序</span><AppSelect aria-label="字幕双语顺序" disabled={busy} value={draft.bilingual_order} onValueChange={newValue => change({bilingual_order:newValue as typeof draft.bilingual_order},true)}><AppSelectOption value="source_then_translation">原文在上</AppSelectOption><AppSelectOption value="translation_then_source">译文在上</AppSelectOption></AppSelect></label>
      <div className={styles.colorFields}>{([["primary_color","文字颜色"],["emphasis_color","强调颜色"],["stroke_color","描边颜色"],["background_color","背景颜色"]] as const).map(([key,label]) => <div className={styles.field} key={key}><span>{label}</span><ColorPicker label={`字幕${label}`} disabled={busy} value={draft[key]} onChange={value => change({[key]:value},true)}/></div>)}</div>
      <label><span>描边宽度</span><input aria-label="字幕描边宽度" required type="number" min="0" max="12" disabled={busy} value={draft.stroke_width} onChange={event => change({stroke_width:Number(event.target.value)})} /></label>
      <label><span>背景不透明度 {Math.round(draft.background_opacity*100)}%</span><input aria-label="字幕背景不透明度" type="range" min="0" max="100" disabled={busy} value={Math.round(draft.background_opacity*100)} onChange={event => change({background_opacity:Number(event.target.value)/100})} onPointerUp={()=>save()} onKeyUp={()=>save()} /></label>
    </div>
    <button className={styles.secondaryButton} type="button" disabled={busy || !changed || !draft.font_family.trim()} onClick={()=>save()}>保存字幕样式</button>
  </section>;
}

function ColorPicker({ label, value, disabled, onChange }: { label: string; value: string; disabled: boolean; onChange: (value: string) => void }) {
  return <Popover.Root><Popover.Trigger className={styles.colorTrigger} aria-label={label} title={`${label} ${value}`} disabled={disabled}><span style={{backgroundColor:value}} aria-hidden="true"/></Popover.Trigger><Popover.Portal><Popover.Positioner className={styles.popoverPositioner} side="bottom" align="start" sideOffset={6}><Popover.Popup className={styles.popover} aria-label={`${label}选择`}><div className={styles.palette}>{["#FFFFFF","#000000","#FFD54F","#FF5B5B","#4F8BFF","#32B8A3"].map(color=><button key={color} type="button" aria-label={`${label} ${color}`} title={color} disabled={disabled} aria-pressed={value.toUpperCase()===color} style={{backgroundColor:color}} onClick={()=>onChange(color)}/>)}</div><label className={styles.customColor}>自选颜色<input aria-label={`${label}自选`} type="color" value={value} disabled={disabled} onChange={event=>onChange(event.target.value)}/></label></Popover.Popup></Popover.Positioner></Popover.Portal></Popover.Root>;
}

export function SubtitleWorkspace({
  activeTrack,
  busy,
  onDraftChange,
  onPreviewExact,
  onSelectCover,
  onSelectCue,
  onSelectTrack,
  onSaveCover,
  onSaveSubtitle,
  onUndo,
  onRedo,
  selectedCover,
  selectedCueId,
  taskId, versionId, canUndo, canRedo, cover, montage, planAspectRatio, subtitleTracks: tracks, translationCapability, frameCapability,
}: {
  activeTrack: CreationTaskSubtitleTrack;
  busy: boolean;
  onDraftChange: () => void;
  onPreviewExact: (input: CreationTaskExactEditInput) => void;
  onSelectCover: () => void;
  onSelectCue: (cueId: string) => void;
  onSelectTrack: (trackId: string) => void;
  onSaveCover: () => void;
  onSaveSubtitle: (mode: "zh" | "en" | "zh-en") => void;
  onUndo: () => void;
  onRedo: () => void;
  selectedCover: boolean;
  selectedCueId: string | null;
  taskId: string; versionId: string | null; canUndo: boolean; canRedo: boolean | undefined;
  cover: CreationTaskCoverAsset | null; montage: Pick<CreationTaskMontage, "duration_seconds" | "output_url" | "aspect_ratio"> | null;
  planAspectRatio: NonNullable<CreationTaskMontage["aspect_ratio"]>; subtitleTracks: CreationTaskSubtitleTrack[];
  translationCapability: CreationTaskCapability; frameCapability: CreationTaskCapability;
}) {
  const sourceTrack = tracks.find((track) => track.language === "zh-CN") ?? activeTrack;
  const translationTrack = tracks.find((track) => track.language === "en-US") ?? null;
  const [frameTime, setFrameTime] = useState(cover?.frame_time_seconds ?? 0);
  const [coverTitle, setCoverTitle] = useState(cover?.title ?? "");
  const [titlePosition, setTitlePosition] = useState<"center" | "bottom">(cover?.layout.title_position ?? "bottom");
  const [findText, setFindText] = useState("");
  const [replaceText, setReplaceText] = useState("");
  const [saveSubtitleMode, setSaveSubtitleMode] = useState<"zh" | "en" | "zh-en">(
    activeTrack.language === "en-US" ? "en" : "zh",
  );
  const videoRef = useRef<HTMLVideoElement>(null);
  const [view, setView] = useState<"subtitle" | "style" | "cover">("subtitle");
  const chineseVisible = sourceTrack.language === "zh-CN" && sourceTrack.status === "valid" && sourceTrack.style.enabled !== false;
  const englishVisible = translationTrack?.status === "valid" && translationTrack.style.enabled !== false;
  const chineseSavable = tracks.some((track) => track.language === "zh-CN" && track.status === "valid" && track.cues.length > 0);
  const englishSavable = tracks.some((track) => track.language === "en-US" && track.status === "valid" && track.cues.length > 0);
  const selectedSavable = saveSubtitleMode === "zh" ? chineseSavable : saveSubtitleMode === "en" ? englishSavable : chineseSavable && englishSavable;
  const displayMode = chineseVisible ? englishVisible ? "zh-en" : "zh" : englishVisible ? "en" : "none";
  const duration = montage?.duration_seconds ?? 0;
  const currentCueIndex = Math.max(0, activeTrack.cues.findIndex((cue) => cue.id === selectedCueId));
  const currentCue = activeTrack.cues[currentCueIndex];
  const coverChanged = coverTitle !== (cover?.title ?? "") || frameTime !== (cover?.frame_time_seconds ?? 0) || titlePosition !== (cover?.layout.title_position ?? "bottom");

  useEffect(() => {
    setFrameTime(cover?.frame_time_seconds ?? 0);
    setCoverTitle(cover?.title ?? "");
    setTitlePosition(cover?.layout.title_position ?? "bottom");
  }, [cover?.id, cover?.timeline_version_id, cover?.frame_time_seconds, cover?.title, cover?.layout.title_position]);

  function seekFrame(next: number) {
    const bounded = Math.max(0, Math.min(duration, next));
    setFrameTime(bounded);
    if (videoRef.current) videoRef.current.currentTime = bounded;
  }

  useEffect(() => {
    if (selectedCueId && currentCue) seekFrame(currentCue.start_seconds);
  }, [selectedCueId, activeTrack.id, currentCue?.start_seconds]);

  function enableBilingual() {
    onSelectTrack(sourceTrack.id);
    onPreviewExact({
      operation_type: "ConfigureSubtitleTrack",
      track_id: sourceTrack.id,
      target_language: "en-US",
    });
  }

  function toggleSubtitleTrack(language: "zh-CN" | "en-US") {
    const nextChinese = language === "zh-CN" ? !chineseVisible : chineseVisible;
    const nextEnglish = language === "en-US" ? !englishVisible : englishVisible;
    const subtitle_display = nextChinese ? nextEnglish ? "zh-en" : "zh" : nextEnglish ? "en" : "none";
    onPreviewExact({ operation_type: "ConfigureSubtitleTrack", subtitle_display });
  }

  return (
    <section aria-label="字幕与封面工作区" className={styles.workspace}>
      <Tabs.Root value={view} onValueChange={value => setView(value as typeof view)} className={styles.tabRoot}>
        <div className={styles.toolbar}>
          <Tabs.List className={styles.tabs} aria-label="字幕与封面视图"><Tabs.Tab value="subtitle">字幕</Tabs.Tab><Tabs.Tab value="style">样式</Tabs.Tab><Tabs.Tab value="cover">封面</Tabs.Tab></Tabs.List>
          <div className={styles.history} role="group" aria-label="字幕历史编辑"><button className={styles.iconButton} aria-label="撤销" title="撤销" disabled={busy || !canUndo} onClick={onUndo} type="button"><Undo2 size={16}/></button><button className={styles.iconButton} aria-label="重做" title="重做" disabled={busy || !canRedo} onClick={onRedo} type="button"><Redo2 size={16}/></button></div>
          {view !== "cover" ? <div className={styles.exportActions}>
            <Popover.Root><Popover.Trigger className={styles.iconButton} aria-label="字幕导出设置" title="字幕导出设置" disabled={busy}><Settings2 size={16}/></Popover.Trigger><Popover.Portal><Popover.Positioner className={styles.popoverPositioner} side="bottom" align="end" sideOffset={6}><Popover.Popup className={styles.popover} aria-label="字幕导出设置"><label className={styles.exportField}>保存字幕语言<AppSelect aria-label="保存字幕语言" disabled={busy} value={saveSubtitleMode} onValueChange={newValue => setSaveSubtitleMode(newValue as "zh" | "en" | "zh-en")}><AppSelectOption value="zh">中文 SRT</AppSelectOption><AppSelectOption value="en">英文 SRT</AppSelectOption><AppSelectOption value="zh-en">中英双语 SRT（一个文件，中文在前）</AppSelectOption></AppSelect></label>{!selectedSavable ? <p className={styles.capabilityLine}>所选语言暂无有效字幕轨，请先检查或生成字幕。</p> : null}</Popover.Popup></Popover.Positioner></Popover.Portal></Popover.Root>
            <button className={styles.secondaryButton} disabled={busy || !selectedSavable} onClick={() => onSaveSubtitle(saveSubtitleMode)} type="button"><Save size={15}/>保存字幕</button>
          </div> : null}
        </div>
        <div className={styles.columns}>
          <section className={styles.mediaPanel}>
            <header><span><Play size={15}/>{montage?.output_url ? "当前成片" : "草稿字幕"}</span><small>{duration.toFixed(1)} 秒</small></header>
            {montage?.output_url ? <video key={versionId} aria-label="字幕成片预览" controls onLoadedMetadata={() => seekFrame(frameTime)} preload="metadata" ref={videoRef} style={{aspectRatio:(montage.aspect_ratio ?? planAspectRatio).replace(":","/")}} src={apiUrl(`${montage.output_url}?version=${encodeURIComponent(versionId ?? "")}`)}/> : <p role="status">当前草稿尚无有效成片；字幕可继续编辑，画面预览需重新生成后查看。</p>}
          </section>
          <div className={styles.editor}>
            {view === "subtitle" ? <div className={styles.trackChoice}><span>编辑轨道</span><AppSelect aria-label="当前编辑字幕轨道" disabled={busy} value={activeTrack.id} onValueChange={onSelectTrack}>{tracks.map(track=><AppSelectOption key={track.id} value={track.id}>{track.language === "zh-CN" ? "中文轨" : "英文轨"} · {track.cues.length} 条</AppSelectOption>)}</AppSelect></div> : null}
            <Tabs.Panel value="subtitle" keepMounted className={styles.panel}>
              <div aria-label="字幕轨道开关" className={styles.trackVisibility} role="group">
                <button type="button" role="switch" aria-label="中文轨开关" aria-checked={chineseVisible} disabled={busy || !tracks.some(track => track.language === "zh-CN" && track.status === "valid")} onClick={() => toggleSubtitleTrack("zh-CN")}>显示中文</button>
                <button type="button" role="switch" aria-label="英文轨开关" aria-checked={englishVisible} disabled={busy || translationTrack?.status !== "valid"} onClick={() => toggleSubtitleTrack("en-US")}>显示英文</button>
                <button className={styles.quietButton} type="button" disabled={busy || displayMode === "none"} onClick={() => onPreviewExact({operation_type:"ConfigureSubtitleTrack",subtitle_display:"none"})}>关闭字幕</button>
                {translationTrack?.status !== "valid" ? <button className={styles.secondaryButton} type="button" disabled={busy || !translationCapability.available} onClick={enableBilingual}><Languages size={15}/>生成英文字幕</button> : null}
              </div>
              {!translationCapability.available && !translationTrack ? <details className={styles.propertySection}><summary>翻译暂不可用</summary><p>{translationCapability.message}{translationCapability.repair_action ? ` 处理方式：${translationCapability.repair_action}` : ""}</p></details> : null}
              <label className={styles.cueSelection}><span>选择字幕</span><AppSelect aria-label="选择字幕" disabled={busy} onValueChange={newValue => { const cue=activeTrack.cues.find(item=>item.id===newValue);if(cue){onSelectCue(cue.id);if(videoRef.current)videoRef.current.currentTime=cue.start_seconds;} }} value={currentCue?.id ?? ""}>{activeTrack.cues.map((cue,index)=><AppSelectOption key={cue.id} value={cue.id}>{index+1} · {cue.start_seconds.toFixed(1)}–{cue.end_seconds.toFixed(1)} 秒 · {cue.text}</AppSelectOption>)}</AppSelect></label>
              <div aria-label={`${activeTrack.language === "zh-CN" ? "中文" : "英文"}字幕列表`} className={styles.subtitleCueList}>{currentCue ? <SubtitleCueEditor key={`${taskId}:${versionId}:${activeTrack.id}:${currentCue.id}`} busy={busy} cue={currentCue} index={currentCueIndex} onDraftChange={onDraftChange} onPreview={onPreviewExact} onSelect={() => onSelectCue(currentCue.id)} selected={selectedCueId === currentCue.id} track={activeTrack}/> : <p>当前轨道没有字幕条目。</p>}</div>
              <details className={styles.propertySection}><summary>全文查找替换</summary><label className={styles.field}>查找<input aria-label="字幕查找文字" maxLength={96} value={findText} disabled={busy} onChange={event=>{setFindText(event.target.value);onDraftChange();}}/></label><label className={styles.field}>替换为<input aria-label="字幕替换文字" maxLength={96} value={replaceText} disabled={busy} onChange={event=>{setReplaceText(event.target.value);onDraftChange();}}/></label><button className={styles.secondaryButton} type="button" disabled={busy || !findText || findText===replaceText} onClick={()=>onPreviewExact({operation_type:"ReplaceSubtitleText",track_id:activeTrack.id,find_text:findText,replacement_text:replaceText})}>应用全文替换</button></details>
            </Tabs.Panel>
            <Tabs.Panel value="style" keepMounted className={styles.panel}><p className={styles.styleScope}>全部字幕样式 · 中英文共用</p><SubtitleStyleEditor key={`${taskId}:${versionId}:${sourceTrack.id}`} style={sourceTrack.style} busy={busy} onDraftChange={onDraftChange} onPreview={onPreviewExact}/></Tabs.Panel>
            <Tabs.Panel value="cover" keepMounted className={styles.panel}>
              <section className={selectedCover ? styles.selectedCover : undefined} onFocus={onSelectCover} aria-label="封面编辑">
                {cover?.status === "valid" && cover.artifact_ref ? <NextImage className={styles.coverImage} alt={cover.title} height={1280} src={apiUrl(`/api/creation-tasks/${taskId}/covers/${cover.id}/image`)} unoptimized width={720}/> : null}
                <div className={styles.coverControls}>
                  <label className={styles.field}><span>成片画面时间（秒）</span><input aria-label="封面画面时间" disabled={busy} max={duration} min="0" onChange={event=>{onDraftChange();seekFrame(Number(event.target.value));}} step="0.1" type="number" value={frameTime}/></label>
                  <label className={styles.field}><span>封面标题</span><input aria-label="封面标题" disabled={busy} onChange={event=>{onDraftChange();setCoverTitle(event.target.value);}} value={coverTitle}/></label>
                  <div className={styles.field}><span>标题位置</span><div className={styles.segmentedControl} role="group" aria-label="封面标题位置"><button aria-pressed={titlePosition === "center"} disabled={busy} onClick={()=>{onDraftChange();setTitlePosition("center");}} type="button">中部</button><button aria-pressed={titlePosition === "bottom"} disabled={busy} onClick={()=>{onDraftChange();setTitlePosition("bottom");}} type="button">底部</button></div></div>
                  {coverChanged ? <small className={styles.coverDraft}>选帧、标题或构图待生成{cover?.status === "valid" ? "；保存使用已生成的封面" : ""}</small> : null}
                  <div className={styles.coverActions}>{cover?.status === "valid" && cover.artifact_ref ? <button className={styles.secondaryButton} disabled={busy} onClick={onSaveCover} type="button"><Save size={15}/>保存封面</button> : null}<button className={styles.secondaryButton} disabled={busy || !frameCapability.available || !coverTitle.trim() || duration<=0} onClick={()=>{onSelectCover();onPreviewExact({operation_type:"GenerateCoverFromFrame",frame_time_seconds:frameTime,title:coverTitle.trim(),title_position:titlePosition});}} type="button">从画面生成封面</button></div>
                </div>
              </section>
            </Tabs.Panel>
          </div>
        </div>
      </Tabs.Root>
    </section>
  );
}
