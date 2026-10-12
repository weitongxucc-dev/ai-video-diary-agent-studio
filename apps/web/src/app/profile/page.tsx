"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { Plus, X } from "lucide-react";
import { AppShell } from "@/components/AppShell";
import { AppSelect, AppSelectOption } from "@/components/AppSelect";
import { CreatorProfileApiError, exportCreatorPreferences, getCreatorProfile, updateCreatorProfile } from "@/lib/profile-api";
import type { CreatorProfile } from "@/lib/types";
import styles from "./ProfileWorkspace.module.css";

type PreferenceRow = { row: number; name: string; value: string };
const fixedPreferences = [
  { key: "tone", label: "语气", options: [["casual", "随意口语"], ["formal", "正式书面"], ["conversational", "对话聊天"]] },
  { key: "sentence_length", label: "句长", options: [["short", "短句（15字以内）"], ["medium", "中句（15-30字）"], ["long", "长句（30字以上）"]] },
  { key: "hook_style", label: "开篇方式", options: [["question", "提问式"], ["exclamation", "感叹式"], ["statement", "陈述式"]] },
];
const fixedKeys = new Set(fixedPreferences.map(item => item.key));

function SavedPreferences({ profile }: { profile: CreatorProfile }) {
  return <dl className={styles.saved}>
    <div><dt>常用表达</dt><dd>{profile.common_expressions.join("、") || "未设置"}</dd></div>
    <div><dt>避免用词</dt><dd>{profile.banned_words.join("、") || "未设置"}</dd></div>
    {Object.entries(profile.style_preferences).map(([key, value]) => {
      const fixed = fixedPreferences.find(item => item.key === key);
      return <div key={key}><dt>{fixed?.label ?? key}</dt><dd>{fixed?.options.find(item => item[0] === value)?.[1] ?? value}</dd></div>;
    })}
  </dl>;
}

export default function ProfilePage() {
  const [profile, setProfile] = useState<CreatorProfile | null>(null);
  const [savedProfile, setSavedProfile] = useState<CreatorProfile | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<{ error: boolean; text: string } | null>(null);
  const [saving, setSaving] = useState(false);
  const [rereading, setRereading] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [conflict, setConflict] = useState(false);
  const [latest, setLatest] = useState<CreatorProfile | null>(null);
  const [custom, setCustom] = useState<PreferenceRow[]>([]);
  const nextRow = useRef(0);
  const [newExpression, setNewExpression] = useState("");
  const [newBannedWord, setNewBannedWord] = useState("");
  const [newName, setNewName] = useState("");
  const [newValue, setNewValue] = useState("");
  const replaceDraft = useCallback((value: CreatorProfile) => {
    setProfile(value); setSavedProfile(value);
    setCustom(Object.entries(value.style_preferences).filter(([key]) => !fixedKeys.has(key))
      .map(([name, preference]) => ({ row: nextRow.current++, name, value: preference })));
    setConflict(false); setLatest(null);
  }, []);
  const loadProfile = useCallback(async () => {
    setLoading(true); setLoadError(null);
    try { replaceDraft(await getCreatorProfile()); }
    catch (cause) { setLoadError(cause instanceof Error ? cause.message : "无法读取创作偏好"); }
    finally { setLoading(false); }
  }, [replaceDraft]);
  useEffect(() => { void loadProfile(); }, [loadProfile]);

  function addListValue(field: "common_expressions" | "banned_words", value: string) {
    const text = value.trim();
    if (!profile || !text) return;
    setProfile({ ...profile, [field]: [...new Set([...profile[field], text])] });
    if (field === "common_expressions") setNewExpression(""); else setNewBannedWord("");
    setFeedback(null);
  }
  function removeListValue(field: "common_expressions" | "banned_words", value: string) {
    if (profile) setProfile({ ...profile, [field]: profile[field].filter(item => item !== value) });
    setFeedback(null);
  }
  function setPreference(key: string, value: string) {
    if (!profile) return;
    const preferences = { ...profile.style_preferences };
    if (value) preferences[key] = value; else delete preferences[key];
    setProfile({ ...profile, style_preferences: preferences }); setFeedback(null);
  }
  function addCustom() {
    const name = newName.trim(), value = newValue.trim();
    if (!name || !value) return;
    if (fixedKeys.has(name) || custom.some(item => item.name.trim() === name)) {
      setFeedback({ error: true, text: "这个偏好名称已有内容，请直接修改对应内容。" }); return;
    }
    setCustom([...custom, { row: nextRow.current++, name, value }]);
    setNewName(""); setNewValue(""); setFeedback(null);
  }
  async function handleSave() {
    if (!profile || conflict) return;
    const names = custom.map(item => item.name.trim());
    if (custom.some(item => !item.name.trim() || !item.value.trim()) || names.some(name => fixedKeys.has(name)) || new Set(names).size !== names.length) {
      setFeedback({ error: true, text: "请填写不重复的偏好名称与内容；不再需要的偏好可以删除。" }); return;
    }
    const preferences = Object.fromEntries([
      ...Object.entries(profile.style_preferences).filter(([key, value]) => fixedKeys.has(key) && value),
      ...custom.map(item => [item.name.trim(), item.value.trim()]),
    ]);
    setSaving(true); setFeedback(null);
    try {
      const updated = await updateCreatorProfile({ expected_revision: profile.revision,
        common_expressions: profile.common_expressions, banned_words: profile.banned_words, style_preferences: preferences });
      replaceDraft(updated); setFeedback({ error: false, text: "创作偏好已保存。" });
    } catch (cause) {
      if (cause instanceof CreatorProfileApiError && cause.status === 409) {
        setConflict(true); setLatest(null);
        setFeedback({ error: true, text: "已保存的偏好发生了变化。本页草稿已保留，请重新读取并核对后再保存。" });
      } else setFeedback({ error: true, text: cause instanceof Error ? cause.message : "保存失败，修改仍保留在本页。" });
    } finally { setSaving(false); }
  }
  async function rereadSaved() {
    setRereading(true);
    try { setLatest(await getCreatorProfile()); setFeedback(null); }
    catch (cause) { setFeedback({ error: true, text: cause instanceof Error ? cause.message : "重新读取失败，草稿仍保留在本页。" }); }
    finally { setRereading(false); }
  }
  async function handleExport() {
    setExporting(true); setFeedback(null);
    try {
      const blob = await exportCreatorPreferences();
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url; link.download = "user.md";
      document.body.appendChild(link);
      try { link.click(); }
      finally { link.remove(); URL.revokeObjectURL(url); }
      setFeedback({ error: false, text: "已开始下载已保存的偏好。" });
    } catch (cause) {
      setFeedback({ error: true, text: cause instanceof Error ? cause.message : "导出失败，修改仍保留在本页。" });
    } finally { setExporting(false); }
  }
  const busy = saving || rereading || exporting;
  const draftStyles = profile ? [...Object.entries(profile.style_preferences).filter(([key, value]) => fixedKeys.has(key) && value), ...custom.map(item => [item.name, item.value])].sort(([a], [b]) => a.localeCompare(b)) : [];
  const hasUnsavedChanges = profile && savedProfile && (
    JSON.stringify(profile.common_expressions) !== JSON.stringify(savedProfile.common_expressions)
    || JSON.stringify(profile.banned_words) !== JSON.stringify(savedProfile.banned_words)
    || JSON.stringify(draftStyles) !== JSON.stringify(Object.entries(savedProfile.style_preferences).sort(([a], [b]) => a.localeCompare(b)))
    || Boolean(newExpression || newBannedWord || newName || newValue)
  );

  return <AppShell toolbar={<span>创作偏好</span>} toolbarActions={<Link className="toolbar-button" href="/settings?section=creators">返回设置</Link>}><div className={styles.page}>
    <h1 className={styles.srOnly}>创作偏好</h1>
    <p className={styles.description}>保存后供后续创作参考，本次要求优先。删除并保存，即可忘记。</p>
    {loading ? <p role="status">正在读取创作偏好…</p> : loadError || !profile ? <div className={styles.feedback} role="alert"><p>{loadError ?? "无法读取创作偏好"}</p><button onClick={() => void loadProfile()} type="button">重新读取</button></div> : <>
      {feedback ? <p className={styles.feedback} role={feedback.error ? "alert" : "status"}>{feedback.text}</p> : null}
      {conflict ? <section className={styles.conflict} aria-label="偏好更新冲突">
        <p>当前草稿尚未保存。重新读取只用于核对，不会替换本页修改。</p>
        <button disabled={busy} onClick={() => void rereadSaved()} type="button">{rereading ? "正在读取…" : "重新读取已保存偏好"}</button>
        {latest ? <><h2>当前已保存的内容</h2><SavedPreferences profile={latest}/><div className={styles.actions}>
          <button disabled={busy} onClick={() => { replaceDraft(latest); setFeedback(null); }} type="button">采用已保存内容</button>
          <button disabled={busy} onClick={() => { setProfile({ ...profile, revision: latest.revision }); setConflict(false); setLatest(null); setFeedback({ error: false, text: "草稿已保留，请核对后保存修改。" }); }} type="button">核对后保留我的修改</button>
        </div></> : null}
      </section> : null}
      <fieldset className={styles.editor} disabled={busy}>
        {([
          { field: "common_expressions", title: "常用表达", label: "添加常用表达", value: newExpression, onChange: setNewExpression, placeholder: "例如：说白了" },
          { field: "banned_words", title: "避免使用的词汇", label: "添加避免用词", value: newBannedWord, onChange: setNewBannedWord, placeholder: "例如：众所周知" },
        ] as const).map(item => <section className={styles.section} key={item.field}>
          <h2>{item.title}</h2><div className={styles.tags}>{profile[item.field].map(value => <span className={styles.tag} key={value}>{value}<button aria-label={`删除${item.title}：${value}`} onClick={() => removeListValue(item.field, value)} type="button"><X size={13}/></button></span>)}</div>
          <div className={styles.addRow}><input aria-label={item.label} onChange={event => item.onChange(event.target.value)} onKeyDown={event => { if (event.key === "Enter") { event.preventDefault(); addListValue(item.field, item.value); } }} placeholder={item.placeholder} value={item.value}/><button disabled={busy || !item.value.trim()} onClick={() => addListValue(item.field, item.value)} type="button"><Plus size={14}/>添加</button></div>
        </section>)}
        <section className={styles.section}><h2>表达风格</h2>{fixedPreferences.map(item => {
          const value = profile.style_preferences[item.key] ?? "";
          return <label className={styles.field} key={item.key}><span>{item.label}</span><AppSelect aria-label={item.label} disabled={busy} onValueChange={next => setPreference(item.key, next)} value={value}>
            <AppSelectOption value="">未设置</AppSelectOption>{item.options.map(([option, label]) => <AppSelectOption key={option} value={option}>{label}</AppSelectOption>)}
            {value && !item.options.some(option => option[0] === value) ? <AppSelectOption value={value}>{value}</AppSelectOption> : null}
          </AppSelect></label>;
        })}</section>
        <section className={styles.section}><h2>其他创作偏好</h2>
          {custom.map((item, index) => <div className={styles.customRow} key={item.row}>
            <input aria-label={`偏好名称 ${index + 1}`} value={item.name} onChange={event => { setCustom(custom.map(row => row.row === item.row ? { ...row, name: event.target.value } : row)); setFeedback(null); }}/>
            <input aria-label={`偏好内容 ${item.name || index + 1}`} value={item.value} onChange={event => { setCustom(custom.map(row => row.row === item.row ? { ...row, value: event.target.value } : row)); setFeedback(null); }}/>
            <button aria-label={`删除偏好 ${item.name || index + 1}`} onClick={() => { setCustom(custom.filter(row => row.row !== item.row)); setFeedback(null); }} type="button"><X size={14}/></button>
          </div>)}
          {!custom.length ? <p className={styles.empty}>尚无其他偏好</p> : null}
          <div className={styles.customRow}><input aria-label="新增偏好名称" onChange={event => setNewName(event.target.value)} placeholder="名称，如视频时长" value={newName}/><input aria-label="新增偏好内容" onChange={event => setNewValue(event.target.value)} placeholder="内容，如通常两分钟" value={newValue}/><button aria-label="添加创作偏好" disabled={busy || !newName.trim() || !newValue.trim()} onClick={addCustom} type="button"><Plus size={14}/></button></div>
        </section>
      </fieldset>
      <footer className={styles.actions}>
        <button className={styles.primary} disabled={busy || conflict} onClick={() => void handleSave()} type="button">{saving ? "正在保存…" : "保存修改"}</button>
        <button disabled={busy} onClick={() => void handleExport()} title="下载当前已保存的偏好" type="button">{exporting ? "正在导出…" : "导出偏好"}</button>
        {hasUnsavedChanges ? <span className={styles.description}>导出只包含已保存内容。</span> : null}
      </footer>
    </>}
  </div></AppShell>;
}
