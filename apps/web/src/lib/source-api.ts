import { localMemberTokenHeader } from "./local-member-auth";

export type SourceConnector = {
  id: string;
  workspace_id: string;
  platform: string;
  protocol: string;
  account_or_rule: string;
  frequency: string;
  permissions: string[];
  cursor: string | null;
  connection_status: string;
  latest_run_id: string | null;
  created_at: string;
};

export type SourceRun = {
  id: string;
  workspace_id: string;
  connector_id: string;
  status: string;
  started_at: string;
  finished_at: string;
  cursor_before: string | null;
  cursor_after: string | null;
  item_count: number;
  reason: string | null;
  impact: string | null;
  repair_action: string | null;
  retryable: boolean | null;
};

export type SourceItem = {
  id: string;
  workspace_id: string;
  connector_id: string;
  source_label: string;
  source_url: string | null;
  content_type: string;
  author: string | null;
  published_at: string | null;
  body: string | null;
  subtitles: string | null;
  media_references: string[];
  license: string;
  title: string;
  file_reference: string | null;
  content_hash: string | null;
  use_state: ContentUseState;
  sections: KnowledgeSection[];
  parameters: KnowledgeParameter[];
  // 网页快照的抓取时间；不是网页抓取时为 null。
  fetched_at: string | null;
  created_at: string;
};

export type KnowledgeSection = {
  locator: string;
  title: string;
  text: string;
};

export type KnowledgeParameter = {
  key: string;
  value: string;
  unit: string | null;
  locator: string;
};

export type KnowledgeItem = {
  id: string;
  workspace_id: string;
  content_type: string;
  title: string;
  body: string | null;
  file_reference: string | null;
  authorization: string;
  ownership: string;
  source_item_id: string | null;
  version: string;
  use_state: ContentUseState;
  // 当前可见范围：shared / private，与服务端同一列。
  visibility?: string;
  content_hash: string | null;
  sections: KnowledgeSection[];
  parameters: KnowledgeParameter[];
  // 当前版本的抓取时间和它实际读到的网址。
  fetched_at: string | null;
  source_url: string | null;
  created_at: string;
  duplicate_of_version?: string | null;
  saved_source_item_id?: string | null;
};

export type KnowledgeItemVersion = {
  version: string;
  content_hash: string | null;
  source_item_id: string | null;
  title: string;
  body: string | null;
  file_reference: string | null;
  sections: KnowledgeSection[];
  parameters: KnowledgeParameter[];
  // 这一版是什么时候、从哪个网址取到的：回查旧版靠它回到当时的内容。
  fetched_at: string | null;
  source_url: string | null;
  saved_at: string;
};

export type KnowledgeItemDetail = KnowledgeItem & { versions: KnowledgeItemVersion[] };

/** 文案引用里绑定的知识条目与固定版本。 */
export type KnowledgeReferenceId = { knowledgeItemId: string; version: string };

/**
 * 解析 `knowledge:{item_id}:{version}` 形式的引用。
 * 不用正则猜分隔位置：版本号在最后一段，按最后一个冒号切，
 * 免得条目 id 本身带冒号时把版本读错。
 */
export function parseKnowledgeReferenceId(referenceId: string): KnowledgeReferenceId | null {
  const prefix = "knowledge:";
  if (!referenceId.startsWith(prefix)) return null;
  const rest = referenceId.slice(prefix.length);
  const separator = rest.lastIndexOf(":");
  if (separator <= 0 || separator === rest.length - 1) return null;
  return { knowledgeItemId: rest.slice(0, separator), version: rest.slice(separator + 1) };
}

/**
 * 在已读到的全部版本里找出引用绑定的那一版。
 * 找不到就返回 null：不拿当前版冒充当时引用的内容。
 */
export function findCitedVersion(
  detail: KnowledgeItemDetail,
  version: string,
): KnowledgeItemVersion | null {
  // 当前头本身就是引用绑定的那一版时用头，否则查版本快照；查不到就返回 null。
  if (detail.version === version) {
    return {
      version: detail.version, content_hash: detail.content_hash, source_item_id: detail.source_item_id,
      title: detail.title, body: detail.body, file_reference: detail.file_reference,
      sections: detail.sections, parameters: detail.parameters,
      fetched_at: detail.fetched_at, source_url: detail.source_url, saved_at: detail.created_at,
    };
  }
  return detail.versions.find((value) => value.version === version) ?? null;
}

export type SourceApiErrorDetails = {
  code?: string;
  impact?: string | null;
  repairAction?: string | null;
  retryable?: boolean | null;
  run?: SourceRun;
};

export class SourceApiError extends Error {
  status: number;
  code: string | null;
  impact: string | null;
  repairAction: string | null;
  retryable: boolean | null;
  run: SourceRun | null;

  constructor(message: string, status: number, details: SourceApiErrorDetails = {}) {
    super(message);
    this.name = "SourceApiError";
    this.status = status;
    this.code = details.code ?? null;
    this.impact = details.impact ?? null;
    this.repairAction = details.repairAction ?? null;
    this.retryable = details.retryable ?? null;
    this.run = details.run ?? null;
  }
}

type ConnectorCreate = Pick<SourceConnector, "platform" | "protocol" | "account_or_rule" | "frequency" | "permissions">;
export type ContentUseState = "available" | "reference_only" | "excluded" | "not_for_automation" | "not_for_publish";
type ManualSourceItem = Pick<SourceItem, "title" | "content_type" | "source_url" | "body" | "subtitles" | "media_references" | "file_reference" | "author"> & {
  license?: string;
  use_state: ContentUseState;
};
// 归属和版本由服务端派生；调用方不伪造旧接口要求的授权文本。
type SaveToKnowledge = Record<string, never>;

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      ...init,
      headers: {
        ...(init?.body ? { "Content-Type": "application/json" } : {}),
        ...init?.headers,
        // 无令牌时为空对象，保持 header 缺席 = 后端方案 A 匿名放行。
        ...localMemberTokenHeader(),
      },
      cache: "no-store",
    });
  } catch {
    throw new SourceApiError("本地服务未接通。", 0);
  }

  const text = await response.text();
  const payload = text ? safeJson(text) : null;
  if (!response.ok) {
    const detail = payload && typeof payload === "object" && "detail" in payload ? payload.detail : payload;
    const object = detail && typeof detail === "object" ? detail as Record<string, unknown> : {};
    throw new SourceApiError(
      typeof object.detail === "string" ? object.detail : typeof payload === "string" ? payload : `请求失败：${response.status}`,
      response.status,
      {
        code: typeof object.error === "string" ? object.error : undefined,
        impact: typeof object.impact === "string" ? object.impact : null,
        repairAction: typeof object.repair_action === "string" ? object.repair_action : null,
        retryable: typeof object.retryable === "boolean" ? object.retryable : null,
        run: object.run && typeof object.run === "object" ? object.run as SourceRun : undefined,
      },
    );
  }
  return payload as T;
}

function safeJson(value: string): unknown {
  try {
    return JSON.parse(value);
  } catch {
    return value;
  }
}

export function listSourceConnectors() {
  return request<{ items: SourceConnector[]; total_count: number }>("/api/source-connectors");
}

export function createSourceConnector(input: ConnectorCreate) {
  return request<SourceConnector>("/api/source-connectors", { method: "POST", body: JSON.stringify(input) });
}

export function getSourceConnector(id: string) {
  return request<SourceConnector>(`/api/source-connectors/${encodeURIComponent(id)}`);
}

export function syncSourceConnector(id: string) {
  return request<{ connector: SourceConnector; run: SourceRun }>(`/api/source-connectors/${encodeURIComponent(id)}/sync`, { method: "POST" });
}

export function listSourceItems(connectorId?: string) {
  const suffix = connectorId ? `?connector_id=${encodeURIComponent(connectorId)}` : "";
  return request<{ items: SourceItem[]; total_count: number }>(`/api/source-items${suffix}`);
}

export function createManualSourceItem(input: ManualSourceItem) {
  return request<SourceItem>("/api/source-items/manual", { method: "POST", body: JSON.stringify(input) });
}

export function saveSourceItemToKnowledge(id: string, input: SaveToKnowledge) {
  return request<KnowledgeItem>(`/api/source-items/${encodeURIComponent(id)}/save-to-knowledge`, { method: "POST", body: JSON.stringify(input) });
}

export function listKnowledgeItems() {
  return request<{ items: KnowledgeItem[]; total_count: number }>("/api/knowledge-items");
}

export function getKnowledgeItem(id: string) {
  return request<KnowledgeItemDetail>(`/api/knowledge-items/${encodeURIComponent(id)}`);
}

export function editKnowledgeItem(id: string, input: { expected_version: string; title: string; body: string }) {
  return request<KnowledgeItemDetail>(`/api/knowledge-items/${encodeURIComponent(id)}/content`, {
    method: "PUT", body: JSON.stringify(input),
  });
}

export function setKnowledgeItemVisibility(id: string, visibility: "shared" | "private") {
  return request<KnowledgeItem>(`/api/knowledge-items/${encodeURIComponent(id)}/visibility`, {
    method: "PUT",
    body: JSON.stringify({ visibility }),
  });
}

export type KnowledgeFileImport = { filename: string; content_base64: string; use_state: ContentUseState };

export function importKnowledgeFile(input: KnowledgeFileImport) {
  return request<SourceItem>("/api/source-items/file", { method: "POST", body: JSON.stringify(input) });
}

/**
 * 抓一个普通网址的完整正文快照，保存原网址和抓取时间。
 * 返回的是这一次的抓取结果；相同正文不新增版本由保存到知识库时判定。
 */
export function captureWebSnapshot(input: { url: string; use_state: ContentUseState }) {
  return request<SourceItem>("/api/source-items/link", { method: "POST", body: JSON.stringify(input) });
}

export function sourceErrorMessage(error: unknown): string {
  if (!(error instanceof SourceApiError)) return "同步失败，请稍后重试。";
  if (error.status === 0) return "本地服务未接通。";
  if (error.status === 401 || error.code === "authentication_required") return "需要登录后才能访问此信息源。";
  if (error.status === 403 || error.code === "access_denied") return "当前账号无权限访问此信息源。";
  if (error.status === 429 || error.code === "rate_limited") return "信息源被限流，请稍后重试。";
  if (error.status === 501 || error.code === "capability_unavailable") return "此平台尚未支持。";
  if (error.status === 409 || error.code === "configuration_required") return "信息源需要先完成配置。";
  return error.message || "同步失败，请稍后重试。";
}
