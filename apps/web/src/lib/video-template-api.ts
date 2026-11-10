import { apiUrl } from "./api";
import type { VideoTemplate, VisualTemplate } from "./types";
import { localMemberTokenHeader } from "./local-member-auth";
export class VideoTemplateRequestError extends Error {
  constructor(public status:number,message:string){super(message);}
}

export async function createVideoTemplateVersion(templateId: string, input: {
  expected_version: number; name: string; style_instruction: string; visual?: VisualTemplate;
}): Promise<VideoTemplate> {
  let response: Response;
  try {
    response = await fetch(apiUrl(`/api/video-templates/${encodeURIComponent(templateId)}/versions`), {
      method:"POST",headers:{"Content-Type":"application/json",...localMemberTokenHeader()},body:JSON.stringify(input),cache:"no-store",
    });
  } catch { throw new Error("模板新版本没有保存：本地服务未接通，请恢复服务后重试。"); }
  const body = await response.json();
  if (!response.ok) throw new VideoTemplateRequestError(response.status,typeof body.detail === "string" ? body.detail : `模板新版本没有保存（${response.status}），请检查当前权限与版本后重试。`);
  return body as VideoTemplate;
}

async function templateRequest(path: string, input?: unknown) {
  const response = await fetch(apiUrl(path), {method: input === undefined ? 'GET' : 'POST',
    headers: {'Content-Type': 'application/json', ...localMemberTokenHeader()},
    ...(input === undefined ? {} : {body: JSON.stringify(input)}), cache: 'no-store'});
  const value = await response.json();
  if (!response.ok) throw new VideoTemplateRequestError(response.status,typeof value.detail === 'string' ? value.detail : '模板操作未完成，请核对版本与来源');
  return value;
}
export async function readVisualTemplates(): Promise<VideoTemplate[]> {
  const value = await templateRequest('/api/video-templates');
  return value.items.filter((item: VideoTemplate) => item.settings.kind === 'visual_component');
}
export async function readVideoTemplateVersion(id:string,version:number):Promise<VideoTemplate> {
  return templateRequest(`/api/video-templates/${encodeURIComponent(id)}?version=${version}`);
}
export async function visualTemplateDocument(visual: VisualTemplate): Promise<string> {
  return (await templateRequest('/api/video-templates/visual-preview', visual)).document;
}
export async function saveVisualTemplateCopy(input: {request_id: string; source_template_id: string;
  source_template_version: number; name: string; visual: VisualTemplate}): Promise<VideoTemplate> {
  return templateRequest('/api/video-templates', input);
}
