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
  created_at: string;
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
  created_at: string;
};

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
type ManualSourceItem = Pick<SourceItem, "title" | "content_type" | "source_url" | "body" | "subtitles" | "media_references" | "file_reference" | "author" | "license">;
type SaveToKnowledge = Pick<KnowledgeItem, "ownership" | "authorization" | "version">;

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
