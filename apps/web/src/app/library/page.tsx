import { MaterialLibraryWorkspace } from "@/components/MaterialLibraryWorkspace";
import { getProductionMaterials } from "@/lib/api.server";
import type { MaterialLibrary } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function LibraryPage() {
  let library: MaterialLibrary = { assets: [], total_count: 0 };
  try { library = await getProductionMaterials(); } catch { /* Render recoverable empty state. */ }
  return <MaterialLibraryWorkspace initialLibrary={library} />;
}
