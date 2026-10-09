/** Selected-material commands. The caller binds selection and authenticated request. */
export type MaterialSelection =
  | { scope: "asset"; assetId: string }
  | { scope: "segment"; assetId: string; segmentId: string };

type Segment = {
  id: string;
  asset_id: string;
  description: string;
  transcript: string;
  review_status: "raw" | "approved" | "rejected";
  usage_role: "unassigned" | "a_roll" | "b_roll" | "exclude";
  collection_ids: string[];
  system_tags: string[];
  user_tags: string[];
};
export type MaterialAsset = { id: string; user_tags: string[]; segments: Segment[] };
export type MaterialToolsContext = {
  selection: MaterialSelection | null;
  request: (path: string, init?: RequestInit) => Promise<MaterialAsset>;
};

// Same ordered, case-sensitive trim/80-codepoint/dedup contract as _normalized_labels.
function normalizedTags(tags: string[]): string[] {
  return [...new Set(tags.map(tag => [...tag.trim()].slice(0, 80).join("")).filter(Boolean))];
}

export function registerMaterialTools(
  pi: { registerTool: (tool: unknown) => void },
  context: MaterialToolsContext,
): void {
  const selection = context.selection ? { ...context.selection } : null;
  const scopeLabel = selection?.scope === "segment" ? "已选片段" : "已选整条素材";
  function boundSelection(): MaterialSelection {
    if (!selection || !selection.assetId || (selection.scope === "segment" && !selection.segmentId)) {
      throw new Error("请先选择一条素材或一个片段；不能猜测要修改的对象");
    }
    return selection;
  }
  function selectedObject(asset: MaterialAsset, target: MaterialSelection): MaterialAsset | Segment {
    if (asset.id !== target.assetId) throw new Error("回读素材与当前选择不一致，未确认修改结果");
    if (target.scope === "asset") return asset;
    const segment = asset.segments.find(item => item.id === target.segmentId && item.asset_id === target.assetId);
    if (!segment) throw new Error("当前选中片段不存在，未确认修改结果");
    return segment;
  }
  function projection(asset: MaterialAsset, target: MaterialSelection) {
    const object = selectedObject(asset, target);
    return {
      scope: target.scope,
      asset_ref: `asset:${target.assetId}`,
      ...(target.scope === "segment" ? { segment_ref: `segment:${target.segmentId}` } : {}),
      user_tags: object.user_tags,
      ...(target.scope === "segment" ? { system_tags: (object as Segment).system_tags } : {}),
    };
  }
  function result(details: Record<string, unknown>) {
    return { content: [{ type: "text", text: JSON.stringify(details) }], details };
  }
  pi.registerTool({
    name: "read_selected_material",
    label: "读取所选素材",
    description: `读取${scopeLabel}真实的我的标签。范围由页面选择绑定，整条素材与片段分别读取；不接受模型指定ID、路径、成员或工作区。`,
    parameters: { type: "object", properties: {}, additionalProperties: false },
    execute: async () => {
      const target = boundSelection();
      const asset = await context.request(`/api/production/materials/${encodeURIComponent(target.assetId)}`);
      return result(projection(asset, target));
    },
  });
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
      const beforeAsset = await context.request(path);
      const before = selectedObject(beforeAsset, target);
      const tags = normalizedTags(params.tags);
      const updated = params.action === "replace" ? tags
        : params.action === "add" ? normalizedTags([...before.user_tags, ...tags])
        : before.user_tags.filter(tag => !tags.includes(tag));
      if (updated.length > 50) throw new Error("我的标签最多50个，未提交修改；请先移除不需要的标签");
      if (JSON.stringify(updated) === JSON.stringify(before.user_tags)) {
        return result({ ...projection(beforeAsset, target), action: params.action, changed: false });
      }
      const body = target.scope === "asset" ? { user_tags: updated } : {
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
      await context.request(writePath, { method: "PUT", body: JSON.stringify(body) });
      let afterAsset: MaterialAsset;
      try {
        afterAsset = await context.request(path);
      } catch (cause) {
        throw new Error("标签请求已提交，但回读失败，无法确认保存结果；请刷新当前对象后再决定下一步", { cause });
      }
      const after = selectedObject(afterAsset, target);
      if (JSON.stringify(after.user_tags) !== JSON.stringify(updated)) {
        throw new Error("标签请求已提交，但回读标签与修改要求不一致，无法确认保存结果；请刷新当前对象");
      }
      return result({ ...projection(afterAsset, target), action: params.action,
        previous_user_tags: before.user_tags, changed: true });
    },
  });
}
