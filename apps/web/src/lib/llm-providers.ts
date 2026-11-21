export type LlmProviderPreset = {
  id: string;
  label: string;
  baseUrl: string;
  models: string[];
  protocol: "openai_compatible" | "anthropic_messages";
  apiKeyOptional?: boolean;
  usage: "provider_or_unknown";
  cost: "sdk_estimate_or_unknown" | "unknown";
};

type PresetRow = [string, string, string, string[], LlmProviderPreset["protocol"]?, boolean?, LlmProviderPreset["usage"]?, LlmProviderPreset["cost"]?];

/** Presets describe saved connection shapes, not provider availability. */
const PRESET_ROWS: PresetRow[] = [
  ["minimax", "MiniMax", "https://api.minimax.cn/v1", ["MiniMax-M3"]],
  ["deepseek", "DeepSeek", "https://api.deepseek.com", ["deepseek-flash"]],
  ["openai", "OpenAI", "https://api.openai.com/v1", ["gpt-5.4"]],
  ["openrouter", "OpenRouter", "https://openrouter.ai/api/v1", ["openai/gpt-5.4"]],
  ["gemini", "Google Gemini", "https://generativelanguage.googleapis.com/v1beta/openai", ["gemini-3-flash-preview"]],
  ["anthropic", "Anthropic（原生协议待接入）", "https://api.anthropic.com/v1", ["claude-sonnet-4-5"], "anthropic_messages"],
  ["moonshot", "Moonshot · Kimi", "https://api.moonshot.cn/v1", ["kimi-k2-0905-preview"]],
  ["qwen", "通义千问 · DashScope", "https://dashscope.aliyuncs.com/compatible-mode/v1", ["qwen-plus"]],
  ["zhipu", "智谱 · GLM", "https://open.bigmodel.cn/api/paas/v4", ["glm-4.5"]],
  ["baichuan", "百川智能", "https://api.baichuan-ai.com/v1", ["Baichuan4-Turbo"]],
  ["hunyuan", "腾讯混元", "https://api.hunyuan.cloud.tencent.com/v1", ["hunyuan-turbos"]],
  ["siliconflow", "硅基流动", "https://api.siliconflow.cn/v1", ["deepseek-ai/DeepSeek-V3.2"]],
  ["mistral", "Mistral", "https://api.mistral.ai/v1", ["mistral-large-latest"]],
  ["groq", "Groq", "https://api.groq.com/openai/v1", ["llama-3.3-70b-versatile"]],
  ["together", "Together AI", "https://api.together.xyz/v1", ["meta-llama/Llama-3.3-70B-Instruct-Turbo"]],
  ["fireworks", "Fireworks AI", "https://api.fireworks.ai/inference/v1", ["accounts/fireworks/models/llama-v3p1-70b-instruct"]],
  ["xai", "xAI", "https://api.x.ai/v1", ["grok-4"]],
  ["cohere", "Cohere", "https://api.cohere.com/compatibility/v1", ["command-a-03-2025"]],
  ["perplexity", "Perplexity", "https://api.perplexity.ai", ["sonar-pro"]],
  ["ollama", "Ollama（本机）", "http://127.0.0.1:11434/v1", ["qwen3:8b"], "openai_compatible", true, "provider_or_unknown", "unknown"],
  ["lmstudio", "LM Studio（本机）", "http://127.0.0.1:1234/v1", [], "openai_compatible", true, "provider_or_unknown", "unknown"],
  ["custom", "自定义兼容接口", "", [], "openai_compatible", true],
];

export const LLM_PROVIDER_PRESETS: readonly LlmProviderPreset[] = PRESET_ROWS.map(([id, label, baseUrl, models, protocol = "openai_compatible", apiKeyOptional = false, usage = "provider_or_unknown", cost = "sdk_estimate_or_unknown"]) => ({
  id, label, baseUrl, models, protocol, apiKeyOptional, usage, cost,
}));

export function llmProviderPreset(provider: string): LlmProviderPreset {
  return LLM_PROVIDER_PRESETS.find((item) => item.id === provider) ?? LLM_PROVIDER_PRESETS.at(-1)!;
}
