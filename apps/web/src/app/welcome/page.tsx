"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  type ChangeEvent,
  type FormEvent,
  useCallback,
  useEffect,
  useMemo,
  useState,
} from "react";
import { PixelIcon } from "@/components/PixelIcon";
import {
  getRuntimeHealth,
  getRuntimeSettings,
  getSetupStatus,
  updateRuntimeSettings,
} from "@/lib/api";
import type {
  RuntimeHealth,
  RuntimeHealthItem,
  RuntimeSettings,
  RuntimeSettingsUpdate,
  SetupCheckItem,
  SetupCheckStatus,
  SetupStatus,
} from "@/lib/types";

type StepId = "welcome" | "model" | "paths" | "verify";

type StepDef = {
  id: StepId;
  index: number;
  title: string;
  caption: string;
};

const STEP_ORDER: StepDef[] = [
  {
    id: "welcome",
    index: 1,
    title: "欢迎",
    caption: "了解产品定位与流程",
  },
  {
    id: "model",
    index: 2,
    title: "AI 模型",
    caption: "配置 DeepSeek",
  },
  {
    id: "paths",
    index: 3,
    title: "目录设置",
    caption: "Obsidian + 视频路径",
  },
  {
    id: "verify",
    index: 4,
    title: "验证并完成",
    caption: "运行 /api/is_setup",
  },
];

const STATUS_LABEL: Record<SetupCheckStatus, string> = {
  ready: "就绪",
  missing: "未配置",
  degraded: "降级",
  invalid: "异常",
};

type VerifyState =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "ok"; status: SetupStatus }
  | { kind: "fallback"; health: RuntimeHealth; error: string }
  | { kind: "error"; message: string };

type PathFormState = {
  obsidianVaultPath: string;
  videoInboxPath: string;
  videoOutputPath: string;
  draftOutputPath: string;
  jianyingDraftRootPath: string;
};

type InitialPathState = {
  loaded: boolean;
  paths: PathFormState;
};

function readSelectedDirectoryName(event: ChangeEvent<HTMLInputElement>): string {
  const files = event.target.files;
  if (!files || files.length === 0) {
    return "";
  }
  const first = files[0] as File & { webkitRelativePath?: string };
  const relative = first.webkitRelativePath ?? "";
  if (!relative) {
    return "";
  }
  return relative.split("/")[0] ?? "";
}

function statusTone(status: SetupCheckStatus): string {
  if (status === "ready") return "green";
  if (status === "missing") return "red";
  if (status === "degraded") return "yellow";
  return "red";
}

export default function WelcomePage() {
  const router = useRouter();
  const [step, setStep] = useState<StepId>("welcome");
  const [apiKey, setApiKey] = useState("");
  const [apiKeyVisible, setApiKeyVisible] = useState(false);
  const [verifyingKey, setVerifyingKey] = useState(false);
  const [keyFeedback, setKeyFeedback] = useState<{
    tone: "green" | "red" | "yellow";
    text: string;
  } | null>(null);
  const [savingPaths, setSavingPaths] = useState(false);
  const [pathsFeedback, setPathsFeedback] = useState<{
    tone: "green" | "red";
    text: string;
  } | null>(null);
  const [verify, setVerify] = useState<VerifyState>({ kind: "idle" });
  const [consent, setConsent] = useState(false);
  const [initialPaths, setInitialPaths] = useState<InitialPathState>({
    loaded: false,
    paths: {
      obsidianVaultPath: "",
      videoInboxPath: "",
      videoOutputPath: "",
      draftOutputPath: "",
      jianyingDraftRootPath: "",
    },
  });
  const [paths, setPaths] = useState<PathFormState>(initialPaths.paths);

  useEffect(() => {
    let cancelled = false;
    async function bootstrap() {
      try {
        const settings = await getRuntimeSettings();
        if (cancelled) {
          return;
        }
        const seeded = readPathsFromSettings(settings);
        setInitialPaths({ loaded: true, paths: seeded });
        setPaths(seeded);
      } catch {
        if (!cancelled) {
          setInitialPaths({ loaded: true, paths: initialPaths.paths });
        }
      }
    }
    void bootstrap();
    return () => {
      cancelled = true;
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const currentStepIndex = useMemo(
    () => STEP_ORDER.findIndex((entry) => entry.id === step),
    [step],
  );
  const stepDefs = STEP_ORDER;

  const gotoStep = useCallback((next: StepId) => {
    setStep(next);
  }, []);

  const onVerifyKey = useCallback(async () => {
    const trimmed = apiKey.trim();
    if (!trimmed) {
      setKeyFeedback({ tone: "red", text: "请先填入 DeepSeek API Key。" });
      return;
    }
    setVerifyingKey(true);
    setKeyFeedback(null);
    try {
      await updateRuntimeSettings({ deepseek_api_key: trimmed });
      const health = await getRuntimeHealth();
      const llmItem = health.items.find((item) =>
        item.id.toLowerCase().includes("llm") ||
        item.id.toLowerCase().includes("deepseek") ||
        item.label.includes("DeepSeek"),
      );
      if (llmItem && llmItem.status === "ready") {
        setKeyFeedback({ tone: "green", text: "DeepSeek 配置通过验证。" });
      } else if (llmItem) {
        setKeyFeedback({
          tone: "yellow",
          text: `已保存，但状态：${STATUS_LABEL[llmItem.status] ?? llmItem.status}（${llmItem.reason}）`,
        });
      } else {
        setKeyFeedback({
          tone: "green",
          text: "DeepSeek Key 已保存，可在下一步继续。",
        });
      }
    } catch (error) {
      setKeyFeedback({
        tone: "red",
        text: `验证失败：${(error as Error).message}`,
      });
    } finally {
      setVerifyingKey(false);
    }
  }, [apiKey]);

  const onSubmitPaths = useCallback(
    async (event: FormEvent<HTMLFormElement>) => {
      event.preventDefault();
      setSavingPaths(true);
      setPathsFeedback(null);
      const payload: RuntimeSettingsUpdate = {
        obsidian_vault_path: paths.obsidianVaultPath.trim(),
        video_inbox_path: paths.videoInboxPath.trim(),
        video_output_path: paths.videoOutputPath.trim(),
        draft_output_path: paths.draftOutputPath.trim(),
        jianying_draft_root_path: paths.jianyingDraftRootPath.trim(),
      };
      try {
        await updateRuntimeSettings(payload);
        setPathsFeedback({ tone: "green", text: "目录已保存。" });
        gotoStep("verify");
      } catch (error) {
        setPathsFeedback({
          tone: "red",
          text: `保存失败：${(error as Error).message}`,
        });
      } finally {
        setSavingPaths(false);
      }
    },
    [gotoStep, paths],
  );

  const runVerification = useCallback(async () => {
    setVerify({ kind: "loading" });
    try {
      const status = await getSetupStatus();
      setVerify({ kind: "ok", status });
    } catch (error) {
      try {
        const health = await getRuntimeHealth();
        setVerify({
          kind: "fallback",
          health,
          error: (error as Error).message,
        });
      } catch (fallbackError) {
        setVerify({
          kind: "error",
          message: (fallbackError as Error).message,
        });
      }
    }
  }, []);

  useEffect(() => {
    if (step === "verify" && verify.kind === "idle") {
      void runVerification();
    }
  }, [step, verify.kind, runVerification]);

  const onFinish = useCallback(() => {
    router.push("/");
  }, [router]);

  const onSkipFinish = useCallback(() => {
    router.push("/");
  }, [router]);

  const onConsentChange = useCallback(
    (event: React.ChangeEvent<HTMLInputElement>) => {
      setConsent(event.target.checked);
    },
    [],
  );

  return (
    <div className="retro-desktop-shell">
      <div className="retro-window-frame">
        <main className="welcome-page">
          <header className="welcome-header pixel-card">
            <div className="welcome-header-main">
              <span className="section-kicker">首次运行</span>
              <h1>AI 视频日记 · Agent Studio</h1>
              <p>
                本向导帮你完成首次配置：AI 模型密钥、Obsidian Vault 与视频目录。
                配置保存后可随时在「设置」页修改。
              </p>
            </div>
            <div className="welcome-header-side">
              <PixelIcon name="agent" />
              <span>共 {stepDefs.length} 步</span>
            </div>
          </header>

          <ol className="welcome-stepper" aria-label="引导步骤">
            {stepDefs.map((entry) => {
              const reached = currentStepIndex >= entry.index - 1;
              const active = entry.id === step;
              return (
                <li
                  aria-current={active ? "step" : undefined}
                  className={`welcome-stepper-item ${active ? "active" : ""} ${reached ? "reached" : ""}`}
                  key={entry.id}
                >
                  <span className="welcome-stepper-index">
                    {String(entry.index).padStart(2, "0")}
                  </span>
                  <div>
                    <strong>{entry.title}</strong>
                    <span>{entry.caption}</span>
                  </div>
                </li>
              );
            })}
          </ol>

          <section className="welcome-stage pixel-card">
            {step === "welcome" ? (
              <StepWelcome onStart={() => gotoStep("model")} />
            ) : null}
            {step === "model" ? (
              <StepModel
                apiKey={apiKey}
                apiKeyVisible={apiKeyVisible}
                feedback={keyFeedback}
                onChangeApiKey={setApiKey}
                onToggleVisibility={() => setApiKeyVisible((value) => !value)}
                onVerify={onVerifyKey}
                verifying={verifyingKey}
              />
            ) : null}
            {step === "paths" ? (
              <StepPaths
                feedback={pathsFeedback}
                loaded={initialPaths.loaded}
                onChange={setPaths}
                onSubmit={onSubmitPaths}
                paths={paths}
                saving={savingPaths}
              />
            ) : null}
            {step === "verify" ? (
              <StepVerify
                consent={consent}
                onConsentChange={onConsentChange}
                onFinish={onFinish}
                onSkip={onSkipFinish}
                onRetry={runVerification}
                state={verify}
              />
            ) : null}
          </section>

          <nav aria-label="引导导航" className="welcome-nav">
            <button
              className="pixel-button secondary"
              disabled={currentStepIndex <= 0}
              onClick={() =>
                gotoStep(stepDefs[Math.max(0, currentStepIndex - 1)].id)
              }
              type="button"
            >
              ← 上一步
            </button>
            <Link className="pixel-button secondary" href="/">
              跳过引导
            </Link>
            <button
              className="pixel-button"
              disabled={currentStepIndex >= stepDefs.length - 1}
              onClick={() =>
                gotoStep(stepDefs[Math.min(stepDefs.length - 1, currentStepIndex + 1)].id)
              }
              type="button"
            >
              下一步 →
            </button>
          </nav>
        </main>
      </div>
    </div>
  );
}

function StepWelcome({ onStart }: { onStart: () => void }) {
  return (
    <div className="welcome-step welcome-step-welcome">
      <div>
        <span className="section-kicker">Step 1</span>
        <h2>欢迎来到 AI 视频日记</h2>
        <p>
          Agent Studio 帮助创作者把 Obsidian / Hermes / 外链素材整理成口播稿、剪辑计划与剪映草稿。
          首次运行需要完成三件事：配置大模型、绑定 Obsidian Vault 与视频目录、确认运行环境。
        </p>
      </div>
      <ul className="welcome-feature-list">
        <li>
          <PixelIcon name="check" />
          <div>
            <strong>真实素材 · 真实产出</strong>
            <span>不训练模型，所有赞踩与偏好都会写回 prompt 上下文。</span>
          </div>
        </li>
        <li>
          <PixelIcon name="check" />
          <div>
            <strong>剪映兼容草稿</strong>
            <span>真实可编辑的 .draft 草稿包，保留剪映轨道、字幕、封面。</span>
          </div>
        </li>
        <li>
          <PixelIcon name="check" />
          <div>
            <strong>本地优先</strong>
            <span>视频与草稿全部走本地路径，配置错了立刻能看到失败原因。</span>
          </div>
        </li>
      </ul>
      <div className="welcome-step-actions">
        <button className="pixel-button" onClick={onStart} type="button">
          开始配置 →
        </button>
      </div>
    </div>
  );
}

function StepModel({
  apiKey,
  apiKeyVisible,
  feedback,
  onChangeApiKey,
  onToggleVisibility,
  onVerify,
  verifying,
}: {
  apiKey: string;
  apiKeyVisible: boolean;
  feedback: { tone: "green" | "red" | "yellow"; text: string } | null;
  onChangeApiKey: (value: string) => void;
  onToggleVisibility: () => void;
  onVerify: () => void;
  verifying: boolean;
}) {
  return (
    <div className="welcome-step welcome-step-model">
      <div>
        <span className="section-kicker">Step 2</span>
        <h2>AI 模型 · DeepSeek</h2>
        <p>
          填入你的 DeepSeek API Key。点击「验证并保存」会写入配置并立刻请求{" "}
          <code>/api/runtime/health</code> 检查 llm 项是否就绪。
        </p>
      </div>

      <div className="welcome-field">
        <label htmlFor="deepseek-key">
          <span>DeepSeek API Key</span>
        </label>
        <div className="welcome-field-row">
          <input
            autoComplete="off"
            id="deepseek-key"
            name="deepseek_api_key"
            onChange={(event) => onChangeApiKey(event.target.value)}
            placeholder="sk-..."
            type={apiKeyVisible ? "text" : "password"}
            value={apiKey}
          />
          <button
            className="pixel-button secondary"
            onClick={onToggleVisibility}
            type="button"
          >
            {apiKeyVisible ? "隐藏" : "显示"}
          </button>
          <button
            className="pixel-button"
            disabled={verifying}
            onClick={onVerify}
            type="button"
          >
            {verifying ? "验证中…" : "验证并保存"}
          </button>
        </div>
        {feedback ? (
          <p className={`welcome-feedback ${feedback.tone}`}>{feedback.text}</p>
        ) : (
          <p className="welcome-feedback muted">
            Key 仅保存在本地 runtime config，不会被发送至第三方。
          </p>
        )}
      </div>
    </div>
  );
}

function StepPaths({
  paths,
  onChange,
  onSubmit,
  saving,
  loaded,
  feedback,
}: {
  paths: PathFormState;
  onChange: (paths: PathFormState) => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
  saving: boolean;
  loaded: boolean;
  feedback: { tone: "green" | "red"; text: string } | null;
}) {
  return (
    <form className="welcome-step welcome-step-paths" onSubmit={onSubmit}>
      <div>
        <span className="section-kicker">Step 3</span>
        <h2>本地目录</h2>
        <p>
          配置 Obsidian Vault、视频待处理 / 输出目录与剪映草稿根路径。
          文本框填绝对路径，下方的目录选择器会记住文件夹名称（浏览器限制无法直接读绝对路径）。
        </p>
      </div>

      <div className="welcome-field-grid">
        <PathField
          label="Obsidian Vault 路径"
          name="obsidian_vault_path"
          onChange={(value) => onChange({ ...paths, obsidianVaultPath: value })}
          onPick={(name) => onChange({ ...paths, obsidianVaultPath: name })}
          placeholder="/Users/you/Documents/ObsidianVault"
          value={paths.obsidianVaultPath}
        />
        <PathField
          label="视频待处理目录"
          name="video_inbox_path"
          onChange={(value) => onChange({ ...paths, videoInboxPath: value })}
          onPick={(name) => onChange({ ...paths, videoInboxPath: name })}
          placeholder="/Users/you/Movies/AI-Video-Inbox"
          value={paths.videoInboxPath}
        />
        <PathField
          label="视频输出目录"
          name="video_output_path"
          onChange={(value) => onChange({ ...paths, videoOutputPath: value })}
          onPick={(name) => onChange({ ...paths, videoOutputPath: name })}
          placeholder="/Users/you/Movies/AI-Video-Output"
          value={paths.videoOutputPath}
        />
        <PathField
          label="剪映草稿目录"
          name="draft_output_path"
          onChange={(value) => onChange({ ...paths, draftOutputPath: value })}
          onPick={(name) => onChange({ ...paths, draftOutputPath: name })}
          placeholder="/Users/you/Movies/JianyingDrafts"
          value={paths.draftOutputPath}
        />
        <PathField
          label="剪映根目录（10.7 检测）"
          name="jianying_draft_root_path"
          onChange={(value) =>
            onChange({ ...paths, jianyingDraftRootPath: value })
          }
          onPick={(name) =>
            onChange({ ...paths, jianyingDraftRootPath: name })
          }
          placeholder="/Users/you/Library/.../JianyingPro"
          value={paths.jianyingDraftRootPath}
        />
      </div>

      {!loaded ? (
        <p className="welcome-feedback muted">正在读取已保存的目录…</p>
      ) : null}
      {feedback ? (
        <p className={`welcome-feedback ${feedback.tone}`}>{feedback.text}</p>
      ) : null}

      <div className="welcome-step-actions">
        <button className="pixel-button" disabled={saving} type="submit">
          {saving ? "保存中…" : "保存目录并继续"}
        </button>
      </div>
    </form>
  );
}

function PathField({
  label,
  name,
  value,
  onChange,
  onPick,
  placeholder,
}: {
  label: string;
  name: string;
  value: string;
  onChange: (value: string) => void;
  onPick: (folderName: string) => void;
  placeholder: string;
}) {
  return (
    <label className="welcome-field">
      <span>{label}</span>
      <div className="welcome-field-row">
        <input
          name={name}
          onChange={(event) => onChange(event.target.value)}
          placeholder={placeholder}
          type="text"
          value={value}
        />
        <label className="pixel-button secondary welcome-pick-button">
          选择目录
          <input
            multiple
            name={`${name}__picker`}
            onChange={(event) => {
              const folderName = readSelectedDirectoryName(event);
              if (folderName) {
                onPick(folderName);
              }
            }}
            type="file"
            // @ts-expect-error: webkitdirectory is a non-standard attribute for picking folders.
            webkitdirectory=""
            directory=""
          />
        </label>
      </div>
    </label>
  );
}

function StepVerify({
  state,
  onRetry,
  onFinish,
  onSkip,
  consent,
  onConsentChange,
}: {
  state: VerifyState;
  onRetry: () => void;
  onFinish: () => void;
  onSkip: () => void;
  consent: boolean;
  onConsentChange: (event: React.ChangeEvent<HTMLInputElement>) => void;
}) {
  return (
    <div className="welcome-step welcome-step-verify">
      <div>
        <span className="section-kicker">Step 4</span>
        <h2>验证并完成</h2>
        <p>调用 <code>GET /api/is_setup</code> 检查所有项目是否就绪。</p>
      </div>

      {state.kind === "loading" || state.kind === "idle" ? (
        <div className="welcome-verify-status">
          <span>正在请求 /api/is_setup…</span>
        </div>
      ) : null}

      {state.kind === "ok" ? (
        <VerifyChecklist items={state.status.items} summary={state.status} />
      ) : null}

      {state.kind === "fallback" ? (
        <VerifyFallback health={state.health} notice={state.error} />
      ) : null}

      {state.kind === "error" ? (
        <div className="welcome-verify-status error">
          <strong>无法验证</strong>
          <span>{state.message}</span>
          <button className="pixel-button secondary" onClick={onRetry} type="button">
            重试
          </button>
        </div>
      ) : null}

      <label className="welcome-consent">
        <input
          aria-label="同意隐私政策与使用条款"
          checked={consent}
          onChange={onConsentChange}
          required
          type="checkbox"
        />
        <span>
          我已阅读并同意{" "}
          <Link href="/privacy" target="_blank">
            隐私政策
          </Link>{" "}
          与{" "}
          <Link href="/terms" target="_blank">
            使用条款
          </Link>
        </span>
      </label>

      <div className="welcome-step-actions">
        <button className="pixel-button secondary" onClick={onRetry} type="button">
          重新检查
        </button>
        <button className="pixel-button secondary" onClick={onSkip} type="button">
          先跳过
        </button>
        <button
          className="pixel-button"
          disabled={!consent}
          onClick={onFinish}
          title={consent ? "进入主页" : "请先勾选同意隐私政策与使用条款"}
          type="button"
        >
          进入主页
        </button>
      </div>
    </div>
  );
}

function VerifyChecklist({
  items,
  summary,
}: {
  items: SetupCheckItem[];
  summary: SetupStatus;
}) {
  return (
    <div className="welcome-verify-result">
      <div className="welcome-verify-banner">
        <strong>
          {summary.complete
            ? `全部 ${summary.total_count} 项通过`
            : `${summary.ready_count}/${summary.total_count} 通过`}
        </strong>
        <span>
          {summary.complete
            ? "可以进入主页了。"
            : "存在未通过项，可先调整后再次验证。"}
        </span>
      </div>
      <ul className="welcome-verify-list">
        {items.map((item) => (
          <li
            className={`welcome-verify-row ${statusTone(item.status)}`}
            key={item.id}
          >
            <span className={`welcome-verify-light ${statusTone(item.status)}`} />
            <div>
              <strong>{item.label}</strong>
              <span>{item.detail || STATUS_LABEL[item.status]}</span>
              {item.repair_action ? (
                <code>修复动作：{item.repair_action}</code>
              ) : null}
            </div>
            <span className={`step-state ${statusTone(item.status)}`}>
              {STATUS_LABEL[item.status]}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function VerifyFallback({
  health,
  notice,
}: {
  health: RuntimeHealth;
  notice: string;
}) {
  const items: SetupCheckItem[] = health.items.map((item: RuntimeHealthItem) => ({
    id: item.id,
    label: item.label,
    status: item.status,
    detail: item.reason,
    impact: item.impact,
    repair_action: item.repair_action,
  }));
  const summary: SetupStatus = {
    complete: health.summary.invalid === 0 && health.summary.missing === 0,
    ready_count: health.summary.ready,
    total_count: health.summary.total,
    items,
  };
  return (
    <div className="welcome-verify-result">
      <div className="welcome-verify-banner warning">
        <strong>/api/is_setup 暂未返回</strong>
        <span>回退使用 /api/runtime/health（{notice}）。</span>
      </div>
      <VerifyChecklist items={summary.items} summary={summary} />
    </div>
  );
}

function readPathsFromSettings(settings: RuntimeSettings): PathFormState {
  const entries = Object.fromEntries(
    settings.settings.map((entry) => [entry.name, entry.value ?? ""]),
  );
  return {
    obsidianVaultPath: entries.obsidian_vault_path ?? "",
    videoInboxPath: entries.video_inbox_path ?? "",
    videoOutputPath: entries.video_output_path ?? "",
    draftOutputPath: entries.draft_output_path ?? "",
    jianyingDraftRootPath: entries.jianying_draft_root_path ?? "",
  };
}