import type {MaterialSelection, MaterialAsset, Segment} from './contracts.ts';
import {utcVersion} from './projections.ts';

export function createMaterialSelection(selected: MaterialSelection | null) {
  const selection = selected ? { ...selected } : null;
  const scopeLabel = selection?.scope === "segment" ? "已选片段" : "已选整条素材";
  function boundSelection(): MaterialSelection {
    if (!selection || !selection.assetId || (selection.scope === "segment" && !selection.segmentId)) {
      throw new Error("请先选择一条素材或一个片段；不能猜测要修改的对象");
    }
    return { ...selection };
  }
  function selectedObject(asset: MaterialAsset, target: MaterialSelection): MaterialAsset | Segment {
    if (asset.id !== target.assetId) throw new Error("回读素材与当前选择不一致，未确认修改结果");
    if (target.scope === "asset") return asset;
    const segment = asset.segments.find(item => item.id === target.segmentId && item.asset_id === target.assetId);
    if (!segment) throw new Error("当前选中片段不存在，未确认修改结果");
    return segment;
  }
  function verifyVersion(asset: MaterialAsset, target: MaterialSelection) {
    if (target.expectedUpdatedAt && utcVersion(asset.updated_at) !== utcVersion(target.expectedUpdatedAt)) throw new Error("所选素材版本已变化，请重新查看后发送；未提交修改");
    const object = selectedObject(asset, target);
    if (target.scope === "segment" && target.expectedSegmentUpdatedAt && utcVersion(object.updated_at) !== utcVersion(target.expectedSegmentUpdatedAt)) throw new Error("所选片段版本已变化，请重新查看后发送；未提交修改");
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
  function advanceVersion(asset: MaterialAsset, object: MaterialAsset | Segment) {
    selection!.expectedUpdatedAt = asset.updated_at;
    if (selection!.scope === 'segment') selection!.expectedSegmentUpdatedAt = object.updated_at;
  }
  return {scopeLabel, boundSelection, selectedObject, verifyVersion, projection, advanceVersion};
}
