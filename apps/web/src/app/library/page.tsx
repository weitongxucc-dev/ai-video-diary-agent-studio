import { MaterialLibraryWorkspace } from "@/components/MaterialLibraryWorkspace";
import { WorkspaceRoute } from "@/components/workbench/WorkspaceRoute";
import {
  getMaterialCollections,
  getMaterialLibraryInsights,
  getProductionMaterials,
} from "@/lib/api.server";
import type {
  MaterialCollections,
  MaterialLibrary,
  MaterialLibraryInsights,
} from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function LibraryPage({ searchParams }: { searchParams?: Promise<{ tag?: string; mode?: string; resource?: string }> }) {
  const params = searchParams ? await searchParams : {};
  // 原样透传查询值：服务端按原名精确相等筛选，客户端 trim 会改掉真实存在的
  // 标签名，让词云点进来查不到。只有空值表示不筛选。
  const tag = params.tag || undefined;
  let library: MaterialLibrary = { assets: [], total_count: 0, limit: 24, offset: 0, has_more: false };
  let insights: MaterialLibraryInsights = { asset_count: 0, segment_count: 0, tags: [], recommended_gaps: [] };
  let collections: MaterialCollections = { collections: [], total_count: 0 };
  [library, insights, collections] = await Promise.all([
    getProductionMaterials({ tag }).catch(() => library),
    getMaterialLibraryInsights().catch(() => insights),
    getMaterialCollections().catch(() => collections),
  ]);
  return <WorkspaceRoute label="素材库"><MaterialLibraryWorkspace initialLibrary={library} initialInsights={insights} initialCollections={collections} initialTag={tag} initialMode={params.mode} initialResource={params.resource} /></WorkspaceRoute>;
}
