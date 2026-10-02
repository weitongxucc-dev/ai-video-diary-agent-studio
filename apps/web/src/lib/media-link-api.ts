import { localMemberTokenHeader } from "./local-member-auth";

export type MediaLinkType = "video" | "audio" | "image";

export type MediaLinkCheck = {
  url: string;
  supported: boolean;
  media_type: MediaLinkType | null;
  file_name: string | null;
  size_bytes: number | null;
  message: string;
};

export type MediaLinkImport = {
  url: string;
  media_type: MediaLinkType;
  file_name: string;
  size_bytes: number;
  file_path: string;
  asset_id: string;
  provenance_id: string;
  reused_existing_file: boolean;
  analysis_status: "pending" | "ready" | "blocked" | "failed";
  analysis_message: string;
};

export type MediaLinkErrorCode =
  | "invalid_url"
  | "unsupported_scheme"
  | "credentials_in_url"
  | "social_page"
  | "private_address"
  | "host_unresolved"
  | "not_reachable"
  | "not_found"
  | "credentials_required"
  | "web_page_not_media"
  | "unsupported_media_type"
  | "too_large"
  | "empty_file"
  | "incomplete_download"
  | "too_many_redirects"
  | "unreadable_format"
  | "provenance_unavailable";

export class MediaLinkApiError extends Error {
  status: number;
  code: MediaLinkErrorCode | null;

  constructor(message: string, status: number, code: MediaLinkErrorCode | null = null) {
    super(message);
    this.name = "MediaLinkApiError";
    this.status = status;
    this.code = code;
  }
}

async function post<T>(path: string, body: { url: string }): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...localMemberTokenHeader(),
      },
      body: JSON.stringify(body),
      cache: "no-store",
    });
  } catch {
    throw new MediaLinkApiError("本地服务未接通。", 0);
  }
  const text = await response.text();
  const payload = text ? (JSON.parse(text) as unknown) : null;
  if (!response.ok) {
    const detail =
      payload && typeof payload === "object" && "detail" in payload
        ? (payload as { detail: unknown }).detail
        : payload;
    const object = detail && typeof detail === "object" ? (detail as Record<string, unknown>) : {};
    throw new MediaLinkApiError(
      typeof object.detail === "string"
        ? object.detail
        : typeof detail === "string"
          ? detail
          : `请求失败：${response.status}`,
      response.status,
      typeof object.code === "string" ? (object.code as MediaLinkErrorCode) : null,
    );
  }
  return payload as T;
}

/** 提交前检查：只读来源，确认媒体类型与可接入结果，不下载文件。 */
export function checkMediaLink(url: string): Promise<MediaLinkCheck> {
  return post<MediaLinkCheck>("/api/production/materials/link/check", { url });
}

/** 下载并登记；只有下载完整、文件非空且出处已登记才返回成功。 */
export function importMediaLink(url: string): Promise<MediaLinkImport> {
  return post<MediaLinkImport>("/api/production/materials/link/import", { url });
}

const CODE_MESSAGES: Record<MediaLinkErrorCode, string> = {
  invalid_url: "请填写完整的直链地址。",
  unsupported_scheme: "只支持 http(s) 直链。",
  credentials_in_url: "链接中带有账号或密码，请改用公开直链。",
  social_page: "这是社交平台页面，不是媒体文件直链，暂不支持。",
  private_address: "该地址不是公开可访问的来源，暂不支持。",
  host_unresolved: "无法解析来源地址，请检查链接。",
  not_reachable: "来源暂时无法接入，请稍后重试。",
  not_found: "该直链不存在或已失效。",
  credentials_required: "该来源需要登录或凭据，暂不支持。",
  web_page_not_media: "这是网页地址，不是媒体文件直链。",
  unsupported_media_type: "只支持视频、音频和图片文件直链。",
  too_large: "文件超过首批范围允许的大小。",
  empty_file: "下载到的文件为空，未登记。",
  incomplete_download: "下载不完整，未登记。",
  too_many_redirects: "跳转次数过多，未继续访问该来源。",
  unreadable_format: "下载到的文件不是可识别的视频、音频或图片，未登记。",
  provenance_unavailable: "出处尚未登记，本次导入未完成。",
};

export function mediaLinkErrorMessage(error: unknown): string {
  if (error instanceof MediaLinkApiError) {
    return (error.code && CODE_MESSAGES[error.code]) || error.message;
  }
  return "链接导入失败，请重试。";
}