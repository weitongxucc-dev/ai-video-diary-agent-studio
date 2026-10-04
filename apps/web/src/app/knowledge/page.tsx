"use client";

import { useCallback, useEffect, useState } from "react";
import {
  type KnowledgeCategoryCounts,
  type KnowledgeItem,
  type KnowledgeUseCategory,
  listKnowledgeItems,
  sourceErrorMessage,
  updateKnowledgeItemClassification,
} from "@/lib/source-api";
import { KnowledgeWorkspace } from "./KnowledgeWorkspace";

export default function KnowledgePage() {
  const [items, setItems] = useState<KnowledgeItem[]>([]);
  const [categoryCounts, setCategoryCounts] = useState<KnowledgeCategoryCounts | null>(null);
  const [topics, setTopics] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // 分类/主题写入成功后自增，让详情重新读回真实字段。
  const [reloadToken, setReloadToken] = useState(0);

  async function refresh() {
    setLoading(true); setError(null);
    try {
      // 分类数量始终取全量统计，不随页面搜索词缩小。
      const response = await listKnowledgeItems();
      setItems(response.items);
      setCategoryCounts(response.category_counts);
      setTopics(response.topics);
    } catch (cause) {
      setItems([]); setCategoryCounts(null); setTopics([]); setError(sourceErrorMessage(cause));
    } finally { setLoading(false); }
  }
  useEffect(() => { void refresh(); }, []);

  // 写失败时把错误交给详情区就近显示：这里不另起页面级错误条。
  const saveClassification = useCallback(async (
    item: KnowledgeItem,
    input: { use_category: KnowledgeUseCategory | null; topics: string[] },
  ) => {
    try {
      await updateKnowledgeItemClassification(item.id, input);
      setReloadToken((value) => value + 1);
      await refresh();
    } catch (cause) {
      throw new Error(sourceErrorMessage(cause));
    }
  }, []);

  return <KnowledgeWorkspace
    categoryCounts={categoryCounts}
    error={error}
    items={items}
    loading={loading}
    onRefresh={() => void refresh()}
    onUpdateClassification={saveClassification}
    reloadToken={reloadToken}
    topics={topics}
  />;
}