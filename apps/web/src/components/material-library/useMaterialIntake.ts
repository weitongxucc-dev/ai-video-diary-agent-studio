"use client";
import { useEffect, useRef, useState } from "react";
import { useWorkspacePage } from "../workbench/context";
import { getMaterialIntakeStatus, bindMaterialIntake, confirmMaterialCopyComplete } from "@/lib/api";
import type { MaterialPendingCopy } from "@/lib/types";
import type { IntakeStatus } from './material-state';
import { visibleError } from './material-errors';
export function useMaterialIntake() {
    const viewActive = useWorkspacePage()?.active ?? true;
    const [intake, setIntake] = useState<IntakeStatus | null>(null);
    const [intakeError, setIntakeError] = useState("");
    const [intakeBusy, setIntakeBusy] = useState<"bind" | "refresh" | "confirm" | null>("refresh");
    const intakeRevision = useRef(0);
    useEffect(() => {
        if (!viewActive) return;
        let active = true;
        let inFlight = false;
        async function refresh() {
            if (inFlight)
                return;
            inFlight = true;
            const revision = intakeRevision.current;
            try {
                const status = await getMaterialIntakeStatus();
                if (active && revision === intakeRevision.current) {
                    setIntake(status);
                    setIntakeError("");
                }
            }
            catch (cause) {
                if (active && revision === intakeRevision.current)
                    setIntakeError(visibleError(cause, "无法查看素材目录状态，请重试。"));
            }
            finally {
                inFlight = false;
                if (active)
                    setIntakeBusy((current) => current === "refresh" ? null : current);
            }
        }
        void refresh();
        const timer = window.setInterval(() => {
            if (document.visibilityState === "visible")
                void refresh();
        }, 15000);
        return () => { active = false; window.clearInterval(timer); };
    }, [viewActive]);
    async function refreshIntakeStatus() {
        const revision = ++intakeRevision.current;
        setIntakeBusy("refresh");
        try {
            const status = await getMaterialIntakeStatus();
            if (revision === intakeRevision.current) {
                setIntake(status);
                setIntakeError("");
            }
        }
        catch (cause) {
            if (revision === intakeRevision.current)
                setIntakeError(visibleError(cause, "无法查看素材目录状态，请重试。"));
        }
        finally {
            setIntakeBusy(null);
        }
    }
    async function bindIntake() {
        const directory = intake?.configured_directory;
        if (!directory || !intake.binding_available || intake.directory_state !== "ready" || intake.bound_to_current_workspace)
            return;
        ++intakeRevision.current;
        setIntakeBusy("bind");
        setIntakeError("");
        try {
            setIntake(await bindMaterialIntake(directory));
        }
        catch (cause) {
            setIntakeError(visibleError(cause, "接入失败。请核对目录后重新确认。"));
            try {
                setIntake(await getMaterialIntakeStatus());
            }
            catch { /* Preserve the reviewed directory and error. */ }
        }
        finally {
            setIntakeBusy(null);
        }
    }
    async function confirmCopy(file: MaterialPendingCopy) {
        ++intakeRevision.current;
        setIntakeBusy("confirm");
        setIntakeError("");
        try {
            const result = await confirmMaterialCopyComplete({ ...file, confirmation_source: "manual" });
            ++intakeRevision.current;
            setIntake(result.intake);
        }
        catch (cause) {
            setIntakeError(visibleError(cause, "复制完成确认未保存，请重新检查文件后重试。"));
        }
        finally {
            setIntakeBusy(null);
        }
    }
    return { intake, intakeError, intakeBusy, refreshIntakeStatus, bindIntake, confirmCopy, readIntakeAfterScan: async () => { setIntake(await getMaterialIntakeStatus()); } };
}
