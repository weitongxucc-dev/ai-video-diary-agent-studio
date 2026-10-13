"use client";
import { useEffect, useRef, useState } from "react";
import { createMaterialBusinessObject, getAssetBusinessObjects, getMaterialBusinessObjects, saveAssetBusinessObjects } from "@/lib/api";
import type { BusinessObjectKind, MaterialBusinessObject } from "@/lib/types";
import { visibleError } from './material-errors';
export function useMaterialBusinessObjects(selectedAssetId: string | null) {
    const currentAssetIdRef = useRef(selectedAssetId);
    currentAssetIdRef.current = selectedAssetId;
    const [businessObjects, setBusinessObjects] = useState<MaterialBusinessObject[]>([]);
    const [assetObjectIds, setAssetObjectIds] = useState<Record<string, string[]>>({});
    const [objectPending, setObjectPending] = useState<"create" | "save" | null>(null);
    const [objectError, setObjectError] = useState("");
    const [objectRead, setObjectRead] = useState<{
        status: "loading" | "ready" | "error";
        message: string;
    }>({ status: "loading", message: "" });
    const [objectReadTick, setObjectReadTick] = useState(0);
    const objectLoadRevision = useRef(0);
    useEffect(() => {
        let active = true;
        getMaterialBusinessObjects()
            .then(result => { if (active)
            setBusinessObjects(result.items); })
            .catch(cause => { if (active)
            setObjectError(visibleError(cause, "名称条目读取失败，请重试。")); });
        return () => { active = false; };
    }, []);
    useEffect(() => {
        const assetId = selectedAssetId;
        const revision = ++objectLoadRevision.current;
        // 换素材就清掉上一条素材的失败提示；之后迟到的失败不会再挂回来（见下面的目标判断）。
        setObjectError("");
        if (!assetId) {
            setObjectRead({ status: "ready", message: "" });
            return;
        }
        let active = true;
        setObjectRead({ status: "loading", message: "" });
        getAssetBusinessObjects(assetId)
            .then(result => {
            if (!active || revision !== objectLoadRevision.current)
                return;
            setAssetObjectIds(current => ({ ...current, [assetId]: result.items.map(item => item.id) }));
            setObjectRead({ status: "ready", message: "" });
        })
            .catch(cause => {
            if (!active || revision !== objectLoadRevision.current)
                return;
            // 读失败不等于「没有关联」。明确报出来并留重试，不让用户拿空选择去覆盖。
            setObjectRead({ status: "error", message: visibleError(cause, "名称关联读取失败，请重试。") });
        });
        return () => { active = false; };
    }, [selectedAssetId, objectReadTick]);
    async function createBusinessObject(kind: BusinessObjectKind, name: string) {
        const targetAssetId = currentAssetIdRef.current;
        setObjectPending("create");
        setObjectError("");
        try {
            const created = await createMaterialBusinessObject({ kind, name });
            setBusinessObjects(current => [created, ...current.filter(item => item.id !== created.id)]);
            return created;
        }
        catch (cause) {
            const message = visibleError(cause, "名称条目新增失败，请重试");
            // 用户已经切到别的素材：这条迟到失败不挂到眼前那条身上。
            if (currentAssetIdRef.current === targetAssetId)
                setObjectError(message);
            throw new Error(message);
        }
        finally {
            setObjectPending(null);
        }
    }
    async function saveBusinessObjects(assetId: string, businessObjectIds: string[]) {
        setObjectPending("save");
        setObjectError("");
        try {
            const result = await saveAssetBusinessObjects(assetId, businessObjectIds);
            setAssetObjectIds(current => ({ ...current, [assetId]: result.items.map(item => item.id) }));
            return result.items;
        }
        catch (cause) {
            const message = visibleError(cause, "名称关联保存失败，请重试");
            // 同上：目标素材已经不在眼前，就不把它的失败写成当前素材的问题。
            if (currentAssetIdRef.current === assetId)
                setObjectError(message);
            throw new Error(message);
        }
        finally {
            setObjectPending(null);
        }
    }
    return { businessObjects, assetObjectIds, objectPending, objectError, objectRead, createBusinessObject, saveBusinessObjects, retryObjectRead: () => setObjectReadTick(tick => tick + 1), dismissObjectError: () => setObjectError("") };
}
