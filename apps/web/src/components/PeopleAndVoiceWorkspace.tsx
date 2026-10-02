"use client";

import {
  AlertCircle, AudioLines, ChevronRight, FileText, LoaderCircle, Plus,
  RefreshCw, ShieldCheck, UserRound, Volume2, X,
} from "lucide-react";
import { type ChangeEvent, type FormEvent, useEffect, useMemo, useState } from "react";
import { HomeCardDialog } from "@/components/HomeCardDialog";
import { AppShell } from "@/components/AppShell";
import {
  type AuthorizationInput, type MediaAuthorization, type MediaCapability,
  type MediaPreview, type PersonProfile, type PronunciationEntry, type VoiceProfile,
  createAvatarLipsyncPreview, createVoicePreview, getMediaCapability,
  importVoiceReference, listApprovedMaterials, listAuthorizations,
  listMediaPreviewAttempts, listMediaPreviews, listPersonProfileVersions, listPersonProfiles,
  listPronunciationEntries, listVoiceProfiles, mediaProfilesErrorMessage,
  savePersonProfile, savePronunciationEntry, saveVoiceProfile,
} from "@/lib/media-profiles-api";
import styles from "./PeopleAndVoiceWorkspace.module.css";

type View = "people" | "voices" | "dictionary" | "authorizations";
const viewLabel: Record<View, string> = { people: "人物形象", voices: "声音", dictionary: "发音词典", authorizations: "授权记录" };
const now = () => new Date().toISOString();
const id = (prefix: string) => `${prefix}-${globalThis.crypto?.randomUUID?.().slice(0, 8) ?? Date.now()}`;

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
const modelLabel = (kind: "voice" | "avatar") => kind === "voice" ? "本地声音模型" : "人物口型模型";

function modelCapabilityCopy(capability: MediaCapability | null, kind: "voice" | "avatar") {
  const label = modelLabel(kind);
  return capability?.available ? `${label}已配置` : `${label}尚未配置`;
}

function runnableCopy(runnable: boolean, reason: string | null) {
  if (runnable) return "可用于任务";
  if (reason === "authorization_revoked") return "授权已撤销";
  if (reason === "authorization_expired") return "授权已失效";
  if (reason === "authorization_pending") return "授权等待确认";
  return "暂不可用于任务";
}

function bodyReferenceCopy(person: PersonProfile | null) {
  if (!person) return "尚未选择人物。";
  if (!person.body_reference_available) return person.body_reference_reason ?? "人物参考当前不可用。";
  return `工作区素材 ${person.body_reference_name ?? ""}`.trim();
}

function bodyReferenceLabel(person: PersonProfile, runnableLabel: string) {
  if (person.body_reference_kind === "asset") return "参考待核验";
  if (person.body_reference_available) return runnableLabel;
  return "参考素材缺失";
}

function AuthorizationFields({ prefix, kind }: { prefix: string; kind: "person" | "voice" }) {
  return (
    <fieldset className={styles.authorizationFields}>
      <legend>授权信息</legend>
      <label><span>权利人</span><input name={`${prefix}rights_holder`} required placeholder="授权主体名称" /></label>
      <label><span>授权证据</span><input name={`${prefix}evidence_reference`} required placeholder="工作区凭证或已保存的证据链接" /></label>
      <p>{kind === "person" ? "用于人物口型与人物视频。" : "用于配音与人物口型。"}</p>
    </fieldset>
  );
}

function formAuthorization(data: FormData, prefix: string, kind: "person" | "voice"): AuthorizationInput {
  return {
    id: id("authorization"), version: "v1",
    rights_holder: String(data.get(`${prefix}rights_holder`) ?? "").trim(),
    content_types: kind === "person" ? ["person_likeness", "avatar_video"] : ["voice"],
    usage_scopes: kind === "person" ? ["avatar_lipsync"] : ["narration", "avatar_lipsync"],
    effective_from: now(), expires_at: null,
    evidence_reference: String(data.get(`${prefix}evidence_reference`) ?? "").trim(), status: "active",
  };
}

const canRetryPreview = (preview: MediaPreview | undefined) => preview?.status === "failed" || preview?.status === "unavailable" || preview?.status === "blocked";
const isCurrentVoicePreview = (preview: MediaPreview, voice: VoiceProfile | null) => preview.preview_type === "voice" && preview.voice_profile_id === voice?.id && preview.voice_version === voice?.version;
const isCurrentAvatarPreview = (preview: MediaPreview, person: PersonProfile | null, voice: VoiceProfile | null) => preview.preview_type === "avatar_lipsync" && preview.person_profile_id === person?.id && preview.person_version === person?.version && preview.voice_profile_id === voice?.id && preview.voice_version === voice?.version;
const hasAvatarAuthorization = (authorization: MediaAuthorization | undefined) => authorization?.status === "active" && authorization.content_types.includes("avatar_video") && authorization.usage_scopes.includes("avatar_lipsync");
const currentTimestamp = (preview: MediaPreview) => preview.finished_at ?? preview.started_at;

export function PeopleAndVoiceWorkspace() {
  const [view, setView] = useState<View>("people");
  const [people, setPeople] = useState<PersonProfile[]>([]);
  const [voices, setVoices] = useState<VoiceProfile[]>([]);
  const [entries, setEntries] = useState<PronunciationEntry[]>([]);
  const [authorizations, setAuthorizations] = useState<MediaAuthorization[]>([]);
  const [materials, setMaterials] = useState<Array<{ id: string; file_name: string; workspace_reference: string | null }>>([]);
  const [previews, setPreviews] = useState<MediaPreview[]>([]);
  const [attempts, setAttempts] = useState<MediaPreview[]>([]);
  const [personVersions, setPersonVersions] = useState<PersonProfile[]>([]);
  const [personVersionsError, setPersonVersionsError] = useState<string | null>(null);
  const [voiceCapability, setVoiceCapability] = useState<MediaCapability | null>(null);
  const [avatarCapability, setAvatarCapability] = useState<MediaCapability | null>(null);
  const [selectedPersonId, setSelectedPersonId] = useState<string | null>(null);
  const [selectedVoiceId, setSelectedVoiceId] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [voiceReference, setVoiceReference] = useState<{ reference: string; label: string } | null>(null);

  const selectedPerson = people.find((item) => item.id === selectedPersonId) ?? people[0] ?? null;
  const selectedVoice = voices.find((item) => item.id === selectedVoiceId) ?? voices.find((item) => item.person_profile_id === selectedPerson?.id) ?? voices[0] ?? null;
  const currentVoicePreview = previews.find((preview) => isCurrentVoicePreview(preview, selectedVoice));
  const currentAvatarPreview = previews.find((preview) => isCurrentAvatarPreview(preview, selectedPerson, selectedVoice));
  const activePreview = view === "voices" ? currentVoicePreview : currentAvatarPreview;
  const activeObjectCount = view === "people" ? people.length : voices.length;
  const hasSelectedObject = view === "people" ? Boolean(selectedPerson) : view === "voices" ? Boolean(selectedVoice) : false;

  async function refresh() {
    setLoading(true); setError(null);
    try {
      const [personResponse, voiceResponse, entryResponse, authorizationResponse, materialResponse, voiceResult, avatarResult, previewResponse] = await Promise.all([
        listPersonProfiles(), listVoiceProfiles(), listPronunciationEntries(), listAuthorizations(), listApprovedMaterials(),
        getMediaCapability("voice-clone").catch(() => null), getMediaCapability("avatar-lipsync").catch(() => null), listMediaPreviews(),
      ]);
      setPeople(personResponse.items); setVoices(voiceResponse.items); setEntries(entryResponse.items); setAuthorizations(authorizationResponse.items);
      setMaterials(materialResponse.assets.map((asset) => ({ id: asset.id, file_name: asset.file_name, workspace_reference: asset.workspace_reference })));
      setVoiceCapability(voiceResult); setAvatarCapability(avatarResult); setPreviews(previewResponse.items);
      setSelectedPersonId((current) => current ?? personResponse.items[0]?.id ?? null);
      setSelectedVoiceId((current) => current ?? voiceResponse.items[0]?.id ?? null);
    } catch (cause) { setError(mediaProfilesErrorMessage(cause)); }
    finally { setLoading(false); }
  }

  useEffect(() => { void refresh(); }, []);
  useEffect(() => {
    if (loading || error) return;
    if ((view === "people" && people.length === 0) || (view === "voices" && voices.length === 0)) setShowCreate(true);
  }, [error, loading, people.length, view, voices.length]);
  useEffect(() => {
    if (!activePreview) { setAttempts([]); return; }
    void listMediaPreviewAttempts(activePreview.preview_type, activePreview.identity).then((response) => setAttempts(response.items)).catch(() => setAttempts([]));
  }, [activePreview]);
  useEffect(() => {
    const personId = view === "people" ? selectedPerson?.id ?? null : null;
    if (!personId) { setPersonVersions([]); setPersonVersionsError(null); return; }
    let cancelled = false;
    void listPersonProfileVersions(personId)
      .then((response) => { if (cancelled) return; setPersonVersions(response.items); setPersonVersionsError(null); })
      .catch((cause) => { if (cancelled) return; setPersonVersions([]); setPersonVersionsError(mediaProfilesErrorMessage(cause)); });
    return () => { cancelled = true; };
  }, [selectedPerson?.id, view]);

  function chooseView(next: View) { setView(next); setShowCreate(false); setNotice(null); }
  function recordPreview(preview: MediaPreview) {
    setPreviews((items) => [preview, ...items.filter((item) => item.preview_type !== preview.preview_type || item.identity !== preview.identity)]);
    setAttempts((items) => [preview, ...items.filter((item) => item.id !== preview.id)]);
  }

  async function createPerson(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const data = new FormData(event.currentTarget); setBusy("person"); setNotice(null);
    try {
      const saved = await savePersonProfile({ id: selectedPerson?.id ?? id("person"), display_name: String(data.get("display_name") ?? "").trim(), version: String(data.get("version") ?? "").trim(), avatar_reference: String(data.get("avatar_reference") ?? ""), background_capabilities: [String(data.get("background") ?? "").trim()].filter(Boolean), shot_capabilities: [String(data.get("shot") ?? "").trim()].filter(Boolean), authorization: formAuthorization(data, "person_", "person") });
      setNotice(`已保存人物档案 ${saved.display_name}（${saved.version}）。`); await refresh(); setSelectedPersonId(saved.id); setShowCreate(false); event.currentTarget.reset();
    } catch (cause) { setError(mediaProfilesErrorMessage(cause)); } finally { setBusy(null); }
  }

  async function createVoice(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const data = new FormData(event.currentTarget); setBusy("voice"); setNotice(null);
    try {
      if (!voiceCapability?.available || !voiceCapability.model_id) throw new Error("本地声音模型尚未就绪，暂不能登记可执行声音版本。");
      const person = people.find((item) => item.id === String(data.get("person_profile_id")))!;
      const saved = await saveVoiceProfile({ id: selectedVoice?.id ?? id("voice"), person_profile_id: person.id, person_version: person.version, version: String(data.get("version") ?? "").trim(), language: String(data.get("language")) as VoiceProfile["language"], reference_audio_path: String(data.get("reference_audio_path")), reference_text: String(data.get("reference_text") ?? "").trim(), model_id: voiceCapability.model_id, authorization: formAuthorization(data, "voice_", "voice") });
      setNotice(`已登记声音档案 ${saved.version}；这不是已复制声音。`); await refresh(); setSelectedVoiceId(saved.id); setVoiceReference(null); setShowCreate(false); event.currentTarget.reset();
    } catch (cause) { setError(mediaProfilesErrorMessage(cause)); } finally { setBusy(null); }
  }

  async function importVoice(event: ChangeEvent<HTMLInputElement>) {
    const input = event.currentTarget; const file = input.files?.[0]; if (!file) return;
    setBusy("voice-import"); setError(null); setNotice(null);
    try { const imported = await importVoiceReference(file); setVoiceReference({ reference: imported.reference, label: `WAV 已安全导入 · ${imported.duration_seconds.toFixed(1)} 秒` }); setNotice("声音文件已保存到当前工作区；请继续填写参考文字和授权信息。"); }
    catch (cause) { setError(mediaProfilesErrorMessage(cause)); }
    finally { setBusy(null); input.value = ""; }
  }

  async function createEntry(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const data = new FormData(event.currentTarget); setBusy("entry"); setNotice(null);
    try { const saved = await savePronunciationEntry({ id: id("pronunciation"), term: String(data.get("term") ?? "").trim(), language: String(data.get("language")) as PronunciationEntry["language"], target_pronunciation: String(data.get("target") ?? "").trim(), scope: "workspace", scope_reference: null, version: "v1" }); setNotice(`已保存“${saved.term}”的发音规则。`); await refresh(); event.currentTarget.reset(); }
    catch (cause) { setError(mediaProfilesErrorMessage(cause)); } finally { setBusy(null); }
  }

  async function runVoicePreview(retry = false) {
    if (!selectedVoice) return; setBusy("voice-preview"); setError(null);
    try { recordPreview(await createVoicePreview({ voice_profile_id: selectedVoice.id, voice_version: selectedVoice.version, preview_text: selectedVoice.reference_text, retry })); }
    catch (cause) { setError(mediaProfilesErrorMessage(cause)); } finally { setBusy(null); }
  }

  async function runAvatarPreview(retry = false) {
    const personAuthorization = selectedPerson?.current_authorization;
    const voicePreview = previews.find((preview) => isCurrentVoicePreview(preview, selectedVoice) && preview.status === "succeeded");
    if (!selectedPerson || !selectedVoice || !voicePreview || !hasAvatarAuthorization(personAuthorization) || !avatarCapability?.model_id) return;
    setBusy("avatar-preview"); setError(null);
    try { recordPreview(await createAvatarLipsyncPreview({ person_profile_id: selectedPerson.id, person_version: selectedPerson.version, voice_profile_id: selectedVoice.id, voice_version: selectedVoice.version, voice_preview_identity: voicePreview.identity, motion: "natural", expression: "neutral", authorization_id: personAuthorization.id, authorization_version: personAuthorization.version, model_id: avatarCapability.model_id, retry })); }
    catch (cause) { setError(mediaProfilesErrorMessage(cause)); } finally { setBusy(null); }
  }

  const inspector = useMemo(() => {
    if (view === "dictionary") return <div className={styles.inspector}><span>发音词典</span><h3>{entries.length} 条工作区规则</h3><p>生成中文或中英混合配音时，已保存的品牌名、人名和缩写读法会作为任务依据。</p></div>;
    if (view === "authorizations") return <div className={styles.inspector}><span>授权记录</span><h3>{authorizations.length} 条授权</h3><p>人物、声音与用途必须同时有效，相关档案才能进入创作任务。</p></div>;
    const auth = view === "voices" ? selectedVoice?.current_authorization : selectedPerson?.current_authorization;
    const currentName = view === "voices" ? selectedVoice?.reference_text || "声音档案" : selectedPerson?.display_name || "人物形象";
    const cap = view === "voices" ? voiceCapability : avatarCapability;
    return <div className={styles.inspector}><span className={`${styles.status} ${auth ? authTone(auth).tone : ""}`}>{auth ? authTone(auth).label : "尚未选择档案"}</span><h3>{currentName}</h3>{auth ? <dl><div><dt>当前版本</dt><dd>{view === "voices" ? selectedVoice?.version : selectedPerson?.version}</dd></div>{view === "voices" ? <div><dt>语言</dt><dd>{selectedVoice?.language === "zh-en" ? "中英混合" : selectedVoice?.language === "en" ? "英文" : "中文"}</dd></div> : null}<div><dt>授权内容</dt><dd>{authorizationListLabel(auth.content_types)}</dd></div><div><dt>使用范围</dt><dd>{authorizationListLabel(auth.usage_scopes)}</dd></div><div><dt>授权证据</dt><dd title={auth.evidence_reference}>{authorizationEvidenceLabel(auth.evidence_reference)}</dd></div></dl> : <p>选择一个人物或声音档案，查看版本、授权与可执行操作。</p>}{view === "people" && selectedPerson ? <section><h4>形象参考</h4><p>{bodyReferenceCopy(selectedPerson)}</p>{selectedPerson.body_reference_repair_action ? <p>处理：{selectedPerson.body_reference_repair_action}</p> : null}</section> : null}{view === "people" && selectedPerson ? <section><h4>历史版本</h4>{personVersionsError ? <p>{personVersionsError}</p> : personVersions.length ? personVersions.map((item) => <p key={item.version}>{item.version}{item.version === selectedPerson.version ? " · 当前" : ""} · {bodyReferenceCopy(item)}</p>) : <p>还没有历史版本。</p>}</section> : null}<section><h4>制作情况</h4><p>{modelCapabilityCopy(cap, view === "voices" ? "voice" : "avatar")}</p>{cap?.impact ? <p>影响：{cap.impact}</p> : null}{cap?.repair_action ? <p>处理：{cap.repair_action}</p> : null}</section><section><h4>使用说明</h4><p>仅在你触发生成时，发送当前档案所需的已授权参考内容。</p></section></div>;
  }, [authorizations.length, avatarCapability, entries.length, personVersions, personVersionsError, selectedPerson, selectedVoice, view, voiceCapability]);

  return (
    <AppShell inspector={hasSelectedObject ? inspector : undefined} inspectorTitle={`${viewLabel[view]}详情`}>
      <div className={styles.page}>
        {error ? <section className={styles.error} role="alert"><AlertCircle size={18}/><div><strong>{error}</strong><p>请检查本机服务后重试。</p></div><button onClick={() => void refresh()} type="button">重试</button></section> : null}
        {notice ? <p className={styles.notice} role="status">{notice}</p> : null}
        <div className={styles.viewBar}><nav className={styles.tabs} aria-label="人物与声音视图">{(Object.keys(viewLabel) as View[]).map((item) => <button aria-pressed={view === item} className={view === item ? styles.activeTab : undefined} key={item} onClick={() => chooseView(item)} type="button">{viewLabel[item]}</button>)}</nav><button className={styles.secondaryButton} disabled={loading} onClick={() => void refresh()} type="button"><RefreshCw className={loading ? styles.spinning : undefined} size={15} />刷新</button></div>
        {loading ? <div className={styles.loading}><LoaderCircle className={styles.spinning} size={20}/>正在读取当前工作区的人物与声音档案…</div> : null}
        {!loading && error ? <section className={styles.serviceEmpty}><AlertCircle size={22}/><div><strong>人物与声音档案暂不可读取</strong><p>服务恢复后可继续查看和制作。</p></div><button onClick={() => void refresh()} type="button">重新读取</button></section> : null}
        {!loading && !error ? (
          view === "people" || view === "voices" ? (
            <div className={`${styles.workspace}${activeObjectCount ? "" : ` ${styles.singleStage}`}`}>
              {activeObjectCount ? <aside className={styles.objectRail}>
                <header><div><span>{viewLabel[view]}</span><strong>{view === "people" ? `${people.length} 个档案` : `${voices.length} 个档案`}</strong></div><button aria-label={view === "people" ? "新建人物" : "登记声音"} onClick={() => setShowCreate(true)} type="button"><Plus size={16}/></button></header>
                <div className={styles.objectList}>{view === "people" ? people.map((person) => <button aria-pressed={selectedPerson?.id === person.id} className={selectedPerson?.id === person.id ? styles.selectedObject : undefined} key={person.id} onClick={() => { setSelectedPersonId(person.id); setShowCreate(false); }} type="button"><span><UserRound size={17}/></span><div><strong>{person.display_name}</strong><small>{person.version} · {bodyReferenceLabel(person, runnableCopy(person.runnable, person.runnable_reason))}</small></div><ChevronRight size={15}/></button>) : voices.map((voice) => <button aria-pressed={selectedVoice?.id === voice.id} className={selectedVoice?.id === voice.id ? styles.selectedObject : undefined} key={voice.id} onClick={() => { setSelectedVoiceId(voice.id); setShowCreate(false); }} type="button"><span><Volume2 size={17}/></span><div><strong>{people.find((person) => person.id === voice.person_profile_id)?.display_name || "未找到对应人物"}</strong><small>{voice.version} · {voice.language} · {runnableCopy(voice.runnable, voice.runnable_reason)}</small></div><ChevronRight size={15}/></button>)}</div>
              </aside> : null}
              <section className={styles.stage}>
                {showCreate ? (
                  <HomeCardDialog title={view === "people" ? "添加人物" : "添加声音"} onClose={() => setShowCreate(false)}><CreatePanel view={view} selectedPerson={selectedPerson} selectedVoice={selectedVoice} people={people} materials={materials} busy={busy} voiceReference={voiceReference} voiceModelReady={Boolean(voiceCapability?.available && voiceCapability.model_id)} closable={activeObjectCount > 0} onClose={() => setShowCreate(false)} onCreatePerson={createPerson} onCreateVoice={createVoice} onImportVoice={importVoice} /></HomeCardDialog>
                ) : view === "people" ? (
                  <ProfilePreview materialId={materials.find(item => item.workspace_reference === selectedPerson?.avatar_reference)?.id} person={selectedPerson} voice={selectedVoice} capability={avatarCapability} preview={currentAvatarPreview} voiceReady={currentVoicePreview?.status === "succeeded"} authorizationReady={hasAvatarAuthorization(selectedPerson?.current_authorization)} attempts={activePreview?.identity === currentAvatarPreview?.identity ? attempts : []} busy={busy === "avatar-preview"} onPreview={runAvatarPreview}/>
                ) : (
                  <VoicePreview voice={selectedVoice} capability={voiceCapability} preview={currentVoicePreview} attempts={activePreview?.identity === currentVoicePreview?.identity ? attempts : []} busy={busy === "voice-preview"} onPreview={runVoicePreview}/>
                )}
              </section>
            </div>
          ) : <section className={styles.fullStage}>{view === "dictionary" ? <Dictionary entries={entries} onSubmit={createEntry} busy={busy === "entry"}/> : <AuthorizationList authorizations={authorizations}/>}</section>
        ) : null}
      </div>
    </AppShell>
  );
}

function CreatePanel({ view, selectedPerson, selectedVoice, people, materials, busy, voiceReference, voiceModelReady, closable, onClose, onCreatePerson, onCreateVoice, onImportVoice }: { view:"people"|"voices"; selectedPerson:PersonProfile|null; selectedVoice:VoiceProfile|null; people:PersonProfile[]; materials:Array<{id:string;file_name:string;workspace_reference:string|null}>; busy:string|null; voiceReference:{reference:string;label:string}|null; voiceModelReady:boolean; closable:boolean; onClose:()=>void; onCreatePerson:(event:FormEvent<HTMLFormElement>)=>void; onCreateVoice:(event:FormEvent<HTMLFormElement>)=>void; onImportVoice:(event:ChangeEvent<HTMLInputElement>)=>void }) {
  const personMode = view === "people";
  return <div className={styles.editor}><header><div><h2>{personMode ? selectedPerson ? "添加人物" : "创建人物" : selectedVoice ? "保存声音" : "登记声音"}</h2><p>{personMode ? "人物参考只能来自当前工作区中已审核的素材。" : "先导入已授权声音文件；登记档案不会自动复制声音。"}</p></div>{closable ? <button aria-label="关闭创建面板" onClick={onClose} type="button"><X size={17}/></button> : null}</header>{personMode ? <form className={styles.editorForm} onSubmit={onCreatePerson}><label><span>版本名称</span><input name="version" required placeholder="例如：第一版"/></label><label><span>人物名称</span><input name="display_name" required placeholder="例如：品牌主讲人"/></label><label className={styles.fullField}><span>人物参考素材</span><ApprovedMaterialSelect materials={materials} name="avatar_reference"/></label><label><span>可用背景</span><input name="background" placeholder="例如：室内、纯色背景"/></label><label><span>可用景别</span><input name="shot" placeholder="例如：半身、近景"/></label><AuthorizationFields kind="person" prefix="person_"/><button className={styles.primaryButton} disabled={busy === "person" || !materials.some((item) => item.workspace_reference)} type="submit"><UserRound size={16}/>保存人物</button></form> : people.length ? <form className={styles.editorForm} onSubmit={onCreateVoice}><label><span>版本名称</span><input name="version" required placeholder="例如：第一版"/></label><label><span>关联人物</span><select name="person_profile_id" required>{people.map((person) => <option key={person.id} value={person.id}>{person.display_name} · {person.version}</option>)}</select></label><VoiceReferenceField busy={busy === "voice-import"} onImport={onImportVoice} value={voiceReference}/><label className={styles.fullField}><span>参考文字</span><textarea name="reference_text" required rows={3} placeholder="逐字填写参考音频中的内容"/></label><label><span>语言</span><select name="language"><option value="zh">中文</option></select></label><AuthorizationFields kind="voice" prefix="voice_"/><button className={styles.primaryButton} disabled={busy === "voice" || busy === "voice-import" || !voiceReference || !voiceModelReady} type="submit"><AudioLines size={16}/>保存声音</button>{!voiceModelReady ? <small>本地声音模型未就绪，暂不能保存可执行声音。</small> : null}</form> : <div className={styles.blockedEditor}><AlertCircle size={20}/><div><strong>请先创建人物档案</strong><p>声音版本必须关联一个已保存的人物版本和授权。</p></div></div>}</div>;
}

function ApprovedMaterialSelect({ materials, name }: { materials:Array<{id:string;file_name:string;workspace_reference:string|null}>; name:string }) { const selectable = materials.filter((material) => material.workspace_reference); return <select name={name} required disabled={!selectable.length}>{selectable.length ? selectable.map((material) => <option key={material.id} value={material.workspace_reference!}>{material.file_name}</option>) : <option>没有可引用的已审核素材</option>}</select>; }
function VoiceReferenceField({ busy, onImport, value }: { busy:boolean; onImport:(event:ChangeEvent<HTMLInputElement>)=>void; value:{reference:string;label:string}|null }) { return <div className={`${styles.voiceReference} ${styles.fullField}`}><label><span>声音文件</span><input accept=".wav,audio/wav" aria-label="选择已授权 WAV" disabled={busy} onChange={onImport} type="file"/></label><input name="reference_audio_path" readOnly required type="hidden" value={value?.reference ?? ""}/><small>{busy ? "正在验证并导入 WAV…" : value?.label ?? "选择 1–120 秒、单声道或双声道的已授权 WAV。页面不会提交本机绝对路径。"}</small></div>; }
function Empty({ title, text }: { title:string; text:string }) { return <div className={styles.empty}><FileText size={19}/><div><strong>{title}</strong><p>{text}</p></div></div>; }
function PreviewStatus({ preview }: { preview:MediaPreview|undefined }) { return preview ? <p className={styles.previewStatus}>最近尝试：{preview.status === "succeeded" ? "已生成" : preview.status === "running" ? "正在生成" : preview.reason || "未返回原因"}{preview.repair_action ? ` · ${preview.repair_action}` : ""}{canRetryPreview(preview) ? " · 可以重新尝试" : ""}</p> : null; }
function PreviewAttempts({ attempts }: { attempts:MediaPreview[] }) { return attempts.length ? <section className={styles.previewHistory} aria-label="预览尝试历史"><h4>尝试历史</h4>{attempts.map((attempt) => <p key={attempt.id}>第 {attempt.attempt} 次 · {attempt.status === "succeeded" ? "已生成" : attempt.status === "running" ? "正在生成" : attempt.reason || "未返回原因"} · {new Date(currentTimestamp(attempt)).toLocaleString("zh-CN")}</p>)}</section> : null; }
function ProfilePreview({ materialId, person, voice, capability, preview, voiceReady, authorizationReady, attempts, busy, onPreview }: { materialId?:string;person:PersonProfile|null;voice:VoiceProfile|null;capability:MediaCapability|null;preview:MediaPreview|undefined;voiceReady:boolean;authorizationReady:boolean;attempts:MediaPreview[];busy:boolean;onPreview:(retry?:boolean)=>void }) { const retry = canRetryPreview(preview); const samePerson = Boolean(person && voice && voice.person_profile_id === person.id && voice.person_version === person.version); const blockedReason = !samePerson ? "请选择与当前人物版本匹配的声音档案。" : !voiceReady ? "先生成当前声音版本的短预览。" : !authorizationReady ? "当前人物缺少人物口型授权。" : modelCapabilityCopy(capability,"avatar"); return <div className={styles.preview}><span className={styles.previewIcon}><UserRound size={28}/></span><span className={styles.previewEyebrow}>人物预览</span><strong>{person?.display_name || "选择人物形象"}</strong><p>{person ? `当前版本 ${person.version} · 形象来源：${bodyReferenceCopy(person)}` : "保存人物档案后，可在这里核对版本、授权与口型预览。"}</p>{preview?.status === "succeeded" && preview.output_url ? <video aria-label="人物口型预览" controls src={preview.output_url}/> : materialId && person?.body_reference_available ? <div className={styles.originalPreview}><video aria-label="原始人物视频" controls preload="metadata" src={`/api/production/materials/${encodeURIComponent(materialId)}/media`}/><small>原始视频</small></div> : <div className={styles.mediaPlaceholder}><UserRound size={42}/><span>{person ? person.body_reference_available ? "等待生成短预览" : bodyReferenceCopy(person) : "尚未选择人物"}</span></div>}<div className={styles.previewActions}><button className={styles.primaryButton} disabled={!capability?.available || !person || !samePerson || !voiceReady || !authorizationReady || busy} onClick={() => onPreview(retry)} type="button">{retry ? "重新制作" : "试做口型"}</button><small>{blockedReason}</small></div><PreviewStatus preview={preview}/><PreviewAttempts attempts={attempts}/></div>; }
function VoicePreview({ voice, capability, preview, attempts, busy, onPreview }: { voice:VoiceProfile|null;capability:MediaCapability|null;preview:MediaPreview|undefined;attempts:MediaPreview[];busy:boolean;onPreview:(retry?:boolean)=>void }) { const retry = canRetryPreview(preview); return <div className={styles.preview}><span className={styles.previewIcon}><AudioLines size={28}/></span><span className={styles.previewEyebrow}>声音预览</span><strong>{voice ? `声音 ${voice.version}` : "选择声音档案"}</strong><p>{voice?.reference_text || "登记声音档案后，可在这里核对参考文字、语言和真实生成结果。"}</p>{preview?.status === "succeeded" && preview.output_url ? <audio aria-label="声音预览" controls src={preview.output_url}/> : <p className={styles.mediaPlaceholder}>还没有试听片段</p>}<div className={styles.previewActions}><button className={styles.primaryButton} disabled={!capability?.available || !voice || busy} onClick={() => onPreview(retry)} type="button">{retry ? "重新制作" : "试听声音"}</button><small>{modelCapabilityCopy(capability,"voice")}</small></div><PreviewStatus preview={preview}/><PreviewAttempts attempts={attempts}/></div>; }
function Dictionary({ entries, onSubmit, busy }: { entries:PronunciationEntry[];onSubmit:(event:FormEvent<HTMLFormElement>)=>void;busy:boolean }) { return <><header className={styles.sectionHeader}><div><span>发音词典</span><h2>工作区发音规则</h2><p>统一品牌名、人名、缩写和中英文混合内容的读法。</p></div><em>{entries.length} 条</em></header><div className={styles.records}>{entries.map((entry)=><article key={entry.id}><AudioLines size={16}/><div><strong>{entry.term}</strong><p>{entry.target_pronunciation} · {entry.language} · {entry.version}</p></div></article>)}{!entries.length&&<Empty title="还没有发音规则" text="添加品牌名、人名或缩写的统一读法。"/>}</div><form className={styles.inlineForm} onSubmit={onSubmit}><label><span>词语</span><input name="term" required/></label><label><span>目标发音</span><input name="target" required/></label><label><span>语言</span><select name="language"><option value="zh">中文</option><option value="en">英文</option><option value="zh-en">中英混合</option></select></label><button className={styles.primaryButton} disabled={busy} type="submit">保存发音规则</button></form></>; }
function AuthorizationList({ authorizations }: { authorizations:MediaAuthorization[] }) { return <><header className={styles.sectionHeader}><div><span>授权记录</span><h2>当前有效性与使用范围</h2><p>授权状态决定人物和声音是否可以进入创作任务。</p></div><em>{authorizations.length} 条</em></header><div className={styles.records}>{authorizations.map((auth)=>{const state=authTone(auth);return <article key={auth.id}><ShieldCheck size={16}/><div><strong>{auth.rights_holder}</strong><p>{authorizationListLabel(auth.content_types)} · {authorizationListLabel(auth.usage_scopes)} · {auth.version}</p><small title={auth.evidence_reference}>{authorizationEvidenceLabel(auth.evidence_reference)}</small></div><span className={`${styles.status} ${state.tone}`}>{state.label}</span></article>;})}{!authorizations.length&&<Empty title="还没有授权记录" text="保存人物或声音档案时会同时建立授权记录。"/>}</div></>; }
