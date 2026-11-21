import { describe, expect, it } from "vitest";
import { LLM_PROVIDER_PRESETS, llmProviderPreset } from "../llm-providers";

describe("LLM provider presets", () => {
  it("covers the 22 backend catalog entries with matching protocol metadata", () => {
    expect(LLM_PROVIDER_PRESETS).toHaveLength(22);
    expect(new Set(LLM_PROVIDER_PRESETS.map((item) => item.id))).toEqual(new Set([
      "minimax", "deepseek", "openai", "openrouter", "gemini", "anthropic", "moonshot", "qwen",
      "zhipu", "baichuan", "hunyuan", "siliconflow", "mistral", "groq", "together", "fireworks",
      "xai", "cohere", "perplexity", "ollama", "lmstudio", "custom",
    ]));
    expect(llmProviderPreset("anthropic").protocol).toBe("anthropic_messages");
    expect(llmProviderPreset("ollama").apiKeyOptional).toBe(true);
    expect(llmProviderPreset("unknown").id).toBe("custom");
  });
});
