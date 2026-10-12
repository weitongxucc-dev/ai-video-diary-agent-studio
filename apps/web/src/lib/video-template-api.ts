import { apiUrl } from "./api";
import type { VideoTemplate } from "./types";
import { localMemberTokenHeader } from "./local-member-auth";

export async function createVideoTemplateVersion(templateId: string, input: {
  expected_version: number; name: string; style_instruction: string;
}): Promise<VideoTemplate> {
  let response: Response;
  try {
    response = await fetch(apiUrl(`/api/video-templates/${encodeURIComponent(templateId)}/versions`), {
      method:"POST",headers:{"Content-Type":"application/json",...localMemberTokenHeader()},body:JSON.stringify(input),cache:"no-store",
    });
  } catch { throw new Error("模板新版本没有保存：本地服务未接通，请恢复服务后重试。"); }
  const body = await response.json();
  if (!response.ok) throw new Error(typeof body.detail === "string" ? body.detail : `模板新版本没有保存（${response.status}），请检查当前权限与版本后重试。`);
  return body as VideoTemplate;
}
