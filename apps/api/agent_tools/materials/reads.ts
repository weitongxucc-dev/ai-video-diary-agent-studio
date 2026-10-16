import type {MaterialSelection, MaterialAsset, Segment, MaterialPage, MaterialToolsContext} from './contracts.ts';
import type {ToolRegistrar} from '../diary/contracts.ts';
import {materialText, fileName} from './projections.ts';

export function registerMaterialReads(pi: ToolRegistrar, {request, boundSelection, selectedObject, verifyVersion, projection}: {
  request: MaterialToolsContext['request']; boundSelection: () => MaterialSelection;
  selectedObject: (asset: MaterialAsset, target: MaterialSelection) => MaterialAsset | Segment;
  verifyVersion: (asset: MaterialAsset, target: MaterialSelection) => void;
  projection: (asset: MaterialAsset, target: MaterialSelection) => Record<string, unknown>;
}) {
  function result(details: Record<string, unknown>) {
    return { content: [{ type: "text", text: JSON.stringify(details) }], details };
  }
  async function read(path: string) {
    try { return await request(path); }
    catch (cause) { throw new Error(materialText(cause instanceof Error ? cause.message : "素材读取失败")); }
  }
  pi.registerTool({
    name: "read_material_library_status",
    label: "读取素材库状态",
    description: "统计当前成员真实可见素材的分析状态，按业务接口逐页读取，只返回数量，不发送整库正文或媒体。空库与请求失败分别表达；不是接收目录或模型就绪检查。",
    parameters: { type: "object", properties: {}, additionalProperties: false },
    execute: async () => {
      const counts: Record<string, number> = { pending: 0, ready: 0, blocked: 0, failed: 0 };
      let offset = 0;
      let total: number | null = null;
      do {
        const page = await read(`/api/production/materials?limit=100&offset=${offset}`) as MaterialPage;
        if (total === null) total = page.total_count;
        if (page.total_count !== total || page.offset !== offset || (page.has_more && !page.assets.length)) {
          throw new Error("素材库分页发生变化或未能继续，数量尚未确认；请重试");
        }
        for (const asset of page.assets) counts[asset.analysis_status] += 1;
        offset += page.assets.length;
        if (!page.has_more) break;
      } while (true);
      if (offset !== total) throw new Error("素材库分页数量不一致，数量尚未确认；请重试");
      return result({ total_count: total, analysis_counts: counts, empty: total === 0, complete: true,
        source: "当前成员可见素材库分页回读", instruction: "分析数量不是可用候选数或用途许可；已分析不表示转写和OCR均成功，分项事实需读取所选对象。" });
    },
  });
  pi.registerTool({
    name: "search_material_library",
    label: "检索可见素材",
    description: "按文字在当前成员可见库检索素材候选，包括尚未分析的素材；由服务端在分页前检索文件名、标签及已有文字证据。只返回少量文件与状态摘要及真实匹配总数，不表示已适合成片，不自动选择或修改。",
    parameters: { type: "object", additionalProperties: false, properties: {
      query: { type: "string", minLength: 1, maxLength: 120 },
      offset: { type: "integer", minimum: 0 }, limit: { type: "integer", minimum: 1, maximum: 8 },
    }, required: ["query"] },
    execute: async (_id: string, params: { query: string; offset?: number; limit?: number }) => {
      const page = await read(`/api/production/materials?q=${encodeURIComponent(params.query)}&limit=${params.limit ?? 8}&offset=${params.offset ?? 0}`) as MaterialPage;
      return result({ matches: page.assets.filter(asset => asset.use_state !== "excluded").map(asset => ({ asset_ref: `asset:${asset.id}`, file_name: fileName(asset),
        media_type: asset.media_type, duration_seconds: asset.duration_seconds, analysis_status: asset.analysis_status,
        use_state: asset.use_state, user_tags: asset.user_tags })),
        total_count: page.total_count, total_count_scope: "服务端可见文件匹配总数，含不使用条目；不是可用候选数量",
        excluded_count_on_page: page.assets.filter(asset => asset.use_state === "excluded").length,
        offset: page.offset, limit: page.limit, has_more: page.has_more,
        source: "当前成员可见素材库文字检索，已剔除本页不使用内容", instruction: "返回的是文件候选，不是用途结论；零匹配不等于空库，空库须通过库状态核实。不使用条目不外发内容，总计不能当可用候选数量。其他使用限制仍保持，不能因检索命中自动选片；需要阅读内容或修改时请用户明确选择对象。" });
    },
  });
  pi.registerTool({
    name: "suggest_selected_material_tags",
    label: "查看片段标签建议",
    description: "仅根据当前选中片段的已保存画面观察给出待采纳标签和采样依据；不修改任何标签，不推断永久习惯。没有明确片段选择时须先选择。",
    parameters: { type: "object", properties: {}, additionalProperties: false },
    execute: async () => {
      const target = boundSelection();
      if (target.scope !== "segment") throw new Error("请先选择需要标签建议的片段");
      const asset = await read(`/api/production/materials/${encodeURIComponent(target.assetId)}`) as MaterialAsset;
      verifyVersion(asset, target);
      const segment = selectedObject(asset, target) as Segment;
      const seen = new Set(segment.user_tags);
      const suggestions = asset.use_state === "excluded" || segment.description.trim() || segment.understanding?.status !== "ready" ? []
        : segment.understanding.facts.flatMap(fact => {
          const label = (fact.label || fact.text).trim();
          if (!label || [...label].length > 32 || seen.has(label)) return [];
          seen.add(label);
          return [{ label: materialText(label), source: "visual_observation", reason: materialText(fact.text),
            kind: fact.kind, sample_times: fact.sample_times, segment_ref: `segment:${segment.id}` }];
        });
      return result({ ...projection(asset, target), suggestions, changed: false,
        instruction: "建议尚未采纳，没有写入我的标签。来源为当前片段观察，不是用户习惯或精确动作区间；人工描述优先，未重新提升旧识别。只有用户明确采纳后才可调用标签修改工具。" });
    },
  });
  pi.registerTool({
    name: "read_selected_material",
    label: "读取所选素材",
    description: "读取发送时明确选中的整条素材或片段：文件与原资产时间、用户说明、我的标签和系统标签、画面理解、已有转写及OCR的独立状态和文字证据。整条素材按片段分页，片段只读自身范围；无选择须先选择。不接受模型指定ID、路径、成员或工作区，不读取原始文件。",
    parameters: { type: "object", additionalProperties: false, properties: {
      offset: { type: "integer", minimum: 0 }, limit: { type: "integer", minimum: 1, maximum: 10 },
    } },
    execute: async (_id: string, params: { offset?: number; limit?: number } = {}) => {
      const target = boundSelection();
      const asset = await read(`/api/production/materials/${encodeURIComponent(target.assetId)}`) as MaterialAsset;
      verifyVersion(asset, target);
      const object = selectedObject(asset, target);
      if (asset.use_state === "excluded") {
        return result({ ...projection(asset, target), use_state: asset.use_state, content_withheld: true,
          instruction: "这条素材已设为不使用，文字分析和原始内容不外发；仅保留原有我的标签管理信息，不改变限制。" });
      }
      const segments = target.scope === "segment" ? [object as Segment] : asset.segments;
      const offset = params.offset ?? 0, limit = params.limit ?? 5;
      const selected = segments.slice(offset, offset + limit);
      const ranges = selected.length ? selected.map(segment => [segment.start_seconds, segment.end_seconds])
        : target.scope === "asset" && !segments.length ? [[0, asset.duration_seconds ?? Infinity]] : [];
      const speech = asset.speech_analysis;
      return result({ ...projection(asset, target), file_name: fileName(asset), media_type: asset.media_type,
        duration_seconds: asset.duration_seconds, use_state: asset.use_state, analysis_status: asset.analysis_status,
        review_status: asset.review_status, usage_role: asset.usage_role,
        analysis_message: materialText(asset.analysis_message ?? ""),
        user_description: materialText(asset.user_description ?? ""), upload_context: materialText(asset.upload_context ?? ""),
        evidence_source: "当前选中素材业务回读；时间均对应该原文件，用户说明不等于系统观察",
        segments: selected.map(segment => {
          const visual = segment.understanding;
          return { segment_ref: `segment:${segment.id}`, start_seconds: segment.start_seconds, end_seconds: segment.end_seconds,
            description: materialText(segment.description), stored_transcript: materialText(segment.transcript),
            review_status: segment.review_status, usage_role: segment.usage_role,
            user_tags: segment.user_tags, system_tags: segment.system_tags,
            visual: { status: visual?.status ?? segment.visual_status ?? "not_recorded",
              summary: visual?.status === "ready" ? materialText(visual.summary) : "",
              facts: visual?.status === "ready" ? visual.facts.map(fact => ({ kind: fact.kind, label: fact.label ? materialText(fact.label) : null, text: materialText(fact.text), sample_times: fact.sample_times })) : [],
              unknowns: visual ? visual.unknowns.map(materialText) : [], message: materialText(visual?.message ?? "") },
            ocr: { status: visual?.ocr_status ?? "not_recorded", frames: visual?.ocr_status === "ready"
              ? visual.ocr_frames.map(frame => ({ timestamp_seconds: frame.timestamp_seconds,
                observations: frame.observations.map(item => ({ text: materialText(item.text), confidence: item.confidence, retrieval_eligible: item.retrieval_eligible })) })) : [] },
          };
        }),
        segment_page: { total_count: segments.length, offset, limit, has_more: offset + selected.length < segments.length },
        speech: { status: speech?.status ?? "not_recorded", message: materialText(speech?.message ?? ""),
          observations: speech?.status === "ready" ? speech.observations.filter(item => ranges.some(([start, end]) => item.end_ms > start * 1000 && item.start_ms < end * 1000))
            .map(item => ({ start_ms: item.start_ms, end_ms: item.end_ms, text: materialText(item.text) })) : [] },
        instruction: "review_status是实际审核状态，approved表示已确认；usage_role是用途：b_roll就是界面‘用作配图’，a_roll为主画面，exclude为不使用，unassigned为未指定。用途与标签分开，不因没有‘配图’标签否认已确认用途。description为已存片段描述，user_description为空不代表片段描述或人工审核不存在。画面观察、声音陈述、OCR文字、已存片段转录和用户补充分别表达；blocked或failed只表示对应识别未完成，不否定人工记录。OCR retrieval_eligible=false仅为低置信线索。不得把推断当作观察，也不能仅凭标签宣称用途。" });
    },
  });
}
