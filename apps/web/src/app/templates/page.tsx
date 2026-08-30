import { TemplateLibraryWorkspace } from "@/components/TemplateLibraryWorkspace";
import { getContentTemplates } from "@/lib/api.server";
import type { ContentTemplate } from "@/lib/types";

export const dynamic = "force-dynamic";

async function loadTemplates(): Promise<{ templates: ContentTemplate[]; offline: boolean }> {
  try {
    const response = await getContentTemplates();
    return { templates: response.templates, offline: false };
  } catch {
    return { templates: [], offline: true };
  }
}

export default async function TemplatesPage() {
  const state = await loadTemplates();
  return <TemplateLibraryWorkspace {...state} />;
}
