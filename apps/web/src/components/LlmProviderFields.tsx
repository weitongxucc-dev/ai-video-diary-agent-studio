"use client";

import { useMemo, useState } from "react";

type ProviderPreset = {
  id: string;
  label: string;
  baseUrl: string;
  models: string[];
  apiKeyOptional?: boolean;
};

const PROVIDERS: ProviderPreset[] = [
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

  return (
    <div className="llm-settings-stack">
      <label className="settings-field wide">
          <span>模型服务</span>
        <select
          aria-label="模型服务"
          name="llm_provider"
          onChange={(event) => selectProvider(event.target.value)}
          value={provider}
        >
          {PROVIDERS.map((item) => (
            <option key={item.id} value={item.id}>
              {item.label}
            </option>
          ))}
        </select>
        <small className="settings-field-helper">
          模型服务决定默认连接地址；模型仍可自由填写，不会锁定在预设列表中。
        </small>
      </label>

      <label className="settings-field wide">
        <span>
          API Key
          <span
            className={`settings-secret-badge ${apiKeyConfigured ? "green" : "red"}`}
          >
            {apiKeyConfigured
              ? "已配置"
              : preset.apiKeyOptional
                ? "可选"
                : "未配置"}
          </span>
        </span>
        <input
          aria-label="模型 API Key"
          autoComplete="off"
          maxLength={512}
          name="llm_api_key"
          placeholder={
            apiKeyConfigured
              ? "已配置 · 留空不修改"
              : "粘贴模型服务提供的 API Key"
          }
          type="password"
        />
        <small className="settings-field-helper">
          密钥只保存在这台电脑，不会在设置接口中明文返回。
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
          可选择推荐项，也可直接输入账号中可用的模型 ID。
        </small>
      </label>

      <details className="settings-advanced">
        <summary>高级连接设置</summary>
        <label className="settings-field wide">
          <span>OpenAI-compatible Base URL</span>
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
