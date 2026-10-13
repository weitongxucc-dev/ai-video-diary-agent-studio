import type { VideoTemplate } from '../types/templates';
import type { ContentTemplates } from '../types/templates';
import type { ContentVisibility } from '../types/common';
import { request } from './transport';
import { creationPlanRequest } from './creation-transport';
export function setVideoTemplateVisibility(templateId: string, visibility: ContentVisibility): Promise<{
    item: VideoTemplate;
}> {
    return request<{
        item: VideoTemplate;
    }>(`/api/video-templates/${encodeURIComponent(templateId)}/visibility`, { method: "PUT", body: JSON.stringify({ visibility }) });
}
export function getContentTemplates(): Promise<ContentTemplates> {
    return request<ContentTemplates>("/api/content-templates");
}
export function listVideoTemplates(): Promise<{
    items: VideoTemplate[];
}> {
    return creationPlanRequest("/api/video-templates");
}
export function saveVideoTemplate(taskId: string, input: {
    name: string;
    style_instruction: string;
    template_id?: string;
    expected_version?: number;
}): Promise<VideoTemplate> {
    return creationPlanRequest(`/api/creation-tasks/${encodeURIComponent(taskId)}/templates`, {
        method: "POST", body: JSON.stringify(input),
    });
}
