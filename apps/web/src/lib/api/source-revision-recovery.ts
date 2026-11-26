import { creationPlanRequest } from "./creation-transport";

export type SourceRevisionRecoveryInput = {
    request_id: string;
    expected_timeline_version_id: string;
    expected_timeline_sequence: number;
    asset_id: string;
    expected_bound_asset_updated_at: string;
    expected_asset_updated_at: string;
};

export type SourceRevisionRecoveryReceipt = {
    request_id: string;
    status: "recovered";
    task_id: string;
    plan_id: string;
    timeline_version_id: string;
    timeline_sequence: number;
    asset_id: string;
    previous_asset_updated_at: string;
    asset_updated_at: string;
};

function recoveriesPath(taskId: string) {
    return `/api/creation-tasks/${encodeURIComponent(taskId)}/source-revision-recoveries`;
}

export function recoverSourceRevision(taskId: string, input: SourceRevisionRecoveryInput) {
    return creationPlanRequest<SourceRevisionRecoveryReceipt>(recoveriesPath(taskId), {
        method: "POST",
        body: JSON.stringify(input),
    });
}

export function readSourceRevisionRecovery(taskId: string, requestId: string) {
    return creationPlanRequest<SourceRevisionRecoveryReceipt>(`${recoveriesPath(taskId)}/${encodeURIComponent(requestId)}`);
}

export function sameSourceRevision(first: string, second: string) {
    const time = Date.parse(first);
    // Date.parse drops submillisecond precision. The server uses microsecond
    // revisions and may render an equivalent UTC offset as Z or +00:00.
    const fraction = (value: string) => (/\.(\d+)(?:Z|[+-]\d{2}:\d{2})$/i.exec(value)?.[1] ?? "")
        .padEnd(6, "0").slice(3);
    return Number.isFinite(time) && time === Date.parse(second) && fraction(first) === fraction(second);
}

export function matchesSourceRevisionRecovery(receipt: SourceRevisionRecoveryReceipt, taskId: string,
    planId: string, input: SourceRevisionRecoveryInput) {
    return receipt?.status === "recovered" && receipt.request_id === input.request_id
        && receipt.task_id === taskId && receipt.plan_id === planId && receipt.asset_id === input.asset_id
        && receipt.timeline_version_id === input.expected_timeline_version_id
        && receipt.timeline_sequence === input.expected_timeline_sequence
        && sameSourceRevision(receipt.previous_asset_updated_at, input.expected_bound_asset_updated_at)
        && sameSourceRevision(receipt.asset_updated_at, input.expected_asset_updated_at);
}
