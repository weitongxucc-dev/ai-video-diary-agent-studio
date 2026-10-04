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
  content_hash: string | null;
  sections: KnowledgeSection[];
  parameters: KnowledgeParameter[];
  // 当前版本的抓取时间和它实际读到的网址。
  fetched_at: string | null;
  source_url: string | null;
  created_at: string;
  duplicate_of_version?: string | null;
  // 用户明确选择的用途分类；null 就是未分类，不按格式推断。
  use_category: KnowledgeUseCategory | null;
  // 用户明确登记的主题/对象关系。
  topics: string[];
  // 这一版的真实解析结果：parsed 有可用结构，text_only 只是整段纯文本。
  parse_status: "parsed" | "text_only" | null;
};

/** 知识用途分类：服务端枚举，不接受自由文本。 */
export type KnowledgeUseCategory = "产品知识" | "公司文档" | "菜单条目" | "销售话术";

/** 概览按真实用途分类统计；未分类单列。 */
export type KnowledgeCategoryCounts = Record<string, number> & {
  未分类: number;
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

export function listKnowledgeItems(filter?: { use_category?: string; topic?: string }) {
  const params = new URLSearchParams();
  if (filter?.use_category !== undefined) params.set("use_category", filter.use_category);
  if (filter?.topic) params.set("topic", filter.topic);
  const suffix = params.toString() ? `?${params.toString()}` : "";
  return request<{
    items: KnowledgeItem[];
    total_count: number;
    category_counts: KnowledgeCategoryCounts;
    topics: string[];
  }>(`/api/knowledge-items${suffix}`);
}

/** 保存用户明确选择的用途分类与主题/对象关系；null/空列表就是清空。 */
export function updateKnowledgeItemClassification(
  id: string,
  input: { use_category: KnowledgeUseCategory | null; topics: string[] },
) {
  return request<KnowledgeItem>(`/api/knowledge-items/${encodeURIComponent(id)}`, {
    method: "PATCH", body: JSON.stringify(input),
  });
}

export function getKnowledgeItem(id: string) {
  return request<KnowledgeItemDetail>(`/api/knowledge-items/${encodeURIComponent(id)}`);
}

export type KnowledgeFileImport = { filename: string; content_base64: string };

export function importKnowledgeFile(input: KnowledgeFileImport) {
  return request<SourceItem>("/api/source-items/file", { method: "POST", body: JSON.stringify(input) });
}

/**
 * 抓一个普通网址的完整正文快照，保存原网址和抓取时间。
 * 返回的是这一次的抓取结果；相同正文不新增版本由保存到知识库时判定。
 */
export function captureWebSnapshot(url: string) {
  return request<SourceItem>("/api/source-items/link", { method: "POST", body: JSON.stringify({ url }) });
}

/** 参数比较的一侧证据：固定到知识条目版本和页码/段落出处。 */
export type ParameterComparisonSide = {
  knowledge_item_id: string;
  version: string;
  value: string;
  locator: string;
};

export type ParameterAdjudication = {
  id: string;
  operator: string;
  reason: string;
  decided_at: string;
  left_version_ref: string;
  right_version_ref: string;
  retained_side: string;
  retained_value: string;
  corrected_value: string | null;
};

/** status 为 pending 时两侧证据都成立，不能当成唯一事实。 */
export type ParameterComparison = {
  id: string;
  workspace_id: string;
  object_label: string;
  parameter_key: string;
  unit: string;
  condition: string;
  left: ParameterComparisonSide;
  right: ParameterComparisonSide;
  created_at: string;
  status: "agreed" | "pending" | "adjudicated";
  is_conflict: boolean;
  adjudication: ParameterAdjudication | null;
};

export type ParameterComparisonCreate = {
  object_label: string;
  parameter_key: string;
  unit: string;
  condition: string;
  left_knowledge_item_id: string;
  left_version: string;
  left_locator: string;
  right_knowledge_item_id: string;
  right_version: string;
  right_locator: string;
};

export function listParameterComparisons(knowledgeItemId?: string) {
  const suffix = knowledgeItemId ? `?knowledge_item_id=${encodeURIComponent(knowledgeItemId)}` : "";
  return request<{ items: ParameterComparison[]; total_count: number }>(
    `/api/knowledge-items/parameter-comparisons${suffix}`,
  );
}

export function createParameterComparison(input: ParameterComparisonCreate) {
  return request<ParameterComparison>("/api/knowledge-items/parameter-comparisons", {
    method: "POST", body: JSON.stringify(input),
  });
}

/** 裁定人由当前已解析的身份决定，这里只提交理由和保留哪一侧。 */
export function adjudicateParameterComparison(id: string, input: {
  reason: string; retained_side: "left" | "right"; retained_value?: string; corrected_value?: string;
}) {
  return request<ParameterComparison>(
    `/api/knowledge-items/parameter-comparisons/${encodeURIComponent(id)}/adjudications`,
    { method: "POST", body: JSON.stringify(input) },
  );
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
