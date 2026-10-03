import { MaterialLibraryWorkspace } from "@/components/MaterialLibraryWorkspace";
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

export default async function LibraryPage({ searchParams }: { searchParams?: Promise<{ tag?: string }> }) {
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
  // URL 是标签筛选的唯一来源：标签变化时重挂载，清除后不会残留上一次的筛选状态
  return <MaterialLibraryWorkspace key={tag ?? "all"} initialLibrary={library} initialInsights={insights} initialCollections={collections} initialTag={tag} />;
}
