import { AppShell } from "@/components/AppShell";
import { HomeWorkspace } from "@/components/HomeWorkspace";
import { getMaterialLibraryInsights, getProductionFoundation, listCreationTasks } from "@/lib/api.server";
import type { MaterialLibraryInsights, ProductionFoundation } from "@/lib/types";

export const dynamic = "force-dynamic";

async function loadFoundation(): Promise<ProductionFoundation | null> {
  try {
    return await getProductionFoundation();
  } catch {
    return null;
  }
}

export default async function HomePage({ searchParams }: { searchParams?: Promise<{ new?: string }> }) {
  const params = searchParams ? await searchParams : {};
  const [foundation, creationTaskResponse, insights] = await Promise.all([
    loadFoundation(),
    listCreationTasks().catch(() => null),
    getMaterialLibraryInsights().catch(() => null),
  ]);
  const creationTasks = creationTaskResponse?.items ?? [];

  return <AppShell
    autoFocus={params.new === "1"}
    creationTasks={creationTasks}
    workspaceStatus={foundation?.readiness.ready ? { kind: "connected", label: "可以开工" } : { kind: "attention", label: "需要处理" }}
  >
    <HomeWorkspace foundation={foundation} tasks={creationTasks} insights={insights} />
  </AppShell>;
}
