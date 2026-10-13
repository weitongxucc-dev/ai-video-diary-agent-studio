export function visibleError(cause: unknown, fallback: string) {
    if (!(cause instanceof Error))
        return fallback;
    let message = cause.message;
    if (message.startsWith("{")) {
        try {
            const payload = JSON.parse(message) as {
                detail?: unknown;
            };
            message = typeof payload.detail === "string" ? payload.detail : "";
        }
        catch {
            return fallback;
        }
    }
    if (message === "API server unreachable")
        return "暂时无法连接本地服务，请确认应用正常运行后重试";
    return /[\u3400-\u9fff]/.test(message) ? message : fallback;
}
