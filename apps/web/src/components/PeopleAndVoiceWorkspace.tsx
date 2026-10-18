"use client";
import { AppSelect, AppSelectOption } from "@/components/AppSelect";
import { AudioLines, Plus, MessageSquarePlus, RefreshCw, ShieldCheck, UserRound, Upload } from "lucide-react";
import { type FormEvent, type SyntheticEvent, useEffect, useId, useRef, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { referenceAssistantProfile, readAssistantLocateReference, ASSISTANT_LOCATE_REFERENCE_EVENT, type SelectedAssistantReference } from "@/lib/assistant-reference";
import { getMaterialIntakeStatus, bindMaterialIntake } from "@/lib/api";
import type { MaterialIntakeStatus } from "@/lib/types";
import { importLocalMaterials } from "@/lib/directoryPicker";
import { AppShell } from "@/components/AppShell";
import { Button } from "@/components/ui/button";
import { HomeCardDialog } from "@/components/HomeCardDialog";
import { VoiceSamplingSetup } from "@/components/voice-sampling";
import {
  type MediaAuthorization, type MediaPreview, type PersonProfile, type VoiceProfile, type PronunciationEntry,
  type ProfileReference, type ReferenceMaterial, type MediaCapability, type VoiceReferenceImport,
  listPersonProfiles, listVoiceProfiles, listPersonProfileVersions, listVoiceProfileVersions,
  getPersonProfile, getVoiceProfile, listReferenceMaterials, getReferenceMaterial, scanReferenceMaterials,
  savePersonProfile, saveVoiceProfile, importVoiceReference, getDefaultVoice, setDefaultVoice,
  listPronunciationEntries, listAuthorizations, savePronunciationEntry,
  listMediaPreviews, createVoicePreview, createAvatarLipsyncPreview, getMediaCapability, cancelPreview,
  mediaProfilesErrorMessage, MediaProfilesApiError,
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
const previewLanguageLabels = { zh: "中文", en: "English" };

export function PeopleAndVoiceWorkspace({ initialView = "people", embedded = false, returnTaskId }: { initialView?: View; embedded?: boolean; returnTaskId?: string }) {
  const locateQuery = useSearchParams().get("locate_ref");
  const [view, setView] = useState<View>(initialView);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState("");
  const [people, setPeople] = useState<PersonProfile[]>([]);
  const [voices, setVoices] = useState<VoiceProfile[]>([]);
  const [entries, setEntries] = useState<PronunciationEntry[]>([]);
  const [authorizations, setAuthorizations] = useState<MediaAuthorization[]>([]);
  const [selectedPerson, setPerson] = useState<PersonProfile | null>(null);
  const [selectedVoice, setVoice] = useState<VoiceProfile | null>(null);
  const [versions, setVersions] = useState<Array<PersonProfile | VoiceProfile>>([]);
  const [defaultVoice, setDefault] = useState<VoiceProfile | null>(null);
  const [previews, setPreviews] = useState<MediaPreview[]>([]);
  const editorFormId = useId();
  const [editorBusy, setEditorBusy] = useState(false);
  const [capabilities, setCapabilities] = useState<{ voice?: MediaCapability; person?: MediaCapability }>({});
  useEffect(() => { let active = true; void Promise.all([getMediaCapability("voice-clone"), getMediaCapability("avatar-lipsync")]).then(([voice, person]) => { if (active) setCapabilities({ voice, person }); }).catch(cause => { if (active) setError(mediaProfilesErrorMessage(cause)); }); return () => { active = false; }; }, []);
  const [showDetail, setShowDetail] = useState(false);
  const [showCreate, setShowCreate] = useState(false);
  const [showSampling, setShowSampling] = useState(false);
  const [editorProfile, setEditorProfile] = useState<PersonProfile | VoiceProfile | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [previewText, setPreviewText] = useState("");
  const [bilingualPreviewLanguage, setBilingualPreviewLanguage] = useState<"zh" | "en">("zh");
  useEffect(() => { setPreviewText(selectedVoice?.reference_text ?? ""); }, [selectedVoice?.id, selectedVoice?.version, selectedVoice?.reference_text]);
  const [notice, setNotice] = useState<string | null>(null);
  const editorGeneration = useRef(0);
  const latestSelection = useRef({view,person:selectedPerson,voice:selectedVoice});
  latestSelection.current = {view,person:selectedPerson,voice:selectedVoice};
  const editorTicket = editorGeneration.current;
  useEffect(() => {
    let active = true;
    const locate = (reference: SelectedAssistantReference | null) => {
      if (!reference || (reference.kind !== "person" && reference.kind !== "voice")) return;
      const ticket = ++editorGeneration.current;
      setError(null); setShowCreate(false); setShowSampling(false); setShowDetail(false);
      if (typeof reference.profile_id !== "string" || !reference.profile_id.trim() || typeof reference.version !== "string" || !reference.version.trim()) {
        setError("档案引用缺少固定版本或标识，请重新从对话选择档案。");
        return;
      }
      const read = reference.kind === "voice" ? getVoiceProfile : getPersonProfile;
      void read(reference.profile_id, reference.version).then(object => {
        if (!active || editorGeneration.current !== ticket) return;
        setView(reference.kind === "voice" ? "voices" : "people");
        if (reference.kind === "voice") setVoice(object as VoiceProfile); else setPerson(object as PersonProfile);
        setShowDetail(true);
      }).catch(cause => {
        if (!active || editorGeneration.current !== ticket) return;
        setError(cause instanceof MediaProfilesApiError && cause.status === 404
          ? "引用的档案版本不存在或已不可访问，请重新选择有效档案。"
          : cause instanceof MediaProfilesApiError && cause.status === 403
            ? "当前成员无权查看这个档案版本，请返回有权访问的档案。"
            : mediaProfilesErrorMessage(cause));
      });
    };
    const onLocate = (event: Event) => locate((event as CustomEvent<SelectedAssistantReference>).detail);
    window.addEventListener(ASSISTANT_LOCATE_REFERENCE_EVENT, onLocate);
    if (locateQuery) {
      try { locate(readAssistantLocateReference(new URLSearchParams({ locate_ref: locateQuery }))); }
      catch { setShowDetail(false); setError("引用定位格式不可用，请重新从对话选择档案。"); }
    }
    return () => { active = false; window.removeEventListener(ASSISTANT_LOCATE_REFERENCE_EVENT, onLocate); };
  }, [locateQuery]);
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
    } catch(cause){setError(mediaProfilesErrorMessage(cause));}finally{setLoading(false);}
  }
  useEffect(() => { void refresh(); }, []);
  const generationCapability = view === "voices" ? capabilities.voice : capabilities.person;
  const selected = view === "voices" ? selectedVoice : selectedPerson;
  const voiceModelMismatch = view === "voices" && generationCapability?.available && generationCapability.model_id && selectedVoice?.model_id !== generationCapability.model_id;
  const previewLanguage = selectedVoice?.language === "zh-en" ? bilingualPreviewLanguage : selectedVoice?.language;
  const profileVoicePreviews = previews.filter(run => run.preview_type === "voice" && run.voice_profile_id === selectedVoice?.id && run.voice_version === selectedVoice?.version);
  const voicePreviews = profileVoicePreviews.filter(run => run.language === previewLanguage);
  const selectedVoicePreview=profileVoicePreviews.find(run=>run.status==="succeeded");
  const activePreview = view === "voices" ? voicePreviews.find(run=>run.preview_text===previewText.trim()) ?? voicePreviews[0] : previews.find(run=>run.preview_type === "avatar_lipsync" && run.person_profile_id === selectedPerson?.id && run.person_version === selectedPerson?.version && run.voice_profile_id===selectedVoice?.id && run.voice_version===selectedVoice?.version);
  const pendingVoicePreview=profileVoicePreviews.find(run=>run.status==="queued"||run.status==="running");
  const voicePreviewBusy=Boolean(pendingVoicePreview);
  const selectedId = selected?.id;
  const selectedVersion = selected?.version;
  useEffect(() => {
    if (!selectedId) { setVersions([]); return; }
    let active = true;
    (view === "voices" ? listVoiceProfileVersions(selectedId) : listPersonProfileVersions(selectedId)).then(result => { if (active) setVersions(result.items); }).catch(cause => { if (active) setError(mediaProfilesErrorMessage(cause)); });
    return () => { active = false; };
  }, [selectedId, selectedVersion, view]);
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
        await createVoicePreview({ voice_profile_id: selectedVoice.id, voice_version: selectedVoice.version, preview_text: previewText, language: previewLanguage, retry: Boolean(activePreview && !["queued", "running", "succeeded"].includes(activePreview.status)) });
      } else if (selectedPerson && selectedVoice) {
        const upstream = selectedVoicePreview;
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
  const editorCard = showCreate ? <HomeCardDialog title={view==="voices" ? "登记声音" : "登记人物"} footer={<button className={styles.primaryButton} type="submit" form={editorFormId} disabled={editorBusy}>{editorBusy ? "正在处理…" : "保存参考"}</button>} onClose={() => {++editorGeneration.current;setShowCreate(false);}}><ProfileEditor voiceCapability={capabilities.voice} formId={editorFormId} onBusyChange={setEditorBusy} voice={view==="voices"} selected={editorProfile} onSave={async object => {
        await refresh();
        if(editorGeneration.current===editorTicket && latestSelection.current.view===view){
          if(view==="voices")setVoice(object as VoiceProfile);else setPerson(object as PersonProfile);
          setNotice("档案新版本已保存；未调用生成模型。");setShowCreate(false);setShowDetail(true);
        }
      }}/></HomeCardDialog> : null;
  const body = <div className={styles.page}>
    {showSampling ? <HomeCardDialog title="制作我的声音" onClose={() => setShowSampling(false)}><VoiceSamplingSetup onSkip={() => setShowSampling(false)} onReceived={() => { void refresh(); }}/></HomeCardDialog> : null}
    {returnTaskId ? <Link className={styles.returnLink} href={`/tasks/${encodeURIComponent(returnTaskId)}`}>返回当前任务</Link> : null}
    <nav className={styles.workspaceTabs}>{(embedded ? ["voices"] as View[] : Object.keys(viewLabel) as View[]).map(key => <button key={key} type="button" onClick={() => { ++editorGeneration.current;setView(key);setQuery("");setShowCreate(false);setShowDetail(false); }} aria-pressed={view===key}>{viewLabel[key]}</button>)}</nav>
    {error ? error.includes("预算尚未标定") ? <section><p role="alert">本机生成资源限额尚未配置，参考已保存。 <Link href="/health">查看运行条件</Link></p><details><summary>资源限额诊断</summary><p>{error}</p></details></section> : <p role="alert">{error}</p> : null}{notice ? <p role="status">{notice}</p> : null}
    {view === "dictionary" ? <Dictionary entries={entries} onSubmit={entry} busy={busy}/> : view === "authorizations" ? <AuthorizationList authorizations={authorizations}/> : <>
      <header className={styles.sectionHeader}><h2>{viewLabel[view]}</h2>{view === "voices" ? <button type="button" onClick={() => { setShowDetail(false); setShowCreate(false); setShowSampling(true); }}>录制我的声音</button> : null}<button type="button" onClick={() => { ++editorGeneration.current;setEditorProfile(null);setShowDetail(false);setShowCreate(true); }}><Plus size={16}/>新增{view === "voices" ? "声音" : "人物"}</button><button type="button" aria-label="刷新列表" title="刷新列表" onClick={() => { setError(null); void refresh(); }}><RefreshCw size={16}/></button></header>
      <p className={styles.optionalSetup}>{view === "voices" ? "自己的声音可稍后设置；原声素材剪辑不需要先创建声音档案。" : "人物形象可稍后添加；不出镜的视频不需要人物档案。添加参考后仍需实际预览验证生成能力。"}</p>
      <label className={styles.profileSearch}>搜索名称<input aria-label="搜索人物与声音名称" type="search" placeholder="输入名称" value={query} onChange={event=>setQuery(event.target.value)}/></label>
      {loading ? <p role="status">正在读取档案…</p> : null}
      <div className={styles.workspace}><aside className={styles.objectRail}>{(view === "voices" ? voices : people).filter(item=>(item.display_name||"").toLocaleLowerCase().includes(query.trim().toLocaleLowerCase())).map(item => <button key={item.id} type="button" aria-pressed={selected?.id===item.id} onClick={() => { ++editorGeneration.current;if (view === "voices") setVoice(item as VoiceProfile); else setPerson(item as PersonProfile); referenceAssistantProfile({kind:view==="voices"?"voice":"person",profile_id:item.id,version:item.version,label:item.display_name||"未命名档案"}); setShowCreate(false);setShowDetail(true); }}><span className={styles.objectIcon}>{view === "voices" ? <AudioLines size={16}/> : <UserRound size={16}/>}</span><span className={styles.objectInfo}><strong>{item.display_name || "未命名档案"}</strong><small>{item.runnable ? "参考已保存" : "参考需要处理"}</small></span></button>)}</aside></div>
      {!loading && !error && !(view==="voices"?voices:people).length ? <Empty title="还没有档案" text="登记有效参考即可保存，人物关联与模型不是登记前提。"/> : null}
      {!loading && query.trim() && (view==="voices"?voices:people).length>0 && !(view==="voices"?voices:people).some(item=>(item.display_name||"").toLocaleLowerCase().includes(query.trim().toLocaleLowerCase())) ? <p>没有匹配的档案，请换个名称或清空搜索。</p> : null}
      {showDetail && selected ? <HomeCardDialog kind="detail" title={selected.display_name||"未命名档案"} onClose={()=>{++editorGeneration.current;setShowDetail(false);setShowCreate(false);}}><section className={styles.stage}>
        <div className={styles.profileHeading}><strong>{view==="voices"?"声音参考":"人物参考"}</strong><button type="button" aria-label="引用当前档案到对话" title="引用当前档案到对话" onClick={()=>referenceAssistantProfile({kind:view==="voices"?"voice":"person",profile_id:selected.id,version:selected.version,label:selected.display_name||"未命名档案"},true)}><MessageSquarePlus size={16}/></button></div><button type="button" onClick={() => { ++editorGeneration.current;setEditorProfile(selected);setShowCreate(true); }}>编辑参考</button><p>{usageLabel(selected.use_state)} · 参考已保存</p>
        <label>固定版本<AppSelect aria-label="档案版本" value={selected.version} onValueChange={newValue => { ++editorGeneration.current;const version=newValue; void run(async () => { const object=view==="voices" ? await getVoiceProfile(selected.id,version) : await getPersonProfile(selected.id,version);if(view==="voices")setVoice(object as VoiceProfile);else setPerson(object as PersonProfile);referenceAssistantProfile({kind:view==="voices"?"voice":"person",profile_id:object.id,version:object.version,label:object.display_name||"未命名档案"}); }); }}>{versions.map((item,index) => <AppSelectOption key={item.version} value={item.version}>第{versions.length-index}版</AppSelectOption>)}</AppSelect></label>
        <ReferencePlayback object={selected} voice={view === "voices"}/><details><summary>档案标识</summary><p>{selected.id} · {selected.version}</p></details>{!selected.runnable ? <p>当前参考不能用于生成。编辑参考可重新选择来源或检查使用限制。</p> : null}{selected.runnable_reason ? <details><summary>参考诊断</summary><p>{selected.runnable_reason}</p></details> : null}
        {view === "voices" && selectedVoice ? <><p>{selectedVoice.reference_text || "参考文字未填写；生成前需填写实际对应文字。"}</p>{selectedVoice.language === "zh-en" ? <div role="group" aria-label="试听语言" className={styles.previewLanguage}><span>试听语言</span>{(["zh", "en"] as const).map(language => <Button key={language} type="button" variant={previewLanguage === language ? "secondary" : "outline"} aria-pressed={previewLanguage === language} disabled={busy} onClick={() => setBilingualPreviewLanguage(language)}>{previewLanguageLabels[language]}</Button>)}</div> : <p>试听语言：{previewLanguageLabels[selectedVoice.language]}</p>}<label>短试听文字<textarea aria-label="短试听文字" value={previewText} onChange={event => setPreviewText(event.target.value)} disabled={busy}/></label><button type="button" disabled={busy || !selectedVoice.runnable} onClick={() => void run(async () => { setDefault(await setDefaultVoice(selectedVoice.id,selectedVoice.version)); setNotice("已保存默认声音的固定版本。"); })}>{defaultVoice?.id===selectedVoice.id && defaultVoice.version===selectedVoice.version ? "当前默认版本" : "设为默认声音"}</button></> : <label>本次预览声音<AppSelect aria-label="人物预览声音" value={selectedVoice?.id ?? ""} onValueChange={newValue => setVoice(voices.find(v => v.id===newValue) ?? null)}><AppSelectOption value="">选择独立声音</AppSelectOption>{voices.map(v => <AppSelectOption key={v.id} value={v.id}>{v.display_name}</AppSelectOption>)}</AppSelect></label>}
        {view!=="voices" ? selectedVoicePreview ? <p>口型将使用该声音版本最近成功的试听：{selectedVoicePreview.preview_text || "历史记录未保存文字，音频身份仍为已记录的同一声音版本。"}</p> : <p>先为选中的声音生成短试听，再回来制作人物口型。 <button type="button" onClick={()=>{setView("voices");setShowDetail(Boolean(selectedVoice));}}>打开声音试听</button></p> : null}
        {generationCapability && !generationCapability.available ? <section role="status"><p>参考已保存，暂不可生成。请准备本地{view === "voices" ? "声音" : "人物口型"}模型后重试。 <Link href="/health">查看运行条件</Link></p><details><summary>模型准备详情</summary><p>{generationCapability.message}</p><p>{generationCapability.repair_action}</p></details></section> : null}
        {voiceModelMismatch ? <p>该声音版本尚未选择当前模型，请编辑参考，选择已配置模型并保存新版本。</p> : null}
        {view === "voices" && pendingVoicePreview && pendingVoicePreview.language !== previewLanguage ? <p role="status">该声音还有另一种语言的试听等待或生成。可切换语言查看或取消。</p> : null}
        <button type="button" disabled={busy || voicePreviewBusy || Boolean(voiceModelMismatch) || !selected.runnable || generationCapability?.available === false || (view === "voices" ? !selectedVoice?.reference_text.trim() || !previewText.trim() : !selectedVoice || !selectedVoicePreview) || activePreview?.status==="queued" || activePreview?.status==="running"} onClick={() => void preview()}>{view==="voices" ? "生成短试听" : "制作人物短预览"}</button>
        {activePreview ? <section key={activePreview.identity} aria-label="生成预览状态"><strong>{view==="voices"?"生成试听结果":"生成口型结果"}</strong>{view==="voices" ? <><p>这次结果的试听语言：{previewLanguage && previewLanguageLabels[previewLanguage]}</p><p>这次结果的试听文字：{activePreview.preview_text || "历史记录未保存试听文字"}</p>{activePreview.preview_text!==previewText.trim() ? <p>当前输入与这条结果不同，修改文字后需要生成新的试听。</p> : null}</> : null}<p>{({queued:"等待设备许可",running:"正在生成",succeeded:"已生成",cancelled:"已取消",failed:"失败",blocked:"阻断",unavailable:"能力不可用"})[activePreview.status]}</p><p>{activePreview.impact} {activePreview.repair_action}</p>{activePreview.reason ? <details><summary>预览诊断</summary><p>{activePreview.reason}</p></details> : null}{activePreview.status==="succeeded" && activePreview.output_url ? view==="voices" ? <audio controls src={activePreview.output_url}/> : <video controls src={activePreview.output_url}/> : null}{["queued","running"].includes(activePreview.status) ? <button type="button" onClick={() => void run(async () => { await cancelPreview(activePreview); await refresh(); })}>取消预览</button> : null}</section> : null}
      </section>{editorCard}</HomeCardDialog> : editorCard}

    </>}
  </div>;
  return embedded ? body : <AppShell>{body}</AppShell>;
}

function ReferencePlayback({object,voice}:{object:PersonProfile|VoiceProfile;voice:boolean}) {
  const [material, setMaterial] = useState<ReferenceMaterial|null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { let active=true; setMaterial(null); setError(null); if(object.reference?.asset_id) void getReferenceMaterial(object.reference.asset_id).then(item=>{if(active)setMaterial(item);}).catch(cause=>{if(active)setError(mediaProfilesErrorMessage(cause));}); return()=>{active=false;}; }, [object.id,object.version,object.reference?.asset_id]);
  const ref=object.reference; const url=object.reference_url;
  const importedWav = voice && (!ref?.asset_id || ref.asset_id.startsWith("voice_source_"));
  if (!url) return <p>{object.reference_issue || "参考来源失联或不可读取；保留原档案，可重新选择来源并保存新版本。"}</p>;
  function position(media:HTMLMediaElement) { media.currentTime=ref?.start_seconds??0; }
  function boundary(media:HTMLMediaElement) { if(ref?.end_seconds!=null && media.currentTime>=ref.end_seconds){media.pause();media.currentTime=ref.start_seconds??0;} }
  return <section aria-label="已保存参考">{error ? <p role="alert">{error}</p> : null}<p>原始参考 · {importedWav ? "原始 WAV 样本" : material?.file_name || "已保存参考"}{ref?.start_seconds!=null ? ` · ${ref.start_seconds}–${ref.end_seconds}秒` : " · 完整文件"}</p>{importedWav && material?.file_name ? <details><summary>参考文件标识</summary><p>{material.file_name}</p></details> : null}{voice ? <audio aria-label="原始声音参考" controls src={url} onLoadedMetadata={e=>position(e.currentTarget)} onTimeUpdate={e=>boundary(e.currentTarget)}/> : material?.media_type==="image" ? <img alt="原始人物参考" src={url}/> : <video aria-label="原始人物参考" controls src={url} onLoadedMetadata={e=>position(e.currentTarget)} onTimeUpdate={e=>boundary(e.currentTarget)}/>}</section>;
}

function ProfileEditor({voice,selected,onSave,formId,onBusyChange,voiceCapability}:{voiceCapability?:MediaCapability;voice:boolean;selected:PersonProfile|VoiceProfile|null;onSave:(item:PersonProfile|VoiceProfile)=>Promise<void>;formId:string;onBusyChange:(busy:boolean)=>void}) {
  const configuredModel=voiceCapability?.model_id;
  const [materials,setMaterials]=useState<ReferenceMaterial[]>([]); const [total,setTotal]=useState(0);
  const [assetId,setAsset]=useState(selected?.reference?.asset_id ?? "");
  const [imported,setImported]=useState<string|null>(selected && voice && !selected.reference?.asset_id ? (selected as VoiceProfile).reference_audio_path : null);
  const [importInfo,setImportInfo]=useState<VoiceReferenceImport|null>(null);
  const [showIntakeRepair,setShowIntakeRepair]=useState(false);
  const [importRepair,setImportRepair]=useState(false);
  const [localPreview,setLocalPreview]=useState<string|null>(null);
  const [busy,setBusy]=useState(false); const [error,setError]=useState<string|null>(null); const [notice,setNotice]=useState<string|null>(null);
  const [start,setStart]=useState(selected?.reference?.start_seconds != null ? String(selected.reference.start_seconds) : "");
  const [end,setEnd]=useState(selected?.reference?.end_seconds != null ? String(selected.reference.end_seconds) : "");
  const [segmentId,setSegment]=useState(selected?.reference?.segment_id ?? "");
  useEffect(()=>{onBusyChange(busy);},[busy,onBusyChange]);
  useEffect(()=>()=>{if(localPreview)URL.revokeObjectURL(localPreview);},[localPreview]);
  async function load(offset=0) { const result=await listReferenceMaterials(offset); setMaterials(items=>offset?[...items,...result.assets]:result.assets);setTotal(result.total_count); }
  useEffect(()=>{void load().catch(cause=>setError(mediaProfilesErrorMessage(cause)));},[]);
  const [originalMaterial,setOriginalMaterial]=useState<ReferenceMaterial|null>(null);
  useEffect(()=>{if(selected?.reference?.asset_id)void getReferenceMaterial(selected.reference.asset_id).then(setOriginalMaterial).catch(cause=>setError(mediaProfilesErrorMessage(cause)));},[selected?.id,selected?.version,selected?.reference?.asset_id]);
  const availableMaterials=originalMaterial && !materials.some(item=>item.id===originalMaterial.id) ? [originalMaterial,...materials] : materials;
  const material=availableMaterials.find(item=>item.id===assetId);
  const pendingMaterialId=material && material.media_type!=="image" && material.duration_seconds==null ? material.id : null;
  useEffect(()=>{
    if(!pendingMaterialId)return;
    const timer=setInterval(()=>{void getReferenceMaterial(pendingMaterialId).then(item=>{setMaterials(items=>items.map(current=>current.id===item.id?item:current));setOriginalMaterial(current=>current?.id===item.id?item:current);}).catch(cause=>{setError(mediaProfilesErrorMessage(cause));clearInterval(timer);});},1500);
    return()=>clearInterval(timer);
  },[pendingMaterialId]);
  const permittedMaterial=(item:ReferenceMaterial)=>item.workspace_reference && item.use_state!=="excluded" && (voice?item.media_type!=="image":item.media_type!=="audio");
  function selectMaterial(value:string) { setAsset(value);setImported(null);setImportInfo(null);setLocalPreview(null);setSegment("");setStart("");setEnd("");setError(null); }
  async function importMaterial() {
    setBusy(true);setError(null);setNotice(null);setImportRepair(false);
    try {
      const result=await importLocalMaterials();
      if("kind" in result) throw new Error("请在桌面客户端导入文件，或从已有素材中选择参考。");
      if(result.canceled) return;
      if(result.error) {setImportRepair(true);throw new Error(result.error);}
      const copied=result.copied??[];
      if(!copied.length) throw new Error(result.failures?.[0]?.error || "没有新文件导入，请重新选择文件。");
      await scanReferenceMaterials();
      const directory="directory" in result && typeof result.directory==="string" ? result.directory : null;
      const isImported=(item:ReferenceMaterial,file:{fileName:string})=>item.file_name===file.fileName && (!directory || item.path===`${directory}/${file.fileName}`);
      const all:ReferenceMaterial[]=[];
      let offset=0, count=0;
      do { const page=await listReferenceMaterials(offset);all.push(...page.assets);count=page.total_count;offset+=page.assets.length;if(!page.assets.length)break; } while(offset<count && !copied.every(file=>all.some(item=>isImported(item,file))));
      setMaterials(all);setTotal(count);
      const matches=all.filter(item=>copied.some(file=>isImported(item,file)) && permittedMaterial(item));
      if(directory && matches.length===1) selectMaterial(matches[0].id);
      if(!matches.length) throw new Error("文件已接收，参考素材尚未登记。请点击刷新素材后选择；原文件和当前草稿已保留。");
      setNotice(`已导入${matches.length}条可选参考。${matches.length>1 ? "请在已有素材中选择本次参考。" : "请选择同一人物连续出现或说话的范围。"}${result.failures?.length ? "部分文件未导入，请重新选择。" : ""}`);
    } catch(cause){setError(cause instanceof Error ? cause.message : mediaProfilesErrorMessage(cause));}finally{setBusy(false);}
  }
  async function importWav(file:File) {
    setBusy(true);setError(null);
    try {const result=await importVoiceReference(file);setImported(result.reference);setImportInfo({...result,file_name:file.name});setAsset("");setStart("");setEnd("");setSegment("");setLocalPreview(URL.createObjectURL(file));}
    catch(cause){setError(cause instanceof Error ? cause.message : mediaProfilesErrorMessage(cause));}finally{setBusy(false);}
  }
  async function save(event:FormEvent<HTMLFormElement>) {
    event.preventDefault();const data=new FormData(event.currentTarget);setBusy(true);setError(null);
    try {
      const referencePath=imported || material?.workspace_reference;
      if(!referencePath) throw new Error("请选择真实参考文件。");
      if(material && material.media_type!=="image" && material.duration_seconds==null) throw new Error("文件已接收，正在读取真实时长。完成后再保存参考，当前草稿已保留。");
      if((start==="")!==(end==="")) throw new Error("请同时填写起止时间。");
      if(start!=="" && (!Number.isFinite(Number(start)) || !Number.isFinite(Number(end)) || Number(start)<0 || Number(end)<=Number(start) || (material?.duration_seconds!=null && Number(end)>material.duration_seconds))) throw new Error("请选择有效范围：结束时间需晚于开始时间，且不能超出文件时长。");
      const reference:ProfileReference|undefined=material ? {asset_id:material.id,segment_id:segmentId||null,start_seconds:start===""?null:Number(start),end_seconds:end===""?null:Number(end)} : undefined;
      const common={id:selected?.id??id(voice?"voice":"person"),display_name:String(data.get("display_name")).trim(),reference,use_state:String(data.get("use_state"))};
      const saved=voice ? await saveVoiceProfile({...common,language:String(data.get("language")) as VoiceProfile["language"],reference_audio_path:referencePath,reference_text:String(data.get("reference_text")??"").trim(),model_id:String(data.get("model_id")??"").trim(),...((selected as VoiceProfile|null)?.person_profile_id ? {person_profile_id:(selected as VoiceProfile).person_profile_id,person_version:(selected as VoiceProfile).person_version} : {})}) : await savePersonProfile({...common,avatar_reference:referencePath,background_capabilities:(selected as PersonProfile|null)?.background_capabilities ?? [],shot_capabilities:(selected as PersonProfile|null)?.shot_capabilities ?? []});
      await onSave(saved);
    } catch(cause){setError(cause instanceof Error ? cause.message : mediaProfilesErrorMessage(cause));}finally{setBusy(false);}
  }
  const previewUrl=material ? `/api/production/materials/${encodeURIComponent(material.id)}/media` : imported ? localPreview ?? selected?.reference_url : null;
  return <form id={formId} className={styles.editorForm} onSubmit={save}>{error?<p role="alert">{error}</p>:null}{importRepair ? <button type="button" onClick={()=>setShowIntakeRepair(true)}>接入素材目录</button> : null}{showIntakeRepair ? <HomeCardDialog title="素材接收目录" onClose={()=>setShowIntakeRepair(false)}><ProfileIntakeRepair onReady={()=>{setShowIntakeRepair(false);setImportRepair(false);setError(null);setNotice("目录已关联到当前工作区，可以继续导入；当前草稿已保留。");}}/></HomeCardDialog> : null}{notice?<p role="status">{notice}</p>:null}
    <label>名称<input name="display_name" required defaultValue={selected?.display_name??""}/></label>
    <p>关闭不保存。版本自动生成，保存参考不会运行模型。</p>
    <div className={styles.importActions}><button type="button" disabled={busy} onClick={()=>void importMaterial()}><Upload size={16}/>导入{voice ? "视频或音频" : "人物视频"}</button><button type="button" disabled={busy} onClick={()=>void load().catch(cause=>setError(mediaProfilesErrorMessage(cause)))}>刷新素材</button></div>
    {voice?<label>导入WAV<input aria-label="导入声音WAV" type="file" accept=".wav,audio/wav" disabled={busy} onChange={event=>{const file=event.target.files?.[0];if(file)void importWav(file);event.target.value="";}}/></label>:null}
    <label>已有素材<AppSelect aria-label="参考素材" value={assetId} disabled={busy} onValueChange={newValue =>selectMaterial(newValue)}><AppSelectOption value="">选择素材</AppSelectOption>{availableMaterials.filter(permittedMaterial).map(item=><AppSelectOption key={item.id} value={item.id}>{item.file_name}</AppSelectOption>)}</AppSelect></label>
    {materials.length<total?<button type="button" disabled={busy} onClick={()=>void load(materials.length).catch(cause=>setError(mediaProfilesErrorMessage(cause)))}>读取下一页素材</button>:null}
    {material && material.media_type!=="image" && material.duration_seconds==null ? <p role="status">正在读取参考文件时长，完成后可选取范围和保存。</p> : null}
    {material || imported ? <section className={styles.referenceSelection} aria-label="本次参考预览"><p>{material?.file_name || importInfo?.file_name || "已保存声音参考"}{(material?.duration_seconds ?? importInfo?.duration_seconds)!=null ? ` · ${(material?.duration_seconds ?? importInfo!.duration_seconds).toFixed(1)}秒` : ""}</p>{previewUrl ? <ReferenceSelectionPreview url={previewUrl} mediaType={material?.media_type ?? "audio"} voice={voice} start={Number(start)||0} end={end===""?null:Number(end)} onError={()=>setError("参考文件暂时不能播放，请重新选择来源；当前名称和文字已保留。")}/> : <p>参考暂时无法读取，请重新导入文件或选择已有素材。</p>}{imported ? <p>已登记导入声音，尚未生成模型结果。</p> : null}</section> : null}
    {material?.segments.length ? <label>片段<AppSelect aria-label="参考片段" value={segmentId} onValueChange={newValue =>{setSegment(newValue);const segment=material.segments.find(s=>s.id===newValue);setStart(segment?String(segment.start_seconds):"");setEnd(segment?String(segment.end_seconds):"");}}><AppSelectOption value="">完整文件或自选范围</AppSelectOption>{material.segments.map(item=><AppSelectOption key={item.id} value={item.id}>{item.start_seconds}–{item.end_seconds}秒</AppSelectOption>)}</AppSelect></label>:null}
    {material?.duration_seconds!=null?<><label>开始秒<input aria-label="参考开始秒" type="number" step="0.01" min="0" max={material.duration_seconds} value={start} onChange={e=>{setStart(e.target.value);setSegment("");}}/><input aria-label="调整参考开始" type="range" step="0.01" min="0" max={material.duration_seconds} value={start||0} onChange={e=>{setStart(e.target.value);if(end==="")setEnd(String(material.duration_seconds));setSegment("");}}/></label><label>结束秒<input aria-label="参考结束秒" type="number" step="0.01" min="0" max={material.duration_seconds} value={end} onChange={e=>{setEnd(e.target.value);setSegment("");}}/><input aria-label="调整参考结束" type="range" step="0.01" min="0" max={material.duration_seconds} value={end||material.duration_seconds} onChange={e=>{setEnd(e.target.value);if(start==="")setStart("0");setSegment("");}}/></label><p>请试听或查看所选范围，保留同一人物连续出现或说话的片段；多人混声请缩小范围。</p></>:null}
    {voice?<><label>参考文字（生成前填写）<textarea name="reference_text" defaultValue={(selected as VoiceProfile|null)?.reference_text??""}/></label><p>填写原始样本实际说出的文字。尚未配置转写也可先保存，生成配音前需补齐。</p><label>语言<AppSelect name="language" defaultValue={(selected as VoiceProfile|null)?.language??"zh"}><AppSelectOption value="zh">中文</AppSelectOption><AppSelectOption value="en">英文</AppSelectOption><AppSelectOption value="zh-en">中英混合</AppSelectOption></AppSelect></label><label>生成模型（可选）<AppSelect name="model_id" defaultValue={(selected as VoiceProfile|null)?.model_id??""}><AppSelectOption value="">仅登记参考，暂不指定模型</AppSelectOption>{configuredModel ? <AppSelectOption value={configuredModel}>当前已配置声音模型</AppSelectOption> : null}{(selected as VoiceProfile|null)?.model_id && (selected as VoiceProfile).model_id!==configuredModel ? <AppSelectOption value={(selected as VoiceProfile).model_id}>原版本模型</AppSelectOption>:null}</AppSelect></label></>:null}
    <label>使用限制<AppSelect name="use_state" defaultValue={selected?.use_state??"available"}><AppSelectOption value="available">可用于任务</AppSelectOption><AppSelectOption value="reference_only">仅参考</AppSelectOption><AppSelectOption value="excluded">不使用</AppSelectOption><AppSelectOption value="not_for_automation">不要自动选用</AppSelectOption><AppSelectOption value="not_for_publish">不发布</AppSelectOption></AppSelect></label>
  </form>;
}

function ProfileIntakeRepair({onReady}:{onReady:()=>void}) {
  const [status,setStatus]=useState<MaterialIntakeStatus|null>(null);
  const [busy,setBusy]=useState(false);const [error,setError]=useState<string|null>(null);
  useEffect(()=>{let active=true;void getMaterialIntakeStatus().then(value=>{if(active)setStatus(value);}).catch(cause=>{if(active)setError(cause instanceof Error?cause.message:"素材接收状态读取失败，请重试。");});return()=>{active=false;};},[]);
  async function bind() {
    if(!status?.configured_directory || !status.binding_available || status.directory_state!=="ready")return;
    setBusy(true);setError(null);
    try {const value=await bindMaterialIntake(status.configured_directory);setStatus(value);if(value.bound_to_current_workspace)onReady();}
    catch(cause){setError(cause instanceof Error?cause.message:"目录关联失败，请重试。");}finally{setBusy(false);}
  }
  return <div className={styles.intakeRepair}>{error?<p role="alert">{error}</p>:null}{!status ? <p>正在读取素材接收状态…</p> : <><p>{status.directory_message}</p>{status.repair_action?<p>{status.repair_action}</p>:null}{status.bound_to_current_workspace && status.directory_state==="ready" ? <button type="button" onClick={onReady}>返回并继续导入</button> : !status.bound_to_current_workspace && status.binding_available && status.directory_state==="ready" && status.configured_directory ? <><p>关联当前设置的素材目录后，导入内容归属此工作区；现有人物草稿保留。</p><button type="button" disabled={busy} onClick={()=>void bind()}>{busy?"正在关联…":"关联到当前工作区"}</button></> : <Link href="/settings?section=paths">打开保存位置设置</Link>}<details><summary>目录详情</summary><p>{status.configured_directory || "目录不可披露"}</p></details></>}</div>;
}

function ReferenceSelectionPreview({url,mediaType,voice,start,end,onError}:{url:string;mediaType:ReferenceMaterial["media_type"];voice:boolean;start:number;end:number|null;onError:()=>void}) {
  const player=useRef<HTMLMediaElement|null>(null);
  useEffect(()=>{if(player.current && player.current.readyState>=1)player.current.currentTime=start;},[start,end]);
  const props={controls:true,src:url,onLoadedMetadata:(event:SyntheticEvent<HTMLMediaElement>)=>{event.currentTarget.currentTime=start;},onTimeUpdate:(event:SyntheticEvent<HTMLMediaElement>)=>{if(end!=null && event.currentTarget.currentTime>=end){event.currentTarget.pause();event.currentTarget.currentTime=start;}},onError};
  return mediaType==="image" ? <img src={url} alt="本次人物参考" onError={onError}/> : voice ? <audio key={url} aria-label="本次声音参考" ref={node=>{player.current=node;}} {...props}/> : <video key={url} aria-label="本次人物参考" ref={node=>{player.current=node;}} {...props}/>;
}

function Empty({title,text}:{title:string;text:string}){return <div className={styles.empty}><strong>{title}</strong><p>{text}</p></div>;}
function Dictionary({ entries, onSubmit, busy }: { entries:PronunciationEntry[];onSubmit:(event:FormEvent<HTMLFormElement>)=>void;busy:boolean }) { return <><header className={styles.sectionHeader}><div><span>朗读替换</span><h2>工作区朗读规则</h2><p>填写普通文字，例如“AI”读作“诶艾”。只替换送入声音模型的文字，原文仍用于文案和字幕；不支持音标或SSML。</p><p>保存不是试听成功。到声音入口显式生成短试听，核对实际读法。</p></div><em>{entries.length} 条</em></header><div className={styles.records}>{entries.map((entry)=><article key={entry.id}><AudioLines size={16}/><div><strong>{entry.term}</strong><p>{entry.target_pronunciation} · {entry.language} · {entry.version}</p></div></article>)}{!entries.length&&<Empty title="还没有发音规则" text="添加品牌名、人名或缩写的统一读法。"/>}</div><form className={styles.inlineForm} onSubmit={onSubmit}><label><span>词语</span><input name="term" required/></label><label><span>朗读文字</span><input name="target" placeholder="例如：诶艾" required/></label><label><span>语言</span><AppSelect name="language"><AppSelectOption value="zh">中文</AppSelectOption><AppSelectOption value="en">英文</AppSelectOption><AppSelectOption value="zh-en">中英混合</AppSelectOption></AppSelect></label><button className={styles.primaryButton} disabled={busy} type="submit">保存发音规则</button></form></>; }
function AuthorizationList({ authorizations }: { authorizations:MediaAuthorization[] }) { return <><header className={styles.sectionHeader}><div><span>授权记录</span><h2>当前有效性与使用范围</h2><p>已有授权的有效状态与使用范围仍会限制生成。</p></div><em>{authorizations.length} 条</em></header><div className={styles.records}>{authorizations.map((auth)=>{const state=authTone(auth);return <article key={auth.id}><ShieldCheck size={16}/><div><strong>{auth.rights_holder}</strong><p>{authorizationListLabel(auth.content_types)} · {authorizationListLabel(auth.usage_scopes)} · {auth.version}</p><small title={auth.evidence_reference}>{authorizationEvidenceLabel(auth.evidence_reference)}</small></div><span className={`${styles.status} ${state.tone}`}>{state.label}</span></article>;})}{!authorizations.length&&<Empty title="还没有授权记录" text="普通登记不要求授权证明；已有明确授权与限制仍保留。"/>}</div></>; }
