import { TemplateLibraryWorkspace } from "@/components/TemplateLibraryWorkspace";
import { listVideoTemplates } from "@/lib/api.server";
import type { VideoTemplate } from "@/lib/types";

export const dynamic = "force-dynamic";

async function loadTemplates(): Promise<{ templates: VideoTemplate[]; offline: boolean }> {
  try {
    const response = await listVideoTemplates();
    return { templates: response.items, offline: false };
  } catch {
    return { templates: [], offline: true };
  }
}

export default async function TemplatesPage() {
  const state = await loadTemplates();
  return <TemplateLibraryWorkspace {...state} />;
}
