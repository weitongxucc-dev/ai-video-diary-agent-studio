export type LlmProviderPreset = {
  id: string;
  label: string;
  baseUrl: string;
  models: string[];
  apiKeyOptional?: boolean;
  helper?: string;
};

/**
 * Provider presets are connection starters, not availability claims.
 *
 * Pi currently sends OpenAI Chat Completions requests, so the catalog only
 * contains providers with a compatible endpoint or a local OpenAI-compatible
 * gateway. Users can still replace the endpoint and model when a region or
 * account uses a different host. Saving a preset never contacts the provider.
 */
export const LLM_PROVIDER_PRESETS: readonly LlmProviderPreset[] = [
  {
    id: "minimax",
    label: "MiniMax",
    baseUrl: "https://api.minimax.cn/v1",
    models: ["MiniMax-M3", "MiniMax-M2.5", "MiniMax-M2.1"],
  },
  {
    id: "deepseek",
    label: "DeepSeek",
    baseUrl: "https://api.deepseek.com",
    models: ["deepseek-chat", "deepseek-reasoner", "deepseek-flash", "deepseek-v4-pro"],
  },
  {
    id: "openai",
    label: "OpenAI",
    baseUrl: "https://api.openai.com/v1",
    models: ["gpt-5.4", "gpt-5-mini", "gpt-4.1", "o3"],
  },
  {
    id: "gemini",
    label: "Google Gemini",
    baseUrl: "https://generativelanguage.googleapis.com/v1beta/openai",
    models: ["gemini-3-flash-preview", "gemini-3.1-pro-preview", "gemini-2.5-flash"],
    helper: "使用 Gemini 的 OpenAI 兼容入口。",
  },
  {
    id: "qwen",
    label: "通义千问 · DashScope",
    baseUrl: "https://dashscope.aliyuncs.com/compatible-mode/v1",
    models: ["qwen3.5-plus", "qwen-plus", "qwen-max", "qwen3-coder-plus"],
    helper: "可按地域把连接地址改为对应的 DashScope 兼容入口。",
  },
  {
    id: "moonshot",
    label: "Moonshot · Kimi",
    baseUrl: "https://api.moonshot.cn/v1",
    models: ["kimi-k2.5", "kimi-k2-turbo-preview", "moonshot-v1-128k"],
  },
  {
    id: "zhipu",
    label: "智谱 · GLM",
    baseUrl: "https://open.bigmodel.cn/api/paas/v4",
    models: ["glm-5", "glm-4.5", "glm-4.5-air"],
  },
  {
    id: "baichuan",
    label: "百川智能",
    baseUrl: "https://api.baichuan-ai.com/v1",
    models: ["Baichuan4-Turbo", "Baichuan4"],
  },
  {
    id: "openrouter",
    label: "OpenRouter",
    baseUrl: "https://openrouter.ai/api/v1",
    models: ["openai/gpt-5.4", "anthropic/claude-sonnet-4.6", "google/gemini-3-flash-preview"],
    helper: "通过一个兼容入口切换多个模型供应商。",
  },
  {
    id: "siliconflow",
    label: "硅基流动",
    baseUrl: "https://api.siliconflow.cn/v1",
    models: ["Qwen/Qwen3-32B", "deepseek-ai/DeepSeek-V3", "Pro/MiniMaxAI/MiniMax-M1-80k"],
  },
  {
    id: "groq",
    label: "Groq",
    baseUrl: "https://api.groq.com/openai/v1",
    models: ["openai/gpt-oss-120b", "llama-4-scout-17b-16e-instruct", "qwen/qwen3-32b"],
  },
  {
    id: "together",
    label: "Together AI",
    baseUrl: "https://api.together.xyz/v1",
    models: ["meta-llama/Llama-3.3-70B-Instruct-Turbo", "Qwen/Qwen3-Next-80B-A3B-Instruct"],
  },
  {
    id: "ollama",
    label: "Ollama（本机）",
    baseUrl: "http://127.0.0.1:11434/v1",
    models: ["qwen3:8b", "gemma3:12b", "llama3.3"],
    apiKeyOptional: true,
    helper: "需要本机 Ollama 已启动并提供对应模型。",
  },
  {
    id: "lmstudio",
    label: "LM Studio（本机）",
    baseUrl: "http://127.0.0.1:1234/v1",
    models: [],
    apiKeyOptional: true,
    helper: "模型名称以 LM Studio 当前加载的模型为准。",
  },
  {
    id: "custom",
    label: "自定义兼容接口",
    baseUrl: "",
    models: [],
    apiKeyOptional: true,
    helper: "连接地址需要提供 OpenAI Chat Completions 兼容接口。",
  },
];

export function llmProviderPreset(provider: string): LlmProviderPreset {
  return LLM_PROVIDER_PRESETS.find((item) => item.id === provider) ?? LLM_PROVIDER_PRESETS.at(-1)!;
}
