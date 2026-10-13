import type {BusinessRequest, ToolRegistrar} from './contracts.ts';

export function registerMaterialCandidates(pi: ToolRegistrar, {request}: {
  request: BusinessRequest;
}) {
  pi.registerTool({
    name: "find_material_candidates",
    label: "按描述找候选素材",
    description:
      "按一句描述（如「更亮的城市夜景」）从已分析、符合实际使用限制的素材里找若干候选镜头，不要求统一逐条审核；每条带可核对依据（文件名、标签、时长、来源、匹配原因、镜头引用）。只给候选与依据，供用户和 agent 一起裁量；绝不自动替换、不改时间线。",
    parameters: {
      type: "object",
      properties: {
        description: { type: "string", minLength: 1, maxLength: 160 },
        usage_role: { type: "string", enum: ["a_roll", "b_roll"] },
        limit: { type: "integer", minimum: 1, maximum: 20 },
      },
      required: ["description"], additionalProperties: false,
    },
    execute: async (_id: string, params: { description: string; usage_role?: string; limit?: number }) => {
      const limit = params.limit ?? 8;
      const roleQuery = params.usage_role ? `&usage_role=${encodeURIComponent(params.usage_role)}` : "";
      const matches = (await request(`/api/production/materials/search?q=${encodeURIComponent(params.description)}&limit=${limit}${roleQuery}`)) as Array<Record<string, unknown>>;

      // 为「可核对依据」补充 asset 级元数据（文件名、标签、时长、来源）。
      // 先取素材库第一页；命中候选若不在这一页（素材库大于一页时会这样），
      // 就按 asset_ref 逐个精确取，不靠猜、不用 null 填坑。
      // 注意分页上限：路由 list 端点 limit > 100 会直接 422
      // （main.py list_production_materials），所以这里用 100。
      const library = (await request("/api/production/materials?limit=100")) as {
        assets?: Array<Record<string, unknown>>;
        total_count?: number;
        has_more?: boolean;
        limit?: number;
        offset?: number;
      };
      const byAssetId = new Map<string, Record<string, unknown>>();
      for (const asset of library.assets || []) {
        byAssetId.set(String(asset.id), asset);
      }

      const assetIdOf = (assetRef: string): string =>
        assetRef.startsWith("asset:") ? assetRef.slice("asset:".length) : assetRef;

      // 不在第一页里的候选：逐个取回。走同一个 request()（已透传 Cookie/workspace），
      // 由后端 get_asset(workspace_id, asset_id) 做归属校验；不属于本工作区的
      // 会 404，这里捕获后当作「拿不到依据」，绝不把别的空间的元数据带出来。
      const missingIds = [...new Set(
        matches.map((m) => assetIdOf(String(m.asset_ref || "")))
          .filter((id) => id && !byAssetId.has(id)),
      )];
      const missingEvidence: string[] = [];
      await Promise.all(missingIds.map(async (assetId) => {
        try {
          const asset = await request(`/api/production/materials/${encodeURIComponent(assetId)}`);
          if (asset && typeof asset === "object") byAssetId.set(assetId, asset as Record<string, unknown>);
        } catch {
          missingEvidence.push(assetId);
        }
      }));

      const candidates = matches.map((match) => {
        const assetRef = String(match.asset_ref || "");
        const assetId = assetIdOf(assetRef);
        const asset = byAssetId.get(assetId) || {};
        const userTags = (asset.user_tags as string[]) || [];
        const systemTags = (asset.system_tags as string[]) || [];
        // 素材本体信息以逐个取回的那份为准（我们刚为它付了一次请求，而且它就是
        // workspace 归属校验过的那份）；只有取不到时才退回检索响应里那份
        // asset_file_name。
        const fileName = asset.file_name || match.asset_file_name || null;
        return {
          asset_ref: assetRef,
          segment_ref: match.segment_ref,
          score: match.score,
          // —— 片段级依据（来自检索响应，schema 已放行）——
          rank: match.rank ?? null,
          match_tier: match.match_tier ?? null,
          matched_terms: (match.matched_terms as string[]) || [],
          matched_conditions: (match.matched_conditions as string[]) || [],
          time_range: match.time_range || null,
          start_seconds: match.start_seconds ?? null,
          end_seconds: match.end_seconds ?? null,
          segment_duration_seconds: match.duration_seconds ?? null,
          // —— asset 级依据 ——
          file_name: fileName,
          duration_seconds: asset.duration_seconds ?? null,
          tags: [...new Set([...userTags, ...systemTags, ...((match.tags as string[]) || [])])],
          source_reference: asset.source_reference ?? asset.source_ref ?? null,
          reasons: (match.reasons as string[]) || [],
        };
      });

      const totalCount = typeof library.total_count === "number" ? library.total_count : null;
      const hasMore = Boolean(library.has_more);
      // 任一候选缺 asset 级依据 = 依据不全。宁可明说不全，也不让 agent 拿着
      // 一堆 null 当完整依据讲给用户。
      const evidenceComplete = missingEvidence.length === 0;
      const evidenceNote = evidenceComplete
        ? (hasMore && totalCount !== null
            ? `素材库共 ${totalCount} 条，本次只读了第一页，但每条候选的依据都已按 asset 精确取回。`
            : "每条候选的依据都已取回。")
        : `依据不全：素材 ${missingEvidence.join("、")} 的元数据取不到，读取失败的具体原因未确定（本次没有拿到可判定的原因，不据此断定素材已删除或不属于当前工作区）。` +
          `上面点名的候选的 file_name/tags 若有值，只来自检索响应、未经本工作区元数据复核，不等于归属或内容已核实；duration_seconds 与 source_reference 本次取不到，就是空。`;

      const details = {
        candidates,
        evidence_complete: evidenceComplete,
        evidence_note: evidenceNote,
        library_total: totalCount,
        library_has_more: hasMore,
        instruction:
          "以上只是候选与依据，不代表替换。请把依据（文件名/时间点/标签/时长/匹配原因/排第几）摆给用户看，由用户或 agent 判断后，再走正常剪辑预览流程；本工具不执行任何修改。" +
          (evidenceComplete ? "" : ` 注意：必须向用户说明哪些候选的依据没取全（本次是：${missingEvidence.join("、")}），且只针对这些候选说依据不全——没取到的字段就说没取到，不要当成已知；这些候选的 file_name/tags 若有值只来自检索响应，未经本工作区元数据复核，不等于归属或内容已核实。`),
      };
      return { content: [{ type: "text", text: JSON.stringify(details) }], details };
    },
  });

}
