"use client";

import { useEffect, useRef, useState } from "react";
import { type KnowledgeItem, listKnowledgeItems, sourceErrorMessage } from "@/lib/source-api";
import { WorkspaceRoute } from "@/components/workbench/WorkspaceRoute";
import { KnowledgeWorkspace } from "./KnowledgeWorkspace";

export default function KnowledgePage() {
  return <WorkspaceRoute label="知识库"><KnowledgePageContent/></WorkspaceRoute>;
}

function KnowledgePageContent() {
  const [items, setItems] = useState<KnowledgeItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const latestRequest = useRef(0);
  async function refresh() {
    const request = ++latestRequest.current;
    setLoading(true); setError(null);
    try {
      const response = await listKnowledgeItems();
      if (request !== latestRequest.current) return;
      setItems(response.items);
    } catch (cause) {
      if (request !== latestRequest.current) return;
      setError(sourceErrorMessage(cause));
    } finally { if (request === latestRequest.current) setLoading(false); }
  }
  useEffect(() => {
    void refresh();
    return () => { latestRequest.current += 1; };
  }, []);
  return <KnowledgeWorkspace error={error} items={items} loading={loading} onRefresh={() => void refresh()} />;
}
