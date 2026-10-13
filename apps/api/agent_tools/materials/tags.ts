import type {MaterialSelection, MaterialAsset, Segment, MaterialToolsContext} from './contracts.ts';
import type {ToolRegistrar} from '../diary/contracts.ts';
import {utcVersion} from './projections.ts';

// Same ordered, case-sensitive trim/80-codepoint/dedup contract as _normalized_labels.
function normalizedTags(tags: string[]): string[] {
  return [...new Set(tags.map(tag => [...tag.trim()].slice(0, 80).join("")).filter(Boolean))];
}

export function registerMaterialTagCommands(pi: ToolRegistrar, {request, scopeLabel, boundSelection, selectedObject, verifyVersion, projection, advanceVersion}: {
  request: MaterialToolsContext['request']; scopeLabel: string; boundSelection: () => MaterialSelection;
  selectedObject: (asset: MaterialAsset, target: MaterialSelection) => MaterialAsset | Segment;
  verifyVersion: (asset: MaterialAsset, target: MaterialSelection) => void;
  projection: (asset: MaterialAsset, target: MaterialSelection) => Record<string, unknown>;
  advanceVersion: (asset: MaterialAsset, object: MaterialAsset | Segment) => void;
}) {
  function result(details: Record<string, unknown>) {
    return { content: [{ type: "text", text: JSON.stringify(details) }], details };
  }
  pi.registerTool({
    name: "update_selected_material_tags",
    label: "修改我的标签",
    description: `仅按用户明确要求替换、添加或移除${scopeLabel}的我的标签。整条素材不修改片段，片段不修改整条或其他片段；不编辑系统标签，其他属性原样保留。无选择先澄清，不猜对象。移除按标签文字完全匹配、区分大小写；空数组replace表示明确清空我的标签。结果来自写入后同对象回读，不把请求提交当保存成功。`,
    parameters: {
      type: "object", additionalProperties: false,
      properties: {
        action: { type: "string", enum: ["replace", "add", "remove"] },
        tags: { type: "array", maxItems: 50, items: { type: "string" } },
      },
      required: ["action", "tags"],
    },
    execute: async (_id: string, params: { action: "replace" | "add" | "remove"; tags: string[] }) => {
      const target = boundSelection();
      if (!["replace", "add", "remove"].includes(params.action)) throw new Error("请明确替换、添加或移除我的标签");
      const path = `/api/production/materials/${encodeURIComponent(target.assetId)}`;
      const beforeAsset = await request(path) as MaterialAsset;
      verifyVersion(beforeAsset, target);
      const before = selectedObject(beforeAsset, target);
      const tags = normalizedTags(params.tags);
      const updated = params.action === "replace" ? tags
        : params.action === "add" ? normalizedTags([...before.user_tags, ...tags])
        : before.user_tags.filter(tag => !tags.includes(tag));
      if (updated.length > 50) throw new Error("我的标签最多50个，未提交修改；请先移除不需要的标签");
      if (JSON.stringify(updated) === JSON.stringify(before.user_tags)) {
        return result({ ...projection(beforeAsset, target), action: params.action, changed: false });
      }
      const body = target.scope === "asset" ? { user_tags: updated, expected_asset_updated_at: target.expectedUpdatedAt ?? beforeAsset.updated_at } : {
        expected_asset_updated_at: target.expectedUpdatedAt ?? beforeAsset.updated_at,
        expected_segment_updated_at: target.expectedSegmentUpdatedAt ?? before.updated_at,
        // Existing segment PUT replaces these fields; omission would erase metadata.
        description: (before as Segment).description,
        transcript: (before as Segment).transcript,
        review_status: (before as Segment).review_status,
        usage_role: (before as Segment).usage_role,
        collection_ids: (before as Segment).collection_ids,
        user_tags: updated,
      };
      const writePath = target.scope === "asset" ? `${path}/tags`
        : `${path}/segments/${encodeURIComponent(target.segmentId)}`;
      const submittedAsset = await request(writePath, { method: "PUT", body: JSON.stringify(body) }) as MaterialAsset;
      const submitted = selectedObject(submittedAsset, target);
      if (!Number.isFinite(Date.parse(submittedAsset.updated_at)) || !Number.isFinite(Date.parse(submitted.updated_at))) {
        throw new Error("标签请求已提交，但写入返回缺少有效对象版本，无法确认保存结果；请刷新当前对象");
      }
      let afterAsset: MaterialAsset;
      try {
        afterAsset = await request(path) as MaterialAsset;
      } catch (cause) {
        throw new Error("标签请求已提交，但回读失败，无法确认保存结果；请刷新当前对象后再决定下一步", { cause });
      }
      const after = selectedObject(afterAsset, target);
      if (utcVersion(afterAsset.updated_at) !== utcVersion(submittedAsset.updated_at) || utcVersion(after.updated_at) !== utcVersion(submitted.updated_at)) {
        throw new Error("标签请求已提交，但回读对象版本与本次写入不一致，无法确认保存结果；请刷新当前对象");
      }
      if (JSON.stringify(after.user_tags) !== JSON.stringify(updated)) {
        throw new Error("标签请求已提交，但回读标签与修改要求不一致，无法确认保存结果；请刷新当前对象");
      }
      // Only this turn's successful CAS response, verified by the same-object GET,
      // advances its private expectation; caller selection and IDs stay frozen.
      advanceVersion(submittedAsset, submitted);
      return result({ ...projection(afterAsset, target), action: params.action,
        previous_user_tags: before.user_tags, changed: true });
    },
  });
}
