import { saveSourceItemToKnowledge } from "./source-api";

export async function saveVerifiedSourceToKnowledge(sourceId: string) {
  const saved = await saveSourceItemToKnowledge(sourceId, {});
  if (!saved.id || !saved.version || saved.saved_source_item_id !== sourceId ||
      !["available", "reference_only", "excluded", "not_for_automation", "not_for_publish"].includes(saved.use_state)) {
    throw new Error("保存回执与当前来源不一致；原信息条目仍保留，可重试。");
  }
  return saved;
}
