import { localMemberTokenHeader } from "../local-member-auth";
/**
 * Browser requests use the relative Next.js `/api` route so middleware can
 * proxy them to the bundled Python server. Server Components and Server
 * Actions run in Node, where a relative URL is invalid; they use the same
 * runtime-selected API port directly instead.
 */
const serverApiBase = process.env.API_PORT
    ? `http://127.0.0.1:${process.env.API_PORT}`
    : (process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000");
export const API_BASE = typeof window === "undefined"
    ? serverApiBase
    : (process.env.NEXT_PUBLIC_API_BASE ?? "");
export function apiUrl(path: string): string {
    return path;
}
export type ServerAuthorizationProvider = () => Promise<string | null>;
let serverAuthorizationProvider: ServerAuthorizationProvider | null = null;
export function setServerAuthorizationProvider(provider: ServerAuthorizationProvider): void {
    serverAuthorizationProvider = provider;
}
export class ApiRequestError extends Error {
    constructor(public readonly status: number, message: string) { super(message); }
}
/** Identity injection is shared by JSON, streams and native body/media reads. */
export async function authorizedFetch(url: string, init?: RequestInit): Promise<Response> {
    const serverAuthorization = await getServerAuthorization();
    return fetch(url, {
        ...init,
        headers: {
            ...(init?.headers ?? {}),
            ...(serverAuthorization ? { Authorization: serverAuthorization } : {}),
            ...localMemberTokenHeader(),
        },
        cache: "no-store",
    });
}
export async function request<T>(path: string, init?: RequestInit): Promise<T> {
    const response = await authorizedFetch(`${API_BASE}${path}`, {
        ...init,
        headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    });
    if (!response.ok) {
        const message = await response.text();
        throw new ApiRequestError(response.status, message || `Request failed: ${response.status}`);
    }
    return response.json() as Promise<T>;
}
export async function getServerAuthorization(): Promise<string | null> {
    if (typeof window !== "undefined" || !serverAuthorizationProvider)
        return null;
    return serverAuthorizationProvider();
}
