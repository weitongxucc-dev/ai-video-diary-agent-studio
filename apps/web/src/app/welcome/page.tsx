"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { completeOnboarding } from "@/lib/api/runtime";
import { MaterialDiscoveryPanel } from "@/components/material-library/MaterialDiscoveryPanel";
import { VoiceSamplingSetup } from "@/components/voice-sampling";
import styles from "./welcome.module.css";

type Step = "welcome" | "materials" | "voice" | "person" | "preferences";
const steps: { id: Step; title: string }[] = [
  { id: "welcome", title: "欢迎" }, { id: "materials", title: "素材" },
  { id: "voice", title: "我的声音" }, { id: "person", title: "人物形象" },
  { id: "preferences", title: "偏好" },
];

export default function WelcomePage() {
  const router = useRouter();
  const [step, setStep] = useState<Step>("welcome");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [workspaceId, setWorkspaceId] = useState<string | null>(null);
  const [consent, setConsent] = useState(false);

  async function initialize(skip: boolean) {
    setBusy(true); setError("");
    try {
      const result = await completeOnboarding();
      if (!result.completed) throw new Error("工作区尚未完成初始化，请重试。");
      setWorkspaceId(result.workspace_id);
      if (skip) { router.replace("/"); router.refresh(); return; }
      const saved = localStorage.getItem(`welcome-step:${result.workspace_id}`);
      setStep(steps.some(item => item.id === saved && item.id !== "welcome") ? saved as Step : "materials");
    } catch (e) { setError(e instanceof Error ? e.message : "无法打开工作区，请检查原存储位置后重试。"); }
    finally { setBusy(false); }
  }
  function go(next: Step) {
    setStep(next);
    if (workspaceId) localStorage.setItem(`welcome-step:${workspaceId}`, next);
  }
  function finish() {
    if (workspaceId) localStorage.removeItem(`welcome-step:${workspaceId}`);
    router.replace("/"); router.refresh();
  }

  return <main className={styles.page}>
    <header><span className="section-kicker">曜核</span><h1>{step === "welcome" ? "从你的第一条视频开始" : "按你的需要设置"}</h1><p>素材、声音、人物和偏好都可以稍后补齐。已保存的内容会保留。</p></header>
    <nav aria-label="首次设置步骤" className={styles.steps}>{steps.map(item => <button key={item.id} type="button" aria-current={step === item.id ? "step" : undefined} disabled={!workspaceId || busy} onClick={() => go(item.id)}>{item.title}</button>)}</nav>
    {error ? <div role="alert" className={styles.error}><p>{error}</p><Link href="/onboarding">检查工作区存储</Link></div> : null}
    <section className={styles.stage}>
      {step === "welcome" ? <>
        <h2>先进入工作区，再逐项设置</h2><p>可以浏览、导入和剪辑已有素材。需要对话模型、自己的声音或人物形象时，再连接对应能力。</p>
        <label className={styles.consent}><input type="checkbox" checked={consent} onChange={e => setConsent(e.target.checked)} />我已阅读并同意 <Link href="/privacy" target="_blank">隐私政策</Link>与<Link href="/terms" target="_blank">使用条款</Link></label>
        <div className={styles.actions}><button type="button" className="ui-button" disabled={busy || !consent} onClick={() => void initialize(false)}>{busy ? "正在打开工作区…" : "开始设置"}</button><button type="button" className="ui-button secondary" disabled={busy || !consent} onClick={() => void initialize(true)}>全部跳过，开始使用</button></div>
      </> : null}
      {step === "materials" ? <><MaterialDiscoveryPanel /><div className={styles.actions}><button className="ui-button secondary" type="button" onClick={() => go("voice")}>稍后设置</button><button className="ui-button" type="button" onClick={() => go("voice")}>继续</button></div></> : null}
      {step === "voice" ? <><VoiceSamplingSetup onSkip={() => go("person")} onReceived={() => go("person")} /><button className="ui-button secondary" type="button" onClick={() => go("person")}>继续设置人物</button></> : null}
      {step === "person" ? <><h2>人物形象（可选）</h2><p>在“人物与声音”中添加和预览形象。不设置人物也可以用已有画面完成视频。</p><Link className="ui-button secondary" href="/profiles">打开人物与声音</Link><div className={styles.actions}><button className="ui-button secondary" type="button" onClick={() => go("preferences")}>稍后设置</button><button className="ui-button" type="button" onClick={() => go("preferences")}>继续</button></div></> : null}
      {step === "preferences" ? <><h2>偏好与模型（可选）</h2><p>模型连接、常用表达和创作偏好可以在设置中按需补齐。未配置的能力会在使用时说明原因。</p><Link className="ui-button secondary" href="/settings">打开设置</Link><div className={styles.actions}><button className="ui-button secondary" type="button" onClick={finish}>稍后设置</button><button className="ui-button" type="button" onClick={finish}>进入工作台</button></div></> : null}
    </section>
    {step !== "welcome" ? <footer className={styles.actions}><button className="ui-button secondary" type="button" onClick={() => go(steps[Math.max(0, steps.findIndex(item => item.id === step) - 1)].id)}>上一步</button><button className="ui-button secondary" type="button" onClick={finish}>跳过剩余设置，开始使用</button><span>以后可从素材库、人物与声音或设置继续。</span></footer> : null}
  </main>;
}
