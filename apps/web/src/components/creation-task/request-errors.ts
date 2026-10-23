import { CreationPlanApiError } from "@/lib/api";
export function errorDetails(cause: unknown) {
    if (cause instanceof CreationPlanApiError) {
        return {
            message: cause.reason || cause.message,
            impact: cause.impact,
            repairAction: cause.repairAction,
            code: cause.code,
        };
    }
    return {
        message: cause instanceof Error ? cause.message : "操作没有完成。",
        impact: null,
        repairAction: null,
        code: null,
    };
}
