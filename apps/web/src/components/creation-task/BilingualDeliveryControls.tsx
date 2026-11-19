"use client";

import { useEffect, useState } from "react";
import { listVoiceProfiles, mediaProfilesErrorMessage, type VoiceProfile } from "@/lib/media-profiles-api";
import type { CreationPlanInput } from "@/lib/types";
import { AppSelect, AppSelectOption } from "../AppSelect";
import { DeliveryVoiceSample } from "./DeliveryVoiceSample";
import ui from "../CreationRequestPanel.module.css";

export type VoiceChoice = NonNullable<CreationPlanInput["selected_voice"]>;
type Language = "zh" | "en";
type Voices = Partial<Record<Language, VoiceChoice>>;
const labels = { zh: "中文版声音", en: "英文版声音" };

export function BilingualDeliveryControls({ pending, onPrepare }: {
  pending: boolean;
  onPrepare: (voices: Voices) => void;
}) {
  const [profiles, setProfiles] = useState<VoiceProfile[]>([]);
  const [selection, setSelection] = useState<Voices>({});
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [read, setRead] = useState(0);
  useEffect(() => {
    if (!read) return;
    let current = true;
    setError("");
    setLoading(true);
    void listVoiceProfiles().then(result => {
      if (current) setProfiles(result.items.filter(voice => voice.runnable));
    }).catch(cause => {
      if (current) setError(mediaProfilesErrorMessage(cause));
    }).finally(() => { if (current) setLoading(false); });
    return () => { current = false; };
  }, [read]);

  const staleSelection = (["zh", "en"] as const).filter(language => {
    const selected = selection[language];
    return selected && !profiles.some(voice => voice.id === selected.profile_id && voice.version === selected.version &&
      (voice.language === language || voice.language === "zh-en"));
  });
  const hasSelection = Boolean(selection.zh || selection.en);
  return <>
    <details onToggle={event => { if (event.currentTarget.open) setRead(current => current || 1); }}>
      <summary>分别指定中英文声音</summary>
      {loading ? <p role="status">正在读取声音…</p> : null}
      {error ? <p role="alert">{error}</p> : null}
      {(["zh", "en"] as const).map(language => {
          const options = profiles.filter(voice => voice.language === language || voice.language === "zh-en");
          const selected = selection[language];
          const selectedKey = selected ? JSON.stringify([selected.profile_id, selected.version]) : "";
          return <div className={ui.section} key={language}><label>{labels[language]}
            <AppSelect aria-label={labels[language]} value={selectedKey} disabled={pending || loading} onValueChange={key => {
              const voice = options.find(item => JSON.stringify([item.id, item.version]) === key);
              setSelection(current => ({ ...current, [language]: voice ? { profile_id: voice.id, version: voice.version } : undefined }));
            }}>
              <AppSelectOption value="">沿用作品设置</AppSelectOption>
              {staleSelection.includes(language) ? <AppSelectOption value={selectedKey} disabled>所选声音已不可用</AppSelectOption> : null}
              {options.map(voice => {
                const key = JSON.stringify([voice.id, voice.version]);
                return <AppSelectOption key={key} value={key}>{voice.display_name} · {voice.version}</AppSelectOption>;
              })}
            </AppSelect>
          </label>
            {selected && options.some(voice => voice.id === selected.profile_id && voice.version === selected.version) ?
              <DeliveryVoiceSample key={selectedKey} language={language} profileId={selected.profile_id} version={selected.version} /> : null}
          </div>;
        })}
      <button type="button" className={ui.button} disabled={loading || pending} onClick={() => setRead(value => value + 1)}>刷新声音列表</button>
    </details>
    {staleSelection.length ? <p role="status">{staleSelection.map(language => labels[language]).join("、")}已不可用，请重新选择声音。</p> : null}
    <button type="button" className={ui.button} disabled={pending || !!staleSelection.length || (hasSelection && (loading || !!error))} onClick={() => onPrepare(selection)}>准备中文版和英文版</button>
  </>;
}
