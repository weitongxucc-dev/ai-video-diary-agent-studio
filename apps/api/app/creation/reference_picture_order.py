"""Allocate confirmed reference picture on the same frame clock as the delivery."""
from dataclasses import dataclass, replace

from app.creation.duration_requirement import DurationConflict
from app.materials.contracts import MaterialAsset, MaterialSegment, effective_segment_usage_role
from app.materials.source_files import automatic_segment_eligible

FRAME_RATE = 30


@dataclass(frozen=True)
class ReferencePictureRange:
    asset: MaterialAsset
    segment: MaterialSegment
    start_frame: int
    end_frame: int


@dataclass(frozen=True)
class ReferencePictureOrder:
    asset_ids: tuple[str, ...]
    source_start_seconds: dict[str, float]

    @classmethod
    def from_plan(cls, plan):
        if plan.material_selection_mode == "source_order":
            return cls(tuple(plan.selected_material_asset_ids), plan.material_source_start_seconds)
        return None


def reference_picture_ranges(workspace_id, assets, asset_ids, source_start_seconds):
    """Use explicit sources in their chosen order, omitting unavailable source intervals."""
    if not asset_ids:
        raise DurationConflict("按参考片顺序需要明确选定参考视频。")
    by_id = {asset.id: asset for asset in assets}
    ranges = []
    for asset_id in asset_ids:
        asset = by_id.get(asset_id)
        if asset is None:
            raise DurationConflict("已选参考视频已不可用，请核对本作品的选材范围。")
        cursor = round(source_start_seconds.get(asset_id, 0) * FRAME_RATE)
        before = len(ranges)
        for segment in sorted(asset.segments, key=lambda item: (item.start_seconds, item.end_seconds)):
            if (effective_segment_usage_role(asset, segment) not in {"unassigned", "b_roll"}
                    or not automatic_segment_eligible(asset, segment, workspace_id)):
                continue
            begin = max(cursor, round(segment.start_seconds * FRAME_RATE))
            end = round(segment.end_seconds * FRAME_RATE)
            if begin < end:
                ranges.append(ReferencePictureRange(asset, segment, begin, end))
                cursor = end
        if len(ranges) == before:
            raise DurationConflict("所选参考视频从源入点起没有可用的补充画面。")
    return ranges


def require_reference_picture_frames(ranges, offset_frames, frame_count):
    available = max(0, sum(span.end_frame - span.start_frame for span in ranges) - offset_frames)
    if available < frame_count:
        missing = (frame_count - available) / FRAME_RATE
        raise DurationConflict(
            f"按参考片顺序还缺少 {missing:.3f} 秒有效画面，请补充已授权参考镜头或修订交付时长。"
        )


def take_reference_picture(ranges, offset_frames, frame_count):
    """Slice an ordered source stream with a global B-roll frame offset."""
    require_reference_picture_frames(ranges, offset_frames, frame_count)
    remaining = frame_count
    selected = []
    for span in ranges:
        source_frames = span.end_frame - span.start_frame
        start = span.start_frame + min(offset_frames, source_frames)
        offset_frames -= start - span.start_frame
        take = min(remaining, span.end_frame - start)
        if take:
            selected.append(replace(span, start_frame=start, end_frame=start + take))
            remaining -= take
        if not remaining:
            return selected
    return selected
