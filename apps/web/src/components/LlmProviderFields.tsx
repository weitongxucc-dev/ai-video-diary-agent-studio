"use client";

import { useMemo, useRef, useState } from "react";
import styles from "./LlmProviderFields.module.css";
import { CardSelect, HomeCardDialog } from "./HomeCardDialog";

type ProviderPreset = {
  id: string;
  label: string;
  baseUrl: string;
  models: string[];
  apiKeyOptional?: boolean;
};

const PROVIDERS: ProviderPreset[] = [
  {
    id: "minimax",
    label: "MiniMax",
    baseUrl: "https://api.minimax.cn/v1",
    models: ["MiniMax-M3"],
  },
  {
    id: "deepseek",
    label: "DeepSeek",
    baseUrl: "https://api.deepseek.com",
    models: ["deepseek-chat", "deepseek-reasoner"],
  },
  {
    id: "openai",
    label: "OpenAI",
    baseUrl: "https://api.openai.com/v1",
    models: ["gpt-5.4", "gpt-5-mini"],
  },
  {
    id: "openrouter",
    label: "OpenRouter",
    baseUrl: "https://openrouter.ai/api/v1",
    models: ["openai/gpt-5.4", "anthropic/claude-sonnet-4.6"],
  },
  {
    id: "gemini",
    label: "Google Gemini",
    baseUrl: "https://generativelanguage.googleapis.com/v1beta/openai",
    models: ["gemini-3-flash-preview", "gemini-3.1-pro-preview"],
  },
  {
    id: "ollama",
    label: "Ollama（本机）",
    baseUrl: "http://127.0.0.1:11434/v1",
    models: ["qwen3:8b", "gemma3:12b"],
    apiKeyOptional: true,
  },
  {
    id: "custom",
    label: "自定义兼容接口",
    baseUrl: "",
    models: [],
    apiKeyOptional: true,
  },
];

type LlmProviderFieldsProps = {
  provider: string;
  baseUrl: string;
  model: string;
  apiKeyConfigured: boolean;
};

export function LlmProviderFields({
  provider: initialProvider,
  baseUrl: initialBaseUrl,
  model: initialModel,
  apiKeyConfigured,
}: LlmProviderFieldsProps) {
  const fallbackProvider = PROVIDERS.some((item) => item.id === initialProvider)
    ? initialProvider
    : "custom";
  const [provider, setProvider] = useState(fallbackProvider);
  const [baseUrl, setBaseUrl] = useState(initialBaseUrl);
  const [model, setModel] = useState(initialModel);
  const [keyDeleteArmed, setKeyDeleteArmed] = useState(false);
  const keyDeleteSubmit = useRef<HTMLButtonElement>(null);
  const preset = useMemo(
    () => PROVIDERS.find((item) => item.id === provider) ?? PROVIDERS.at(-1)!,
    [provider],
  );

  function selectProvider(nextProvider: string) {
    const next = PROVIDERS.find((item) => item.id === nextProvider);
    if (!next) return;
    setProvider(next.id);
    setBaseUrl(next.baseUrl);
    setModel(next.models[0] ?? "");
  }

  const keyConfigured = apiKeyConfigured && provider === initialProvider;

  return (
    <div className="llm-settings-stack">
      <label className="settings-field wide">
          <span>服务商</span>
        <CardSelect label="模型服务" name="llm_provider" onChange={selectProvider} value={provider} options={PROVIDERS.map(item => ({ value:item.id, label:item.label }))} />
        <small className="settings-field-helper">
          选择你已开通的服务。
        </small>
      </label>

      <label className="settings-field wide">
        <span>
          连接密钥
          <span
            className={`settings-secret-badge ${keyConfigured ? "green" : "red"}`}
          >
            {keyConfigured ? "已配置" : preset.apiKeyOptional ? "可选" : "未配置"}
          </span>
        </span>
        <div className={styles.keyRow}>
          <input
            aria-label="模型 API Key"
            autoComplete="off"
            maxLength={512}
            name="llm_api_key"
            placeholder={keyConfigured ? "已配置 · 留空不修改" : "粘贴服务商提供的密钥"}
            type="password"
          />
          {keyConfigured ? (
            <span className="settings-field-actions">
              {keyDeleteArmed ? (
                <>
                  <button hidden ref={keyDeleteSubmit} name="llm_key_action" type="submit" value="delete" />
                  <HomeCardDialog title="删除连接密钥" onClose={() => setKeyDeleteArmed(false)} footer={<><button data-card-cancel onClick={() => setKeyDeleteArmed(false)} type="button">取消</button><button onClick={() => keyDeleteSubmit.current?.click()} type="button">确认删除密钥</button></>}>
                    <p>删除此服务商在本机保存的密钥。之后需要重新填写，才能继续需要密钥的文字模型调用。</p>
                  </HomeCardDialog>
                </>
              ) : (
                <button className={styles.removeButton} onClick={() => setKeyDeleteArmed(true)} type="button">
                  移除密钥
                </button>
              )}
            </span>
          ) : null}
        </div>
        <small className="settings-field-helper">
          仅保存在这台电脑，不会显示完整内容。
        </small>
      </label>

      <label className="settings-field wide">
        <span>模型</span>
        <input
          aria-label="模型"
          list="llm-model-presets"
          name="llm_model"
          onChange={(event) => setModel(event.target.value)}
          placeholder="输入模型标识"
          required
          value={model}
        />
        <datalist id="llm-model-presets">
          {preset.models.map((item) => (
            <option key={item} value={item} />
          ))}
        </datalist>
        <small className="settings-field-helper">
          填写服务商提供的型号名称。
        </small>
      </label>

      <details className="settings-advanced">
        <summary>连接详情</summary>
        <label className="settings-field wide">
          <span>连接地址</span>
          <input
            aria-label="模型服务地址"
            name="llm_base_url"
            onChange={(event) => setBaseUrl(event.target.value)}
            placeholder="https://api.example.com/v1"
            required
            type="url"
            value={baseUrl}
          />
          <small className="settings-field-helper">
            系统调用该地址下的 /chat/completions；自定义代理和本地服务也可接入。
          </small>
        </label>
      </details>
    </div>
  );
}
