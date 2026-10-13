import type { LocalMember, LocalMemberListResponse } from '../types/members';
export type { LocalMember, LocalMemberListResponse } from '../types/members';
import { request } from './transport';
/**
 * Local member commands fail with a JSON body `{"error": kind, "detail": message}`;
 * surface the human-readable `detail` instead of the raw body.
 */
export function membersErrorMessage(cause: unknown): string {
    if (cause instanceof Error && cause.message) {
        try {
            const payload = JSON.parse(cause.message) as {
                detail?: unknown;
            };
            if (payload && typeof payload.detail === "string")
                return payload.detail;
        }
        catch {
            // not a JSON error body; fall through to the raw message
        }
        return cause.message;
    }
    return "成员操作失败";
}
export function listLocalMembers(): Promise<LocalMemberListResponse> {
    return request<LocalMemberListResponse>("/api/members");
}
export function addLocalMember(input: {
    name: string;
    role?: "owner" | "member";
}): Promise<LocalMember> {
    return request<LocalMember>("/api/members", { method: "POST", body: JSON.stringify(input) });
}
export function removeLocalMember(memberId: string): Promise<{
    deleted: string;
}> {
    return request<{
        deleted: string;
    }>(`/api/members/${encodeURIComponent(memberId)}`, { method: "DELETE" });
}
export function setLocalMemberRole(memberId: string, role: "owner" | "member"): Promise<LocalMember> {
    return request<LocalMember>(`/api/members/${encodeURIComponent(memberId)}`, { method: "PATCH", body: JSON.stringify({ role }) });
}
export function createLocalInviteCode(): Promise<{
    invite_code: string;
}> {
    return request<{
        invite_code: string;
    }>("/api/members/invite-code", { method: "POST" });
}
/**
 * 加入空间。后端 `POST /api/members/join` 返回 `MemberWithTokenResponse`，其
 * `member_token` 是调用方唯一能拿到本人令牌的时机（main.py 的注释：token 只在
 * join 与 whoami 两处暴露）。所以这里如实返回带令牌的类型，调用方必须立刻落盘，
 * 否则后续请求会一直匿名（方案 A 下按 owner 放行，多成员形同虚设）。
 */
export function joinLocalMember(input: {
    code: string;
    name: string;
}): Promise<LocalMember & {
    member_token: string;
}> {
    return request<LocalMember & {
        member_token: string;
    }>("/api/members/join", { method: "POST", body: JSON.stringify(input) });
}
