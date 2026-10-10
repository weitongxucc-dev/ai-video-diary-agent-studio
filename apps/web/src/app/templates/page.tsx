import { TemplateLibraryWorkspace } from "@/components/TemplateLibraryWorkspace";
import { getContentTemplates, listVideoTemplates } from "@/lib/api.server";
import type { ContentTemplate, VideoTemplate } from "@/lib/types";

export const dynamic = "force-dynamic";

async function loadTemplates(): Promise<{ templates: VideoTemplate[]; catalog: ContentTemplate[]; offline: boolean }> {
  try {
    const [response, catalog] = await Promise.all([listVideoTemplates(), getContentTemplates()]);
    return { templates: response.items, catalog: catalog.templates, offline: false };
  } catch {
    return { templates: [], catalog: [], offline: true };
  }
}

export default async function TemplatesPage() {
  const state = await loadTemplates();
  return <TemplateLibraryWorkspace {...state} />;
}
