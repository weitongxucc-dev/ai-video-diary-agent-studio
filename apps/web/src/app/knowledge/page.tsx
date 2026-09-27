"use client";

import { useEffect, useState } from "react";
import { type KnowledgeItem, listKnowledgeItems, sourceErrorMessage } from "@/lib/source-api";
import { KnowledgeWorkspace } from "./KnowledgeWorkspace";

export default function KnowledgePage() {
  const [items, setItems] = useState<KnowledgeItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  async function refresh() {
    setLoading(true); setError(null);
    try {
      const response = await listKnowledgeItems();
      setItems(response.items);
    } catch (cause) {
      setItems([]); setError(sourceErrorMessage(cause));
    } finally { setLoading(false); }
  }
  useEffect(() => { void refresh(); }, []);
  return <KnowledgeWorkspace error={error} items={items} loading={loading} onRefresh={() => void refresh()} />;
}
