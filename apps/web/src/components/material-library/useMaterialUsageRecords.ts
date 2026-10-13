"use client";
import { useEffect, useRef, useState } from "react";
import { getAssetSegmentUsageRecords } from "@/lib/api";
import type { MaterialUsageRecord } from "@/lib/types";
import { visibleError } from './material-errors';
export function useMaterialUsageRecords(selectedAssetId: string | null, selectedSegmentId: string | null) {
    const [usageRecords, setUsageRecords] = useState<MaterialUsageRecord[]>([]);
    const [usageRead, setUsageRead] = useState<{
        status: "loading" | "ready" | "error";
        message: string;
    }>({ status: "loading", message: "" });
    const usageLoadRevision = useRef(0);
    const [usageReadTick, setUsageReadTick] = useState(0);
    useEffect(() => {
        const assetId = selectedAssetId;
        const segmentId = selectedSegmentId;
        const revision = ++usageLoadRevision.current;
        if (!assetId || !segmentId) {
            setUsageRecords([]);
            setUsageRead({ status: "ready", message: "" });
            return;
        }
        let active = true;
        // 换片段就先清空上一片段的记录：加载中不能把旧记录的文案挂在当前片段上。
        setUsageRecords([]);
        setUsageRead({ status: "loading", message: "" });
        getAssetSegmentUsageRecords(assetId, segmentId)
            .then(result => {
            if (!active || revision !== usageLoadRevision.current)
                return;
            setUsageRecords(result.items);
            setUsageRead({ status: "ready", message: "" });
        })
            .catch(cause => {
            if (!active || revision !== usageLoadRevision.current)
                return;
            setUsageRecords([]);
            setUsageRead({ status: "error", message: visibleError(cause, "使用记录读取失败，请重试。") });
        });
        return () => { active = false; };
    }, [selectedAssetId, selectedSegmentId, usageReadTick]);
    return { usageRecords, usageRead, retryUsageRead: () => setUsageReadTick(value => value + 1) };
}
