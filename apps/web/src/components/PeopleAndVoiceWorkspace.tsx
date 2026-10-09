"use client";
import { AudioLines, Plus, ShieldCheck } from "lucide-react";
import { type FormEvent, useEffect, useRef, useState } from "react";
import { AppShell } from "@/components/AppShell";
import { HomeCardDialog } from "@/components/HomeCardDialog";
import {
  type MediaAuthorization, type MediaPreview, type PersonProfile, type VoiceProfile, type PronunciationEntry,
  type ProfileReference, type ReferenceMaterial,
  listPersonProfiles, listVoiceProfiles, listPersonProfileVersions, listVoiceProfileVersions,
  getPersonProfile, getVoiceProfile, listReferenceMaterials, getReferenceMaterial,
  savePersonProfile, saveVoiceProfile, importVoiceReference, getDefaultVoice, setDefaultVoice,
  listPronunciationEntries, listAuthorizations, savePronunciationEntry,
  listMediaPreviews, createVoicePreview, createAvatarLipsyncPreview, getMediaCapability, cancelPreview,
  mediaProfilesErrorMessage,
} from "@/lib/media-profiles-api";
import styles from "./PeopleAndVoiceWorkspace.module.css";

function authTone(auth: MediaAuthorization) {
  if (auth.status === "active") return { label: "授权有效", tone: styles.success };
  if (auth.status === "pending") return { label: "等待确认", tone: styles.warning };
  return { label: auth.status === "revoked" ? "授权已撤销" : "授权已失效", tone: styles.danger };
}

const authorizationLabels: Record<string, string> = {
  narration: "配音生成", avatar_lipsync: "人物口型", person_likeness: "人物形象", avatar_video: "人物视频", voice: "声音",
};
const authorizationLabel = (value: string) => authorizationLabels[value] ?? "已授权内容";
const authorizationListLabel = (values: string[]) => values.map(authorizationLabel).join("、");
const authorizationEvidenceLabel = (reference: string) => reference.startsWith("workspace://") ? "已保存授权凭证" : "外部授权凭证";
const useLabels:Record<string,string> = {available:"可用于任务",reference_only:"仅参考",excluded:"不使用",not_for_automation:"不要自动选用",not_for_publish:"不发布"};
const usageLabel = (value?: string) => value ? useLabels[value] ?? "历史使用限制" : "历史状态待核验";


type View = "people" | "voices" | "dictionary" | "authorizations";
const id = (prefix: string) => `${prefix}-${crypto.randomUUID()}`;
const viewLabel: Record<View, string> = { people: "人物形象", voices: "声音", dictionary: "朗读替换", authorizations: "授权记录" };

export function PeopleAndVoiceWorkspace({ initialView = "people", embedded = false }: { initialView?: View; embedded?: boolean }) {
  const [view, setView] = useState<View>(initialView);
  const [people, setPeople] = useState<PersonProfile[]>([]);
  const [voices, setVoices] = useState<VoiceProfile[]>([]);
  const [entries, setEntries] = useState<PronunciationEntry[]>([]);
  const [authorizations, setAuthorizations] = useState<MediaAuthorization[]>([]);
  const [selectedPerson, setPerson] = useState<PersonProfile | null>(null);
  const [selectedVoice, setVoice] = useState<VoiceProfile | null>(null);
  const [versions, setVersions] = useState<Array<PersonProfile | VoiceProfile>>([]);
  const [defaultVoice, setDefault] = useState<VoiceProfile | null>(null);
  const [previews, setPreviews] = useState<MediaPreview[]>([]);
  const [showCreate, setShowCreate] = useState(false);
  const [editorProfile, setEditorProfile] = useState<PersonProfile | VoiceProfile | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [previewText, setPreviewText] = useState("");
  useEffect(() => { setPreviewText(selectedVoice?.reference_text ?? ""); }, [selectedVoice?.id, selectedVoice?.version]);
  const [notice, setNotice] = useState<string | null>(null);
  const editorGeneration = useRef(0);
  const latestSelection = useRef({view,person:selectedPerson,voice:selectedVoice});
  latestSelection.current = {view,person:selectedPerson,voice:selectedVoice};
  const editorTicket = editorGeneration.current;
  async function refresh() {
    const before=latestSelection.current;
    try {
      const [p,v,d,e,a,runs,personDetail,voiceDetail] = await Promise.all([
        listPersonProfiles(),listVoiceProfiles(),getDefaultVoice(),listPronunciationEntries(),listAuthorizations(),listMediaPreviews(),
        before.person ? getPersonProfile(before.person.id,before.person.version) : Promise.resolve(null),
        before.voice ? getVoiceProfile(before.voice.id,before.voice.version) : Promise.resolve(null),
      ]);
      setPeople(p.items);setVoices(v.items);setDefault(d);setEntries(e.items);setAuthorizations(a.items);setPreviews(runs.items);
      setPerson(current=>current?.id===before.person?.id && current?.version===before.person?.version ? personDetail ?? p.items[0] ?? null : current);
      setVoice(current=>current?.id===before.voice?.id && current?.version===before.voice?.version ? voiceDetail ?? v.items[0] ?? null : current);
    } catch(cause){setError(mediaProfilesErrorMessage(cause));}
  }
  useEffect(() => { void refresh(); }, []);
  const selected = view === "voices" ? selectedVoice : selectedPerson;
  const activePreview = previews.find(run => view === "voices" ? run.preview_type === "voice" && run.voice_profile_id === selectedVoice?.id && run.voice_version === selectedVoice.version : run.preview_type === "avatar_lipsync" && run.person_profile_id === selectedPerson?.id && run.person_version === selectedPerson.version);
  useEffect(() => {
    if (!selected) { setVersions([]); return; }
    let active = true;
    (view === "voices" ? listVoiceProfileVersions(selected.id) : listPersonProfileVersions(selected.id)).then(result => { if (active) setVersions(result.items); }).catch(cause => { if (active) setError(mediaProfilesErrorMessage(cause)); });
    return () => { active = false; };
  }, [selected?.id, selected?.version, view]);
  useEffect(() => {
    if (!previews.some(run => run.status === "queued" || run.status === "running")) return;
    const timer = setInterval(() => { void listMediaPreviews().then(r => setPreviews(r.items)).catch(cause => setError(mediaProfilesErrorMessage(cause))); }, 1500);
    return () => clearInterval(timer);
  }, [previews]);
  async function run(action: () => Promise<unknown>) {
    setBusy(true); setError(null);
    try { await action(); } catch (cause) { setError(cause instanceof Error ? cause.message : mediaProfilesErrorMessage(cause)); }
    finally { setBusy(false); }
  }
  async function preview() {
    await run(async () => {
      if (view === "voices" && selectedVoice) {
        await createVoicePreview({ voice_profile_id: selectedVoice.id, voice_version: selectedVoice.version, preview_text: previewText, retry: Boolean(activePreview && !["queued", "running", "succeeded"].includes(activePreview.status)) });
      } else if (selectedPerson && selectedVoice) {
        const upstream = previews.find(r => r.preview_type === "voice" && r.voice_profile_id === selectedVoice.id && r.voice_version === selectedVoice.version && r.status === "succeeded");
        if (!upstream) throw new Error("先完成当前声音版本的独立试听，再制作人物短预览。");
        const capability = await getMediaCapability("avatar-lipsync");
        if (!capability.model_id) throw new Error(capability.message);
        await createAvatarLipsyncPreview({ person_profile_id: selectedPerson.id, person_version: selectedPerson.version, voice_profile_id: selectedVoice.id, voice_version: selectedVoice.version, voice_preview_identity: upstream.identity, motion: "natural", expression: "neutral", model_id: capability.model_id, authorization_id: selectedPerson.current_authorization?.id ?? null, authorization_version: selectedPerson.current_authorization?.version ?? null, retry: Boolean(activePreview && !["queued", "running", "succeeded"].includes(activePreview.status)) });
      }
      await refresh();
    });
  }
  async function entry(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const form = event.currentTarget; const data = new FormData(form);
    await run(async () => { await savePronunciationEntry({ id:id("pronunciation"), term:String(data.get("term")), language:String(data.get("language")) as "zh", target_pronunciation:String(data.get("target")), scope:"workspace", scope_reference:null, version:"v1" }); await refresh(); form.reset(); });
  }
  const body = <div className={styles.page}>
    <nav className={styles.workspaceTabs}>{(embedded ? ["voices"] as View[] : Object.keys(viewLabel) as View[]).map(key => <button key={key} type="button" onClick={() => { ++editorGeneration.current;setView(key);setShowCreate(false); }} aria-pressed={view===key}>{viewLabel[key]}</button>)}</nav>
    {error ? <p role="alert">{error}</p> : null}{notice ? <p role="status">{notice}</p> : null}
    {view === "dictionary" ? <Dictionary entries={entries} onSubmit={entry} busy={busy}/> : view === "authorizations" ? <AuthorizationList authorizations={authorizations}/> : <>
      <header className={styles.sectionHeader}><h2>{viewLabel[view]}</h2><button type="button" onClick={() => { ++editorGeneration.current;setEditorProfile(null);setShowCreate(true); }}><Plus size={16}/>新增{view === "voices" ? "声音" : "人物"}</button><button type="button" onClick={() => { setError(null); void refresh(); }}>刷新列表</button></header>
      <div className={styles.workspace}><aside className={styles.objectRail}>{(view === "voices" ? voices : people).map(item => <button key={item.id} type="button" aria-pressed={selected?.id===item.id} onClick={() => { ++editorGeneration.current;if (view === "voices") setVoice(item as VoiceProfile); else setPerson(item as PersonProfile); setShowCreate(false); }}><strong>{item.display_name || item.id}</strong><small>{item.version} · {item.runnable ? "参考可用" : "参考暂不能用于生成"}</small></button>)}</aside>
      <section className={styles.stage}>{selected ? <>
        <h3>{selected.display_name || selected.id}</h3><button type="button" onClick={() => { ++editorGeneration.current;setEditorProfile(selected);setShowCreate(true); }}>保存新版本</button><p>版本 {selected.version} · {usageLabel(selected.use_state)} · 参考登记不等于模型生成</p>
        <label>固定版本<select aria-label="档案版本" value={selected.version} onChange={event => { ++editorGeneration.current;const version=event.target.value; void run(async () => { if(view==="voices") setVoice(await getVoiceProfile(selected.id, version)); else setPerson(await getPersonProfile(selected.id, version)); }); }}>{versions.map(item => <option key={item.version} value={item.version}>{item.version}</option>)}</select></label>
        <ReferencePlayback object={selected} voice={view === "voices"}/>{!selected.runnable ? <p>当前参考不能用于生成。请查看来源状态和使用限制，重新定位后保存新版本。</p> : null}{selected.runnable_reason ? <details><summary>参考诊断</summary><p>{selected.runnable_reason}</p></details> : null}
        {view === "voices" && selectedVoice ? <><p>{selectedVoice.reference_text || "参考文字未填写；生成前需填写实际对应文字。"}</p><label>短试听文字<textarea aria-label="短试听文字" value={previewText} onChange={event => setPreviewText(event.target.value)} disabled={busy}/></label><button type="button" disabled={busy || !selectedVoice.runnable} onClick={() => void run(async () => { setDefault(await setDefaultVoice(selectedVoice.id,selectedVoice.version)); setNotice("已保存默认声音的固定版本。"); })}>{defaultVoice?.id===selectedVoice.id && defaultVoice.version===selectedVoice.version ? "当前默认版本" : "设为默认声音"}</button></> : <label>本次预览声音<select aria-label="人物预览声音" value={selectedVoice?.id ?? ""} onChange={event => setVoice(voices.find(v => v.id===event.target.value) ?? null)}><option value="">选择独立声音</option>{voices.map(v => <option key={v.id} value={v.id}>{v.display_name} · {v.version}</option>)}</select></label>}
        <button type="button" disabled={busy || activePreview?.status==="queued" || activePreview?.status==="running"} onClick={() => void preview()}>{view==="voices" ? "生成短试听" : "制作人物短预览"}</button>
        {activePreview ? <section aria-label="生成预览状态"><p>{({queued:"等待设备许可",running:"正在生成",succeeded:"已生成",cancelled:"已取消",failed:"失败",blocked:"阻断",unavailable:"能力不可用"})[activePreview.status]}</p><p>{activePreview.impact} {activePreview.repair_action}</p>{activePreview.reason ? <details><summary>预览诊断</summary><p>{activePreview.reason}</p></details> : null}{activePreview.status==="succeeded" && activePreview.output_url ? view==="voices" ? <audio controls src={activePreview.output_url}/> : <video controls src={activePreview.output_url}/> : null}{["queued","running"].includes(activePreview.status) ? <button type="button" onClick={() => void run(async () => { await cancelPreview(activePreview); await refresh(); })}>取消预览</button> : null}</section> : null}
      </> : <Empty title="还没有档案" text="登记有效参考即可保存，人物关联与模型不是登记前提。"/>}</section></div>
      {showCreate ? <HomeCardDialog title={view==="voices" ? "登记声音" : "登记人物"} onClose={() => {++editorGeneration.current;setShowCreate(false);}}><ProfileEditor voice={view==="voices"} selected={editorProfile} onSave={async object => {
        await refresh();
        if(editorGeneration.current===editorTicket && latestSelection.current.view===view){
          if(view==="voices")setVoice(object as VoiceProfile);else setPerson(object as PersonProfile);
          setNotice("档案新版本已保存；未调用生成模型。");setShowCreate(false);
        }
      }}/></HomeCardDialog> : null}
    </>}
  </div>;
  return embedded ? body : <AppShell>{body}</AppShell>;
}

function ReferencePlayback({object,voice}:{object:PersonProfile|VoiceProfile;voice:boolean}) {
  const [material, setMaterial] = useState<ReferenceMaterial|null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { let active=true; setMaterial(null); setError(null); if(object.reference?.asset_id) void getReferenceMaterial(object.reference.asset_id).then(item=>{if(active)setMaterial(item);}).catch(cause=>{if(active)setError(mediaProfilesErrorMessage(cause));}); return()=>{active=false;}; }, [object.id,object.version]);
  const ref=object.reference; const url=object.reference_url;
  if (!url) return <p>{object.reference_issue || "参考来源失联或不可读取；保留原档案，可重新选择来源并保存新版本。"}</p>;
  function position(media:HTMLMediaElement) { media.currentTime=ref?.start_seconds??0; }
  function boundary(media:HTMLMediaElement) { if(ref?.end_seconds!=null && media.currentTime>=ref.end_seconds){media.pause();media.currentTime=ref.start_seconds??0;} }
  return <section aria-label="已保存参考">{error ? <p role="alert">{error}</p> : null}<p>原始参考 · {material?.file_name || ref?.asset_id || "导入声音"}{ref?.start_seconds!=null ? ` · ${ref.start_seconds}–${ref.end_seconds}秒` : " · 完整文件"}</p>{voice ? <audio aria-label="原始声音参考" controls src={url} onLoadedMetadata={e=>position(e.currentTarget)} onTimeUpdate={e=>boundary(e.currentTarget)}/> : material?.media_type==="image" ? <img alt="原始人物参考" src={url}/> : <video aria-label="原始人物参考" controls src={url} onLoadedMetadata={e=>position(e.currentTarget)} onTimeUpdate={e=>boundary(e.currentTarget)}/>}</section>;
}

function ProfileEditor({voice,selected,onSave}:{voice:boolean;selected:PersonProfile|VoiceProfile|null;onSave:(item:PersonProfile|VoiceProfile)=>Promise<void>}) {
  const [configuredModel,setConfiguredModel]=useState<string | null>(null);
  useEffect(()=>{if(voice)void getMediaCapability("voice-clone").then(cap=>setConfiguredModel(cap.model_id)).catch(cause=>setError(mediaProfilesErrorMessage(cause)));},[voice]);
  const [materials,setMaterials]=useState<ReferenceMaterial[]>([]); const [total,setTotal]=useState(0);
  const [assetId,setAsset]=useState(selected?.reference?.asset_id ?? ""); const [imported,setImported]=useState<string|null>(null);
  const [busy,setBusy]=useState(false); const [error,setError]=useState<string|null>(null);
  const [start,setStart]=useState(selected?.reference?.start_seconds != null ? String(selected.reference.start_seconds) : "");const [end,setEnd]=useState(selected?.reference?.end_seconds != null ? String(selected.reference.end_seconds) : "");const [segmentId,setSegment]=useState(selected?.reference?.segment_id ?? "");
  async function load(offset=0) { try { const result=await listReferenceMaterials(offset); setMaterials(items=>offset?[...items,...result.assets]:result.assets);setTotal(result.total_count); } catch(cause){setError(mediaProfilesErrorMessage(cause));} }
  useEffect(()=>{void load();},[]);
  const [originalMaterial,setOriginalMaterial]=useState<ReferenceMaterial|null>(null);
  useEffect(()=>{if(selected?.reference?.asset_id)void getReferenceMaterial(selected.reference.asset_id).then(setOriginalMaterial).catch(cause=>setError(mediaProfilesErrorMessage(cause)));},[selected?.id,selected?.version]);
  const availableMaterials=originalMaterial && !materials.some(item=>item.id===originalMaterial.id) ? [originalMaterial,...materials] : materials;
  const material=availableMaterials.find(item=>item.id===assetId);
  async function save(event:FormEvent<HTMLFormElement>) {
    event.preventDefault();const data=new FormData(event.currentTarget);setBusy(true);setError(null);
    try {
      const referencePath=imported || material?.workspace_reference;
      if(!referencePath) throw new Error("请选择真实参考文件。");
      if((start==="")!==(end==="")) throw new Error("请同时填写起止时间。");
      const reference:ProfileReference|undefined=material ? {asset_id:material.id,segment_id:segmentId||null,start_seconds:start===""?null:Number(start),end_seconds:end===""?null:Number(end)} : undefined;
      const common={id:selected?.id??id(voice?"voice":"person"),display_name:String(data.get("display_name")).trim(),reference,use_state:String(data.get("use_state"))};
      const saved=voice ? await saveVoiceProfile({...common,language:String(data.get("language")) as VoiceProfile["language"],reference_audio_path:referencePath,reference_text:String(data.get("reference_text")??"").trim(),model_id:String(data.get("model_id")??"").trim(),...((selected as VoiceProfile|null)?.person_profile_id ? {person_profile_id:(selected as VoiceProfile).person_profile_id,person_version:(selected as VoiceProfile).person_version} : {})}) : await savePersonProfile({...common,avatar_reference:referencePath,background_capabilities:(selected as PersonProfile|null)?.background_capabilities ?? [],shot_capabilities:(selected as PersonProfile|null)?.shot_capabilities ?? []});
      await onSave(saved);
    } catch(cause){setError(cause instanceof Error?cause.message:mediaProfilesErrorMessage(cause));}finally{setBusy(false);}
  }
  return <form className={styles.editorForm} onSubmit={save}>{error?<p role="alert">{error}</p>:null}<label>名称<input name="display_name" required defaultValue={selected?.display_name??""}/></label><p>版本自动生成。保存参考不需要生成模型；新版本保留明确来源。</p>
    <label>已有素材<select aria-label="参考素材" value={assetId} onChange={event=>{setAsset(event.target.value);setImported(null);setSegment("");setStart("");setEnd("");}}><option value="">选择素材</option>{availableMaterials.filter(item=>item.workspace_reference && item.use_state!=="excluded" && (voice?item.media_type!=="image":item.media_type!=="audio")).map(item=><option key={item.id} value={item.id}>{item.file_name}</option>)}</select></label>
    {materials.length<total?<button type="button" onClick={()=>void load(materials.length)}>读取下一页素材</button>:null}
    {material?.segments.length ? <label>片段<select aria-label="参考片段" value={segmentId} onChange={event=>{setSegment(event.target.value);const segment=material.segments.find(s=>s.id===event.target.value);setStart(segment?String(segment.start_seconds):"");setEnd(segment?String(segment.end_seconds):"");}}><option value="">完整文件或自选范围</option>{material.segments.map(item=><option key={item.id} value={item.id}>{item.start_seconds}–{item.end_seconds}秒</option>)}</select></label>:null}
    {material?.duration_seconds!=null?<><label>开始秒<input aria-label="参考开始秒" type="number" step="any" min="0" max={material.duration_seconds} value={start} onChange={e=>{setStart(e.target.value);setSegment("");}}/></label><label>结束秒<input aria-label="参考结束秒" type="number" step="any" min="0" max={material.duration_seconds} value={end} onChange={e=>{setEnd(e.target.value);setSegment("");}}/></label><p>来源 {material.file_name} · {material.duration_seconds}秒</p></>:null}
    {voice?<><label>导入WAV<input aria-label="导入声音WAV" type="file" accept=".wav,audio/wav" disabled={busy} onChange={event=>{const file=event.target.files?.[0];if(!file)return;setBusy(true);void importVoiceReference(file).then(result=>{setImported(result.reference);setAsset("");setStart("");setEnd("");setSegment("");}).catch(cause=>setError(mediaProfilesErrorMessage(cause))).finally(()=>setBusy(false));}}/></label>{imported?<p>已登记导入声音，尚未生成模型结果。</p>:null}<label>生成模型（可选）<select name="model_id" defaultValue={(selected as VoiceProfile|null)?.model_id??""}><option value="">仅登记参考，暂不指定模型</option>{configuredModel ? <option value={configuredModel}>当前已配置声音模型</option> : null}{(selected as VoiceProfile|null)?.model_id && (selected as VoiceProfile).model_id!==configuredModel ? <option value={(selected as VoiceProfile).model_id}>原版本模型（当前状态由预览核验）</option>:null}</select></label><label>参考文字（生成前填写）<textarea name="reference_text" defaultValue={(selected as VoiceProfile|null)?.reference_text??""}/></label><label>语言<select name="language" defaultValue={(selected as VoiceProfile|null)?.language??"zh"}><option value="zh">中文</option><option value="en">英文</option><option value="zh-en">中英混合</option></select></label></>:null}
    <label>使用限制<select name="use_state" defaultValue={selected?.use_state??"available"}><option value="available">可用于任务</option><option value="reference_only">仅参考</option><option value="excluded">不使用</option><option value="not_for_automation">不要自动选用</option><option value="not_for_publish">不发布</option></select></label><button type="submit" disabled={busy}>保存新版本</button>
  </form>;
}

function Empty({title,text}:{title:string;text:string}){return <div className={styles.empty}><strong>{title}</strong><p>{text}</p></div>;}
function Dictionary({ entries, onSubmit, busy }: { entries:PronunciationEntry[];onSubmit:(event:FormEvent<HTMLFormElement>)=>void;busy:boolean }) { return <><header className={styles.sectionHeader}><div><span>朗读替换</span><h2>工作区朗读规则</h2><p>填写普通文字，例如“AI”读作“诶艾”。只替换送入声音模型的文字，原文仍用于文案和字幕；不支持音标或SSML。</p><p>保存不是试听成功。到声音入口显式生成短试听，核对实际读法。</p></div><em>{entries.length} 条</em></header><div className={styles.records}>{entries.map((entry)=><article key={entry.id}><AudioLines size={16}/><div><strong>{entry.term}</strong><p>{entry.target_pronunciation} · {entry.language} · {entry.version}</p></div></article>)}{!entries.length&&<Empty title="还没有发音规则" text="添加品牌名、人名或缩写的统一读法。"/>}</div><form className={styles.inlineForm} onSubmit={onSubmit}><label><span>词语</span><input name="term" required/></label><label><span>朗读文字</span><input name="target" placeholder="例如：诶艾" required/></label><label><span>语言</span><select name="language"><option value="zh">中文</option><option value="en">英文</option><option value="zh-en">中英混合</option></select></label><button className={styles.primaryButton} disabled={busy} type="submit">保存发音规则</button></form></>; }
function AuthorizationList({ authorizations }: { authorizations:MediaAuthorization[] }) { return <><header className={styles.sectionHeader}><div><span>授权记录</span><h2>当前有效性与使用范围</h2><p>已有授权的有效状态与使用范围仍会限制生成。</p></div><em>{authorizations.length} 条</em></header><div className={styles.records}>{authorizations.map((auth)=>{const state=authTone(auth);return <article key={auth.id}><ShieldCheck size={16}/><div><strong>{auth.rights_holder}</strong><p>{authorizationListLabel(auth.content_types)} · {authorizationListLabel(auth.usage_scopes)} · {auth.version}</p><small title={auth.evidence_reference}>{authorizationEvidenceLabel(auth.evidence_reference)}</small></div><span className={`${styles.status} ${state.tone}`}>{state.label}</span></article>;})}{!authorizations.length&&<Empty title="还没有授权记录" text="普通登记不要求授权证明；已有明确授权与限制仍保留。"/>}</div></>; }
