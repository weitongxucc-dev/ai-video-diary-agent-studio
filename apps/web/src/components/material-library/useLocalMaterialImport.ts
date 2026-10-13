"use client";
import { useState } from "react";
import { importLocalMaterials } from "@/lib/directoryPicker";
export function useLocalMaterialImport(canImport: boolean) {
    const [importBusy, setImportBusy] = useState(false);
    const [importResult, setImportResult] = useState<{
        copied: {
            sourceName: string;
            fileName: string;
        }[];
        skipped: string[];
        failures: {
            sourceName: string;
            error: string;
        }[];
        canceled: boolean;
        error?: string;
        warning?: string;
    } | null>(null);
    async function runImport() {
        if (!canImport)
            return;
        setImportBusy(true);
        setImportResult(null);
        try {
            const result = await importLocalMaterials();
            if ("kind" in result) {
                setImportResult({
                    copied: [], skipped: [], failures: [], canceled: false,
                    error: "当前环境不支持从应用内导入素材，请使用桌面客户端。",
                });
                return;
            }
            setImportResult({
                copied: result.copied ?? [],
                skipped: result.skipped ?? [],
                failures: result.failures ?? [],
                canceled: result.canceled === true,
                error: result.error,
                warning: result.warning,
            });
        }
        finally {
            setImportBusy(false);
        }
    }
    return { importBusy, importResult, runImport };
}
