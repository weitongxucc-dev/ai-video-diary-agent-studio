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

export default async function LibraryPage() {
  let library: MaterialLibrary = { assets: [], total_count: 0, limit: 24, offset: 0, has_more: false };
  let insights: MaterialLibraryInsights = { asset_count: 0, segment_count: 0, tags: [], recommended_gaps: [] };
  let collections: MaterialCollections = { collections: [], total_count: 0 };
  [library, insights, collections] = await Promise.all([
    getProductionMaterials().catch(() => library),
    getMaterialLibraryInsights().catch(() => insights),
    getMaterialCollections().catch(() => collections),
  ]);
  return <MaterialLibraryWorkspace initialLibrary={library} initialInsights={insights} initialCollections={collections} />;
}
