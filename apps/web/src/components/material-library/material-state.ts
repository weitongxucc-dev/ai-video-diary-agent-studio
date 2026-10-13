import { getMaterialIntakeStatus } from "@/lib/api";
import type { MaterialAsset } from "@/lib/types";
export type Filter = "all" | "raw" | "approved" | "rejected";
export type MediaTypeFilter = "all" | MaterialAsset["media_type"];
export type SegmentEditAnchor = {
    assetId: string;
    segmentId: string;
    startSeconds: number;
    endSeconds: number;
};
export type IntakeStatus = Awaited<ReturnType<typeof getMaterialIntakeStatus>>;
export type Busy = "scan" | "search" | "load" | "analyze" | "review" | "segment" | "collection" | "retry" | "notes" | null;
export const directoryStateLabel: Record<IntakeStatus["directory_state"], string> = {
    ready: "目录可用",
    missing: "目录不存在",
    unmounted: "外接盘未连接",
    read_only: "目录不可写",
    invalid: "目录不可用",
    changed: "目录已变更",
    unknown: "等待确认",
};
export const useStateOptions: {
    value: MaterialAsset["use_state"];
    label: string;
    impact: string;
}[] = [
    { value: "available", label: "可用于创作", impact: "可用于选片和发布。" },
    { value: "reference_only", label: "仅参考", impact: "只供查看，不用于成片。" },
    { value: "excluded", label: "不使用", impact: "不用于创作或发布。" },
    { value: "not_for_automation", label: "不用于自动任务", impact: "手动创作可用，自动任务不使用。" },
    { value: "not_for_publish", label: "不用于发布", impact: "可用于制作预览，发布时排除。" },
];
// 关联还没读回来时传这个空数组，而不是每次渲染现造一个：同一个引用，
// 子组件就不会因为父页重渲染而重算一次“选择有没有变”。
export const EMPTY_OBJECT_IDS: readonly string[] = [];
